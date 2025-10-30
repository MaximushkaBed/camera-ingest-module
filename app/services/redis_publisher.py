import redis.asyncio as redis
import json
from typing import Any
from app.core.config import settings

class RedisPublisher:
    def __init__(self):
        self.host = settings.REDIS_HOST
        self.port = settings.REDIS_PORT
        self.r = redis.Redis(host=self.host, port=self.port, decode_responses=True)
        print(f"Async RedisPublisher initialized: {self.host}:{self.port}")

    async def publish(self, channel: str, event_type: str, data: Any):
        message = { "event_type": event_type, "data": data }
        await self.r.publish(channel, json.dumps(message))


redis_publisher = RedisPublisher()