"""Vault index: discovers notes, parses their headers, builds a light link graph."""
from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from . import frontmatter as fm
from .pathutil import matches_any, split_rel
from .settings import Settings, STATE_DIR

_WIKILINK_RE = re.compile(r"!?\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|[^\]]*)?\]\]")
_MDLINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


@dataclass
class NoteInfo:
    rel: str                          # posix path relative to the vault root
    abs: str
    size: int
    mtime_ns: int
    created: datetime
    hash: str
    has_fm: bool
    values: dict[str, list[str]]      # header key -> clean values
    missing: list[str]                # required header keys that are absent/empty
    links: list[str]                  # wikilink targets (as written, may include a path)
    md_links: list[str]               # relative markdown links / embeds
    word_count: int = 0

    @property
    def folder(self) -> str:
        return split_rel(self.rel)[0]

    @property
    def name(self) -> str:
        return split_rel(self.rel)[1]

    @property
    def title(self) -> str:
        n = self.name
        return n[:-3] if n.lower().endswith(".md") else n

    @property
    def signature(self) -> tuple[int, int]:
        return (self.mtime_ns, self.size)


def file_created(path: str, mode: str = "birth_or_mtime") -> datetime:
    st = os.stat(path)
    birth = getattr(st, "st_birthtime", None)
    if birth is None and os.name == "nt":
        birth = st.st_ctime
    if mode == "name_or_earliest":
        from .datefmt import date_in_name
        named = date_in_name(os.path.basename(path))
        if named is not None:
            return named
        mode = "earliest"
    if mode == "mtime":
        ts = st.st_mtime
    elif mode == "earliest":
        ts = min(x for x in (birth, st.st_mtime) if x)
    else:
        ts = birth or st.st_mtime
    return datetime.fromtimestamp(ts)


def content_hash(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=12).hexdigest()


def read_text(abs_path: str) -> str:
    """Read a note without newline translation (so CRLF survives)."""
    with open(abs_path, "r", encoding="utf-8", newline="") as f:
        return f.read()


