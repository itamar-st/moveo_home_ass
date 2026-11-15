"""In-process asyncio pub/sub broadcaster for live /api/alerts/stream SSE clients."""

import asyncio
from typing import Any


class AlertBroadcaster:
    """Fan-out broadcaster pushing alert and scoring events to active SSE subscribers."""

    def __init__(self, max_queue_size: int = 100) -> None:
        self._max_queue_size = max_queue_size
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    @property
    def subscriber_count(self) -> int:
        """Number of currently connected SSE subscribers."""
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register a new subscriber queue."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber queue."""
        self._subscribers.discard(queue)

    def publish(self, event: str, data: dict[str, Any]) -> int:
        """Publish an event to all active subscribers; return count delivered."""
        payload = {"event": event, "data": data}
        delivered = 0
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(payload)
                delivered += 1
            except asyncio.QueueFull:
                self._subscribers.discard(queue)
        return delivered


alert_broadcaster = AlertBroadcaster()
