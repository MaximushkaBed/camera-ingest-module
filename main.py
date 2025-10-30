import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.api.cameras import router as cameras_router
from app.services.websocket_manager import websocket_router

from app.services.telegram_bot import start_telegram_listener, stop_telegram_listener
from app.services.websocket_manager import websocket_redis_listener
from app.services.camera_worker import load_cameras_from_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Код, который выполнится при старте приложения
    print("Application startup...")
    
    # Запускаем наших слушателей
    start_telegram_listener()
    # Для вебсокета тоже создадим задачу
    websocket_task = asyncio.create_task(websocket_redis_listener())

    await load_cameras_from_db()

    yield

    # Код, который выполнится при остановке приложения
    print("Application shutdown...")
    
    # Грациозно останавливаем наших слушателей
    await stop_telegram_listener()
    
    websocket_task.cancel()
    try:
        await websocket_task
    except asyncio.CancelledError:
        pass

    

# Создаем приложение с новым менеджером жизненного цикла
app = FastAPI(lifespan=lifespan)

# Подключаем роутеры
app.include_router(cameras_router, prefix="/api", tags=["Cameras"])
app.include_router(websocket_router, prefix="/api", tags=["WebSockets"])

@app.get("/")
async def root():
    return {"message": "Camera Ingest Module is running."}