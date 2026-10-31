"""The planned folder tree ("taxonomy") — the user's target structure.

It starts from an AI proposal / the current vault / typing, and is then edited by hand.
Once it is "almost 100% right" the user freezes it: the AI may then only *suggest* an
extra nested folder (shown in the Organizer for approval) — it never changes the tree.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path

from .pathutil import is_under, sanitize_folder, split_rel
from .settings import STATE_DIR
from .store import read_json, write_json


@dataclass
class Folder:
    path: str
    description: str = ""
    llm: bool = True            # may the router put notes here? (False = only rules/you can)


@dataclass
class Suggestion:
    path: str                   # suggested new folder
    notes: list[str] = field(default_factory=list)
    reason: str = ""


class Taxonomy:
    def __init__(self, vault: str | Path):
        self.file = Path(vault) / STATE_DIR / "taxonomy.json"
        self.folders: dict[str, Folder] = {}
        self.frozen: bool = False
        self.suggestions: dict[str, Suggestion] = {}
        self.load()

    # ---- persistence -------------------------------------------------------------
    def load(self) -> None:
        data = read_json(self.file, {}) or {}
        self.folders = {}
        for d in data.get("folders", []):
            if isinstance(d, dict) and d.get("path"):
                self.folders[d["path"]] = Folder(d["path"], d.get("description", ""), bool(d.get("llm", True)))
        self.frozen = bool(data.get("frozen", False))
        self.suggestions = {}
        for d in data.get("suggestions", []):
            if isinstance(d, dict) and d.get("path"):
                self.suggestions[d["path"]] = Suggestion(d["path"], list(d.get("notes", [])), d.get("reason", ""))

    def save(self) -> None:
        write_json(self.file, {
            "frozen": self.frozen,
            "folders": [asdict(f) for f in sorted(self.folders.values(), key=lambda f: f.path.lower())],
            "suggestions": [asdict(s) for s in self.suggestions.values()],
        })

    # ---- queries -----------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.folders)

    def __contains__(self, path: str) -> bool:
        return path in self.folders

    def paths(self) -> list[str]:
        return sorted(self.folders, key=str.lower)

    def routable(self) -> list[Folder]:
        return [f for f in sorted(self.folders.values(), key=lambda f: f.path.lower()) if f.llm]

    def children(self, path: str) -> list[str]:
        depth = path.count("/") + 1 if path else 0
        return sorted((p for p in self.folders
                       if is_under(p, path) and p != path and p.count("/") == depth), key=str.lower)

    def signature(self) -> str:
        """Fingerprint used in cache keys: changes when routing-relevant info changes."""
        return "|".join(f"{f.path}={f.description}" for f in self.routable())

    def match(self, text: str) -> str | None:
        """Map a model's folder answer to a real taxonomy path (exact → case-insens → basename → fuzzy)."""
        import difflib
        t = (text or "").strip().strip("/")
        if not t:
            return None
        if t in self.folders:
            return t
        low = {p.lower(): p for p in self.folders}
        if t.lower() in low:
            return low[t.lower()]
        tail = [p for p in self.folders if p.lower().endswith("/" + t.lower()) or split_rel(p)[1].lower() == t.lower()]
        if len(tail) == 1:
            return tail[0]
        close = difflib.get_close_matches(t.lower(), list(low), n=1, cutoff=0.82)
        return low[close[0]] if close else None

    # ---- editing -----------------------------------------------------------------
    def add(self, path: str, description: str = "", *, llm: bool = True) -> str:
        path = sanitize_folder(path)
        if not path:
            raise ValueError("folder path is empty")
        parts = path.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            self.folders.setdefault(parent, Folder(parent))
        if path in self.folders:
            f = self.folders[path]
            if description:
                f.description = description
        else:
            self.folders[path] = Folder(path, description, llm)
        self.suggestions.pop(path, None)
        return path

    def set_description(self, path: str, description: str) -> None:
        if path in self.folders:
            self.folders[path].description = description

    def set_llm(self, path: str, allowed: bool) -> None:
        if path in self.folders:
            self.folders[path].llm = allowed

    def rename(self, old: str, new: str) -> dict[str, str]:
        """Rename a folder and all its descendants. Returns {old_path: new_path} for every
        affected folder (the session uses it to re-target staged moves and rules)."""
        old = old.strip("/")
        new = sanitize_folder(new)
        if old not in self.folders:
            raise KeyError(old)
        if not new:
            raise ValueError("new name is empty")
        if new == old:
            return {}
        if is_under(new, old):
            raise ValueError("cannot move a folder into itself")
        mapping: dict[str, str] = {}
        for p in list(self.folders):
            if is_under(p, old):
                np = new + p[len(old):]
                if np in self.folders and not is_under(np, old):
                    raise ValueError(f"'{np}' already exists")
                mapping[p] = np
        folders: dict[str, Folder] = {}
        for p, f in self.folders.items():
            if p in mapping:
                f = Folder(mapping[p], f.description, f.llm)
            folders[f.path] = f
        self.folders = folders
        # make sure the new parent chain exists
        parts = new.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            self.folders.setdefault(parent, Folder(parent))
        for sp in list(self.suggestions):
            if sp in mapping:
                s = self.suggestions.pop(sp)
                s.path = mapping[sp]
                self.suggestions[s.path] = s
        return mapping

    def remove(self, path: str, *, with_children: bool = False) -> list[str]:
        """Remove a folder. Children are kept (and re-parented implicitly) unless with_children."""
        removed = []
        for p in list(self.folders):
            if p == path or (with_children and is_under(p, path)):
                del self.folders[p]
                removed.append(p)
        return removed

    def replace_all(self, folders: list[Folder]) -> None:
        self.folders = {}
        for f in folders:
            self.add(f.path, f.description, llm=f.llm)

    # ---- suggestions -------------------------------------------------------------
    def suggest(self, path: str, note: str, reason: str = "") -> None:
        try:
            path = sanitize_folder(path)
        except ValueError:
            return
        if not path or path in self.folders:
            return
        s = self.suggestions.setdefault(path, Suggestion(path))
        if note not in s.notes:
            s.notes.append(note)
        if reason and not s.reason:
            s.reason = reason

    def dismiss_suggestion(self, path: str) -> None:
        self.suggestions.pop(path, None)

    def prompt_lines(self) -> str:
        return "\n".join(f"- {f.path}" + (f": {f.description}" if f.description else "") for f in self.routable())
