import logging
import asyncio
import re
from pyrogram import Client, filters, types, errors
from pyrogram.enums import ChatType

import config
from bridge import queue_to_aiogram # Import the queue

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Use a session name unique to the user account
app = Client("user_account", api_id=config.API_ID, api_hash=config.API_HASH, phone_number=config.PHONE_NUMBER)

target_bot_usernames = list(config.TARGET_BOTS.values())

async def get_target_bot_ids():
    """Resolves bot usernames to their user IDs. Assumes client is already running."""
    ids = {}
    # No longer use 'async with app:' here as app is managed externally
    if not app.is_connected:
        logger.error("Pyrogram client not connected in get_target_bot_ids")
        return None # Indicate failure
    
    for key, username in config.TARGET_BOTS.items():
        try:
            user = await app.get_users(username)
            if user and isinstance(user, types.User) and user.is_bot:
                ids[user.id] = key # Store mapping ID -> key (grok/copilot/plex)
                logger.info(f"Resolved @{username} to ID: {user.id}")
            else:
                logger.error(f"Could not resolve or invalid user: @{username}")
        except errors.UsernameNotOccupied:
            logger.error(f"Username not occupied: @{username}")
        except Exception as e:
            logger.error(f"Error resolving @{username}: {e}")
    return ids

target_bot_ids_map = {}

# --- Keywords to identify intermediate messages from Perplexity (adjust as needed) ---
# Using regex for flexibility (case-insensitive)
PLEX_INTERMEDIATE_PATTERNS = re.compile(
    # Match lines STARTING with keywords, optionally followed by ... or other chars/emojis
    r"^(Thinking|Searching|Generating|Working on it|Just a moment|Let me check|Analyzing|Let me see|Hold on)(|\.\.\.).*",
    re.IGNORECASE
)

# --- Helper to find the latest sent message ID for a bot ---
# WARNING: This is a simplification and not robust for high concurrency!
def find_latest_sent_msg_id_for_bot(bot_key):
    latest_id = None
    for msg_id, (_, _, map_bot_key) in reversed(config.PYROGRAM_SENT_MSG_MAP.items()):
        if map_bot_key == bot_key:
            latest_id = msg_id
            break
    return latest_id


