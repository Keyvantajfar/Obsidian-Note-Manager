"""Path helpers: glob matching, name/folder sanitising, collision-free destinations."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Callable, Iterable

_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED_WIN = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


@lru_cache(maxsize=512)
def glob_to_regex(pattern: str) -> re.Pattern:
    """gitignore-ish glob: ``*`` (no slash), ``**`` (anything), ``?``."""
    pat = pattern.replace("\\", "/").strip()
    while pat.startswith("./"):
        pat = pat[2:]
    pat = pat.lstrip("/")
    if pat.endswith("/"):
        pat += "**"
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out), re.IGNORECASE)


def matches_glob(pattern: str, rel: str) -> bool:
    """Does `rel` (posix, relative) match `pattern`?

    A pattern with no slash matches any single path component (like .gitignore),
    ``Dir/**`` also matches ``Dir`` itself.
    """
    pattern = pattern.strip()
    if not pattern:
        return False
    rx = glob_to_regex(pattern)
    rel = rel.strip("/")
    if rx.fullmatch(rel):
        return True
    if pattern.endswith("/**") and glob_to_regex(pattern[:-3]).fullmatch(rel):
        return True
    if "/" not in pattern.strip("/"):
        return any(rx.fullmatch(part) for part in rel.split("/"))
    return False


def matches_any(patterns: Iterable[str], rel: str) -> bool:
    return any(matches_glob(p, rel) for p in patterns)


def sanitize_component(name: str) -> str:
    name = _BAD_CHARS.sub("-", name).strip().rstrip(". ")
    if name.lower() in _RESERVED_WIN:
        name = "_" + name
    return name


def sanitize_folder(path: str) -> str:
    """Normalise a user-typed folder path. Raises ValueError for anything unsafe."""
    raw = (path or "").replace("\\", "/").strip().strip("/")
    if not raw:
        return ""
    parts = []
    for part in raw.split("/"):
        part = part.strip()
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("'..' is not allowed in folder paths")
        if re.match(r"^[A-Za-z]:$", part):
            raise ValueError("drive letters are not allowed in folder paths")
        clean = sanitize_component(part)
        if not clean:
            raise ValueError(f"invalid folder name: {part!r}")
        if clean.startswith("."):
            raise ValueError("folders starting with '.' are reserved")
        parts.append(clean)
    return "/".join(parts)


def sanitize_note_name(name: str) -> str:
    name = sanitize_component(name.replace("\\", "/").split("/")[-1])
    if not name:
        raise ValueError("empty file name")
    if not name.lower().endswith(".md"):
        name += ".md"
    return name


def join_rel(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name


def split_rel(rel: str) -> tuple[str, str]:
    folder, _, name = rel.rpartition("/")
    return folder, name


def unique_dest(rel: str, taken_lower: set[str], exists: Callable[[str], bool]) -> str:
    """Return `rel`, or `stem (1).ext`, `stem (2).ext`... that is neither taken nor on disk."""
    def busy(r: str) -> bool:
        return r.lower() in taken_lower or exists(r)

    if not busy(rel):
        return rel
    folder, name = split_rel(rel)
    stem, ext = os.path.splitext(name)
    n = 1
    while True:
        cand = join_rel(folder, f"{stem} ({n}){ext}")
        if not busy(cand):
            return cand
        n += 1


def is_under(folder: str, ancestor: str) -> bool:
    """Is `folder` equal to or inside `ancestor`? ('' is the vault root)."""
    if not ancestor:
        return True
    return folder == ancestor or folder.startswith(ancestor + "/")
