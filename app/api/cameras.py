from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse, StreamingResponse
from typing import Dict

from app.models.camera import CameraCreate, Camera
from app.services.camera_worker import (
    register_camera, 
    get_all_cameras, 
    get_worker, 
    unregister_camera, 
    start_worker, 
    stop_worker
)

router = APIRouter()

@router.post("/cameras", response_model=Camera)
async def register_new_camera(camera_data: CameraCreate):
    """Регистрирует новую камеру."""
    if camera_data.source_type in ["rtsp", "mjpeg"] and not camera_data.source_url:
        raise HTTPException(status_code=400, detail=f"Source type '{camera_data.source_type}' requires a source_url.")

    camera = Camera(**camera_data.model_dump())
    
    try:
        registered_camera = await register_camera(camera)
        return registered_camera
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/cameras", response_model=Dict[str, Camera])
async def get_cameras_list():
    """Возвращает список всех зарегистрированных камер."""
    return get_all_cameras()

@router.delete("/cameras/{camera_id}", status_code=204)
async def unregister_existing_camera(camera_id: str):
    """Удаляет камеру и останавливает ее воркер."""
    if not await unregister_camera(camera_id):
        raise HTTPException(status_code=404, detail="Camera not found")
    return Response(status_code=204)

@router.get("/cameras/{camera_id}/frame/latest", response_class=Response)
async def get_latest_frame(camera_id: str):
    """Возвращает последний кадр с камеры в виде JPEG."""
    worker = get_worker(camera_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Camera or worker not found.")
    jpeg_bytes = worker.get_latest_frame_jpeg()
    if not jpeg_bytes:
        raise HTTPException(status_code=404, detail="No frames available for this camera.")
    return Response(content=jpeg_bytes, media_type="image/jpeg")

@router.get("/cameras/{camera_id}/stream/live.mjpeg")
async def get_live_mjpeg_stream(camera_id: str):
    """Отдает живой MJPEG-поток с камеры для просмотра в браузере или VLC."""
    worker = get_worker(camera_id)
    if not worker or not worker.is_running:
        raise HTTPException(status_code=404, detail="Camera worker not found or not running.")
    
    return StreamingResponse(
        worker.get_mjpeg_stream_generator(),
        media_type='multipart/x-mixed-replace; boundary=frame'
    )