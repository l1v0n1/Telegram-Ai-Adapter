import logging
import asyncio
import time
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.utils.exceptions import MessageNotModified, MessageToEditNotFound, MessageCantBeEdited
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import config
from bridge import queue_to_aiogram, queue_to_pyrogram

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not config.BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found in environment variables")

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# --- State Maps --- 
# Map: (user_chat_id, user_message_id) -> bot_placeholder_message_id (for single requests)
placeholder_message_map = {}
# Map: (user_chat_id, user_message_id) -> state_dict (for /all requests)
all_request_states = {}

# --- Helper Functions for /all --- 

def create_all_markup(state: dict) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=len(config.TARGET_BOTS))
    buttons = []
    # Ensure consistent order based on config keys
    for bot_key in config.TARGET_BOTS.keys():
        button_text = bot_key.capitalize()
        if bot_key == state.get("current_view"): 
            button_text = f"> {button_text} <"
        elif bot_key in state["responses"]:
            button_text += " ✅" # Mark completed
        else:
             button_text += " ⏳" # Mark pending
        buttons.append(InlineKeyboardButton(button_text, callback_data=f"all_view:{bot_key}"))
    markup.add(*buttons)
    return markup

def format_all_message(state: dict) -> str:
    current_bot = state.get("current_view")
    response_text = state["responses"].get(current_bot, f"_Response from {current_bot.capitalize()} not received yet..._" if current_bot else "_Select a bot to view its response._")
    
    header = f"*Responses for:* `{state['prompt'][:100]}{'...' if len(state['prompt']) > 100 else ''}`\n\n"
    footer = "\n--------------------\n"
    
    if not current_bot:
        return header + "_Select a bot below to view its response._"
    
    # Ensure minimum length for editing later if needed
    full_text = f"{header}--- *{current_bot.capitalize()}* ---\n{response_text}{footer}"
    return full_text[:4090] # Keep within Telegram limits, approx

async def finalize_all_request(key, reason="Timeout"):
    # Don't remove the state, just mark as finalized and update the message
    # state = all_request_states.pop(key, None)
    state = all_request_states.get(key)
    if not state or state.get("finalized", False): # Check if exists and not already finalized
        return
    
    logger.info(f"Finalizing /all request for {key} due to {reason}")
    state["finalized"] = True # Mark as finalized
    
    # Cancel timeout task if it's still running
    if state.get("timeout_task") and not state["timeout_task"].done():
        state["timeout_task"].cancel()
        state["timeout_task"] = None # Clear the task reference

    chat_id, _ = key
    placeholder_msg_id = state["placeholder_msg_id"]
    
    # Determine final text and markup
    if not state["responses"]:
         final_text = "_No responses received from any bot before timeout._"
         markup = None # No buttons if no responses
    else:
        # Default to showing the first bot that responded, or the first configured bot
        if not state.get("current_view"):
             state["current_view"] = next((b for b in config.TARGET_BOTS.keys() if b in state["responses"]), list(config.TARGET_BOTS.keys())[0])
        final_text = format_all_message(state)
        markup = create_all_markup(state) # Keep the markup
        
    try:
        await bot.edit_message_text(
            text=final_text,
            chat_id=chat_id,
            message_id=placeholder_msg_id,
            reply_markup=markup, # Ensure markup is included in the final edit
            parse_mode=types.ParseMode.MARKDOWN
        )
        logger.info(f"Final message/markup updated for /all request {key}")
    except Exception as e:
        logger.error(f"Failed to edit final /all message {placeholder_msg_id} for {key}: {e}")

# --- Message Processing --- 

