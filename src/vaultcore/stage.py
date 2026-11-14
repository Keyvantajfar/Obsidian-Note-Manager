"""The staging area: every proposed change (AI, rule or manual) lives here until applied.

Nothing touches the vault until the user applies the accepted changes. The stage is
persisted, so closing the program loses nothing.

Priority when two sources propose for the same (note, kind): manual > rule > ai.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

from .settings import STATE_DIR
from .store import read_json, write_json

PRIORITY = {"manual": 3, "rule": 2, "ai": 1}
STATUSES = ("proposed", "accepted", "rejected")
KINDS = ("header", "move")


@dataclass
class Change:
    id: str
    kind: str                       # header | move
    note: str                       # vault-relative path when proposed
    source: str                     # ai | rule | manual
    status: str = "proposed"        # proposed | accepted | rejected
    data: dict = field(default_factory=dict)
    # header: {"fields": {key: value}, "overwrite": [keys]}
    # move:   {"folder": "A/B", "name": "New name.md" | None}
    confidence: float | None = None
    reason: str = ""
    base_hash: str = ""
    created_at: float = field(default_factory=time.time)
    warnings: list[str] = field(default_factory=list)

    @property
    def priority(self) -> int:
        return PRIORITY.get(self.source, 0)

    def summary(self) -> str:
        if self.kind == "move":
            name = self.data.get("name")
            return f"→ {self.data.get('folder') or '(root)'}/" + (f"  as {name}" if name else "")
        fields = self.data.get("fields", {})
        parts = []
        for k, v in fields.items():
            parts.append(f"{k}={', '.join(v) if isinstance(v, list) else v}")
        return "; ".join(parts)


def new_id() -> str:
    return uuid.uuid4().hex[:10]


class Stage:
    def __init__(self, vault: str | Path):
        self.path = Path(vault) / STATE_DIR / "stage.json"
        self.changes: dict[str, Change] = {}
        self._by_key: dict[tuple[str, str], str] = {}
        self._dirty = False
        self.listeners: list[Callable[[], None]] = []
        self.load()

    # ---- persistence -------------------------------------------------------------
    def load(self) -> None:
        data = read_json(self.path, {}) or {}
        self.changes.clear()
        self._by_key.clear()
        for d in data.get("changes", []):
            try:
                c = Change(**{k: v for k, v in d.items() if k in Change.__dataclass_fields__})
            except TypeError:
                continue
            self.changes[c.id] = c
            self._by_key[(c.note, c.kind)] = c.id

    def flush(self) -> None:
        if not self._dirty:
            return
        write_json(self.path, {"changes": [asdict(c) for c in self.changes.values()]})
        self._dirty = False

    def _touch(self) -> None:
        self._dirty = True
        for cb in list(self.listeners):
            try:
                cb()
            except Exception:
                pass

    # ---- queries -----------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.changes)

    def get(self, note: str, kind: str) -> Change | None:
        cid = self._by_key.get((note, kind))
        return self.changes.get(cid) if cid else None

    def for_note(self, note: str) -> list[Change]:
        return [c for c in (self.get(note, k) for k in KINDS) if c]

    def all(self, *, kind: str | None = None, status: str | None = None, source: str | None = None) -> list[Change]:
        res = [c for c in self.changes.values()
               if (kind is None or c.kind == kind) and (status is None or c.status == status)
               and (source is None or c.source == source)]
        res.sort(key=lambda c: (c.kind, c.note.lower()))
        return res

    def counts(self) -> dict[str, int]:
        out = {"proposed": 0, "accepted": 0, "rejected": 0}
        for c in self.changes.values():
            out[c.status] = out.get(c.status, 0) + 1
        return out

    # ---- mutation ----------------------------------------------------------------
    def upsert(self, change: Change, *, force: bool = False) -> Change | None:
        """Insert/replace the change for (note, kind). A lower-priority source never
        replaces a higher-priority one unless `force`. Returns the stored change or None."""
        key = (change.note, change.kind)
        existing = self.get(*key)
        if existing is not None:
            if existing.priority > change.priority and not force:
                return None
            change.id = existing.id
        self.changes[change.id] = change
        self._by_key[key] = change.id
        self._touch()
        return change

    def set_status(self, cid: str, status: str) -> None:
        c = self.changes.get(cid)
        if c and status in STATUSES and c.status != status:
            c.status = status
            self._touch()

    def set_status_many(self, cids, status: str) -> int:
        n = 0
        for cid in cids:
            c = self.changes.get(cid)
            if c and c.status != status:
                c.status = status
                n += 1
        if n:
            self._touch()
        return n

    def remove(self, cid: str) -> None:
        c = self.changes.pop(cid, None)
        if c:
            self._by_key.pop((c.note, c.kind), None)
            self._touch()

    def remove_where(self, pred: Callable[[Change], bool]) -> int:
        ids = [c.id for c in self.changes.values() if pred(c)]
        for i in ids:
            c = self.changes.pop(i)
            self._by_key.pop((c.note, c.kind), None)
        if ids:
            self._touch()
        return len(ids)

    def clear(self) -> None:
        self.changes.clear()
        self._by_key.clear()
        self._touch()

    def rename_note(self, old: str, new: str) -> None:
        """A note was moved/renamed on disk: re-key any remaining changes for it."""
        for kind in KINDS:
            cid = self._by_key.pop((old, kind), None)
            if cid and cid in self.changes:
                self.changes[cid].note = new
                self._by_key[(new, kind)] = cid
        self._touch()

    def edit_data(self, cid: str, **data) -> None:
        c = self.changes.get(cid)
        if c:
            c.data.update(data)
            self._touch()
