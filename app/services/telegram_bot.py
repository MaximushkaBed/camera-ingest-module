import os
import asyncio
import json
from telegram import Bot
from telegram.error import TelegramError
from app.services.redis_publisher import RedisPublisher

# Global bot instance
bot: Bot = None
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

async def init_telegram_bot():
    """Initializes the Telegram bot instance."""
    global bot
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token:
        bot = Bot(token=token)
        print("Telegram Bot initialized.")
    else:
        print("TELEGRAM_BOT_TOKEN not found. Telegram notifications disabled.")

async def send_telegram_notification(message: str):
    """Sends a message to the configured Telegram chat ID."""
    if not bot or not TELEGRAM_CHAT_ID:
        # print("Telegram bot not configured or chat ID missing.")
        return
    
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
    except TelegramError as e:
        print(f"Error sending Telegram message: {e}")

async def telegram_event_listener():
    """Listens to Redis events and sends Telegram notifications for relevant events."""
    redis_publisher = RedisPublisher()
    pubsub = redis_publisher.r.pubsub()

    await pubsub.psubscribe("camera:*") 
    
    print("Telegram listener started, subscribed to camera:*")

    async for message in pubsub.listen():
        if message and message["type"] == "pmessage":
            try:
                # data уже строка, так как в RedisPublisher стоит decode_responses=True
                data = json.loads(message["data"])
                event_type = data.get("event_type")
                event_data = data.get("data", {})
                camera_id = event_data.get("camera_id", "Unknown")

                notification_message = None

                if event_type == "motion.detected":
                    notification_message = (
                        f"🚨 **[MOTION DETECTED]** on Camera: `{camera_id}`\n"
                        f"Timestamp: {event_data.get('timestamp')}"
                    )
                elif event_type == "camera.disconnected":
                    notification_message = (
                        f"⚠️ **[DISCONNECTED]** Camera: `{camera_id}`\n"
                        f"Reason: {event_data.get('reason', 'Stream error')}"
                    )
                elif event_type == "camera.connected":
                    notification_message = (
                        f"✅ **[CONNECTED]** Camera: `{camera_id}`"
                    )

                if notification_message:
                    # Run the async send function
                    asyncio.create_task(send_telegram_notification(notification_message))

            except json.JSONDecodeError:
                print(f"Failed to decode JSON message: {message['data']}")
            except Exception as e:
                print(f"Error in Telegram listener: {e}")

# Note: The listener needs to be started in a separate thread/task in the main application.
