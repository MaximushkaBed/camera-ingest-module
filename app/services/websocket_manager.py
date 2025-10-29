import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List, Dict, Optional

from app.services.redis_publisher import RedisPublisher

# --- WebSocket Manager ---
class ConnectionManager:
    """Manages active WebSocket connections."""
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict):
        """Sends a JSON message to all active connections."""
        message_json = json.dumps(message)
        connections = self.active_connections[:]
        for connection in connections:
            try:
                await connection.send_text(message_json)
            except RuntimeError:
                self.disconnect(connection)

manager = ConnectionManager()
websocket_router = APIRouter()
websocket_listener_task: Optional[asyncio.Task] = None

# --- Redis Listener for WebSocket Broadcast ---
async def websocket_redis_listener():
    """Listens to Redis Pub/Sub and broadcasts messages to all WebSocket clients."""
    pubsub = None
    try:
        redis_publisher = RedisPublisher()
        pubsub = redis_publisher.r.pubsub()
        await pubsub.psubscribe("camera:*")
        print("WebSocket Redis listener started, subscribed to camera:*")

        async for message in pubsub.listen():
            if message and message["type"] == "pmessage":
                try:
                    data = json.loads(message["data"])
                    await manager.broadcast(data)
                except json.JSONDecodeError:
                    print(f"Failed to decode JSON message from Redis: {message['data']}")
                except Exception as e:
                    print(f"Error processing message in WebSocket listener: {e}")
    except asyncio.CancelledError:
        print("WebSocket listener task cancelled.")
    except Exception as e:
        print(f"WebSocket listener error: {e}")
    finally:
        if pubsub:
            await pubsub.close()
        print("WebSocket listener stopped.")


# --- WebSocket Endpoint ---
@websocket_router.websocket("/events")
async def websocket_endpoint(websocket: WebSocket):
    """Client endpoint for receiving real-time events."""
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text() 
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print("Client disconnected from WebSocket.")