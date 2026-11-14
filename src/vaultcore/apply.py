"""Applying staged changes safely: dry-run plan → journaled execution → undo.

Safety rules:
  * never overwrite an existing note (collisions are renamed ``Name (1).md`` or skipped)
  * header writes are atomic (temp file + replace), byte-exact, CRLF/BOM-safe, body untouched
  * every operation is journaled (``.vaultmgr/journal/<batch>.jsonl``) so a batch can be undone
  * originals are copied to ``.vaultmgr/backups/<batch>/`` before a write (setting)
  * a note edited on disk since it was proposed is re-merged against its *current* content
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import frontmatter as fm
from .index import VaultIndex, content_hash, read_text
from .pathutil import join_rel, sanitize_folder, sanitize_note_name, split_rel, unique_dest
from .settings import Settings, STATE_DIR
from .stage import Change


@dataclass
class Issue:
    level: str          # info | warn | block
    text: str


@dataclass
class PlanItem:
    change_id: str
    kind: str
    note: str
    dst: str | None = None                  # move target (relative path)
    fields: dict = field(default_factory=dict)
    overwrite: list[str] = field(default_factory=list)
    preview_old: str = ""
    preview_new: str = ""
    noop: bool = False
    issues: list[Issue] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(i.level == "block" for i in self.issues)

    @property
    def warned(self) -> bool:
        return any(i.level == "warn" for i in self.issues)


@dataclass
class ApplyPlan:
    items: list[PlanItem] = field(default_factory=list)
    mkdirs: list[str] = field(default_factory=list)

    def runnable(self) -> list[PlanItem]:
        return [i for i in self.items if not i.blocked and not i.noop]

    def counts(self) -> dict[str, int]:
        return {
            "headers": sum(1 for i in self.runnable() if i.kind == "header"),
            "moves": sum(1 for i in self.runnable() if i.kind == "move"),
            "new_folders": len(self.mkdirs),
            "blocked": sum(1 for i in self.items if i.blocked),
            "warnings": sum(1 for i in self.items if i.warned and not i.blocked),
            "noop": sum(1 for i in self.items if i.noop),
        }


@dataclass
class ApplyResult:
    batch_id: str = ""
    applied: list[str] = field(default_factory=list)          # change ids done (incl. no-ops)
    failed: list[tuple[str, str]] = field(default_factory=list)
    moved: dict[str, str] = field(default_factory=dict)       # old rel -> new rel
    written: list[str] = field(default_factory=list)          # rel paths whose header changed
    removed_dirs: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- planning

def build_plan(index: VaultIndex, settings: Settings, changes: list[Change],
               is_locked: Callable[[str], bool] = lambda rel: False) -> ApplyPlan:
    plan = ApplyPlan()
    schema = settings.header_schema
    headers = [c for c in changes if c.kind == "header"]
    moves = [c for c in changes if c.kind == "move"]

    for c in headers:
        item = PlanItem(c.id, "header", c.note, fields=dict(c.data.get("fields", {})),
                        overwrite=list(c.data.get("overwrite", [])))
        plan.items.append(item)
        ni = index.get(c.note)
        if ni is None or not os.path.exists(index.abs(c.note)):
            item.issues.append(Issue("block", "note no longer exists"))
            continue
        if is_locked(c.note):
            item.issues.append(Issue("block", "note is locked"))
            continue
        try:
            old = read_text(index.abs(c.note))
        except (OSError, UnicodeDecodeError) as e:
            item.issues.append(Issue("block", f"cannot read note: {e}"))
            continue
        new = fm.apply_fields(old, schema, item.fields, item.overwrite)
        if fm.body_of(new) != fm.body_of(old):
            item.issues.append(Issue("block", "internal safety check failed: body would change"))
            continue
        item.preview_old, item.preview_new = old, new
        if new == old:
            item.noop = True
            item.issues.append(Issue("info", "header already has these values"))
        elif c.base_hash and content_hash(old) != c.base_hash:
            item.issues.append(Issue("info", "note changed since it was proposed — merged against current content"))

    # ---- moves
    vacated = {c.note.lower() for c in moves}
    taken: set[str] = set()
    made: set[str] = set()

    def exists(rel: str) -> bool:
        return os.path.exists(index.abs(rel)) and rel.lower() not in vacated

    for c in sorted(moves, key=lambda c: c.note.lower()):
        item = PlanItem(c.id, "move", c.note)
        plan.items.append(item)
        if index.get(c.note) is None or not os.path.exists(index.abs(c.note)):
            item.issues.append(Issue("block", "note no longer exists"))
            continue
        if is_locked(c.note):
            item.issues.append(Issue("block", "note is locked"))
            continue
        try:
            folder = sanitize_folder(c.data.get("folder", ""))
            name = sanitize_note_name(c.data["name"]) if c.data.get("name") else split_rel(c.note)[1]
        except ValueError as e:
            item.issues.append(Issue("block", str(e)))
            continue
        dst = join_rel(folder, name)
        if dst == c.note:
            item.noop = True
            item.dst = dst
            item.issues.append(Issue("info", "already there"))
            continue
        if dst.lower() != c.note.lower():
            if exists(dst) or dst.lower() in taken:
                if settings.collision_policy == "skip":
                    item.issues.append(Issue("block", f"'{dst}' already exists"))
                    continue
                new_dst = unique_dest(dst, taken, exists)
                item.issues.append(Issue("warn", f"'{dst}' exists — will be saved as '{split_rel(new_dst)[1]}'"))
                dst = new_dst
        taken.add(dst.lower())
        item.dst = dst
        for w in index.move_warnings(c.note, dst):
            item.issues.append(Issue("warn", w))
        parts = split_rel(dst)[0].split("/") if split_rel(dst)[0] else []
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            if not os.path.isdir(index.abs(d)) and d not in made:
                made.add(d)
                plan.mkdirs.append(d)
    return plan


# --------------------------------------------------------------------------- journal

def _journal_dir(vault: str) -> Path:
    return Path(vault) / STATE_DIR / "journal"


def _backup_dir(vault: str, batch: str) -> Path:
    return Path(vault) / STATE_DIR / "backups" / batch


class Journal:
    def __init__(self, vault: str, label: str = "apply"):
        self.vault = vault
        self.batch = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        d = _journal_dir(vault)
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{self.batch}.jsonl"
        self._write({"op": "meta", "label": label, "time": time.time()})

    def _write(self, entry: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()

    def log(self, **entry) -> None:
        self._write(entry)

    def is_empty(self) -> bool:
        return sum(1 for _ in open(self.path, "r", encoding="utf-8")) <= 1


def _write_text_atomic(path: str, text: str, mtime_ns: int | None) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".vm-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        try:
            shutil.copymode(path, tmp)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if mtime_ns is not None:
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, mtime_ns))


def write_note(vault: str, settings: Settings, journal: Journal, rel: str, new_text: str) -> None:
    """Journaled, backed-up, atomic write of a whole note (used for header writes and body saves)."""
    abs_path = os.path.join(vault, *rel.split("/"))
    old_text = read_text(abs_path)
    st = os.stat(abs_path)
    backed = False
    if settings.backup:
        dst = _backup_dir(vault, journal.batch) / Path(*rel.split("/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(abs_path, dst)
        backed = True
    _write_text_atomic(abs_path, new_text, st.st_mtime_ns if settings.preserve_mtime else None)
    st2 = os.stat(abs_path)
    journal.log(op="write", path=rel, backup=backed, before_hash=content_hash(old_text),
                after_hash=content_hash(new_text), mtime_ns=st.st_mtime_ns, after_mtime_ns=st2.st_mtime_ns)


# --------------------------------------------------------------------------- execution

def execute(vault: str, index: VaultIndex, settings: Settings, plan: ApplyPlan,
            label: str = "apply") -> ApplyResult:
    res = ApplyResult()
    items = plan.runnable()
    noops = [i for i in plan.items if i.noop]
    res.applied.extend(i.change_id for i in noops)
    if not items:
        return res
    journal = Journal(vault, label)
    res.batch_id = journal.batch
    schema = settings.header_schema

    # 1) header writes, at the notes' current paths
    for it in items:
        if it.kind != "header":
            continue
        try:
            abs_path = index.abs(it.note)
            old = read_text(abs_path)
            new = fm.apply_fields(old, schema, it.fields, it.overwrite)     # re-merge against *current* text
            if fm.body_of(new) != fm.body_of(old):
                raise RuntimeError("body would change")
            if new != old:
                write_note(vault, settings, journal, it.note, new)
                res.written.append(it.note)
            res.applied.append(it.change_id)
        except Exception as e:  # noqa: BLE001 — report per item, keep going
            res.failed.append((it.change_id, f"{type(e).__name__}: {e}"))

    # 2) folders
    created = set()
    for d in plan.mkdirs:
        p = os.path.join(vault, *d.split("/"))
        if not os.path.isdir(p):
            try:
                os.makedirs(p, exist_ok=True)
                journal.log(op="mkdir", path=d)
                created.add(d)
            except OSError as e:
                res.failed.append((f"mkdir:{d}", str(e)))

    # 3) moves
    src_dirs: set[str] = set()
    for it in items:
        if it.kind != "move":
            continue
        src, dst = it.note, it.dst
        try:
            sp, dp = index.abs(src), index.abs(dst)
            if not os.path.exists(sp):
                raise FileNotFoundError("source vanished")
            if os.path.exists(dp) and dst.lower() != src.lower():
                raise FileExistsError(f"'{dst}' appeared — not overwriting")
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            try:
                os.rename(sp, dp)
            except OSError:
                shutil.move(sp, dp)
            journal.log(op="move", src=src, dst=dst)
            res.moved[src] = dst
            res.applied.append(it.change_id)
            src_dirs.add(split_rel(src)[0])
        except Exception as e:  # noqa: BLE001
            res.failed.append((it.change_id, f"{type(e).__name__}: {e}"))

    # 4) prune folders the moves emptied
    if settings.prune_empty_dirs:
        for d in sorted(src_dirs, key=lambda x: -x.count("/")):
            cur = d
            while cur:
                p = os.path.join(vault, *cur.split("/"))
                try:
                    if os.path.isdir(p) and not os.listdir(p):
                        os.rmdir(p)
                        journal.log(op="rmdir", path=cur)
                        res.removed_dirs.append(cur)
                    else:
                        break
                except OSError:
                    break
                cur = split_rel(cur)[0]
    if journal.is_empty():
        try:
            journal.path.unlink()
        except OSError:
            pass
        res.batch_id = ""
    return res


def save_note_text(vault: str, settings: Settings, rel: str, new_text: str, label: str = "edit note") -> str:
    """Save an edited note as its own undoable batch. Returns the batch id."""
    journal = Journal(vault, label)
    write_note(vault, settings, journal, rel, new_text)
    return journal.batch


# --------------------------------------------------------------------------- undo

@dataclass
class BatchInfo:
    id: str
    label: str
    time: float
    ops: int
    undone: bool


def list_batches(vault: str) -> list[BatchInfo]:
    out = []
    d = _journal_dir(vault)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.jsonl")):
        undone = p.name.endswith(".undone.jsonl")
        bid = p.name.replace(".undone.jsonl", "").replace(".jsonl", "")
        label, t, n = "", 0.0, 0
        try:
            with open(p, "r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    e = json.loads(line)
                    if i == 0:
                        label, t = e.get("label", ""), e.get("time", 0.0)
                    else:
                        n += 1
        except (OSError, ValueError):
            continue
        out.append(BatchInfo(bid, label, t, n, undone))
    return out


@dataclass
class UndoResult:
    batch_id: str = ""
    restored: int = 0
    moved_back: int = 0
    conflicts: list[str] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)       # rel paths whose state changed


def undo_batch(vault: str, batch_id: str | None = None) -> UndoResult:
    batches = [b for b in list_batches(vault) if not b.undone]
    res = UndoResult()
    if not batches:
        return res
    target = next((b for b in batches if b.id == batch_id), None) if batch_id else batches[-1]
    if target is None:
        return res
    res.batch_id = target.id
    path = _journal_dir(vault) / f"{target.id}.jsonl"
    ops = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                ops.append(json.loads(line))
            except ValueError:
                continue
    for e in reversed(ops):
        op = e.get("op")
        try:
            if op == "write":
                ap = os.path.join(vault, *e["path"].split("/"))
                bk = _backup_dir(vault, target.id) / Path(*e["path"].split("/"))
                if not os.path.exists(ap):
                    res.conflicts.append(f"{e['path']}: missing, cannot restore header")
                elif content_hash(read_text(ap)) != e["after_hash"]:
                    res.conflicts.append(f"{e['path']}: edited since the batch — left as is")
                elif not (e.get("backup") and bk.exists()):
                    res.conflicts.append(f"{e['path']}: no backup stored, cannot restore")
                else:
                    shutil.copy2(bk, ap)
                    if e.get("mtime_ns"):
                        st = os.stat(ap)
                        os.utime(ap, ns=(st.st_atime_ns, e["mtime_ns"]))
                    res.restored += 1
                    res.touched.append(e["path"])
            elif op == "move":
                src, dst = os.path.join(vault, *e["src"].split("/")), os.path.join(vault, *e["dst"].split("/"))
                if not os.path.exists(dst):
                    res.conflicts.append(f"{e['dst']}: missing, cannot move back")
                elif os.path.exists(src) and e["src"].lower() != e["dst"].lower():
                    res.conflicts.append(f"{e['src']}: occupied, '{e['dst']}' left in place")
                else:
                    os.makedirs(os.path.dirname(src), exist_ok=True)
                    os.rename(dst, src)
                    res.moved_back += 1
                    res.touched += [e["src"], e["dst"]]
            elif op == "mkdir":
                p = os.path.join(vault, *e["path"].split("/"))
                if os.path.isdir(p) and not os.listdir(p):
                    os.rmdir(p)
            elif op == "rmdir":
                os.makedirs(os.path.join(vault, *e["path"].split("/")), exist_ok=True)
        except OSError as ex:
            res.conflicts.append(f"{e.get('path') or e.get('src')}: {ex}")
    path.rename(path.with_name(f"{target.id}.undone.jsonl"))
    return res