class VaultIndex:
    def __init__(self, vault: str | Path, settings: Settings):
        self.vault = str(Path(vault).resolve())
        self.settings = settings
        self.notes: dict[str, NoteInfo] = {}
        self.dirs: set[str] = set()          # real folders (relative), excluding ignored ones
        self._sigs: dict[str, tuple[int, int]] = {}

    # ---- discovery ---------------------------------------------------------------
    def abs(self, rel: str) -> str:
        return os.path.join(self.vault, *rel.split("/")) if rel else self.vault

    def is_ignored(self, rel: str) -> bool:
        if any(part.startswith(".") for part in rel.split("/")):
            return True
        return matches_any(self.settings.ignore_globs, rel)

    def _walk(self) -> tuple[dict[str, tuple[int, int]], set[str]]:
        sigs: dict[str, tuple[int, int]] = {}
        dirs: set[str] = set()
        root = self.vault
        for cur, dnames, fnames in os.walk(root):
            relcur = os.path.relpath(cur, root).replace(os.sep, "/")
            relcur = "" if relcur == "." else relcur
            dnames[:] = [d for d in dnames if not self.is_ignored(f"{relcur}/{d}" if relcur else d)]
            if relcur:
                dirs.add(relcur)
            for fn in fnames:
                if not fn.lower().endswith(".md"):
                    continue
                rel = f"{relcur}/{fn}" if relcur else fn
                if self.is_ignored(rel):
                    continue
                try:
                    st = os.stat(os.path.join(cur, fn))
                except OSError:
                    continue
                sigs[rel] = (st.st_mtime_ns, st.st_size)
        return sigs, dirs

    def scan(self) -> "ScanDiff":
        """Full (re)scan. Unchanged files (same mtime+size) are not re-read."""
        sigs, dirs = self._walk()
        old = set(self.notes)
        added = [r for r in sigs if r not in old]
        removed = [r for r in old if r not in sigs]
        changed = [r for r in sigs if r in old and self.notes[r].signature != sigs[r]]
        for r in removed:
            self.notes.pop(r, None)
        for r in added + changed:
            self._load(r)
        self.dirs = dirs
        self._sigs = sigs
        return ScanDiff(added, changed, removed)

    def diff_on_disk(self) -> "ScanDiff":
        """Cheap check: what differs between the index and the disk (no reads)."""
        sigs, _ = self._walk()
        added = [r for r in sigs if r not in self.notes]
        removed = [r for r in self.notes if r not in sigs]
        changed = [r for r in sigs if r in self.notes and self.notes[r].signature != sigs[r]]
        return ScanDiff(added, changed, removed)

    def refresh(self, rels: Iterable[str]) -> None:
        """Re-read specific notes (or drop them if they vanished)."""
        for rel in rels:
            if os.path.exists(self.abs(rel)) and not self.is_ignored(rel):
                self._load(rel)
            else:
                self.notes.pop(rel, None)
        # folders may have changed
        _, self.dirs = self._walk()

    def _load(self, rel: str) -> None:
        p = self.abs(rel)
        try:
            text = read_text(p)
            st = os.stat(p)
        except (OSError, UnicodeDecodeError):
            self.notes.pop(rel, None)
            return
        schema = self.settings.header_schema
        parsed = fm.parse(text)
        values = {e.key: e.values() for e in parsed.entries if e.key} if parsed.has_fm else {}
        links = [m.group(1).strip() for m in _WIKILINK_RE.finditer(parsed.body)]
        md = [m.group(1) for m in _MDLINK_RE.finditer(parsed.body)
              if not re.match(r"^[a-z][a-z0-9+.-]*:", m.group(1), re.I) and not m.group(1).startswith("#")]
        self.notes[rel] = NoteInfo(
            rel=rel, abs=p, size=st.st_size, mtime_ns=st.st_mtime_ns,
            created=file_created(p, self.settings.date_source), hash=content_hash(text),
            has_fm=parsed.has_fm, values=values, missing=fm.missing_keys(text, schema),
            links=links, md_links=md, word_count=len(parsed.body.split()),
        )

    def reload_schema(self) -> None:
        """Header schema changed: recompute 'missing' for every note (re-reads files)."""
        for rel in list(self.notes):
            self._load(rel)

    # ---- queries -----------------------------------------------------------------
    def text(self, rel: str) -> str:
        return read_text(self.abs(rel))

    def get(self, rel: str) -> NoteInfo | None:
        return self.notes.get(rel)

    def folders(self) -> list[str]:
        """Real folders, including parents of folders that hold notes."""
        out = set(self.dirs)
        for n in self.notes.values():
            f = n.folder
            while f:
                out.add(f)
                f = split_rel(f)[0]
        return sorted(out)

    def by_folder(self) -> dict[str, list[NoteInfo]]:
        res: dict[str, list[NoteInfo]] = {}
        for n in self.notes.values():
            res.setdefault(n.folder, []).append(n)
        for v in res.values():
            v.sort(key=lambda n: n.name.lower())
        return res

    def name_index(self) -> dict[str, list[str]]:
        idx: dict[str, list[str]] = {}
        for n in self.notes.values():
            idx.setdefault(n.title.lower(), []).append(n.rel)
        return idx

    def resolve_link(self, target: str, name_idx: dict[str, list[str]] | None = None) -> str | None:
        """Resolve a wikilink target to a note path (Obsidian: by name, or by path)."""
        name_idx = name_idx or self.name_index()
        t = target.strip()
        if t.lower().endswith(".md"):
            t = t[:-3]
        if "/" in t:
            for rel in self.notes:
                if rel[:-3].lower() == t.lower() or rel[:-3].lower().endswith("/" + t.lower()):
                    return rel
            return None
        hits = name_idx.get(t.lower())
        return hits[0] if hits else None

    def vocab(self, key: str, limit: int = 60) -> list[tuple[str, int]]:
        """Most-used values of a header field across the vault."""
        c: Counter[str] = Counter()
        canon: dict[str, str] = {}
        for n in self.notes.values():
            for v in n.values.get(key, []):
                canon.setdefault(v.lower(), v)
                c[v.lower()] += 1
        return [(canon[k], n) for k, n in c.most_common(limit)]

    def stats(self) -> dict:
        total = len(self.notes)
        no_header = sum(1 for n in self.notes.values() if not n.has_fm)
        incomplete = sum(1 for n in self.notes.values() if n.missing)
        return {"notes": total, "no_header": no_header, "incomplete": incomplete,
                "complete": total - incomplete, "folders": len(self.folders())}

    # ---- link safety -------------------------------------------------------------
    def move_warnings(self, src: str, dst: str) -> list[str]:
        """Things a move could break. Plain ``[[Name]]`` links are safe; these are not."""
        warns: list[str] = []
        stem_src = src[:-3] if src.lower().endswith(".md") else src
        stem_dst = dst[:-3] if dst.lower().endswith(".md") else dst
        name_src = split_rel(stem_src)[1].lower()
        # path-qualified wikilinks pointing at the old location
        for rel, n in self.notes.items():
            if rel == src:
                continue
            for l in n.links:
                if "/" in l:
                    lt = l[:-3] if l.lower().endswith(".md") else l
                    if stem_src.lower() == lt.lower() or stem_src.lower().endswith("/" + lt.lower()):
                        warns.append(f"{rel} links to it by path: [[{l}]]")
                        break
        me = self.notes.get(src)
        if me and me.md_links:
            warns.append(f"it contains {len(me.md_links)} relative markdown link(s)/embed(s) that may break")
        # name collisions elsewhere make plain wikilinks ambiguous
        if split_rel(stem_dst)[1].lower() == name_src:
            same = [r for r in self.notes if r != src and split_rel(r[:-3])[1].lower() == name_src]
            if same:
                warns.append(f"another note named '{split_rel(stem_dst)[1]}' exists ({same[0]}) — [[links]] become ambiguous")
        return warns


@dataclass
class ScanDiff:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.added or self.changed or self.removed)