@app.on_message(filters.private & filters.bot & filters.incoming)
async def from_target_bots(client: Client, message: types.Message):
    """Handles NEW messages received FROM the target bots."""
    sender_id = message.from_user.id
    if sender_id not in target_bot_ids_map:
        return # Not from a target bot

    bot_key = target_bot_ids_map[sender_id]
    logger.info(f"[New] Received message from {bot_key} bot (ID: {sender_id}), MsgID: {message.id}, ReplyTo: {message.reply_to_message_id}")

    # --- Rate Limit Handling (Simplified) ---
    if "rate limit exceeded" in (message.text or "").lower():
        retry_after_seconds = 30 # Default
        config.RATE_LIMIT_INFO[bot_key]["limit_exceeded"] = True
        config.RATE_LIMIT_INFO[bot_key]["retry_after"] = retry_after_seconds
        logger.warning(f"[New] Rate limit detected for {bot_key}. Retry after: {retry_after_seconds}s")
        # Find original user request (best effort using reply_to first)
        original_info = config.PYROGRAM_SENT_MSG_MAP.get(message.reply_to_message_id) 
        if not original_info: # Fallback to latest sent if no reply_to correlation
             latest_sent_id = find_latest_sent_msg_id_for_bot(bot_key)
             if latest_sent_id:
                 original_info = config.PYROGRAM_SENT_MSG_MAP.get(latest_sent_id)
        
        if original_info:
            await queue_to_aiogram.put({
                "type": "rate_limit",
                "bot_key": bot_key, "retry_after": retry_after_seconds,
                "chat_id": original_info[0], "reply_to_message_id": original_info[1],
            })
        else:
            logger.warning(f"Could not correlate rate limit message from {bot_key} to a user request.")
        return
    else:
        if config.RATE_LIMIT_INFO[bot_key]["limit_exceeded"]:
            logger.info(f"[New] Rate limit seems lifted for {bot_key}")
        config.RATE_LIMIT_INFO[bot_key]["limit_exceeded"] = False
        config.RATE_LIMIT_INFO[bot_key]["retry_after"] = 0

    # --- Special Handling for NEW Perplexity Intermediate Messages ---
    if bot_key == "plex" and message.text and PLEX_INTERMEDIATE_PATTERNS.search(message.text):
        logger.info(f"[New] Got intermediate message from Plex: '{message.text[:50]}...'")
        latest_sent_plex_msg_id = find_latest_sent_msg_id_for_bot("plex")
        if latest_sent_plex_msg_id:
            original_info = config.PYROGRAM_SENT_MSG_MAP.get(latest_sent_plex_msg_id)
            if original_info:
                config.PLEX_THINKING_TO_USER_MAP[message.id] = original_info
                logger.info(f"Linked Plex thinking msg {message.id} to user request via sent msg {latest_sent_plex_msg_id}")
            else:
                logger.warning(f"Could not find user info for latest sent Plex msg {latest_sent_plex_msg_id}")
        else:
            logger.warning("Could not find latest sent Plex msg ID to link thinking message")
        return # Ignore this message, wait for the edit

    # --- Default Correlation Handling for NEW messages --- 
    original_info = None
    correlation_method = "None"

    # 1. Try correlating via reply_to_message_id (ideal)
    if message.reply_to_message_id:
        correlation_key = message.reply_to_message_id
        original_info = config.PYROGRAM_SENT_MSG_MAP.pop(correlation_key, None)
        if original_info:
            correlation_method = f"ReplyTo ({correlation_key})"

    # 2. Fallback for Grok/Copilot (if no reply_to match was found or reply_to is None)
    if not original_info and bot_key in ["grok", "copilot"]:
        logger.debug(f"Attempting fallback correlation for {bot_key}...")
        latest_sent_id = find_latest_sent_msg_id_for_bot(bot_key)
        if latest_sent_id:
            # Use get() first to check, then pop() if valid
            potential_info = config.PYROGRAM_SENT_MSG_MAP.get(latest_sent_id)
            if potential_info:
                 # We assume this latest message corresponds. Pop it.
                 original_info = config.PYROGRAM_SENT_MSG_MAP.pop(latest_sent_id)
                 correlation_method = f"LatestSent ({latest_sent_id})"
                 logger.info(f"Used fallback correlation for {bot_key} via latest sent msg {latest_sent_id}")
            else:
                 logger.warning(f"Found latest sent ID {latest_sent_id} for {bot_key}, but info was already gone from map.")
        else:
            logger.warning(f"Fallback correlation failed for {bot_key}: Could not find latest sent message ID.")

    # --- Process result --- 
    if original_info:
        original_user_chat_id, original_user_message_id, original_bot_key = original_info
        if original_bot_key == bot_key:
            logger.info(f"[New] Correlated bot msg {message.id} (via {correlation_method}) to user ({original_user_chat_id}, {original_user_message_id})")
            await queue_to_aiogram.put({
                "type": "bot_response", "bot_key": bot_key,
                "chat_id": original_user_chat_id, "reply_to_message_id": original_user_message_id,
                "text": message.text or ""
            })
        else:
            # Put the info back if the bot key didn't match (should be rare)
            config.PYROGRAM_SENT_MSG_MAP[correlation_key if message.reply_to_message_id else latest_sent_id] = original_info
            logger.warning(f"[New] Bot key mismatch! Correlation method {correlation_method} points to {original_bot_key}, but message is from {bot_key}. Restored map entry and discarding message.")
    else:
        # Only log warning if it wasn't an intermediate Plex message
        if not (bot_key == "plex" and message.text and PLEX_INTERMEDIATE_PATTERNS.search(message.text)):
             logger.warning(f"[New] Could not correlate bot message {message.id} from {bot_key} using any method. Discarding.")


@app.on_edited_message(filters.private & filters.bot)
async def on_bot_message_edit(client: Client, message: types.Message):
    """Handles EDITED messages received FROM the target bots (e.g., Perplexity)."""
    sender_id = message.from_user.id
    if sender_id not in target_bot_ids_map:
        return

    bot_key = target_bot_ids_map[sender_id]
    logger.info(f"[Edit] Received edit from {bot_key} bot (ID: {sender_id}), MsgID: {message.id}")

    # --- Rate Limit Check on Edit (less likely, but possible) ---
    # (Simplified rate limit check omitted for brevity in edit handler, add if needed)

    # --- Special Handling for Perplexity Edits ---
    if bot_key == "plex":
        # Check if this edit corresponds to a known "thinking" message
        original_info = config.PLEX_THINKING_TO_USER_MAP.pop(message.id, None)
        if original_info:
            original_user_chat_id, original_user_message_id, original_bot_key = original_info
            logger.info(f"[Edit] Correlated Plex edit {message.id} to user ({original_user_chat_id}, {original_user_message_id}) via thinking map")
            await queue_to_aiogram.put({
                "type": "bot_response", "bot_key": bot_key,
                "chat_id": original_user_chat_id, "reply_to_message_id": original_user_message_id,
                "text": message.text or ""
            })
            # Attempt to clean up the original PYROGRAM_SENT_MSG_MAP entry (best effort)
            # This part is complex without knowing the exact sent_msg_id here.
            # A proper solution might store the sent_msg_id in PLEX_THINKING_TO_USER_MAP too.
            return # Processed as Plex edit
        else:
            # If not found in thinking map, maybe it's an edit of a msg we thought was final?
            logger.warning(f"[Edit] Plex edit {message.id} not found in thinking map. Checking standard correlation...")
            # Fall through to standard correlation below
    
    # --- Standard Handling for Edits (non-Plex, or Plex edit not in thinking map) ---
    # This assumes the ID being edited is the message we sent
    correlation_key = message.id 
    original_info = config.PYROGRAM_SENT_MSG_MAP.pop(correlation_key, None)
    if original_info:
        original_user_chat_id, original_user_message_id, original_bot_key = original_info
        if original_bot_key == bot_key:
            logger.info(f"[Edit] Correlated edit {message.id} (key {correlation_key}) to user ({original_user_chat_id}, {original_user_message_id}) via sent map")
            await queue_to_aiogram.put({
                "type": "bot_response", "bot_key": bot_key,
                "chat_id": original_user_chat_id, "reply_to_message_id": original_user_message_id,
                "text": message.text or ""
            })
        else:
            logger.warning(f"[Edit] Bot key mismatch! Edit key {correlation_key} points to {original_bot_key}, but message is from {bot_key}. Discarding.")
    else:
        # Log only if it wasn't found in the thinking map either (for Plex)
        if bot_key != 'plex' or message.id not in config.PLEX_THINKING_TO_USER_MAP:
            logger.warning(f"[Edit] Could not correlate edit {message.id} (key {correlation_key}) from {bot_key}. Discarding.")


