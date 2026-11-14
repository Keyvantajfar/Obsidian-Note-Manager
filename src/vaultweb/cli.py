"""vaultmgr — command line entry point.

    vaultmgr [gui] [VAULT]      open the app window (default; what a double-click does)
    vaultmgr serve [VAULT]      run the server only; open the printed address in any browser
    vaultmgr tui [VAULT]        the terminal UI (needs: pip install "vaultmgr[tui]")
    vaultmgr cli VAULT CMD      headless commands (status, tag, stage, accept, apply, undo, auto, dates)
    vaultmgr version
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__

COMMANDS = ("gui", "serve", "tui", "cli", "version")


def _prepare_windowed_process() -> None:
    """A windowed build (no console) has no stdout/stderr; code that prints or logs would crash on None."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))
    if sys.platform == "win32" and getattr(sys, "frozen", False) and len(sys.argv) > 1 and sys.argv[1] != "gui":
        try:                                      # started from a terminal with a command: print into that terminal
            import ctypes
            if ctypes.windll.kernel32.AttachConsole(-1):
                sys.stdout = open("CONOUT$", "w", encoding="utf-8")
                sys.stderr = open("CONOUT$", "w", encoding="utf-8")
                sys.stdin = open("CONIN$", "r", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("vault", nargs="?", help="path to the Obsidian vault (omit to pick one in the app)")
    p.add_argument("--demo", action="store_true", help="open a messy sample vault with a fake model (no LLM server needed)")
    p.add_argument("--mock", action="store_true", help="use a built-in fake model instead of the configured LLM")
    p.add_argument("--workflow", choices=["all", "header", "organizer", "dates"], help="open this workspace directly")
    p.add_argument("--import-config", metavar="FILE", help="import LLM settings from an old config.json first")


def _import_config(path: str | None) -> int:
    if not path:
        return 0
    from vaultcore.settings import Profiles
    try:
        prof = Profiles.load().import_legacy(path)
    except (OSError, ValueError) as e:
        print(f"cannot import {path}: {e}", file=sys.stderr)
        return 2
    print(f"imported LLM profile '{prof.name}' (model {prof.model or '-'}) and made it active", file=sys.stderr)
    return 0


def _vault_arg(a: argparse.Namespace) -> tuple[str | None, int]:
    if not a.vault:
        return None, 0
    p = Path(a.vault).expanduser()
    if not p.is_dir():
        print(f"vault not found: {p}", file=sys.stderr)
        return None, 2
    return str(p.resolve()), 0


def main(argv: list[str] | None = None) -> int:
    _prepare_windowed_process()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-V", "--version"):
        argv = ["version"]
    cmd = argv.pop(0) if argv and argv[0] in COMMANDS else "gui"
    if cmd == "version":
        print(f"vaultmgr {__version__}")
        return 0
    if cmd == "cli":
        from vaultcore.cli import main as cli_main
        return cli_main(argv)
    if cmd == "tui":
        try:
            from vaulttui.app import main as tui_main
        except ImportError:
            print('The terminal UI is not installed here. Use: pip install "vaultmgr[tui]"', file=sys.stderr)
            return 2
        return tui_main(argv)

    p = argparse.ArgumentParser(prog=f"vaultmgr {cmd}", description="Vault Manager — AI-assisted Obsidian vault tagger & organizer")
    _common(p)
    if cmd == "gui":
        p.add_argument("--mode", choices=["auto", "webview", "app", "browser"], default="auto",
                       help="window type: native pywebview window, Chromium app window, or a browser tab (default: best available)")
        p.add_argument("--port", type=int, default=0, help="port (default: any free port)")
    else:
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=8765)
    a = p.parse_args(argv)
    vault, rc = _vault_arg(a)
    if rc:
        return rc
    rc = _import_config(a.import_config)
    if rc:
        return rc
    from . import launcher
    if cmd == "serve":
        return launcher.serve(host=a.host, port=a.port, vault=vault, demo=a.demo, mock=a.mock, workflow=a.workflow)
    code = launcher.run(port=a.port, mode=a.mode, vault=vault, demo=a.demo, mock=a.mock, workflow=a.workflow)
    sys.stdout.flush()
    if getattr(sys, "frozen", False):
        os._exit(code)          # a windowed build must not linger on a stuck non-daemon thread after the window closed
    return code


if __name__ == "__main__":
    sys.exit(main())
