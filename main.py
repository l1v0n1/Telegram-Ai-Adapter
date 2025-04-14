import asyncio
import logging
import signal

from bridge import queue_to_pyrogram
import pyrogram_client
import aiogram_bot
import config # To ensure config is loaded

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

shutdown_event = asyncio.Event()

async def process_queue_to_pyrogram():
    """Listens to the queue from aiogram and triggers pyrogram actions."""
    while not shutdown_event.is_set():
        try:
            item = await asyncio.wait_for(queue_to_pyrogram.get(), timeout=1.0)
            if not item:
                continue
            logger.info(f"Received item from aiogram queue: {item['type']}")
            
            if item["type"] == "user_request":
                # Standard single-bot request
                if pyrogram_client.app and pyrogram_client.app.is_connected:
                    try:
                        await pyrogram_client.send_to_bot(
                            bot_key=item["bot_key"],
                            user_chat_id=item["chat_id"],
                            user_message_id=item["message_id"],
                            text=item["text"]
                        )
                    except Exception as e:
                        logger.error(f"Error calling send_to_bot: {e}", exc_info=True)
                        # Notify user of failure?
                else:
                    logger.warning("Pyrogram client not connected or not initialized. Cannot forward single request.")
                    # Notify user?

            elif item["type"] == "all_request":
                # Request to send to all bots
                user_chat_id = item["chat_id"]
                user_message_id = item["message_id"]
                text = item["text"]
                failed_bots = []
                
                if not (pyrogram_client.app and pyrogram_client.app.is_connected):
                     logger.warning("Pyrogram client not connected or not initialized. Cannot forward /all request.")
                     # Notify user? This requires sending back to aiogram
                     continue

                logger.info(f"Processing /all request for user ({user_chat_id}, {user_message_id}) to bots: {list(config.TARGET_BOTS.keys())}")
                for bot_key in config.TARGET_BOTS.keys():
                    try:
                        logger.debug(f"Sending /all sub-request to {bot_key}...")
                        await pyrogram_client.send_to_bot(
                            bot_key=bot_key,
                            user_chat_id=user_chat_id, 
                            user_message_id=user_message_id, # Use the SAME original message ID for correlation
                            text=text
                        )
                        await asyncio.sleep(0.1) # Small delay to avoid hitting limits too quickly
                    except Exception as e:
                        logger.error(f"Error in /all sending to {bot_key}: {e}", exc_info=True)
                        failed_bots.append(bot_key)
                
                if failed_bots:
                    # Optionally notify aiogram bot about initial send failures
                    logger.warning(f"Failed to initially send /all request to: {failed_bots}")
                    # We could put a specific event on queue_to_aiogram here if needed

            else:
                logger.warning(f"Unknown item type received in queue_to_pyrogram: {item['type']}")
            
            queue_to_pyrogram.task_done()
        except asyncio.TimeoutError:
            continue # No item received, check shutdown flag and loop again
        except Exception as e:
            logger.error(f"Error processing item from queue_to_pyrogram: {item} - {e}", exc_info=True)
            if 'item' in locals() and item: 
                queue_to_pyrogram.task_done()
    logger.info("Pyrogram sender task finished.")

async def main():
    """Starts all components and handles graceful shutdown."""
    if not config.API_ID or not config.API_HASH or not config.BOT_TOKEN:
        logger.critical("API_ID, API_HASH, and BOT_TOKEN must be set in .env file! Exiting.")
        return

    logger.info("Starting application...")

    # --- Setup Pyrogram Client --- 
    # Initialize but don't run() yet
    pyrogram_app = pyrogram_client.app
    try:
        logger.info("Starting Pyrogram client...")
        await pyrogram_app.start()
        logger.info("Pyrogram client connected.")
        # Resolve bot IDs after connecting
        resolved_ids = await pyrogram_client.get_target_bot_ids()
        if not resolved_ids:
            logger.critical("Could not resolve target bot IDs. Shutting down.")
            await pyrogram_app.stop()
            return
        pyrogram_client.target_bot_ids_map = resolved_ids
        logger.info(f"Pyrogram client initialized. Monitoring bots: {list(resolved_ids.keys())}")

    except Exception as e:
        logger.critical(f"Failed to start Pyrogram client: {e}", exc_info=True)
        return
    
    # --- Setup Aiogram Bot --- 
    aiogram_dispatcher = aiogram_bot.dp
    # Start the background task to process messages from pyrogram
    aiogram_receiver_task = asyncio.create_task(aiogram_bot.process_queue_to_aiogram())
    # Task for processing messages going TO pyrogram
    pyrogram_sender_task = asyncio.create_task(process_queue_to_pyrogram())

    # Start aiogram polling in the background
    polling_task = asyncio.create_task(aiogram_dispatcher.start_polling())
    logger.info("Aiogram bot polling started.")

    # --- Wait for shutdown signal --- 
    await shutdown_event.wait()

    # --- Graceful Shutdown --- 
    logger.info("Shutdown signal received. Initiating graceful shutdown...")

    # 1. Stop Aiogram polling
    logger.info("Stopping Aiogram polling...")
    aiogram_dispatcher.stop_polling()
    try:
        await asyncio.wait_for(polling_task, timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("Aiogram polling task did not finish promptly.")
    except Exception as e: # Catch potential exceptions during polling stop
         logger.error(f"Error stopping Aiogram polling task: {e}", exc_info=True)
    logger.info("Aiogram polling stopped.")

    # 2. Stop Pyrogram client
    if pyrogram_app.is_connected:
        logger.info("Stopping Pyrogram client...")
        try:
            await pyrogram_app.stop()
            logger.info("Pyrogram client stopped.")
        except Exception as e:
             logger.error(f"Error stopping Pyrogram client: {e}", exc_info=True)
    
    # 3. Cancel helper tasks
    logger.info("Cancelling helper tasks...")
    tasks_to_cancel = [aiogram_receiver_task, pyrogram_sender_task]
    for task in tasks_to_cancel:
        if not task.done():
            task.cancel()
    
    # Wait for tasks to finish cancellation
    await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
    logger.info("Helper tasks cancelled.")

    # 4. Close Aiogram Bot session
    logger.info("Closing Aiogram bot session...")
    try:
        await aiogram_bot.bot.close()
    except Exception as e:
        logger.error(f"Error closing Aiogram bot session: {e}", exc_info=True)
    logger.info("Aiogram bot session closed.")

    logger.info("Shutdown complete.")

def signal_handler(sig, frame):
    logger.info(f"Signal {sig} received, triggering shutdown.")
    # Set the event in a threadsafe manner
    asyncio.get_event_loop().call_soon_threadsafe(shutdown_event.set)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    # Setup signal handlers for graceful shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler, sig, None)
    
    try:
        loop.run_until_complete(main())
    except asyncio.CancelledError:
        logger.info("Main task cancelled.")
    finally:
        # Final cleanup if needed
        logger.info("Application finished.") 