async def process_queue_to_aiogram():
    """Listens to the queue from pyrogram and processes messages."""
    while True:
        item = await queue_to_aiogram.get()
        item_processed = False
        
        try:
            logger.info(f"Received item from pyrogram queue: {item['type']}")
            event_type = item.get("type")
            bot_key = item.get("bot_key")
            user_chat_id = item.get("chat_id")
            user_reply_to_msg_id = item.get("reply_to_message_id") # Original user message ID

            if not user_chat_id or not user_reply_to_msg_id:
                 logger.warning(f"Received item without sufficient chat/message ID: {item}")
                 queue_to_aiogram.task_done()
                 continue

            original_request_key = (user_chat_id, user_reply_to_msg_id)

            # --- Check if it belongs to an active /all request --- 
            if original_request_key in all_request_states:
                state = all_request_states[original_request_key]
                placeholder_msg_id = state["placeholder_msg_id"]
                all_bots = set(config.TARGET_BOTS.keys())
                
                if event_type == "bot_response" and bot_key:
                    state["responses"][bot_key] = item.get("text", "_Error: Empty response received._")
                    logger.info(f"Stored /all response from {bot_key} for {original_request_key}")
                    
                    # Update message if this bot is currently viewed or no view is set
                    if state.get("current_view") == bot_key or not state.get("current_view"):
                         if not state.get("current_view"): # Set first response as default view
                              state["current_view"] = bot_key
                         try:
                              await bot.edit_message_text(
                                   text=format_all_message(state),
                                   chat_id=user_chat_id,
                                   message_id=placeholder_msg_id,
                                   reply_markup=create_all_markup(state),
                                   parse_mode=types.ParseMode.MARKDOWN
                              )
                         except Exception as e:
                              logger.error(f"Error updating /all message {placeholder_msg_id} after {bot_key} response: {e}")
                    else:
                         # Just update markup if a different bot is viewed
                         try:
                              await bot.edit_message_reply_markup(
                                   chat_id=user_chat_id,
                                   message_id=placeholder_msg_id,
                                   reply_markup=create_all_markup(state)
                              )
                         except Exception as e:
                              logger.error(f"Error updating /all markup for {placeholder_msg_id} after {bot_key} response: {e}")

                    # Check if all responses received
                    if state["responses"].keys() == all_bots:
                         logger.info(f"All responses received for /all request {original_request_key}")
                         await finalize_all_request(original_request_key, reason="All responses received")
                    
                    item_processed = True
                
                elif event_type in ["rate_limit", "rate_limit_hit", "send_error"] and bot_key:
                     # Store error status for the specific bot
                     error_text = f"_Failed: {item.get('type')}_"
                     if item.get('retry_after'): error_text += f" (Retry in {item['retry_after']}s)"
                     if item.get('error'): error_text += f" ({item.get('error')})"
                     state["responses"][bot_key] = error_text
                     logger.warning(f"Stored /all error state for {bot_key} for {original_request_key}: {error_text}")
                     # Update display similar to how bot_response does
                     if state.get("current_view") == bot_key or not state.get("current_view"):
                        if not state.get("current_view"): state["current_view"] = bot_key
                        try:
                            await bot.edit_message_text(text=format_all_message(state), chat_id=user_chat_id, message_id=placeholder_msg_id, reply_markup=create_all_markup(state), parse_mode=types.ParseMode.MARKDOWN)
                        except Exception as e: logger.error(f"Error updating /all msg on error for {bot_key}: {e}")
                     else:
                        try:
                            await bot.edit_message_reply_markup(chat_id=user_chat_id, message_id=placeholder_msg_id, reply_markup=create_all_markup(state))
                        except Exception as e: logger.error(f"Error updating /all markup on error for {bot_key}: {e}")
                     # Check if all settled (either response or error)
                     if state["responses"].keys() == all_bots:
                         await finalize_all_request(original_request_key, reason="All settled (error/response)")
                     item_processed = True
                
                else: # Unknown type for /all request
                     logger.warning(f"Unhandled event type {event_type} for active /all request {original_request_key}")
            
            # --- If not processed as /all, handle as single request --- 
            if not item_processed:
                placeholder_key = (user_chat_id, user_reply_to_msg_id)
                bot_placeholder_msg_id = placeholder_message_map.get(placeholder_key)

                if event_type == "bot_response":
                    response_text = "*{bot_key}*:\n\n{text}".format(
                        bot_key=item['bot_key'].capitalize(),
                        text=item['text']
                    )
                    if bot_placeholder_msg_id:
                        try:
                            await bot.edit_message_text(text=response_text, chat_id=user_chat_id, message_id=bot_placeholder_msg_id, parse_mode=types.ParseMode.MARKDOWN)
                            logger.info(f"Edited single message {bot_placeholder_msg_id} in chat {user_chat_id}")
                            if placeholder_key in placeholder_message_map: del placeholder_message_map[placeholder_key]
                        except (MessageNotModified, MessageToEditNotFound, MessageCantBeEdited) as e:
                            logger.warning(f"Failed to edit single message {bot_placeholder_msg_id}: {e}. Sending new.")
                            if placeholder_key in placeholder_message_map: del placeholder_message_map[placeholder_key]
                            await bot.send_message(chat_id=user_chat_id, text=response_text, reply_to_message_id=user_reply_to_msg_id, parse_mode=types.ParseMode.MARKDOWN)
                        except Exception as e:
                            logger.error(f"Unexpected error editing single message {bot_placeholder_msg_id}: {e}", exc_info=True)
                            if placeholder_key in placeholder_message_map: del placeholder_message_map[placeholder_key]
                    else:
                        logger.warning(f"No placeholder message ID found for single request {placeholder_key}. Sending new message.")
                        await bot.send_message(chat_id=user_chat_id, text=response_text, reply_to_message_id=user_reply_to_msg_id, parse_mode=types.ParseMode.MARKDOWN)
                
                elif event_type == "rate_limit":
                    rate_limit_text=f"⚠️ Rate limit detected for *{item['bot_key'].capitalize()}*. Please wait ~{item['retry_after']} seconds."
                    await bot.send_message(chat_id=user_chat_id, text=rate_limit_text, reply_to_message_id=user_reply_to_msg_id, parse_mode=types.ParseMode.MARKDOWN)
                
                elif event_type == "rate_limit_hit":
                    rate_limit_hit_text=f"⏳ Request to *{item['bot_key'].capitalize()}* blocked by rate limit. Try again in ~{item['retry_after']}s."
                    if bot_placeholder_msg_id:
                        try:
                            await bot.edit_message_text(text=rate_limit_hit_text, chat_id=user_chat_id, message_id=bot_placeholder_msg_id, parse_mode=types.ParseMode.MARKDOWN)
                            if placeholder_key in placeholder_message_map: del placeholder_message_map[placeholder_key]
                        except Exception as e: 
                            logger.warning(f"Failed to edit single message {bot_placeholder_msg_id} for rate_limit_hit: {e}. Sending new.")
                            if placeholder_key in placeholder_message_map: del placeholder_message_map[placeholder_key]
                            await bot.send_message(chat_id=user_chat_id, text=rate_limit_hit_text, reply_to_message_id=user_reply_to_msg_id, parse_mode=types.ParseMode.MARKDOWN)
                    else:
                        await bot.send_message(chat_id=user_chat_id, text=rate_limit_hit_text, reply_to_message_id=user_reply_to_msg_id, parse_mode=types.ParseMode.MARKDOWN)
                
                elif event_type == "send_error":
                    send_error_text = f"❌ Error sending message to *{item['bot_key'].capitalize()}*: {item.get('error', 'Unknown error')}"
                    if bot_placeholder_msg_id:
                        try:
                            await bot.edit_message_text(text=send_error_text, chat_id=user_chat_id, message_id=bot_placeholder_msg_id, parse_mode=types.ParseMode.MARKDOWN)
                            if placeholder_key in placeholder_message_map: del placeholder_message_map[placeholder_key]
                        except Exception as e: 
                            logger.warning(f"Failed to edit single message {bot_placeholder_msg_id} for send_error: {e}. Sending new.")
                            if placeholder_key in placeholder_message_map: del placeholder_message_map[placeholder_key]
                            await bot.send_message(chat_id=user_chat_id, text=send_error_text, reply_to_message_id=user_reply_to_msg_id, parse_mode=types.ParseMode.MARKDOWN)
                    else:
                        await bot.send_message(chat_id=user_chat_id, text=send_error_text, reply_to_message_id=user_reply_to_msg_id, parse_mode=types.ParseMode.MARKDOWN)
                else:
                    logger.warning(f"Unknown item type for single request: {event_type}")

        except Exception as e:
            logger.error(f"General error processing item from queue_to_aiogram: {item} - {e}", exc_info=True)
            # Clean up maps if processing failed?
            if 'original_request_key' in locals():
                 if original_request_key in all_request_states: all_request_states.pop(original_request_key, None)
                 if original_request_key in placeholder_message_map: placeholder_message_map.pop(original_request_key, None)
        finally:
            queue_to_aiogram.task_done()

