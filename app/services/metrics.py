from prometheus_client import Gauge, Counter, generate_latest
from typing import Dict

# Define Prometheus metrics
# Gauge for the current status of each camera (0=disconnected, 1=connected)
CAMERA_STATUS = Gauge(
    'camera_ingest_status', 
    'Current connection status of the camera (0=disconnected, 1=connected)', 
    ['camera_id']
)

# Counter for the total number of frames ingested
FRAMES_INGESTED_TOTAL = Counter(
    'camera_ingest_frames_total', 
    'Total number of frames ingested from all sources', 
    ['camera_id', 'source_type']
)

# Counter for motion detection events
MOTION_DETECTED_TOTAL = Counter(
    'camera_ingest_motion_total', 
    'Total number of motion detection events', 
    ['camera_id']
)

# Gauge for the time since the last frame was received
LAST_FRAME_TIMESTAMP = Gauge(
    'camera_ingest_last_frame_timestamp',
    'Unix timestamp of the last ingested frame',
    ['camera_id']
)

def update_camera_status(camera_id: str, is_connected: bool):
    """Updates the camera connection status gauge."""
    CAMERA_STATUS.labels(camera_id=camera_id).set(1 if is_connected else 0)

def increment_frames_ingested(camera_id: str, source_type: str):
    """Increments the total frames ingested counter."""
    FRAMES_INGESTED_TOTAL.labels(camera_id=camera_id, source_type=source_type).inc()

def increment_motion_detected(camera_id: str):
    """Increments the motion detected counter."""
    MOTION_DETECTED_TOTAL.labels(camera_id=camera_id).inc()

def update_last_frame_timestamp(camera_id: str, timestamp: float):
    """Updates the timestamp of the last received frame."""
    LAST_FRAME_TIMESTAMP.labels(camera_id=camera_id).set(timestamp)

def get_metrics() -> bytes:
    """Generates the latest Prometheus metrics in text format."""
    return generate_latest()
