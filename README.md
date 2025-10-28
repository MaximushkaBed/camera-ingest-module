# Camera Ingest Module

## Overview
This service is designed to ingest video streams from multiple sources (RTSP and HTTP Push), maintain a circular buffer of the latest frames, publish events to a Redis message bus, and expose Prometheus metrics for monitoring.

## Features
- **RTSP Ingestion:** Connects to RTSP streams, handles reconnections, and processes frames.
- **HTTP Push Ingestion:** Provides an API endpoint to receive frames pushed from cameras.
- **Circular Buffer:** Stores the latest frames in memory for immediate retrieval.
- **Message Bus:** Publishes camera events (`connected`, `disconnected`, `motion.detected`, `frame.ingested`) to Redis Pub/Sub.
- **Motion Detection:** Basic frame-differencing motion detection for cameras without native support.
- **Monitoring:** Exposes Prometheus metrics on the `/metrics` endpoint.

## Quick Start with Docker Compose
1. **Clone the repository:**
```bash
git clone <REPOSITORY_URL_HERE>
cd camera-ingest-module
```
2. **Start the services (App and Redis):**
```bash
docker-compose up --build
```
The application will be available at `http://localhost:8000`.

## API Endpoints
### 1. Register a Camera
`POST /api/cameras`
Example (RTSP):
```bash
curl -X POST "http://localhost:8000/api/cameras" -H "Content-Type: application/json" -d '{"id": "cam_001", "source_type": "rtsp", "source_url": "rtsp://user:password@ip:port/stream"}'
```
Example (HTTP Push):
```bash
curl -X POST "http://localhost:8000/api/cameras" -H "Content-Type: application/json" -d '{"id": "cam_002", "source_type": "http_push"}'
```
### 2. Get Latest Frame
`GET /api/cameras/{camera_id}/frame/latest`
Example:
```bash
curl -X GET "http://localhost:8000/api/cameras/cam_001/frame/latest" --output latest_frame.jpg
```
### 3. HTTP Push Ingest
`POST /api/ingest/push/{camera_id}`
Example (ingesting a frame from a local file `frame.jpg`):
```bash
curl -X POST "http://localhost:8000/api/ingest/push/cam_002" -F "frame_file=@frame.jpg" -F "timestamp=$(date +%s.%N)"
```
### 4. Prometheus Metrics
`GET /metrics`

## Redis Events
Events are published to the channel `camera:{camera_id}`.

| Event Type | Description | Data Payload |
|---|---|---|
| `camera.connected` | RTSP stream successfully connected. | `{"camera_id": str, "timestamp": float}` |
| `camera.disconnected` | RTSP stream lost connection. | `{"camera_id": str, "reason": str, "timestamp": float}` |
| `frame.ingested` | A new frame was successfully added to the buffer. | `{"camera_id": str, "timestamp": float, "source": str}` |
| `motion.detected` | Motion was detected by the internal algorithm. | `{"camera_id": str, "timestamp": float, "area": int}` |

---

## Development Notes
The motion detection is a basic frame-differencing algorithm and should be tuned or replaced for production use.

## License
This project is licensed under the MIT License.

