"""Run the web app on a free local port and show it in a window that looks like an application.

Window, in order of preference:
  1. pywebview  (native window; only if installed:  pip install "vaultmgr[native]")
  2. an installed Chromium-based browser in --app mode: Edge, Chrome, Chromium, Brave ... (no tabs, no address bar)
  3. the default browser (a normal tab). The server then quits by itself once the tab has been closed.
The app exits when the window closes: the page says goodbye (pagehide), the window process ends, or nothing is heard
for IDLE_LIMIT seconds (the page pings every 20 s). The Quit button in the page works as well.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__
from .app import create_app
from .server import ServerThread
from .state import AppState

TITLE = "Vault Manager"
IDLE_LIMIT = 120.0          # seconds without any request before we assume the window is gone
BYE_GRACE = 3.0             # after the page says goodbye, wait this long for a follow-up request (reload / page change)
WINDOW_SIZE = (1360, 900)

_LINUX_BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
                   "microsoft-edge-stable", "brave-browser", "brave", "vivaldi")


# ---------- browsers ----------

def find_chromium() -> str | None:
    """Path of an installed Chromium-based browser, or None. VAULTMGR_BROWSER overrides the search."""
    forced = os.environ.get("VAULTMGR_BROWSER")
    if forced:
        return shutil.which(forced) or (forced if Path(forced).exists() else None)
    c: list[str] = []
    if sys.platform == "win32":
        roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        for r in filter(None, roots):
            c += [rf"{r}\Microsoft\Edge\Application\msedge.exe", rf"{r}\Google\Chrome\Application\chrome.exe",
                  rf"{r}\BraveSoftware\Brave-Browser\Application\brave.exe", rf"{r}\Chromium\Application\chrome.exe"]
    elif sys.platform == "darwin":
        for base in ("/Applications", str(Path.home() / "Applications")):
            for app, exe in (("Google Chrome", "Google Chrome"), ("Microsoft Edge", "Microsoft Edge"),
                             ("Brave Browser", "Brave Browser"), ("Chromium", "Chromium")):
                c.append(f"{base}/{app}.app/Contents/MacOS/{exe}")
    else:
        c += [p for p in (shutil.which(n) for n in _LINUX_BROWSERS) if p]
    return next((p for p in c if Path(p).exists()), None)


def chromium_cmd(exe: str, url: str, profile: Path) -> list[str]:
    cmd = [exe, f"--app={url}", f"--user-data-dir={profile}", f"--window-size={WINDOW_SIZE[0]},{WINDOW_SIZE[1]}",
           "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"]
    if sys.platform.startswith("linux"):
        cmd.append("--class=VaultManager")
    return cmd


def window_profile_dir() -> Path:
    from vaultcore.settings import config_home
    return config_home() / ".window-profile"


# ---------- window strategies; each returns True if it showed the app and the window has now been closed ----------

class _NativeApi:
    """Exposed to the page as window.pywebview.api — a native folder dialog for the vault picker."""

    def pick_folder(self) -> str | None:
        import webview  # type: ignore
        w = webview.windows[0] if webview.windows else None
        if w is None:
            return None
        r = w.create_file_dialog(webview.FOLDER_DIALOG)
        return str(r[0]) if r else None


def show_webview(url: str, profile: Path) -> bool:
    try:
        import webview  # type: ignore
    except ImportError:
        return False
    try:
        webview.create_window(TITLE, url, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=(900, 600), js_api=_NativeApi())
        webview.start(private_mode=False, storage_path=str(profile / "webview"))
        return True
    except Exception as e:  # missing GTK/Qt/WebView2 runtime: try the next strategy
        print(f"native window unavailable ({e}); trying a browser window", file=sys.stderr)
        return False


def show_chromium(url: str, profile: Path, state: AppState) -> bool:
    exe = find_chromium()
    if not exe:
        return False
    profile.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen(chromium_cmd(exe, url, profile), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    state.desktop["heartbeat"] = True
    try:
        return watch(state, proc)
    finally:
        if proc.poll() is None:                      # the browser may linger in the background (Edge does); it is ours, end it
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


def show_browser_tab(url: str, state: AppState) -> None:
    state.desktop["heartbeat"] = True
    webbrowser.open(url)
    print(f"Vault Manager is open in your browser at {url}. It quits by itself after you close the tab.", flush=True)
    watch(state, None)


def watch(state: AppState, proc, *, idle_limit: float | None = None, bye_grace: float | None = None) -> bool:
    """Block until the window is gone. Four signals, because no single one is reliable on every OS:
    the Quit button, the page says goodbye (pagehide), the window process exits, or nothing was heard for IDLE_LIMIT.
    Returns False only if the browser failed to start at all (exit code != 0 within a few seconds)."""
    idle_limit = IDLE_LIMIT if idle_limit is None else idle_limit
    bye_grace = BYE_GRACE if bye_grace is None else bye_grace
    started = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            if state.quit_requested:
                return True
            if proc is not None and proc.poll() is not None:
                if now - started < 4 and proc.returncode != 0:
                    return False                     # e.g. a sandboxed snap that may not use our profile folder
                if now - started >= 4:
                    return True                      # window process ended = window closed
                proc = None                          # exited at once with success: handed to a running browser; rely on the page
            bye, seen = state.bye, state.last_seen
            if bye > started and now - bye > bye_grace and seen <= bye:
                return True                          # window said goodbye and nothing followed (a reload would have)
            if now - max(seen, started) > idle_limit:
                return True
            time.sleep(0.2)
    except KeyboardInterrupt:
        return True


# ---------- startup helpers ----------

def _post(base: str, path: str, data: dict | None = None) -> dict:
    req = urllib.request.Request(base + path, data=json.dumps(data or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-VaultMgr": "1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def open_initial(base: str, *, vault: str | None, demo: bool, workflow: str | None, mock: bool) -> None:
    try:
        if demo:
            _post(base, "/api/vault/demo")
        elif vault:
            _post(base, "/api/vault/open", {"path": vault, "mock": True if mock else None, "workflow": workflow})
        if workflow and (demo or vault):
            _post(base, "/api/workflow", {"id": workflow})
    except Exception as e:  # noqa: BLE001 — the page will show the vault picker; say why on the console
        print(f"could not open the vault: {e}", file=sys.stderr)


def run(*, host: str = "127.0.0.1", port: int = 0, mode: str = "auto", vault: str | None = None, demo: bool = False,
        mock: bool = False, workflow: str | None = None) -> int:
    """mode: auto | webview | app | browser"""
    state = AppState(mock=mock or demo, workflow=workflow or "all")
    state.desktop = {"enabled": True, "heartbeat": False, "token": secrets.token_urlsafe(16)}
    srv = ServerThread(create_app(state), host=host, port=port).start()
    url = srv.url
    open_initial(url.rstrip("/"), vault=vault, demo=demo, workflow=workflow, mock=mock)
    profile = window_profile_dir()
    try:
        shown = (mode in ("auto", "webview") and show_webview(url, profile)) \
            or (mode in ("auto", "app") and show_chromium(url, profile, state))
        if not shown:
            show_browser_tab(url, state)
    finally:
        srv.stop()
    return 0


def serve(*, host: str = "127.0.0.1", port: int = 8765, vault: str | None = None, demo: bool = False,
          mock: bool = False, workflow: str | None = None) -> int:
    """Server only (no window): for a browser tab, Docker, SSH port-forwarding or the CI smoke test."""
    state = AppState(mock=mock or demo, workflow=workflow or "all")
    state.desktop = {"enabled": False, "heartbeat": False, "token": ""}
    srv = ServerThread(create_app(state, extra_hosts={host} if host not in ("127.0.0.1", "localhost") else None),
                       host=host, port=port).start()
    print(f"Vault Manager {__version__} serving on {srv.url}  (Ctrl+C to stop)", flush=True)
    open_initial(srv.url.rstrip("/"), vault=vault, demo=demo, workflow=workflow, mock=mock)
    try:
        while srv.thread.is_alive() and not state.quit_requested:
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
    return 0
