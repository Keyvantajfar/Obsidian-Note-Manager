"""Application state: which vault is open, the Session, the event bridge, desktop-window signals."""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

from vaultcore.demo import make_demo_vault
from vaultcore.mock import MockLLM
from vaultcore.session import Session
from vaultcore.settings import Profiles
from vaultcore.workflows import WORKFLOWS, apply_workflow

from .hub import EventHub


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class AppState:
    def __init__(self, *, mock: bool = False, workflow: str = "all") -> None:
        self.hub = EventHub()
        self.session: Session | None = None
        self.workflow = workflow if workflow in WORKFLOWS else "all"
        self.mock = mock
        self.mock_timing = (0.25, 0.5)      # (delay, jitter) of the fake model; tests set (0, 0)
        self.scanning = False
        self.scan_error = ""
        self.tasks: set[asyncio.Task] = set()
        self.lock = asyncio.Lock()
        self.demo_dir: str | None = None
        # desktop window bookkeeping (see vaultweb.desktop)
        self.desktop: dict = {"enabled": False, "heartbeat": False, "token": ""}
        self.last_seen = time.monotonic()
        self.bye = 0.0
        self.quit_requested = False

    # ---- helpers -------------------------------------------------------------------
    def need(self) -> Session:
        if self.session is None:
            raise ApiError(409, "no vault is open")
        return self.session

    def spawn(self, coro) -> asyncio.Task:
        t = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        return t

    # ---- vault lifecycle -----------------------------------------------------------
    async def open_vault(self, path: str, *, workflow: str | None = None, mock: bool | None = None,
                         remember: bool = True) -> Session:
        p = Path(path).expanduser()
        if not p.is_dir():
            raise ApiError(400, f"not a folder: {p}")
        async with self.lock:
            await self._close()
            use_mock = self.mock if mock is None else mock
            self.mock = use_mock
            try:
                s = Session(p, llm=MockLLM(delay=self.mock_timing[0], jitter=self.mock_timing[1]) if use_mock else None)
            except (OSError, FileNotFoundError) as e:
                raise ApiError(400, f"cannot open vault: {e}")
            self.session = s
            try:                                    # the core only remembers in memory; persist so 'recent' survives
                if remember:
                    s.profiles.save()
                else:
                    s.profiles.recent_vaults = [v for v in s.profiles.recent_vaults if v != s.vault]
            except OSError:
                pass
            if workflow in WORKFLOWS:
                self.workflow = workflow
            apply_workflow(s, self.workflow)
            s.on(self._on_session_event)
            self.scanning, self.scan_error = True, ""
            self.hub.publish("vault", state="opening")
            try:
                diff = await asyncio.to_thread(s.index.scan)
                s.after_scan(diff)
                s.start_background()
            except Exception as e:  # noqa: BLE001
                self.scan_error = f"{type(e).__name__}: {e}"
            finally:
                self.scanning = False
            self.hub.publish("vault", state="open")
            return s

    async def open_demo(self) -> Session:
        base = Path(tempfile.mkdtemp(prefix="vaultmgr-demo-"))
        self.demo_dir = str(base)
        vault = make_demo_vault(base / "Demo Vault")
        return await self.open_vault(str(vault), mock=True, remember=False)

    async def close_vault(self) -> None:
        async with self.lock:
            await self._close()
            self.hub.publish("vault", state="closed")

    async def _close(self) -> None:
        s, self.session = self.session, None
        if s is not None:
            try:
                s._listeners.clear()
                await s.aclose()
            except Exception:  # noqa: BLE001
                pass

    async def shutdown(self) -> None:
        self.hub.close()
        for t in list(self.tasks):
            t.cancel()
        await self._close()

    def set_workflow(self, wid: str) -> None:
        if wid not in WORKFLOWS:
            raise ApiError(400, f"unknown workspace: {wid}")
        self.workflow = wid
        if self.session is not None:
            apply_workflow(self.session, wid)
            self.session.emit("settings")
        self.hub.publish("workflow", id=wid)

    # ---- Session events -> browser -------------------------------------------------
    def _on_session_event(self, event: str, payload: dict) -> None:
        pub = self.hub.publish
        if event == "job":
            j = payload.get("job")
            if j is None:
                pub("job", kind="cleared", id=None)
                return
            pub("job", kind=payload.get("event"), id=j.id, state=j.state, finished=j.finished_count, total=j.total,
                errors=len(j.errors), title=j.title)
        elif event == "scan":
            d = payload.get("diff")
            pub("scan", added=len(d.added) if d else 0, changed=len(d.changed) if d else 0, removed=len(d.removed) if d else 0)
        elif event == "applied":
            r = payload["result"]
            pub("applied", batch_id=r.batch_id, applied=len(r.applied), failed=len(r.failed))
        elif event == "undone":
            r = payload["result"]
            pub("undone", batch_id=r.batch_id, restored=r.restored, moved_back=r.moved_back, conflicts=len(r.conflicts))
        elif event == "message":
            pub("message", text=payload.get("text", ""))
        elif event == "confirm_needed":
            pub("confirm_needed", count=payload.get("count", 0))
        else:
            pub(event)