@dp.message_handler(commands=['start', 'help'])
async def send_welcome(message: types.Message):
    """Sends a welcome message."""
    await message.reply(
        "Hi! I can forward your messages to specific bots or all at once.\n" 
        "Use `/grok <prompt>`, `/copilot <prompt>`, `/plex <prompt>` for individual bots.\n" 
        "Use `/all <prompt>` to query all bots.\n"
        "You can also mention me in a group (@my_bot_username) followed by the command.\n"
        "Note: This uses the bot owner's premium account."
    )

async def forward_to_pyrogram(bot_key: str, message: types.Message):
    """Handles single bot requests."""
    prompt = message.get_args()
    if not prompt:
        await message.reply(f"Please provide a prompt after /{bot_key}")
        return

    placeholder_message = await message.reply(f"⏳ Forwarding to {bot_key.capitalize()}...", disable_notification=True)
    placeholder_key = (message.chat.id, message.message_id)
    placeholder_message_map[placeholder_key] = placeholder_message.message_id
    logger.info(f"Stored placeholder message ID {placeholder_message.message_id} for single request key {placeholder_key}")
    
    await queue_to_pyrogram.put({
        "type": "user_request", "bot_key": bot_key,
        "chat_id": message.chat.id, "message_id": message.message_id,
        "text": prompt
    })

