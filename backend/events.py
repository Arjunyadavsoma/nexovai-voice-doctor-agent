"""Tiny in-process event bus: the voice pipeline publishes, the dashboard
WebSocket subscribers receive. No extra service needed for one server.
"""
import asyncio
import json
from typing import Any

_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(event: str, data: dict[str, Any]) -> None:
    """Fire-and-forget; slow/absent dashboards never block a call."""
    payload = json.dumps({"event": event, **data})
    for q in list(_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass
