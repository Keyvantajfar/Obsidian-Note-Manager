"""Date fixer (fully offline): compare each note's header date with what the file system says and propose fixes.

Statuses
    missing    the header has no date
    unreadable a value is there but it is not a date we can read
    format     readable, but not written in the configured format (the date itself is kept, only re-formatted)
    later      the header date is *later* than the file's date — typical after copying a vault (the copy time got stamped)
    earlier    the header date is earlier than the file's date (only reported when ``flag_earlier`` is on; usually normal:
               notes get edited long after they were created)
    ok         nothing to do
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from .datefmt import date_in_name, matches_format, parse_date_loose
from .index import file_created

STATUSES = ("missing", "unreadable", "format", "later", "earlier", "ok")
PROBLEMS = ("missing", "unreadable", "format", "later", "earlier")
SOURCES = ["earliest", "mtime", "birth_or_mtime", "name_or_earliest"]
SOURCE_HELP = {
    "earliest": "older of creation time and modified time (best after copying a vault)",
    "mtime": "last modified time",
    "birth_or_mtime": "creation time (wrong after a copy on Windows)",
    "name_or_earliest": "date written in the file name if there is one, else 'earliest'",
}


@dataclass
class DateRow:
    rel: str
    key: str
    current: str            # raw header value ("" if absent)
    parsed: datetime | None
    file_date: datetime
    status: str
    proposed: str           # text that would be written ("" when ok)

    @property
    def delta_days(self) -> float | None:
        return None if self.parsed is None else (self.parsed - self.file_date).total_seconds() / 86400


def date_spec(settings, key: str | None = None):
    """The header field to fix: the given key, else the first field of kind 'date'."""
    for sp in settings.header_schema:
        if sp.kind == "date" and (key is None or sp.key == key):
            return sp
    return None


def file_date(abs_path: str, source: str) -> datetime:
    return file_created(abs_path, source)


def analyze(session, *, key: str | None = None, source: str | None = None, tolerance_days: float = 1.0,
            flag_earlier: bool = False) -> list[DateRow]:
    spec = date_spec(session.settings, key)
    if spec is None:
        return []
    source = source or session.settings.date_source
    tol = timedelta(days=max(0.0, tolerance_days))
    rows: list[DateRow] = []
    for rel, note in session.index.notes.items():
        if session.is_locked(rel):
            continue
        fd = file_date(note.abs, source)
        vals = note.values.get(spec.key, [])
        cur = vals[0] if vals else ""
        parsed = parse_date_loose(cur) if cur else None
        fixed = fd.strftime(spec.date_format)
        if not cur:
            st, prop = "missing", fixed
        elif parsed is None:
            st, prop = "unreadable", fixed
        elif parsed - fd > tol:
            st, prop = "later", fixed
        elif flag_earlier and fd - parsed > tol:
            st, prop = "earlier", fixed
        elif not matches_format(cur, spec.date_format):
            st, prop = "format", parsed.strftime(spec.date_format)
        else:
            st, prop = "ok", ""
        rows.append(DateRow(rel, spec.key, cur, parsed, fd, st, prop))
    rows.sort(key=lambda r: r.rel.lower())
    return rows


def counts(rows: list[DateRow]) -> dict[str, int]:
    out = {s: 0 for s in STATUSES}
    for r in rows:
        out[r.status] += 1
    return out
