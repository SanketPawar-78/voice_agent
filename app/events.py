"""In-process pub/sub so the dashboard gets live call events over SSE."""
import asyncio
import time


class Hub:
    def __init__(self):
        self.subs: set[asyncio.Queue] = set()
        self.active: dict[str, dict] = {}   # call_id -> info (snapshot for new viewers)

    def publish(self, type_: str, **data):
        msg = {"type": type_, "ts": time.time(), **data}
        if type_ == "call_started":
            self.active[data["call_id"]] = msg
        elif type_ == "call_ended":
            self.active.pop(data["call_id"], None)
        for q in list(self.subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(500)
        self.subs.add(q)
        return q

    def unsubscribe(self, q):
        self.subs.discard(q)


hub = Hub()
