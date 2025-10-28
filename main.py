from fastapi import FastAPI
from app.api import cameras
from app.services.metrics import get_metrics
from fastapi.responses import Response

app = FastAPI(
    title="Camera Ingest Module",
    description="Service for ingesting RTSP and HTTP Push video streams, providing a circular buffer, and publishing events to a message bus.",
    version="1.0.0"
)

app.include_router(cameras.router, prefix="/api", tags=["cameras"])

@app.get("/metrics", response_class=Response)
async def metrics():
    """Exposes Prometheus metrics."""
    return Response(content=get_metrics(), media_type="text/plain")

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "camera-ingest-module"}
