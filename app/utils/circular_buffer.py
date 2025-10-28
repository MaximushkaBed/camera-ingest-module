from collections import deque
from typing import Any, Optional, Deque

class CircularBuffer:
    """A thread-safe circular buffer for storing frames."""
    def __init__(self, capacity: int):
        self.capacity: int = capacity
        self._buffer: Deque[Any] = deque(maxlen=capacity)

    def put(self, item: Any) -> None:
        """Adds an item to the buffer. If the buffer is full, the oldest item is discarded."""
        self._buffer.append(item)

    def get_latest(self) -> Optional[Any]:
        """Returns the latest item added to the buffer."""
        if not self._buffer:
            return None
        return self._buffer[-1]

    def get_all(self) -> list[Any]:
        """Returns all items in the buffer, from oldest to newest."""
        return list(self._buffer)

    def __len__(self) -> int:
        return len(self._buffer)
