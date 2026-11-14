"""Background job runner: a pool of async workers with pause / resume / cancel and a
worker count that can be changed while the job is running."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable

from .llm import auth_hint

QUEUED, RUNNING, DONE, ERROR, SKIPPED, STALE, CANCELLED = (
    "queued", "running", "done", "error", "skipped", "stale", "cancelled")

# handler: receives the keys of one unit (1 key, or a micro-batch) and returns [(key, state, message)]
Handler = Callable[[list[str]], Awaitable[list[tuple[str, str, str]]]]


@dataclass
class JobItem:
    key: str
    state: str = QUEUED
    message: str = ""


class Job:
    _counter = 0

    def __init__(self, kind: str, title: str, units: list[list[str]], handler: Handler, workers: int,
                 notify: Callable[["Job", str], None]):
        Job._counter += 1
        self.id = Job._counter
        self.kind = kind
        self.title = title
        self.handler = handler
        self.workers = max(1, workers)
        self.state = QUEUED               # queued | running | paused | done | cancelled
        self.items: dict[str, JobItem] = {k: JobItem(k) for u in units for k in u}
        self.tokens = 0
        self.created = time.time()
        self.started = 0.0
        self.finished = 0.0
        self.log: deque[str] = deque(maxlen=400)
        self._queue: asyncio.Queue[list[str]] = asyncio.Queue()
        for u in units:
            self._queue.put_nowait(u)
        self._notify = notify
        self._resume = asyncio.Event()
        self._resume.set()
        self._cancelled = False
        self._alive: dict[int, asyncio.Task] = {}
        self._done = asyncio.Event()
        self._runner: asyncio.Task | None = None

    # ---- stats -------------------------------------------------------------------
    @property
    def total(self) -> int:
        return len(self.items)

    def count(self, *states: str) -> int:
        return sum(1 for i in self.items.values() if i.state in states)

    @property
    def finished_count(self) -> int:
        return self.count(DONE, ERROR, SKIPPED, STALE, CANCELLED)

    @property
    def errors(self) -> list[JobItem]:
        return [i for i in self.items.values() if i.state == ERROR]

    @property
    def rate(self) -> float:
        if not self.started:
            return 0.0
        dt = (self.finished or time.time()) - self.started
        return self.finished_count / dt if dt > 0 else 0.0

    @property
    def active(self) -> bool:
        return self.state in (QUEUED, RUNNING, "paused")

    def say(self, msg: str) -> None:
        self.log.append(f"{time.strftime('%H:%M:%S')}  {msg}")

    # ---- control -----------------------------------------------------------------
    def start(self) -> None:
        self._runner = asyncio.get_running_loop().create_task(self._run())

    def pause(self) -> None:
        if self.state == RUNNING:
            self.state = "paused"
            self._resume.clear()
            self.say("paused (in-flight requests finish)")
            self._notify(self, "state")

    def resume(self) -> None:
        if self.state == "paused":
            self.state = RUNNING
            self._resume.set()
            self.say("resumed")
            self._notify(self, "state")

    def cancel(self) -> None:
        if not self.active:
            return
        self._cancelled = True
        self._resume.set()
        while True:
            try:
                u = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            for k in u:
                self.items[k].state = CANCELLED
        for t in list(self._alive.values()):
            t.cancel()
        self.say("cancelled")
        if self._runner is not None and not self._runner.done() and not self._alive:
            self._finish()

    def set_workers(self, n: int) -> None:
        self.workers = max(1, int(n))
        if self.state in (RUNNING, "paused"):
            self._spawn()
            self.say(f"workers → {self.workers}")

    async def wait(self) -> None:
        await self._done.wait()

    # ---- engine ------------------------------------------------------------------
    def _spawn(self) -> None:
        loop = asyncio.get_running_loop()
        for idx in range(self.workers):
            if idx not in self._alive and not self._queue.empty() and not self._cancelled:
                self._alive[idx] = loop.create_task(self._worker(idx))

    async def _run(self) -> None:
        self.state = RUNNING
        self.started = time.time()
        self.say(f"started: {self.total} item(s), {self.workers} worker(s)")
        self._notify(self, "started")
        if self._queue.empty():
            self._finish()
            return
        self._spawn()
        while self._alive:
            await asyncio.gather(*list(self._alive.values()), return_exceptions=True)
            self._alive = {i: t for i, t in self._alive.items() if not t.done()}
            if not self._queue.empty() and not self._cancelled:
                self._spawn()
        self._finish()

    def _finish(self) -> None:
        if self._done.is_set():
            return
        for i in self.items.values():
            if i.state in (QUEUED, RUNNING):
                i.state = CANCELLED
        self.state = "cancelled" if self._cancelled else "done"
        self.finished = time.time()
        self.say(f"{self.state}: {self.count(DONE)} done, {self.count(ERROR)} failed, "
                 f"{self.count(SKIPPED, STALE)} skipped")
        self._done.set()
        self._notify(self, "finished")

    async def _worker(self, idx: int) -> None:
        me = asyncio.current_task()
        try:
            while not self._cancelled:
                await self._resume.wait()
                if self._cancelled or idx >= self.workers:
                    break
                try:
                    unit = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                for k in unit:
                    self.items[k].state = RUNNING
                try:
                    results = await self.handler(unit)
                except asyncio.CancelledError:
                    for k in unit:
                        self.items[k].state = CANCELLED
                    if self._cancelled:
                        break
                    raise
                except Exception as e:  # noqa: BLE001 — a failing handler must not kill the pool
                    results = [(k, ERROR, f"{type(e).__name__}: {e}") for k in unit]
                seen = set()
                for key, state, msg in results:
                    it = self.items.get(key)
                    if it is not None:
                        it.state, it.message = state, msg
                        seen.add(key)
                        if state == ERROR:
                            self.say(f"✗ {key}: {msg}")
                            hint = auth_hint(msg)
                            if hint and self.state == RUNNING:
                                self.say(f"⚠ {hint} Job paused — fix it, then resume (space) and 'Retry failed' (r).")
                                self.pause()
                for k in unit:
                    if k not in seen:
                        self.items[k].state, self.items[k].message = ERROR, "handler returned nothing"
                self._notify(self, "progress")
        finally:
            if self._alive.get(idx) is me:
                self._alive.pop(idx, None)


class JobManager:
    def __init__(self) -> None:
        self.jobs: list[Job] = []
        self.listeners: list[Callable[[Job, str], None]] = []

    def _notify(self, job: Job, event: str) -> None:
        for cb in list(self.listeners):
            try:
                cb(job, event)
            except Exception:
                pass

    def submit(self, kind: str, title: str, units: Iterable[list[str]], handler: Handler, workers: int) -> Job:
        job = Job(kind, title, [list(u) for u in units], handler, workers, self._notify)
        self.jobs.append(job)
        if len(self.jobs) > 60:
            self.jobs = [j for j in self.jobs if j.active] + [j for j in self.jobs if not j.active][-40:]
        self._notify(job, "added")
        job.start()
        return job

    def active(self) -> list[Job]:
        return [j for j in self.jobs if j.active]

    def get(self, jid: int) -> Job | None:
        return next((j for j in self.jobs if j.id == jid), None)

    def pause_all(self) -> None:
        for j in self.active():
            j.pause()

    def resume_all(self) -> None:
        for j in self.active():
            j.resume()

    def cancel_all(self) -> None:
        for j in self.active():
            j.cancel()

    def clear_finished(self) -> None:
        self.jobs = [j for j in self.jobs if j.active]
        self._notify(None, "cleared")  # type: ignore[arg-type]
