# Telegram AI Adapter

This project acts as a bridge between your Telegram user account (via Pyrogram) and multiple target bots. It allows you to interact with several bots simultaneously using a single Aiogram-powered bot interface. Messages sent to the Aiogram bot can be forwarded to a specific target bot or broadcasted to all configured target bots. Replies from the target bots are then relayed back to you via the Aiogram bot.

## Features

*   **Single Interface:** Control multiple bots from one chat window.
*   **Direct Forwarding:** Send messages to a specific bot (e.g., `/bot1 Hello there`).
*   **Broadcasting:** Send messages to all configured bots simultaneously (e.g., `/all What is your status?`).
*   **Reply Handling:** Receives responses from target bots and forwards them back to your Aiogram chat.
*   **Asynchronous:** Built using `asyncio`, `Pyrogram`, and `Aiogram` for efficient operation.

## Installation

These instructions assume you have Python 3.8+ and `pip` installed on your system.

**1. Clone the Repository:**

```bash
git clone https://github.com/l1v0n1/Telegram-Ai-Adapter.git
cd Telegram-Ai-Adapter # Or your repository directory name
```

**2. Set up a Virtual Environment:**

It's highly recommended to use a virtual environment to manage dependencies.

*   **macOS / Linux:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

*   **Windows:**
    ```bash
    python -m venv venv
    .\venv\Scripts\activate
    ```

**3. Install Dependencies:**

```bash
pip install -r requirements.txt
```

## Configuration

**1. Telegram API Credentials:**

*   Go to [https://my.telegram.org/apps](https://my.telegram.org/apps) and log in with your Telegram account.
*   Create a new application (you can fill in dummy details for App title and Short name).
*   Note down the `api_id` and `api_hash` provided. **Treat these like passwords!**

**2. Aiogram Bot Token:**

*   Talk to the [BotFather](https://t.me/BotFather) on Telegram.
*   Create a new bot using `/newbot`.
*   Note down the `HTTP API token` provided. **Treat this like a password!**

**3. Target Bots:**

*   Identify the usernames (without the `@`) of the bots you want to interact with.

**4. Create `.env` File:**

*   In the root directory of the project, create a file named `.env`.
*   Add the following lines, replacing the placeholder values with your actual credentials and target bot usernames:

    ```dotenv
    # --- Telegram User API ---
    API_ID=12345678 # Replace with your API ID
    API_HASH=your_api_hash_here # Replace with your API Hash

    # --- Aiogram Bot ---
    BOT_TOKEN=your_bot_token_here # Replace with your Aiogram Bot Token

    # --- Target Bots ---
    # Format: "key": "username"
    # The 'key' is used in commands like /key <message>
    # The 'username' is the Telegram username of the target bot (without @)
    TARGET_BOTS='{"bot1": "some_bot_username", "claude": "claude_ai_bot", "chatgpt": "chatgpt_official_bot"}' # Example, replace with your bots
    ```

    *   **Important:** The `TARGET_BOTS` value **must** be a valid JSON string enclosed in single quotes (as shown). The keys (`"bot1"`, `"claude"`, etc.) are the short names you will use in commands like `/bot1 message`. The values are the actual Telegram usernames of the target bots.

## Running the Application

**1. Initial Pyrogram Login (First Run Only):**

*   Make sure your virtual environment is activated (`source venv/bin/activate` or `.\venv\Scripts\activate`).
*   Run the main script:
    ```bash
    python main.py
    ```
*   Pyrogram will likely ask you to log in by entering your phone number and a code sent to your Telegram account. This creates a `.session` file (e.g., `my_account.session`) which stores your login information securely. This file is included in `.gitignore` by default.

**2. Subsequent Runs:**

*   Activate the virtual environment.
*   Run the main script:
    ```bash
    python main.py
    ```

The application will start, connect your user account (Pyrogram), start your Aiogram bot, and begin bridging messages.

## Usage

1.  Start a chat with the Aiogram bot you created (find it via its username).
2.  To send a message to a specific target bot (using the `key` defined in `TARGET_BOTS` in your `.env` file):
    ```
    /<key> <your message>
    ```
    Example: `/claude What is the weather today?`
3.  To send a message to *all* target bots defined in `TARGET_BOTS`:
    ```
    /all <your message>
    ```
    Example: `/all Hello bots!`
4.  Replies from the target bots will be automatically forwarded back to your chat with the Aiogram bot, prefixed with the bot's key (e.g., `[claude]: The weather is sunny.`).

## Stopping the Application

Press `Ctrl+C` in the terminal where the script is running. The application is designed to shut down gracefully. 