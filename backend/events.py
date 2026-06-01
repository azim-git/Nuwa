import asyncio
from collections import defaultdict


class EventBus:
    def __init__(self):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs[run_id].add(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        self._subs[run_id].discard(q)
        if not self._subs[run_id]:
            self._subs.pop(run_id, None)

    def publish(self, run_id: str, payload: dict) -> None:
        for q in list(self._subs.get(run_id, ())):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass   # slow consumer — drop; next snapshot recovers full state


bus = EventBus()