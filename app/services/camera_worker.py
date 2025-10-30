import asyncio
import cv2
import time
import json
import numpy as np
import uuid
import os
from typing import Optional, Dict
from pathlib import Path

from app.models.camera import Camera
from app.utils.circular_buffer import CircularBuffer
from app.services.redis_publisher import redis_publisher
from app.services.metrics import (
    update_camera_status,
    increment_frames_ingested,
    update_last_frame_timestamp
)
from app.core.config import settings

# --- Глобальные хранилища ---
camera_store: Dict[str, Camera] = {}
worker_store: Dict[str, 'CameraWorker'] = {}
task_store: Dict[str, asyncio.Task] = {}

# --- Папка для временных кадров ---
TEMP_FRAME_DIR = "/tmp/camera_frames"
os.makedirs(TEMP_FRAME_DIR, exist_ok=True)

# --- Определяем абсолютный путь к корню проекта ---
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class CameraWorker:
    """
    Универсальный воркер для захвата, буферизации, трансляции и (опционально) анализа видео.
    """
    def __init__(self, camera_id: str, stream_url: str, buffer_capacity: int = 10):
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.buffer = CircularBuffer(buffer_capacity)
        self._stop_event = asyncio.Event()
        self.is_running = False
        self.reconnect_delay = 1.0
        self.source_type = "rtsp"
        
        # --- Метаданные потока ---
        self.stream_width: Optional[int] = None
        self.stream_height: Optional[int] = None
        self.stream_fps: Optional[float] = None
        
        # --- Переменные для прореженной публикации событий ---
        self.last_event_pub_time: float = 0.0
        self.EVENT_PUB_INTERVAL: int = 1

        # --- ML & Оптимизация (Инициализируется всегда, но используется опционально) ---
        self.person_detection_enabled = settings.ENABLE_PERSON_DETECTION
        self.person_cooldown_end = 0.0
        self.yolo_trigger_cooldown_end = 0.0
        
        self.frames_to_skip = 5
        self.frame_counter = 0
        self.motion_last_frame = None

        if self.person_detection_enabled:
            # Загружаем ML модель только если она включена в настройках
            print(f"Person detection is ENABLED for {camera_id}. Loading YOLO model...")
            weights_path = str(BASE_DIR / "models" / "yolov4-tiny.weights")
            config_path = str(BASE_DIR / "models" / "yolov4-tiny.cfg")
            names_path = str(BASE_DIR / "models" / "coco.names")

            self.net = cv2.dnn.readNetFromDarknet(config_path, weights_path)
            self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

            with open(names_path, "r") as f:
                self.class_names = [line.strip() for line in f.readlines()]
            self.person_class_id = self.class_names.index('person')
            
            layer_names = self.net.getLayerNames()
            self.output_layers = [layer_names[i - 1] for i in self.net.getUnconnectedOutLayers().flatten()]
            print(f"YOLOv4-tiny person detector initialized for worker {camera_id}.")
        else:
            print(f"Person detection is DISABLED for {camera_id}.")

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
                cap = await asyncio.to_thread(cv2.VideoCapture, self.stream_url)
                if not cap.isOpened():
                    await self._handle_disconnect(f"Failed to open stream: {self.stream_url}")
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(60, self.reconnect_delay * 2)
                    continue

                self.stream_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                self.stream_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                self.stream_fps = cap.get(cv2.CAP_PROP_FPS)
                print(f"Stream {self.camera_id} metadata: {self.stream_width}x{self.stream_height} @ {self.stream_fps:.2f} FPS")

                await self._handle_connect()
                self.reconnect_delay = 1.0

                while not self._stop_event.is_set():
                    ret, frame = await asyncio.to_thread(cap.read)
                    if not ret:
                        await self._handle_disconnect("Stream closed or error occurred.")
                        break
                    
                    await self._process_frame(frame, time.time())
                    
                await asyncio.to_thread(cap.release)
            except Exception as e:
                print(f"Error in worker {self.camera_id} run loop: {e}")
                await asyncio.sleep(5)

    async def _handle_connect(self):
        if self.camera_id in camera_store: camera_store[self.camera_id].status = "connected"
        update_camera_status(self.camera_id, True)
        await redis_publisher.publish(
            channel=f"camera:{self.camera_id}", event_type="camera.connected",
            data={"camera_id": self.camera_id, "timestamp": time.time(), "width": self.stream_width, "height": self.stream_height, "fps": self.stream_fps})
        print(f"Camera {self.camera_id} connected.")

    async def _handle_disconnect(self, reason: str):
        if self.camera_id in camera_store: camera_store[self.camera_id].status = "disconnected"
        update_camera_status(self.camera_id, False)
        await redis_publisher.publish(channel=f"camera:{self.camera_id}", event_type="camera.disconnected", data={"camera_id": self.camera_id, "reason": reason, "timestamp": time.time()})
        print(f"Camera {self.camera_id} disconnected. Reason: {reason}")

    def _detect_simple_motion(self, frame_gray: np.ndarray) -> bool:
        if self.motion_last_frame is None:
            self.motion_last_frame = frame_gray
            return False
        frame_delta = cv2.absdiff(self.motion_last_frame, frame_gray)
        self.motion_last_frame = frame_gray
        thresh = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)
        contours, _ = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            if cv2.contourArea(contour) > settings.MOTION_MIN_AREA:
                return True
        return False

    async def _process_frame(self, frame: np.ndarray, timestamp: float):
        self.buffer.put((frame, timestamp))
        increment_frames_ingested(self.camera_id, self.source_type)
        update_last_frame_timestamp(self.camera_id, timestamp)
        
        current_time = time.time()
        if (current_time - self.last_event_pub_time) > self.EVENT_PUB_INTERVAL:
            self.last_event_pub_time = current_time
            await redis_publisher.publish(
                channel=f"camera:{self.camera_id}", event_type="frame.received",
                data={"camera_id": self.camera_id, "timestamp": timestamp, "source": self.source_type})
        
        # --- ГЛАВНЫЙ РУБИЛЬНИК ---
        # Выполняем ML-анализ только если он включен в настройках
        if self.person_detection_enabled:
            self.frame_counter += 1
            if self.frame_counter > self.frames_to_skip:
                self.frame_counter = 0
                if time.time() >= self.yolo_trigger_cooldown_end:
                    frame_gray_blurred = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (21, 21), 0)
                    if self._detect_simple_motion(frame_gray_blurred):
                        self.yolo_trigger_cooldown_end = time.time() + settings.YOLO_TRIGGER_COOLDOWN
                        print(f"Significant motion detected on {self.camera_id}. Running YOLO...")
                        await self._detect_persons_yolo(frame.copy())
    
    async def _detect_persons_yolo(self, frame: np.ndarray):
        if time.time() < self.person_cooldown_end: return
        H, W = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (416, 416), swapRB=True, crop=False)
        self.net.setInput(blob)
        layer_outputs = await asyncio.to_thread(self.net.forward, self.output_layers)
        boxes, confidences, class_ids = [], [], []
        for output in layer_outputs:
            for detection in output:
                scores = detection[5:]
                class_id = np.argmax(scores)
                confidence = scores[class_id]
                if class_id == self.person_class_id and confidence > settings.YOLO_CONFIDENCE_THRESHOLD:
                    box = detection[0:4] * np.array([W, H, W, H])
                    (centerX, centerY, width, height) = box.astype("int")
                    x = int(centerX - (width / 2))
                    y = int(centerY - (height / 2))
                    boxes.append([x, y, int(width), int(height)])
                    confidences.append(float(confidence))
                    class_ids.append(class_id)
        idxs = cv2.dnn.NMSBoxes(boxes, confidences, settings.YOLO_CONFIDENCE_THRESHOLD, 0.4)
        if len(idxs) > 0:
            self.person_cooldown_end = time.time() + settings.PERSON_COOLDOWN_SECONDS
            for i in idxs.flatten():
                (x, y, w, h) = boxes[i]
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                text = f"Person: {confidences[i]:.2f}"
                cv2.putText(frame, text, (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            temp_filename = f"{uuid.uuid4()}.jpg"
            temp_filepath = os.path.join(TEMP_FRAME_DIR, temp_filename)
            await asyncio.to_thread(cv2.imwrite, temp_filepath, frame)
            await redis_publisher.publish(
                channel=f"camera:{self.camera_id}", event_type="person.detected",
                data={"camera_id": self.camera_id, "timestamp": time.time(), "person_count": len(idxs), "frame_path": temp_filepath})
            print(f"YOLO confidently found {len(idxs)} person(s) on {self.camera_id}, event published.")

    def get_latest_frame_jpeg(self) -> Optional[bytes]:
        latest_item = self.buffer.get_latest()
        if latest_item is None: return None
        frame, _ = latest_item
        ret, jpeg = cv2.imencode('.jpg', frame)
        return jpeg.tobytes() if ret else None

    async def get_mjpeg_stream_generator(self):
        last_frame_time = 0
        while self.is_running:
            try:
                latest_item = self.buffer.get_latest()
                if latest_item:
                    frame, timestamp = latest_item
                    if timestamp > last_frame_time:
                        last_frame_time = timestamp
                        ret, jpeg = cv2.imencode('.jpg', frame)
                        if ret:
                            frame_bytes = jpeg.tobytes()
                            yield (b'--frame\r\n'
                                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                await asyncio.sleep(1/30)
            except asyncio.CancelledError:
                print(f"MJPEG stream for {self.camera_id} cancelled.")
                break

# --- Функции управления (без изменений) ---
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

async def register_camera(camera: Camera, save_to_db: bool = True):
    if camera.id in camera_store: await stop_worker(camera.id)
    camera_store[camera.id] = camera
    if camera.source_type in ["rtsp", "mjpeg"] and camera.source_url:
        await start_worker(camera.id, camera.source_type, camera.source_url)
    elif camera.source_type == "http_push":
        await start_worker(camera.id, camera.source_type)
    if save_to_db: _save_cameras_to_db()
    return camera_store[camera.id]

async def unregister_camera(camera_id: str):
    await stop_worker(camera_id)
    update_camera_status(camera_id, False)
    if camera_id in camera_store:
        del camera_store[camera_id]
        _save_cameras_to_db()
        return True
    return False

def _save_cameras_to_db():
    try:
        cameras_dict = {cam_id: cam.model_dump() for cam_id, cam in camera_store.items()}
        with open(settings.CAMERA_DB_FILE, 'w') as f:
            json.dump(cameras_dict, f, indent=4)
    except Exception as e:
        print(f"Error saving cameras to DB: {e}")

async def load_cameras_from_db():
    try:
        if not os.path.exists(settings.CAMERA_DB_FILE):
            print("Camera DB file not found, starting with empty list.")
            return
        with open(settings.CAMERA_DB_FILE, 'r') as f:
            cameras_data = json.load(f)
        print(f"Loading {len(cameras_data)} cameras from DB...")
        tasks = [register_camera(Camera(**cam_data), save_to_db=False) for cam_data in cameras_data.values()]
        await asyncio.gather(*tasks)
    except Exception as e:
        print(f"Error loading cameras from DB: {e}")