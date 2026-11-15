"""A small log file for the windowed app, where there is no console to read errors from.

Location: <config home>/logs/vaultmgr.log (about 256 KB kept, one rotation). Never raises.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
import traceback
from pathlib import Path

from vaultcore.settings import config_home

_installed = False


def log_path() -> Path:
    return config_home() / "logs" / "vaultmgr.log"


def logger() -> logging.Logger:
    return logging.getLogger("vaultmgr")


def install() -> Path | None:
    """Send unhandled exceptions (main thread, worker threads, server handlers) to the log file."""
    global _installed
    if _installed:
        return log_path()
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(p, maxBytes=256_000, backupCount=1, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        lg = logger()
        lg.setLevel(logging.INFO)
        lg.addHandler(h)
        lg.propagate = False
    except OSError:
        return None

    prev_hook, prev_thread = sys.excepthook, threading.excepthook

    def hook(et, ev, tb):
        logger().error("unhandled exception\n%s", "".join(traceback.format_exception(et, ev, tb)))
        prev_hook(et, ev, tb)

    def thook(args):
        logger().error("unhandled exception in thread %s\n%s", getattr(args.thread, "name", "?"),
                       "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
        prev_thread(args)

    sys.excepthook, threading.excepthook = hook, thook
    _installed = True
    from vaultcore import __version__
    logger().info("vaultmgr %s starting on %s", __version__, sys.platform)
    return p


def record(msg: str, exc: BaseException | None = None) -> None:
    try:
        if exc is not None:
            logger().error("%s\n%s", msg, "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        else:
            logger().error("%s", msg)
    except Exception:  # noqa: BLE001
        pass
