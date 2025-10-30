import os
import asyncio
import json
from aiogram import Bot
from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramAPIError
from typing import Optional

from app.services.redis_publisher import RedisPublisher
# ИЗМЕНЕНИЕ: Импортируем наш объект настроек
from app.core.config import settings

# --- Глобальные переменные ---
bot: Optional[Bot] = None
# ИЗМЕНЕНИЕ: Убираем глобальные переменные отсюда, они теперь в settings
telegram_listener_task: Optional[asyncio.Task] = None

async def init_telegram_bot():
    """Инициализирует экземпляр бота Aiogram."""
    global bot
    # ИЗМЕНЕНИЕ: Берем токен и ID из settings
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID

    if token and chat_id:
        bot = Bot(token=token)
        try:
            await bot.get_me()
            print("Telegram Bot initialized successfully (aiogram).")
        except TelegramAPIError as e:
            print(f"Failed to initialize Telegram Bot: {e}")
            bot = None
    else:
        print("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not found in settings. Telegram notifications disabled.")

async def send_telegram_notification(message: str):
    """Отправляет текстовое сообщение в Telegram."""
    if not bot: return
    try:
        await bot.send_message(chat_id=settings.TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
    except TelegramAPIError as e:
        print(f"Error sending Telegram message: {e}")

async def send_frame_with_people(frame_bytes: bytes, caption: str):
    """Отправляет кадр (в виде байтов) как фотографию с подписью."""
    if not bot: return
    try:
        photo = BufferedInputFile(frame_bytes, filename="detected_frame.jpg")
        await bot.send_photo(chat_id=settings.TELEGRAM_CHAT_ID, photo=photo, caption=caption)
        print(f"Sent frame with detected people to Telegram chat {settings.TELEGRAM_CHAT_ID}")
    except TelegramAPIError as e:
        print(f"Error sending frame to Telegram: {e}")

# ... (telegram_event_listener остается без изменений) ...
async def telegram_event_listener():
    pubsub = None
    try:
        redis_publisher = RedisPublisher()
        pubsub = redis_publisher.r.pubsub()
        await pubsub.psubscribe("camera:*")
        print("Telegram listener (aiogram) started, subscribed to camera:*")

        async for message in pubsub.listen():
            if message and message["type"] == "pmessage":
                try:
                    data = json.loads(message["data"])
                    event_type = data.get("event_type")
                    event_data = data.get("data", {})
                    camera_id = event_data.get("camera_id", "Unknown")

                    if event_type == "person.detected":
                        frame_path = event_data.get("frame_path")
                        person_count = event_data.get("person_count", 0)
                        
                        try:
                            with open(frame_path, "rb") as f: frame_bytes = f.read()
                            caption = f"🚨 *PERSON DETECTED* on Camera `{camera_id}`\n👥 People found: {person_count}"
                            await send_frame_with_people(frame_bytes, caption)
                            os.remove(frame_path)
                        except Exception as e:
                            print(f"Error handling person.detected event: {e}")

                except json.JSONDecodeError:
                    print(f"Failed to decode JSON message: {message['data']}")
                except Exception as e:
                    print(f"Error in Telegram listener processing: {e}")

    except asyncio.CancelledError:
        print("Telegram listener task cancelled.")
    finally:
        if pubsub: await pubsub.close()
        print("Telegram listener (aiogram) stopped.")

# ... (start/stop listener остаются без изменений) ...
def start_telegram_listener():
    global telegram_listener_task
    async def startup():
        await init_telegram_bot()
        if bot:
            global telegram_listener_task
            telegram_listener_task = asyncio.create_task(telegram_event_listener())
    asyncio.create_task(startup())

async def stop_telegram_listener():
    global telegram_listener_task
    if telegram_listener_task:
        telegram_listener_task.cancel()
        try: await telegram_listener_task
        except asyncio.CancelledError: pass
    if bot:
        await bot.session.close()
        print("Telegram bot session closed.")