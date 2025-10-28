import cv2
import time
import threading
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

# Global storage for camera objects and workers
camera_store: Dict[str, Camera] = {}
worker_store: Dict[str, 'CameraWorker'] = {}

class CameraWorker:
    """Handles connection, frame reading, and motion detection for a single camera."""
    def __init__(self, camera_id: str, rtsp_url: str, buffer_capacity: int = 10):
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.buffer = CircularBuffer(buffer_capacity)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.is_running = False
        self.last_frame_time = 0.0
        self.reconnect_delay = 1.0

        # Motion Detection State
        self.motion_detection_enabled = True
        self.last_frame: Optional[np.ndarray] = None
        self.motion_cooldown_end = 0.0
        self.MOTION_COOLDOWN_SECONDS = 5
        self.MIN_MOTION_AREA = 500
        self.source_type = "rtsp" # Will be updated by register_camera

    def start(self):
        """Starts the worker thread."""
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name=f"Worker-{self.camera_id}")
        self._thread.daemon = True
        self._thread.start()
        self.is_running = True
        print(f"Worker {self.camera_id} started.")

    def stop(self):
        """Stops the worker thread."""
        if not self.is_running:
            return
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.is_running = False
        print(f"Worker {self.camera_id} stopped.")

    def _run(self):
        """Main loop for the worker thread."""
        while not self._stop_event.is_set():
            cap = cv2.VideoCapture(self.rtsp_url)
            if not cap.isOpened():
                self._handle_disconnect(f"Failed to open stream: {self.rtsp_url}")
                time.sleep(self.reconnect_delay)
                self.reconnect_delay = min(60, self.reconnect_delay * 2) # Exponential backoff
                continue

            self._handle_connect()
            self.reconnect_delay = 1.0 # Reset backoff on successful connection

            while not self._stop_event.is_set():
                ret, frame = cap.read()
                if not ret:
                    self._handle_disconnect("Stream closed or error occurred.")
                    break

                # Process frame
                self._process_frame(frame, time.time(), "rtsp")

                # Control frame rate (optional, to reduce CPU load)
                # time.sleep(0.033) # ~30 FPS

            cap.release()

    def _handle_connect(self):
        """Update camera status and publish connection event."""
        if self.camera_id in camera_store:
            camera_store[self.camera_id].status = "connected"
        update_camera_status(self.camera_id, True)
        redis_publisher.publish(
            channel=f"camera:{self.camera_id}",
            event_type="camera.connected",
            data={"camera_id": self.camera_id, "timestamp": time.time()}
        )
        print(f"Camera {self.camera_id} connected.")

    def _handle_disconnect(self, reason: str):
        """Update camera status and publish disconnection event."""
        if self.camera_id in camera_store:
            camera_store[self.camera_id].status = "disconnected"
        update_camera_status(self.camera_id, False)
        redis_publisher.publish(
            channel=f"camera:{self.camera_id}",
            event_type="camera.disconnected",
            data={"camera_id": self.camera_id, "reason": reason, "timestamp": time.time()}
        )
        print(f"Camera {self.camera_id} disconnected. Reason: {reason}")

    def _detect_motion(self, current_frame: np.ndarray):
        """Basic motion detection using frame differencing."""
        if self.last_frame is None:
            self.last_frame = current_frame
            return

        if time.time() < self.motion_cooldown_end:
            self.last_frame = current_frame # Update last frame to prevent false positives after motion
            return

        # 1. Convert to grayscale
        gray_current = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
        gray_last = cv2.cvtColor(self.last_frame, cv2.COLOR_BGR2GRAY)

        # 2. Compute difference
        frame_delta = cv2.absdiff(gray_last, gray_current)
        thresh = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)[1]

        # 3. Dilate the thresholded image to fill in holes
        thresh = cv2.dilate(thresh, None, iterations=2)

        # 4. Find contours
        contours, _ = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        motion_detected = False
        for contour in contours:
            if cv2.contourArea(contour) < self.MIN_MOTION_AREA:
                continue
            
            # Motion detected
            motion_detected = True
            break

        if motion_detected:
            self.motion_cooldown_end = time.time() + self.MOTION_COOLDOWN_SECONDS
            
            # Publish motion event
            increment_motion_detected(self.camera_id)
            redis_publisher.publish(
                channel=f"camera:{self.camera_id}",
                event_type="motion.detected",
                data={"camera_id": self.camera_id, "timestamp": time.time(), "area": len(contours)}
            )
            print(f"Motion detected on camera {self.camera_id}!")

        # Update last frame for the next iteration
        self.last_frame = current_frame.copy()

    def _process_frame(self, frame: np.ndarray, timestamp: float, source_type: str):
        """Processes a frame received from any source (RTSP or HTTP Push)."""
        self.last_frame_time = timestamp
        self.buffer.put(frame)

        # Update metrics
        increment_frames_ingested(self.camera_id, source_type)
        update_last_frame_timestamp(self.camera_id, timestamp)

        # Publish frame ingested event
        redis_publisher.publish(
            channel=f"camera:{self.camera_id}",
            event_type="frame.ingested",
            data={"camera_id": self.camera_id, "timestamp": timestamp, "source": source_type}
        )

        # Motion Detection
        if self.motion_detection_enabled:
            self._detect_motion(frame)

    def get_latest_frame_jpeg(self) -> Optional[bytes]:
        """Returns the latest frame as a JPEG byte array."""
        frame = self.buffer.get_latest()
        if frame is None:
            return None
        
        # Encode frame to JPEG
        ret, jpeg = cv2.imencode('.jpg', frame)
        if ret:
            return jpeg.tobytes()
        return None

