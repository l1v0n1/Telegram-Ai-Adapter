import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
PHONE_NUMBER = os.getenv("PHONE_NUMBER") # Optional

# Target bot usernames (without @)
TARGET_BOTS = {
    "grok": "grokai",
    "copilot": "copilotofficialbot",
    "plex": "askplexbot"
}

# Simple in-memory mapping for user requests <-> message IDs
# In a production scenario, a database (e.g., Redis, SQLite) would be better
FORWARDING_MAP = {}

# Placeholder for rate limit information (can be more sophisticated)
RATE_LIMIT_INFO = {
    "grok": {"limit_exceeded": False, "retry_after": 0},
    "copilot": {"limit_exceeded": False, "retry_after": 0},
    "plex": {"limit_exceeded": False, "retry_after": 0}
}

# Map: pyrogram_sent_msg_id -> (original_user_chat_id, original_user_msg_id, bot_key)
# Used to correlate replies/edits from target bots back to the user request
PYROGRAM_SENT_MSG_MAP = {}

# Map: plex_thinking_msg_id -> (original_user_chat_id, original_user_msg_id, bot_key)
# Used specifically to correlate Perplexity edits back to the user
PLEX_THINKING_TO_USER_MAP = {}

# Timeout for /all requests in seconds
ALL_REQUEST_TIMEOUT = 45 