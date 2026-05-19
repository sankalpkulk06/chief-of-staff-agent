"""In-memory trace broker for streaming agent execution events to the frontend."""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncGenerator, Callable, Dict, Optional


@dataclass
class TraceEvent:
    type: str       # agent name or "security" | "orchestrator" | "synthesis" | "output_check" | "complete"
    status: str     # "running" | "complete" | "error" | "waiting" | "fallback"
    message: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "message": self.message,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


class _Slot:
    __slots__ = ("queue", "loop", "created_at")

    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self.queue = queue
        self.loop = loop
        self.created_at = time.monotonic()


class TraceBroker:
    _TTL_SECONDS = 300  # clean up slots older than 5 minutes

    def __init__(self) -> None:
        self._slots: Dict[str, _Slot] = {}
        self._cleanup_task: Optional[asyncio.Task] = None

    def create(self, trace_id: str, loop: asyncio.AbstractEventLoop) -> None:
        """Register a new trace slot. Call from the async endpoint before starting the agent."""
        self._slots[trace_id] = _Slot(asyncio.Queue(), loop)
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                self._cleanup_task = loop.create_task(self._cleanup_loop())
            except RuntimeError:
                pass

    def publish_sync(self, trace_id: str, event: TraceEvent) -> None:
        """Publish an event from a worker thread."""
        slot = self._slots.get(trace_id)
        if slot is None:
            return
        slot.loop.call_soon_threadsafe(slot.queue.put_nowait, event)

    def complete(self, trace_id: str) -> None:
        """Signal end-of-stream. Sends a sentinel None into the queue."""
        slot = self._slots.get(trace_id)
        if slot is None:
            return
        slot.loop.call_soon_threadsafe(slot.queue.put_nowait, None)

    async def subscribe(self, trace_id: str) -> AsyncGenerator[TraceEvent, None]:
        """Async generator that yields TraceEvents until the stream is complete."""
        slot = self._slots.get(trace_id)
        if slot is None:
            return
        while True:
            event = await slot.queue.get()
            if event is None:
                break
            yield event
        self._slots.pop(trace_id, None)

    def make_callback(self, trace_id: str) -> Callable[..., None]:
        """Return a thread-safe callback suitable for passing into AgentRunner.run()."""
        def _cb(event_type: str, status: str, message: str, metadata: Optional[dict] = None) -> None:
            self.publish_sync(
                trace_id,
                TraceEvent(
                    type=event_type,
                    status=status,
                    message=message,
                    metadata=metadata or {},
                ),
            )
        return _cb

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            cutoff = time.monotonic() - self._TTL_SECONDS
            stale = [tid for tid, slot in self._slots.items() if slot.created_at < cutoff]
            for tid in stale:
                self._slots.pop(tid, None)


# Module-level singleton
broker = TraceBroker()
