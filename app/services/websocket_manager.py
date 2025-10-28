import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List, Dict

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
        for connection in self.active_connections:
            try:
                await connection.send_text(message_json)
            except RuntimeError:
                # Connection might have closed just before sending
                self.disconnect(connection)

manager = ConnectionManager()
websocket_router = APIRouter()

# --- Redis Listener for WebSocket Broadcast ---
async def websocket_redis_listener():
    """Listens to Redis Pub/Sub and broadcasts messages to all WebSocket clients."""
    redis_publisher = RedisPublisher()
    # Use a separate connection for pubsub
    r = redis_publisher.r.connection_pool.get_connection('pubsub', False)
    pubsub = r.pubsub()
    
    # Subscribe to all camera channels
    pubsub.psubscribe("camera:*") 
    
    print("WebSocket Redis listener started, subscribed to camera:*")

    # Use a thread-safe way to read from pubsub in an async environment
    # This is a simplified approach, a dedicated thread or asyncio.to_thread is better for production
    while True:
        message = pubsub.get_message(ignore_subscribe_messages=True)
        if message and message["type"] == "message":
            try:
                # The message['data'] is already a JSON string from RedisPublisher
                # We can broadcast it directly
                data = json.loads(message["data"])
                
                # Broadcast the event
                await manager.broadcast(data)

            except json.JSONDecodeError:
                print(f"Failed to decode JSON message from Redis: {message['data']}")
            except Exception as e:
                print(f"Error in WebSocket Redis listener: {e}")
        
        # Prevent blocking the event loop
        await asyncio.sleep(0.01)

# Start the Redis listener as a background task on startup
@websocket_router.on_event("startup")
async def start_websocket_listener():
    asyncio.create_task(websocket_redis_listener())

# --- WebSocket Endpoint ---
@websocket_router.websocket("/events")
async def websocket_endpoint(websocket: WebSocket):
    """Client endpoint for receiving real-time events."""
    await manager.connect(websocket)
    try:
        # Keep the connection open
        while True:
            # Optionally receive messages from the client if needed (e.g., subscription requests)
            # For now, we just wait to keep the connection alive
            await websocket.receive_text() 
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print(f"Client disconnected from WebSocket.")
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)
