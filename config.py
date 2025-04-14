import os
import json # Import the json module
import logging # Import logging
from dotenv import load_dotenv

load_dotenv()

# Setup basic logging for config loading issues
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
PHONE_NUMBER = os.getenv("PHONE_NUMBER") # Optional

# Load Target bot usernames from .env
target_bots_json = os.getenv("TARGET_BOTS", '{}') # Default to empty JSON object
TARGET_BOTS = {}
try:
    TARGET_BOTS = json.loads(target_bots_json)
    if not isinstance(TARGET_BOTS, dict):
        logger.error("TARGET_BOTS in .env is not a valid JSON object (dictionary). Using empty dictionary.")
        TARGET_BOTS = {}
except json.JSONDecodeError:
    logger.error("Failed to parse TARGET_BOTS from .env. Ensure it is a valid JSON string. Using empty dictionary.", exc_info=True)
    TARGET_BOTS = {}

if not TARGET_BOTS:
    logger.warning("TARGET_BOTS is empty. No target bots configured. Check your .env file.")


# Simple in-memory mapping for user requests <-> message IDs
# In a production scenario, a database (e.g., Redis, SQLite) would be better
FORWARDING_MAP = {}

# Placeholder for rate limit information (can be more sophisticated)
# Initialize dynamically based on loaded TARGET_BOTS
RATE_LIMIT_INFO = {key: {"limit_exceeded": False, "retry_after": 0} for key in TARGET_BOTS.keys()}

# Map: pyrogram_sent_msg_id -> (original_user_chat_id, original_user_msg_id, bot_key)
# Used to correlate replies/edits from target bots back to the user request
PYROGRAM_SENT_MSG_MAP = {}

# Map: plex_thinking_msg_id -> (original_user_chat_id, original_user_msg_id, bot_key)
# Used specifically to correlate Perplexity edits back to the user
PLEX_THINKING_TO_USER_MAP = {}

# Timeout for /all requests in seconds
ALL_REQUEST_TIMEOUT = 45 