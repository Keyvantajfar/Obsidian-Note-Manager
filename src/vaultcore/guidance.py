"""Free-text instructions you give the AI ("keep calculus under 3_University/Math"),
and per-note locks ("the AI/automation never touches this note")."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .index import NoteInfo
from .pathutil import is_under, matches_glob
from .settings import STATE_DIR
from .store import read_json, write_json

SCOPES = ("global", "folder", "tag", "notes")
TASKS = ("both", "header", "route")


@dataclass
class Instruction:
    id: str
    text: str
    scope: str = "global"         # global | folder | tag | notes
    value: str | list[str] = ""   # folder path / tag / list of note paths
    task: str = "both"            # both | header | route
    enabled: bool = True

    def applies_to(self, note: NoteInfo, task: str) -> bool:
        if not self.enabled or self.task not in ("both", task):
            return False
        if self.scope == "global":
            return True
        if self.scope == "folder":
            return is_under(note.folder, str(self.value).strip("/"))
        if self.scope == "tag":
            tags = [t.lower() for t in note.values.get("tags", [])]
            return str(self.value).lower() in tags
        if self.scope == "notes":
            return note.rel in (self.value if isinstance(self.value, list) else [self.value])
        return False

    def scope_label(self) -> str:
        if self.scope == "global":
            return "everything"
        if self.scope == "notes":
            n = len(self.value) if isinstance(self.value, list) else 1
            return f"{n} note(s)"
        return f"{self.scope}: {self.value}"


class Instructions:
    def __init__(self, vault: str | Path):
        self.file = Path(vault) / STATE_DIR / "instructions.json"
        self.items: list[Instruction] = []
        self.load()

    def load(self) -> None:
        self.items = []
        for d in (read_json(self.file, []) or []):
            if isinstance(d, dict) and d.get("text"):
                self.items.append(Instruction(
                    d.get("id") or uuid.uuid4().hex[:8], d["text"], d.get("scope", "global"),
                    d.get("value", ""), d.get("task", "both"), bool(d.get("enabled", True))))

    def save(self) -> None:
        write_json(self.file, [asdict(i) for i in self.items])

    def add(self, text: str, scope: str = "global", value="", task: str = "both") -> Instruction:
        ins = Instruction(uuid.uuid4().hex[:8], text.strip(), scope, value, task)
        self.items.append(ins)
        return ins

    def get(self, iid: str) -> Instruction | None:
        return next((i for i in self.items if i.id == iid), None)

    def remove(self, iid: str) -> None:
        self.items = [i for i in self.items if i.id != iid]

    def for_note(self, note: NoteInfo, task: str) -> list[Instruction]:
        return [i for i in self.items if i.applies_to(note, task)]

    def affected(self, ins: Instruction, notes) -> list[str]:
        return [n.rel for n in notes if ins.applies_to(n, ins.task if ins.task != "both" else "header")
                or ins.applies_to(n, "route")]

    def retarget_folders(self, mapping: dict[str, str]) -> None:
        for i in self.items:
            if i.scope == "folder" and i.value in mapping:
                i.value = mapping[i.value]

    @staticmethod
    def signature(items: list[Instruction]) -> str:
        if not items:
            return ""
        return hashlib.sha1("\n".join(sorted(i.text for i in items)).encode()).hexdigest()[:12]


class Locks:
    """Notes (or globs) automation must leave alone."""

    def __init__(self, vault: str | Path):
        self.file = Path(vault) / STATE_DIR / "locks.json"
        self.paths: set[str] = set()
        self.load()

    def load(self) -> None:
        data = read_json(self.file, []) or []
        self.paths = {str(p) for p in data}

    def save(self) -> None:
        write_json(self.file, sorted(self.paths))

    def is_locked(self, rel: str) -> bool:
        return rel in self.paths or any(("*" in p or "?" in p) and matches_glob(p, rel) for p in self.paths)

    def toggle(self, rel: str) -> bool:
        if rel in self.paths:
            self.paths.discard(rel)
            return False
        self.paths.add(rel)
        return True

    def set(self, rel: str, locked: bool) -> None:
        (self.paths.add if locked else self.paths.discard)(rel)

    def rename(self, old: str, new: str) -> None:
        if old in self.paths:
            self.paths.discard(old)
            self.paths.add(new)