def start_worker(camera_id: str, source_type: str, source_url: Optional[str] = None):
    """Factory function to start a new worker."""
    if camera_id in worker_store:
        worker_store[camera_id].stop()
    
    if source_type == "rtsp" and source_url:
        worker = CameraWorker(camera_id, source_url)
        worker.source_type = "rtsp"
        worker_store[camera_id] = worker
        worker.start()
    elif source_type == "http_push":
        # For HTTP_PUSH, we start a worker just to manage the buffer and metrics
        # It won't have a stream to run, so we don't call worker.start()
        worker = CameraWorker(camera_id, "") # RTSP URL is irrelevant
        worker.source_type = "http_push"
        worker.is_running = True # Mark as running for API checks
        worker_store[camera_id] = worker
        update_camera_status(camera_id, True) # Mark push camera as always connected
        print(f"Push worker {camera_id} initialized.")

def stop_worker(camera_id: str):
    """Stops a running worker."""
    if camera_id in worker_store:
        worker_store[camera_id].stop()
        del worker_store[camera_id]

def get_worker(camera_id: str) -> Optional[CameraWorker]:
    """Retrieves a worker by ID."""
    return worker_store.get(camera_id)

def get_all_cameras() -> Dict[str, Camera]:
    """Retrieves all registered cameras."""
    return camera_store

def register_camera(camera: Camera):
    """Registers a camera and starts its worker if it's an RTSP source."""
    if camera.id in camera_store:
        # If camera exists, stop old worker and update data
        stop_worker(camera.id)
    
    camera_store[camera.id] = camera
    
    if camera.source_type == "rtsp" and camera.source_url:
        start_worker(camera.id, camera.source_type, camera.source_url)
    elif camera.source_type == "http_push":
        # Start a worker just for buffer management and metrics
        start_worker(camera.id, camera.source_type)
    
    return camera_store[camera.id]

def unregister_camera(camera_id: str):
    """Unregisters a camera and stops its worker."""
    stop_worker(camera_id)
    update_camera_status(camera_id, False) # Ensure metric is updated to disconnected
    if camera_id in camera_store:
        del camera_store[camera_id]
        return True
    return False
