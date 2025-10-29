from fastapi import APIRouter, HTTPException, Response, UploadFile, File, Form
from fastapi.responses import JSONResponse
from typing import Dict
import time
import cv2
import numpy as np

from app.models.camera import CameraCreate, Camera
from app.services.camera_worker import (
    register_camera, 
    get_all_cameras, 
    get_worker, 
    unregister_camera, 
    start_worker, 
    stop_worker
)
from app.services.redis_publisher import redis_publisher

router = APIRouter()

@router.post("/cameras", response_model=Camera)
async def register_new_camera(camera_data: CameraCreate):
    """Registers a new camera and starts the ingestion process if it's an RTSP source."""
    if camera_data.source_type == "rtsp" and not camera_data.source_url:
        raise HTTPException(status_code=400, detail="RTSP source requires a source_url.")
    
    camera = Camera(
        id=camera_data.id,
        source_type=camera_data.source_type,
        source_url=camera_data.source_url,
        status="registered"
    )
    
    registered_camera = await register_camera(camera)
    return registered_camera

@router.get("/cameras", response_model=Dict[str, Camera])
async def get_cameras_list():
    """Returns a list of all registered cameras and their current status."""
    return get_all_cameras()

@router.delete("/cameras/{camera_id}", status_code=204)
async def unregister_existing_camera(camera_id: str):
    """Unregisters a camera and stops its worker."""
    if not await unregister_camera(camera_id):
        raise HTTPException(status_code=404, detail="Camera not found")
    return Response(status_code=204)

@router.post("/cameras/{camera_id}/connect")
async def connect_camera(camera_id: str):
    """Manually start the worker for a registered RTSP camera."""
    camera = get_all_cameras().get(camera_id)
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    if camera.source_type != "rtsp" or not camera.source_url:
        raise HTTPException(status_code=400, detail="Only RTSP cameras can be manually connected.")
    
    await start_worker(camera.id, camera.source_type, camera.source_url)
    return JSONResponse(content={"message": f"Attempting to connect to camera {camera_id}"})

@router.post("/cameras/{camera_id}/disconnect")
async def disconnect_camera(camera_id: str):
    """Manually stop the worker for a registered RTSP camera."""
    worker = get_worker(camera_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Camera worker not found or not running.")
    
    await stop_worker(camera_id)
    return JSONResponse(content={"message": f"Disconnected camera {camera_id}"})

@router.get("/cameras/{camera_id}/frame/latest", response_class=Response)
async def get_latest_frame(camera_id: str):
    """Returns the latest frame from the circular buffer as a JPEG image."""
    worker = get_worker(camera_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Camera or worker not found.")

    jpeg_bytes = worker.get_latest_frame_jpeg()
    if not jpeg_bytes:
        raise HTTPException(status_code=404, detail="No frames available for this camera.")
    
    return Response(content=jpeg_bytes, media_type="image/jpeg")

@router.post("/ingest/push/{camera_id}")
async def http_push_ingest(
    camera_id: str,
    frame_file: UploadFile = File(...),
    timestamp: float = Form(None)
):
    """Accepts an image file via HTTP POST for push-based cameras."""
    worker = get_worker(camera_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Camera not registered or worker not found.")
    
    if worker.source_type != "http_push":
        raise HTTPException(status_code=400, detail="This camera is not configured for HTTP_PUSH ingestion.")

    try:
        image_data = await frame_file.read()
        np_arr = np.frombuffer(image_data, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        
        if frame is None:
            raise HTTPException(status_code=400, detail="Could not decode image file.")

        current_time = timestamp if timestamp else time.time()
        # Process frame asynchronously (adds to buffer, detects motion, etc.)
        await worker._process_frame(frame, current_time, "http_push")

        return JSONResponse(content={"message": "Frame ingested successfully", "timestamp": current_time})

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error during ingestion: {str(e)}")