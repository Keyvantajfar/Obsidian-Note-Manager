"""Server-sent-events hub: turns Session events into a stream the browser listens to.

Noisy events (stage / job progress / ...) are coalesced so a fast job does not flood the page;
the browser reacts to an event by re-fetching the data it shows.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

# events where only the latest one matters within the coalescing window
COALESCE = {"stage", "scan", "taxonomy", "rules", "settings", "watch", "job.progress", "job.state", "status"}
WINDOW = 0.12


class EventHub:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.subs: set[asyncio.Queue] = set()
        self._pending: dict[str, dict] = {}
        self._timer: asyncio.TimerHandle | None = None
        self.seq = 0
        self.closing = False
        self._thread = threading.get_ident()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self._thread = threading.get_ident()

    # ---- publishing ----------------------------------------------------------------
    def publish(self, event: str, **payload: Any) -> None:
        """Thread-safe."""
        if self.loop is None or self.closing:
            return
        if threading.get_ident() == self._thread:
            self._publish(event, payload)
        else:
            self.loop.call_soon_threadsafe(self._publish, event, payload)

    def _publish(self, event: str, payload: dict) -> None:
        key = event
        if event == "job":
            key = "job.progress" if payload.get("kind") == "progress" else "job.state"
            if key == "job.progress":
                key = f"job.progress:{payload.get('id')}"
        if event in COALESCE or key.startswith("job.progress"):
            self._pending[key] = {"event": event, **payload}
            if self._timer is None and self.loop is not None:
                self._timer = self.loop.call_later(WINDOW, self._flush)
            return
        self._send({"event": event, **payload})

    def _flush(self) -> None:
        self._timer = None
        pend, self._pending = self._pending, {}
        for msg in pend.values():
            self._send(msg)

    def _send(self, msg: dict) -> None:
        self.seq += 1
        msg = {"seq": self.seq, "t": round(time.time(), 3), **msg}
        for q in list(self.subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # a slow/hidden tab: drop everything queued and tell it to refetch all
                try:
                    while True:
                        q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                q.put_nowait({"seq": self.seq, "event": "resync"})

    # ---- subscribing ---------------------------------------------------------------
    async def stream(self, request=None, keepalive: float = 15.0):
        """Async generator of SSE frames for one browser connection."""
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.subs.add(q)
        try:
            yield "retry: 1500\n\n"
            yield self._frame({"seq": self.seq, "event": "hello"})
            while not self.closing:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=keepalive)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    if request is not None and await request.is_disconnected():
                        break
                    continue
                yield self._frame(msg)
        finally:
            self.subs.discard(q)

    @staticmethod
    def _frame(msg: dict) -> str:
        return f"id: {msg.get('seq', 0)}\ndata: {json.dumps(msg, default=str)}\n\n"

    def close(self) -> None:
        """Thread-safe: end every stream so the server can shut down quickly."""
        if self.loop is not None and threading.get_ident() != self._thread:
            self.loop.call_soon_threadsafe(self.close)
            return
        self.closing = True
        for q in list(self.subs):
            try:
                q.put_nowait({"event": "bye"})
            except asyncio.QueueFull:
                pass
