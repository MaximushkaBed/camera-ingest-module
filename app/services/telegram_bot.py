import os
import asyncio
import json
from telegram import Bot
from telegram.error import TelegramError
from app.services.redis_publisher import RedisPublisher
from typing import Optional

# --- Глобальные переменные ---
bot: Optional[Bot] = None
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
# ИЗМЕНЕНИЕ: Глобальная переменная для хранения задачи-слушателя
telegram_listener_task: Optional[asyncio.Task] = None

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
        return
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
    except TelegramError as e:
        print(f"Error sending Telegram message: {e}")

async def telegram_event_listener():
    """Listens to Redis events and sends Telegram notifications."""
    pubsub = None
    try:
        redis_publisher = RedisPublisher()
        pubsub = redis_publisher.r.pubsub()
        await pubsub.psubscribe("camera:*")
        print("Telegram listener started, subscribed to camera:*")

        async for message in pubsub.listen():
            if message and message["type"] == "pmessage":
                try:
                    data = json.loads(message["data"])
                    event_type = data.get("event_type")
                    event_data = data.get("data", {})
                    camera_id = event_data.get("camera_id", "Unknown")

                    notification_message = None
                    if event_type == "motion.detected":
                        notification_message = f"🚨 *MOTION DETECTED* on Camera: `{camera_id}`"
                    elif event_type == "camera.disconnected":
                        notification_message = f"⚠️ *DISCONNECTED* Camera: `{camera_id}`"
                    elif event_type == "camera.connected":
                        notification_message = f"✅ *CONNECTED* Camera: `{camera_id}`"

                    if notification_message:
                        asyncio.create_task(send_telegram_notification(notification_message))

                except json.JSONDecodeError:
                    print(f"Failed to decode JSON message: {message['data']}")
                except Exception as e:
                    print(f"Error in Telegram listener processing: {e}")
    except asyncio.CancelledError:
        print("Telegram listener task cancelled.")
    finally:
        if pubsub:
            await pubsub.close()
        print("Telegram listener stopped.")


def start_telegram_listener():
    global telegram_listener_task
    asyncio.create_task(init_telegram_bot())
    telegram_listener_task = asyncio.create_task(telegram_event_listener())

async def stop_telegram_listener():
    global telegram_listener_task
    if telegram_listener_task:
        telegram_listener_task.cancel()
        try:
            await telegram_listener_task
        except asyncio.CancelledError:
            pass # Ожидаемое поведение