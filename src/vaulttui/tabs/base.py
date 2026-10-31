"""Shared helpers for the tabs."""
from __future__ import annotations

import time

from rich.text import Text
from textual.containers import Vertical

from vaultcore.session import Session


class TabBase(Vertical):
    """A tab body. The app calls ``refresh_view`` when something this tab ``WATCH``es changed
    (only while the tab is visible; otherwise on activation)."""

    TAB_ID = ""
    MAIN_FOCUS = ""                    # css selector of the widget that should have focus when the tab opens
    WATCH: set[str] = set()
    MIN_INTERVAL = 0.0                 # seconds between refreshes while jobs are hammering events

    def __init__(self, session: Session, **kw):
        super().__init__(**kw)
        self.session = session
        self._last_refresh = 0.0

    def refresh_view(self) -> None:    # pragma: no cover - overridden
        pass

    def maybe_refresh(self, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and now - self._last_refresh < self.MIN_INTERVAL:
            return False
        self._last_refresh = now
        self.refresh_view()
        return True

    def focus_main(self) -> None:
        if not self.MAIN_FOCUS:
            return
        try:      # focusing a widget inside a hidden tab would flip the tab back — only act for the visible one
            if self.app.query_one("#tabs").active != self.TAB_ID:
                return
            self.query_one(self.MAIN_FOCUS).focus()
        except Exception:
            pass

    def toast(self, msg: str, severity: str = "information") -> None:
        self.app.notify(msg, severity=severity)  # type: ignore[arg-type]


def conf_text(c: float | None) -> Text:
    if c is None:
        return Text("  –  ", style="dim")
    style = "green" if c >= 0.85 else ("yellow" if c >= 0.6 else "red")
    return Text(f"{c:.2f}", style=style)


def status_text(status: str) -> Text:
    return {"proposed": Text("● proposed", style="yellow"),
            "accepted": Text("✔ accepted", style="green"),
            "rejected": Text("✘ rejected", style="red")}.get(status, Text(status))


def header_state(note) -> Text:
    if not note.has_fm:
        return Text("none", style="red")
    if note.missing:
        return Text("partial", style="yellow")
    return Text("complete", style="green")


def short(s: str, n: int) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"
