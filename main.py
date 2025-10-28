from fastapi import FastAPI
from app.api import cameras
from app.services.telegram_bot import init_telegram_bot, telegram_event_listener
from app.services.websocket_manager import websocket_router # New import for WebSocket
from app.services.metrics import get_metrics
from fastapi.responses import Response

app = FastAPI(
    title="Camera Ingest Module",
    description="Service for ingesting RTSP and HTTP Push video streams, providing a circular buffer, and publishing events to a message bus.",
    version="1.0.0"
)

app.include_router(cameras.router, prefix="/api", tags=["cameras"])
app.include_router(websocket_router, prefix="/ws", tags=["websocket"]) # Include WebSocket router

@app.get("/metrics", response_class=Response)
async def metrics():
    """Exposes Prometheus metrics."""
    return Response(content=get_metrics(), media_type="text/plain")

@app.on_event("startup")
async def startup_event():
    await init_telegram_bot()
    # Start the Telegram listener in a background task
    import asyncio
    asyncio.create_task(telegram_event_listener())

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "camera-ingest-module"}
