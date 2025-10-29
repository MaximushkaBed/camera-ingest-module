import asyncio
import cv2
import time
import numpy as np
from typing import Optional, Dict, Any

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

class CameraWorker:
    """Асинхронный воркер для камеры. Работает как задача asyncio, а не в отдельном потоке."""
    def __init__(self, camera_id: str, rtsp_url: str, buffer_capacity: int = 10):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.buffer = CircularBuffer(buffer_capacity)
        self._stop_event = asyncio.Event()
        self.is_running = False
        self.last_frame_time = 0.0
        self.reconnect_delay = 1.0

        self.motion_detection_enabled = True
        self.last_frame: Optional[np.ndarray] = None
        self.motion_cooldown_end = 0.0
        self.MOTION_COOLDOWN_SECONDS = 5
        self.MIN_MOTION_AREA = 500
        self.source_type = "rtsp"

    async def start(self):
        """Запускает воркер как асинхронную задачу."""
        if self.is_running:
            return
        self._stop_event.clear()
        task = asyncio.create_task(self._run(), name=f"Worker-{self.camera_id}")
        task_store[self.camera_id] = task
        self.is_running = True
        print(f"Async Worker {self.camera_id} started.")

    async def stop(self):
        """Останавливает асинхронную задачу воркера."""
        if not self.is_running or self.camera_id not in task_store:
            return
        self._stop_event.set()
        task = task_store.pop(self.camera_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.is_running = False
        print(f"Async Worker {self.camera_id} stopped.")

    async def _run(self):
        """Главный асинхронный цикл воркера."""
        while not self._stop_event.is_set():
            try:
                cap = cv2.VideoCapture(self.rtsp_url)
                if not cap.isOpened():
                    await self._handle_disconnect(f"Failed to open stream: {self.rtsp_url}")
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
                    
                    await self._process_frame(frame, time.time(), "rtsp")
                
                await asyncio.to_thread(cap.release)
            except Exception as e:
                print(f"Error in worker {self.camera_id} run loop: {e}")
                await asyncio.sleep(5) # Пауза перед общей попыткой переподключения

    async def _handle_connect(self):
        if self.camera_id in camera_store:
            camera_store[self.camera_id].status = "connected"
        update_camera_status(self.camera_id, True)
        await redis_publisher.publish(
            channel=f"camera:{self.camera_id}",
            event_type="camera.connected",
            data={"camera_id": self.camera_id, "timestamp": time.time()}
        )
        print(f"Camera {self.camera_id} connected.")

    async def _handle_disconnect(self, reason: str):
        if self.camera_id in camera_store:
            camera_store[self.camera_id].status = "disconnected"
        update_camera_status(self.camera_id, False)
        await redis_publisher.publish(
            channel=f"camera:{self.camera_id}",
            event_type="camera.disconnected",
            data={"camera_id": self.camera_id, "reason": reason, "timestamp": time.time()}
        )
        print(f"Camera {self.camera_id} disconnected. Reason: {reason}")

    async def _detect_motion(self, current_frame: np.ndarray):
        if self.last_frame is None:
            self.last_frame = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
            return

        if time.time() < self.motion_cooldown_end:
            self.last_frame = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
            return

        gray_current = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
        frame_delta = await asyncio.to_thread(cv2.absdiff, self.last_frame, gray_current)
        thresh = await asyncio.to_thread(cv2.threshold, frame_delta, 25, 255, cv2.THRESH_BINARY)
        thresh = await asyncio.to_thread(cv2.dilate, thresh[1], None, iterations=2)
        contours, _ = await asyncio.to_thread(cv2.findContours, thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        motion_detected = any(cv2.contourArea(c) >= self.MIN_MOTION_AREA for c in contours)

        if motion_detected:
            self.motion_cooldown_end = time.time() + self.MOTION_COOLDOWN_SECONDS
            increment_motion_detected(self.camera_id)
            await redis_publisher.publish(
                channel=f"camera:{self.camera_id}",
                event_type="motion.detected",
                data={"camera_id": self.camera_id, "timestamp": time.time()}
            )
            print(f"Motion detected on camera {self.camera_id}!")
        
        self.last_frame = gray_current

    async def _process_frame(self, frame: np.ndarray, timestamp: float, source_type: str):
        self.last_frame_time = timestamp
        self.buffer.put(frame)
        increment_frames_ingested(self.camera_id, source_type)
        update_last_frame_timestamp(self.camera_id, timestamp)
        await redis_publisher.publish(
            channel=f"camera:{self.camera_id}",
            event_type="frame.ingested",
            data={"camera_id": self.camera_id, "timestamp": timestamp, "source": source_type}
        )
        if self.motion_detection_enabled:
            await self._detect_motion(frame)

    def get_latest_frame_jpeg(self) -> Optional[bytes]:
        frame = self.buffer.get_latest()
        if frame is None: return None
        ret, jpeg = cv2.imencode('.jpg', frame)
        return jpeg.tobytes() if ret else None

async def start_worker(camera_id: str, source_type: str, source_url: Optional[str] = None):
    if camera_id in worker_store:
        await stop_worker(camera_id)
    
    if source_type == "rtsp" and source_url:
        worker = CameraWorker(camera_id, source_url)
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

async def register_camera(camera: Camera):
    if camera.id in camera_store:
        await stop_worker(camera.id)
    
    camera_store[camera.id] = camera
    
    if camera.source_type == "rtsp" and camera.source_url:
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