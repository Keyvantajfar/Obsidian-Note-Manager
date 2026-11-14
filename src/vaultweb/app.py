"""The FastAPI application: guards, routes, static UI."""
from __future__ import annotations

import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from vaultcore.rules import RuleError

from . import __version__
from .api import ai, misc, notes, plan, settings, stage, vault
from .common import vm
from .state import ApiError, AppState


def static_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)          # PyInstaller unpack dir
    if base:
        p = Path(base) / "vaultweb" / "static"
        if p.is_dir():
            return p
    return Path(__file__).resolve().parent / "static"


ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class Guard:
    """Pure-ASGI guard (works with streaming responses):
    * Host must be a loopback name (DNS-rebinding protection) and an Origin, if sent, must be this very server;
    * every non-GET request under /api needs the X-VaultMgr header (browsers cannot send it cross-site);
    * every request counts as 'the window is alive'."""

    def __init__(self, app, state: AppState, extra_hosts: set[str] | None = None):
        self.app = app
        self.state = state
        self.hosts = ALLOWED_HOSTS | (extra_hosts or set())

    async def _deny(self, send, status: int, msg: str) -> None:
        data = json.dumps({"error": msg}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
        await send({"type": "http.response.body", "body": data})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        h = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = h.get("host", "").lower()
        hostname = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0] if ":" in host else host
        if hostname not in self.hosts:
            return await self._deny(send, 403, "bad Host header")
        origin = h.get("origin")
        if origin is not None and urlsplit(origin).netloc.lower() != host:
            return await self._deny(send, 403, "cross-origin request refused")
        path = scope["path"]
        if scope["method"] not in SAFE_METHODS and path.startswith("/api") and h.get("x-vaultmgr") != "1":
            return await self._deny(send, 403, "missing X-VaultMgr header")
        self.state.last_seen = time.monotonic()
        return await self.app(scope, receive, send)


def create_app(state: AppState | None = None, *, extra_hosts: set[str] | None = None) -> FastAPI:
    state = state or AppState()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio
        state.hub.bind(asyncio.get_running_loop())
        yield
        await state.shutdown()

    app = FastAPI(title="Vault Manager", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.vm = state
    app.add_middleware(Guard, state=state, extra_hosts=extra_hosts)

    # ---- errors: always {"error": "..."} ------------------------------------------
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, e: ApiError):
        return JSONResponse({"error": e.message}, status_code=e.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, e: StarletteHTTPException):
        return JSONResponse({"error": str(e.detail)}, status_code=e.status_code)

    @app.exception_handler(RuleError)
    async def _rule_error(request: Request, e: RuleError):
        return JSONResponse({"error": str(e)}, status_code=400)

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, e: ValueError):
        return JSONResponse({"error": str(e)}, status_code=400)

    @app.exception_handler(KeyError)
    async def _key_error(request: Request, e: KeyError):
        return JSONResponse({"error": f"not found: {e.args[0] if e.args else ''}"}, status_code=404)

    @app.exception_handler(Exception)
    async def _any_error(request: Request, e: Exception):
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)

    # ---- routes ---------------------------------------------------------------------
    for mod in (vault, notes, stage, ai, plan, misc, settings):
        app.include_router(mod.router)

    @app.get("/api/events")
    async def events(request: Request):
        return StreamingResponse(state.hub.stream(request), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

    @app.post("/desktop/bye")
    async def bye(request: Request):
        """sendBeacon from the page on pagehide (a reload sends it too; the launcher waits a moment)."""
        tok = request.query_params.get("t", "")
        if state.desktop.get("token") and tok == state.desktop["token"]:
            state.bye = time.monotonic()
        return Response(status_code=204)

    @app.post("/api/open-external")
    async def open_external(request: Request):
        import webbrowser
        data = await request.json()
        url = str(data.get("url") or "")
        if not url.lower().startswith(("http://", "https://")):
            raise ApiError(400, "only http(s) links can be opened")
        webbrowser.open(url)
        return {"ok": True}

    sd = static_dir()
    app.mount("/static", StaticFiles(directory=str(sd)), name="static")

    @app.get("/")
    async def index(request: Request):
        page = (sd / "index.html").read_text(encoding="utf-8")
        d = state.desktop
        cfg = {"version": __version__, "desktop": bool(d.get("enabled")), "token": d.get("token", ""),
               "heartbeat": bool(d.get("heartbeat"))}
        tag = f"<script>window.__VM__={json.dumps(cfg)};</script>"
        page = page.replace("<!--VM-CONFIG-->", tag)
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    return app
