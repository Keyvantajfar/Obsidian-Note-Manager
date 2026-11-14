"""Run the app with uvicorn in a background thread (used by the desktop launcher, `serve` and the tests)."""
from __future__ import annotations

import socket
import threading
import time

import uvicorn


def port_free(host: str, port: int) -> bool:
    import sys
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0 if sys.platform == "win32" else 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


class ServerThread:
    """uvicorn.Server on its own thread. `log_config=None` matters for windowed builds that have no stderr."""

    def __init__(self, app, host: str = "127.0.0.1", port: int = 0):
        self.app = app
        self.host = host
        self.port = port or free_port(host)
        cfg = uvicorn.Config(app, host=host, port=self.port, log_config=None, access_log=False, lifespan="on",
                             timeout_graceful_shutdown=2, ws="none")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self._run, name="vaultweb-server", daemon=True)
        self.error: BaseException | None = None

    def _run(self) -> None:
        try:
            self.server.run()
        except BaseException as e:  # noqa: BLE001
            self.error = e

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self, timeout: float = 15.0) -> "ServerThread":
        self.thread.start()
        t0 = time.monotonic()
        while not self.server.started:
            if self.error is not None or not self.thread.is_alive():
                raise RuntimeError(f"server failed to start: {self.error!r}")
            if time.monotonic() - t0 > timeout:
                raise TimeoutError("server did not start in time")
            time.sleep(0.02)
        return self

    def stop(self, timeout: float = 8.0) -> None:
        state = getattr(self.app.state, "vm", None)
        if state is not None:
            state.hub.close()
        self.server.should_exit = True
        self.thread.join(timeout)
