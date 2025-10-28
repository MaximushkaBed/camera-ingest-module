import redis
import json
import os
from typing import Any

class RedisPublisher:
    def __init__(self):
        self.host = os.getenv("REDIS_HOST", "localhost")
        self.port = int(os.getenv("REDIS_PORT", 6379))
        self.r = redis.Redis(host=self.host, port=self.port, decode_responses=True)
        print(f"RedisPublisher initialized: {self.host}:{self.port}")

    def publish(self, channel: str, event_type: str, data: Any):
        """Publishes an event to a Redis channel."""
        message = {
            "event_type": event_type,
            "data": data
        }
        self.r.publish(channel, json.dumps(message))
        # print(f"Published to {channel}: {event_type}")

# Global instance
redis_publisher = RedisPublisher()
