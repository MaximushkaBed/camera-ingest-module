import redis.asyncio as redis
import json
import os
from typing import Any

class RedisPublisher:
    def __init__(self):
        self.host = os.getenv("REDIS_HOST", "localhost")
        self.port = int(os.getenv("REDIS_PORT", 6379))
        self.r = redis.Redis(host=self.host, port=self.port, decode_responses=True)
        print(f"Async RedisPublisher initialized: {self.host}:{self.port}")

    # 1. Сделайте метод асинхронным с помощью 'async'
    async def publish(self, channel: str, event_type: str, data: Any):
        """Publishes an event to a Redis channel asynchronously."""
        message = {
            "event_type": event_type,
            "data": data
        }
        # 2. Используйте 'await' для реальной отправки команды в Redis
        await self.r.publish(channel, json.dumps(message))
        # print(f"Published to {channel}: {event_type}")

# Global instance
redis_publisher = RedisPublisher()