@dp.message_handler(commands=['all'])
async def handle_all(message: types.Message):
    """Handles requests to all bots."""
    prompt = message.get_args()
    if not prompt:
        await message.reply("Please provide a prompt after /all")
        return

    placeholder_message = await message.reply("⏳ Querying all bots...", disable_notification=True)
    key = (message.chat.id, message.message_id)
    
    # Schedule timeout task
    timeout_task = asyncio.create_task(asyncio.sleep(config.ALL_REQUEST_TIMEOUT))
    timeout_task.add_done_callback(lambda _: asyncio.create_task(finalize_all_request(key, reason="Timeout")))

    all_request_states[key] = {
        "placeholder_msg_id": placeholder_message.message_id,
        "expected_bots": set(config.TARGET_BOTS.keys()),
        "responses": {},
        "current_view": None, # No bot selected initially
        "prompt": prompt,
        "timeout_task": timeout_task
    }
    logger.info(f"Initialized /all state for key {key}, placeholder {placeholder_message.message_id}")

    await queue_to_pyrogram.put({
        "type": "all_request",
        "chat_id": message.chat.id,
        "message_id": message.message_id,
        "text": prompt
    })

@dp.callback_query_handler(lambda c: c.data and c.data.startswith('all_view:'))
async def process_all_view_callback(callback_query: types.CallbackQuery):
    """Handles button clicks to switch view in /all responses."""
    try:
        bot_key_to_view = callback_query.data.split(':')[1]
        message = callback_query.message
        key = (message.chat.id, message.reply_to_message.message_id) # Key is based on the ORIGINAL user message

        state = all_request_states.get(key)
        if not state:
            await callback_query.answer("This request has expired or is invalid.", show_alert=True)
            # Optionally try to remove the keyboard if the message still exists
            try: await bot.edit_message_reply_markup(message.chat.id, message.message_id, reply_markup=None)
            except: pass 
            return
        
        if bot_key_to_view not in config.TARGET_BOTS:
             await callback_query.answer("Invalid bot selected.", show_alert=True)
             return

        state["current_view"] = bot_key_to_view
        
        await bot.edit_message_text(
            text=format_all_message(state),
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=create_all_markup(state),
            parse_mode=types.ParseMode.MARKDOWN
        )
        await callback_query.answer(f"Showing {bot_key_to_view.capitalize()}")
        
    except MessageNotModified:
         await callback_query.answer() # Ignore if message hasn't changed
    except Exception as e:
        logger.error(f"Error processing all_view callback: {e}", exc_info=True)
        try: await callback_query.answer("Error switching view.", show_alert=True)
        except Exception: pass # Ignore answer errors


# --- Standard Command Handlers --- 

@dp.message_handler(commands=['grok'])
async def handle_grok(message: types.Message):
    await forward_to_pyrogram("grok", message)

@dp.message_handler(commands=['copilot'])
async def handle_copilot(message: types.Message):
    await forward_to_pyrogram("copilot", message)

@dp.message_handler(commands=['plex'])
async def handle_plex(message: types.Message):
    await forward_to_pyrogram("plex", message)

# Handling mentions in groups (Update to include /all)
@dp.message_handler(content_types=types.ContentType.TEXT)
async def handle_text(message: types.Message):
    bot_username = (await bot.me).username
    mention = f"@{bot_username}"
    if message.chat.type in [types.ChatType.GROUP, types.ChatType.SUPERGROUP] and message.text.startswith(mention):
        text_without_mention = message.text[len(mention):].strip()
        command_args = text_without_mention.split(maxsplit=1)
        command = command_args[0].lower() if command_args else ""
        args_text = command_args[1] if len(command_args) > 1 else ""
        
        pseudo_message = message # Use original message for context
        pseudo_message.text = f"{command} {args_text}" # Reconstruct text for get_args() 
        
        if command == "/grok":
            await handle_grok(pseudo_message)
        elif command == "/copilot":
            await handle_copilot(pseudo_message)
        elif command == "/plex":
            await handle_plex(pseudo_message)
        elif command == "/all":
             await handle_all(pseudo_message)
        else:
            await message.reply("Please specify /grok, /copilot, /plex, or /all after mentioning me.")

async def run_aiogram_bot():
    logger.info("Starting aiogram bot processing queues...")
    asyncio.create_task(process_queue_to_aiogram())
    # Polling is started in main.py
    logger.info("Aiogram bot ready.")

if __name__ == '__main__':
    logger.warning("aiogram_bot.py should not be run directly for the main application. Run main.py instead.")
    pass 