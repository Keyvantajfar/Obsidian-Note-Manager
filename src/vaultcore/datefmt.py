"""Date helpers that need no model: tolerant parsing of header dates and dates embedded in file names."""
from __future__ import annotations

import re
from datetime import datetime

_ISO = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T ]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?(?!\d)")
_NAME = re.compile(r"(?<!\d)((?:19|20)\d\d)[-_. ]?(0[1-9]|1[0-2])[-_. ]?(0[1-9]|[12]\d|3[01])(?!\d)")


def parse_date_loose(text: str) -> datetime | None:
    """Parse the usual header spellings (2024-09-16, 2024-09-16T17:18, 2024/9/16 17:18:05, "[[2024-09-16]]").
    Ambiguous day/month-first forms are deliberately NOT guessed — they count as unreadable."""
    m = _ISO.search((text or "").strip().strip("\"'[]() "))
    if not m:
        return None
    y, mo, d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None


def date_in_name(name: str) -> datetime | None:
    """A calendar date written in a file name (``Journal 2024-01-02.md``, ``20240102 meeting.md``)."""
    m = _NAME.search(name)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def matches_format(text: str, fmt: str) -> bool:
    try:
        datetime.strptime(text.strip(), fmt)
        return True
    except ValueError:
        return False