async def send_to_bot(bot_key: str, user_chat_id: int, user_message_id: int, text: str):
    """Sends a message TO a target bot via the user account."""
    if bot_key not in config.TARGET_BOTS:
        logger.error(f"Unknown bot key: {bot_key}")
        return

    # Check rate limit before sending
    if config.RATE_LIMIT_INFO[bot_key]["limit_exceeded"]:
        retry_after = config.RATE_LIMIT_INFO[bot_key]["retry_after"]
        logger.warning(f"Cannot send to {bot_key} due to rate limit. Try again in {retry_after}s.")
        await queue_to_aiogram.put({
            "type": "rate_limit_hit", # Tell aiogram the request was blocked
            "bot_key": bot_key,
            "chat_id": user_chat_id,
            "reply_to_message_id": user_message_id,
            "retry_after": retry_after
        })
        return

    bot_username = config.TARGET_BOTS[bot_key]
    sent_message = None
    try:
        logger.info(f"Sending message to @{bot_username}: '{text[:50]}...' for user ({user_chat_id}, {user_message_id})")
        sent_message = await app.send_message(bot_username, text)
        logger.info(f"Message sent to @{bot_username}, message_id: {sent_message.id}")

        # Store mapping: sent_message_id -> (original_user_chat_id, original_user_msg_id, bot_key)
        config.PYROGRAM_SENT_MSG_MAP[sent_message.id] = (user_chat_id, user_message_id, bot_key)
        logger.debug(f"Stored correlation map: {sent_message.id} -> ({user_chat_id}, {user_message_id}, {bot_key})")

    except errors.FloodWait as e:
        logger.warning(f"Flood wait error when sending to @{bot_username}: {e.value} seconds")
        config.RATE_LIMIT_INFO[bot_key]["limit_exceeded"] = True
        config.RATE_LIMIT_INFO[bot_key]["retry_after"] = e.value
        await queue_to_aiogram.put({
            "type": "rate_limit_hit", # Tell aiogram the request was blocked
            "bot_key": bot_key,
            "chat_id": user_chat_id,
            "reply_to_message_id": user_message_id,
            "retry_after": e.value
        })
    except Exception as e:
        logger.error(f"Error sending message to @{bot_username}: {e}")
        # Inform user about the failure to send
        await queue_to_aiogram.put({
            "type": "send_error",
            "bot_key": bot_key,
            "chat_id": user_chat_id,
            "reply_to_message_id": user_message_id,
            "error": str(e)
        })

async def run_pyrogram_client():
    # This function is now primarily for initialization logic before start
    # The actual running is handled by main.py using app.start() and waiting
    global target_bot_ids_map
    logger.info("Initializing pyrogram client logic...")
    if not config.API_ID or not config.API_HASH:
        logger.critical("API_ID and API_HASH must be set in .env file!")
        # Indicate failure (e.g., raise exception or return specific value)
        raise ValueError("API_ID and API_HASH not configured.")

    # The bot ID resolution is now done in main.py AFTER app.start()
    # So, we don't call get_target_bot_ids here anymore.
    logger.info("Pyrogram client handlers registered. Ready to be started.")
    # No app.run() here anymore

if __name__ == "__main__":
    # This standalone mode is mainly for the initial login/session creation
    async def standalone_login():
        print("Running Pyrogram client in standalone mode for login...")
        async with app:
            me = await app.get_me()
            print(f"Successfully logged in as {me.first_name} (@{me.username})")
            print("Session file created/updated. You can now stop this (Ctrl+C) and run main.py.")
            # Keep running until interrupted to allow handlers to register if needed briefly
            await asyncio.Event().wait()
    
    asyncio.run(standalone_login()) 