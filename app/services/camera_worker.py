import asyncio
import cv2
import time
import numpy as np
from typing import Optional, Dict, Any
from onvif import ONVIFCamera

from app.models.camera import Camera
from app.utils.circular_buffer import CircularBuffer
from app.services.redis_publisher import redis_publisher
from app.services.metrics import (
    update_camera_status,
    increment_frames_ingested,
    increment_motion_detected,
    update_last_frame_timestamp
)

# --- Глобальные хранилища ---
camera_store: Dict[str, Camera] = {}
worker_store: Dict[str, 'CameraWorker'] = {}
task_store: Dict[str, asyncio.Task] = {}


async def discover_onvif_rtsp_url(host: str, port: int, user: str, pwd: str) -> str:
    """
    Подключается к камере по ONVIF и запрашивает у нее RTSP-адрес первого видеопотока.
    """
    print(f"Attempting ONVIF discovery for {host}:{port}...")
    
    def onvif_probe():
        # onvif-zeep - блокирующая библиотека, поэтому запускаем ее в отдельном потоке
        cam = ONVIFCamera(host, port, user, pwd)
        media_service = cam.create_media_service()
        profiles = media_service.GetProfiles()
        # Берем первый профиль (обычно это главный поток)
        profile_token = profiles[0].token
        
        uri_request = media_service.create_type('GetStreamUri')
        uri_request.ProfileToken = profile_token
        uri_request.StreamSetup = {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': 'TCP'}}
        
        response = media_service.GetStreamUri(uri_request)
        return response.Uri

    try:
        # Запускаем блокирующий код в управляемом asyncio потоке
        rtsp_url = await asyncio.to_thread(onvif_probe)
        print(f"ONVIF discovery successful! Stream URL: {rtsp_url}")
        return rtsp_url
    except Exception as e:
        print(f"ONVIF discovery failed for {host}:{port}. Error: {e}")
        raise ValueError(f"Could not get stream URL via ONVIF. Check credentials and camera support.")


class CameraWorker:
    """Асинхронный воркер для камеры. Работает как задача asyncio, а не в отдельном потоке."""
    def __init__(self, camera_id: str, stream_url: str, buffer_capacity: int = 10):
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.buffer = CircularBuffer(buffer_capacity)
        self._stop_event = asyncio.Event()
        self.is_running = False
        self.last_frame_time = 0.0
        self.reconnect_delay = 1.0

        self.motion_detection_enabled = True
        self.last_frame: Optional[np.ndarray] = None
        self.motion_cooldown_end = 0.0
        self.MOTION_COOLDOWN_SECONDS = 5
        self.MIN_MOTION_AREA = 1000
        self.source_type = "rtsp"

    async def start(self):
        if self.is_running: return
        self._stop_event.clear()
        task = asyncio.create_task(self._run(), name=f"Worker-{self.camera_id}")
        task_store[self.camera_id] = task
        self.is_running = True
        print(f"Async Worker {self.camera_id} started.")

    async def stop(self):
        if not self.is_running or self.camera_id not in task_store: return
        self._stop_event.set()
        task = task_store.pop(self.camera_id, None)
        if task and not task.done():
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
        self.is_running = False
        print(f"Async Worker {self.camera_id} stopped.")

    async def _run(self):
        while not self._stop_event.is_set():
            try:
                cap = cv2.VideoCapture(self.stream_url)
                if not cap.isOpened():
                    await self._handle_disconnect(f"Failed to open stream: {self.stream_url}")
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(60, self.reconnect_delay * 2)
                    continue
                await self._handle_connect()
                self.reconnect_delay = 1.0
                while not self._stop_event.is_set():
                    ret, frame = await asyncio.to_thread(cap.read)
                    if not ret:
                        await self._handle_disconnect("Stream closed or error occurred.")
                        break
                    await self._process_frame(frame, time.time(), self.source_type)
                await asyncio.to_thread(cap.release)
            except Exception as e:
                print(f"Error in worker {self.camera_id} run loop: {e}")
                await asyncio.sleep(5)

    async def _handle_connect(self):
        if self.camera_id in camera_store: camera_store[self.camera_id].status = "connected"
        update_camera_status(self.camera_id, True)
        await redis_publisher.publish(channel=f"camera:{self.camera_id}", event_type="camera.connected", data={"camera_id": self.camera_id, "timestamp": time.time()})
        print(f"Camera {self.camera_id} connected.")

    async def _handle_disconnect(self, reason: str):
        if self.camera_id in camera_store: camera_store[self.camera_id].status = "disconnected"
        update_camera_status(self.camera_id, False)
        await redis_publisher.publish(channel=f"camera:{self.camera_id}", event_type="camera.disconnected", data={"camera_id": self.camera_id, "reason": reason, "timestamp": time.time()})
        print(f"Camera {self.camera_id} disconnected. Reason: {reason}")

    async def _detect_motion(self, current_frame: np.ndarray):
        gray_current = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
        gray_current = cv2.GaussianBlur(gray_current, (21, 21), 0)
        if self.last_frame is None:
            self.last_frame = gray_current
            return
        if time.time() < self.motion_cooldown_end:
            self.last_frame = gray_current
            return
        frame_delta = await asyncio.to_thread(cv2.absdiff, self.last_frame, gray_current)
        thresh = await asyncio.to_thread(cv2.threshold, frame_delta, 50, 255, cv2.THRESH_BINARY)
        thresh = await asyncio.to_thread(cv2.dilate, thresh[1], None, iterations=2)
        contours, _ = await asyncio.to_thread(cv2.findContours, thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        motion_detected = any(cv2.contourArea(c) >= self.MIN_MOTION_AREA for c in contours)
        if motion_detected:
            self.motion_cooldown_end = time.time() + self.MOTION_COOLDOWN_SECONDS
            increment_motion_detected(self.camera_id)
            await redis_publisher.publish(channel=f"camera:{self.camera_id}", event_type="motion.detected", data={"camera_id": self.camera_id, "timestamp": time.time()})
            print(f"Motion detected on camera {self.camera_id}!")
        self.last_frame = gray_current

    async def _process_frame(self, frame: np.ndarray, timestamp: float, source_type: str):
        self.last_frame_time = timestamp
        self.buffer.put(frame)
        increment_frames_ingested(self.camera_id, source_type)
        update_last_frame_timestamp(self.camera_id, timestamp)
        await redis_publisher.publish(channel=f"camera:{self.camera_id}", event_type="frame.ingested", data={"camera_id": self.camera_id, "timestamp": timestamp, "source": source_type})
        if self.motion_detection_enabled:
            await self._detect_motion(frame)

    def get_latest_frame_jpeg(self) -> Optional[bytes]:
        frame = self.buffer.get_latest()
        if frame is None: return None
        ret, jpeg = cv2.imencode('.jpg', frame)
        return jpeg.tobytes() if ret else None


async def start_worker(camera_id: str, source_type: str, source_url: Optional[str] = None):
    if camera_id in worker_store: await stop_worker(camera_id)
    if source_type in ["rtsp", "mjpeg"] and source_url:
        worker = CameraWorker(camera_id, source_url)
        worker.source_type = source_type
        worker_store[camera_id] = worker
        await worker.start()
    elif source_type == "http_push":
        worker = CameraWorker(camera_id, "")
        worker.source_type = "http_push"
        worker.is_running = True
        worker_store[camera_id] = worker
        update_camera_status(camera_id, True)
        print(f"Push worker {camera_id} initialized.")

async def stop_worker(camera_id: str):
    if camera_id in worker_store:
        await worker_store[camera_id].stop()
        del worker_store[camera_id]

def get_worker(camera_id: str) -> Optional[CameraWorker]:
    return worker_store.get(camera_id)

def get_all_cameras() -> Dict[str, Camera]:
    return camera_store

# --- ИЗМЕНЯЕМ ФУНКЦИЮ РЕГИСТРАЦИИ ---
async def register_camera(camera: Camera):
    if camera.id in camera_store:
        await stop_worker(camera.id)
    
    # Главная новая логика
    if camera.source_type == "onvif":
        try:
            # Пытаемся автоматически получить URL
            rtsp_url = await discover_onvif_rtsp_url(
                host=camera.ip_address,
                port=camera.onvif_port,
                user=camera.username,
                pwd=camera.password
            )
            # Сохраняем найденный URL и меняем тип для воркера на 'rtsp'
            camera.source_url = rtsp_url
            camera.source_type = "rtsp"
        except ValueError as e:
            # Если не удалось, возвращаем ошибку, которую перехватит API
            raise e

    camera_store[camera.id] = camera
    
    if camera.source_type in ["rtsp", "mjpeg"] and camera.source_url:
        await start_worker(camera.id, camera.source_type, camera.source_url)
    elif camera.source_type == "http_push":
        await start_worker(camera.id, camera.source_type)
    
    return camera_store[camera.id]

async def unregister_camera(camera_id: str):
    await stop_worker(camera_id)
    update_camera_status(camera_id, False)
    if camera_id in camera_store:
        del camera_store[camera_id]
        return True
    return False