import asyncio

# Queue for messages/events from Pyrogram client to Aiogram bot
queue_to_aiogram = asyncio.Queue()

# Queue for messages/commands from Aiogram bot to Pyrogram client
queue_to_pyrogram = asyncio.Queue() 