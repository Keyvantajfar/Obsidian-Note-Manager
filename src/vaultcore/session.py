"""Session: the one object a UI talks to. It owns the vault state (index, stage, plan, rules,
instructions, locks), the LLM, the job runner and the apply/undo machinery.

A UI (the Textual TUI today, a GUI later) only needs to
  * call methods on `Session`,
  * subscribe with ``session.on(callback)`` to be told when something changed
    (callback(event: str, payload: dict) — events: scan, stage, taxonomy, rules, job, applied, undone, settings, watch, message).

Everything that talks to the LLM is a coroutine / background job and must be started from a
running asyncio loop; everything else is synchronous.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from . import apply as applymod
from . import frontmatter as fm
from . import planning, tagvocab
from .analyze import Analyzer, NoteAnalysis, WorkItem
from .cache import Cache
from .guidance import Instruction, Instructions, Locks
from .index import NoteInfo, VaultIndex, content_hash, read_text
from .jobs import DONE, ERROR, STALE, SKIPPED, Job, JobManager
from .llm import LLMClient
from .pathutil import is_under, matches_any, sanitize_folder, split_rel
from .rules import Rule, RuleError, RuleSet
from .settings import LLMProfile, Profiles, STATE_DIR, Settings
from .stage import Change, Stage, new_id
from .store import read_json, write_json
from .taxonomy import Folder, Taxonomy


class Session:
    def __init__(self, vault: str | Path, *, llm=None, profiles: Profiles | None = None,
                 settings: Settings | None = None):
        self.vault = str(Path(vault).resolve())
        if not os.path.isdir(self.vault):
            raise FileNotFoundError(f"vault not found: {self.vault}")
        (Path(self.vault) / STATE_DIR).mkdir(exist_ok=True)
        self.settings = settings or Settings.load(self.vault)
        self.profiles = profiles or Profiles.load()
        self.index = VaultIndex(self.vault, self.settings)
        self.stage = Stage(self.vault)
        self.taxonomy = Taxonomy(self.vault)
        self.rules = RuleSet(self.vault)
        self.instructions = Instructions(self.vault)
        self.locks = Locks(self.vault)
        self.cache = Cache(self.vault)
        self.jobs = JobManager()
        self._triage_file = Path(self.vault) / STATE_DIR / "triage.json"
        self.triage: dict[str, dict] = read_json(self._triage_file, {}) or {}
        self._skip: set[str] = set()
        self._rule_locked: set[str] = set()
        self._llm_override = llm
        self._llm: object | None = llm
        self._listeners: list[Callable[[str, dict], None]] = []
        self._bg: list[asyncio.Task] = []
        self.paused_watch = False
        self.last_message = ""
        self.analyzer = Analyzer(settings=self.settings, index=self.index, taxonomy=self.taxonomy,
                                 instructions=self.instructions, cache=self.cache,
                                 llm_getter=self.get_llm, folder_of=self.effective_folder)
        self.stage.listeners.append(lambda: self.emit("stage"))
        self.jobs.listeners.append(self._on_job_event)
        self.profiles.remember_vault(self.vault)

    # ------------------------------------------------------------------ events
    def on(self, cb: Callable[[str, dict], None]) -> None:
        self._listeners.append(cb)

    def off(self, cb: Callable[[str, dict], None]) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)

    def emit(self, event: str, **payload) -> None:
        for cb in list(self._listeners):
            try:
                cb(event, payload)
            except Exception:
                pass

    def say(self, msg: str) -> None:
        self.last_message = msg
        self.emit("message", text=msg)

    # ------------------------------------------------------------------ LLM
    def get_llm(self):
        if self._llm_override is not None:
            return self._llm_override
        if self._llm is None:
            self._llm = LLMClient(self.profiles.get_active())
        return self._llm

    def use_profile(self, profile: LLMProfile) -> None:
        """Switch to (and persist) an LLM profile."""
        self.profiles.upsert(profile)
        self.profiles.active = profile.name
        self.profiles.save()
        old = self._llm
        self._llm = None if self._llm_override is None else self._llm
        if old is not None and self._llm_override is None and hasattr(old, "aclose"):
            try:
                asyncio.get_running_loop().create_task(old.aclose())
            except RuntimeError:
                pass
        self.emit("settings")

    @property
    def model_label(self) -> str:
        p = getattr(self.get_llm(), "profile", None)
        return getattr(p, "model", "") or "(no model set)"

    # ------------------------------------------------------------------ lifecycle
    def scan(self) -> "object":
        """Full scan (blocking). UIs may instead run ``session.index.scan`` in a thread and then
        call :meth:`after_scan` on the main thread."""
        return self.after_scan(self.index.scan())

    def after_scan(self, diff) -> "object":
        self.analyzer.invalidate()
        self._prune_stage()
        if self.rules.rules:
            self.apply_rules(None)
        self.emit("scan", diff=diff)
        return diff

    def reload_schema(self) -> None:
        self.index.reload_schema()
        self.analyzer.invalidate()
        self.emit("scan", diff=None)

    def save_settings(self) -> None:
        self.settings.validate()
        self.settings.save(self.vault)
        self.emit("settings")

    def start_background(self) -> None:
        loop = asyncio.get_running_loop()
        self._bg = [loop.create_task(self._flusher()), loop.create_task(self._watcher())]

    async def _flusher(self) -> None:
        while True:
            await asyncio.sleep(1.5)
            self.flush()

    async def _watcher(self) -> None:
        while True:
            await asyncio.sleep(max(2.0, self.settings.watch_interval))
            if self.paused_watch:
                continue
            try:
                self.check_disk()
            except Exception as e:  # noqa: BLE001
                self.say(f"watcher error: {e}")

    def check_disk(self) -> "object":
        """Pick up edits made outside the program; in assist/auto, queue the AI for new/changed notes."""
        d = self.index.diff_on_disk()
        if not d:
            return d
        self.index.refresh(d.added + d.changed + d.removed)
        self.analyzer.invalidate()
        self._prune_stage()
        touched = d.added + d.changed
        if touched and self.rules.rules:
            self.apply_rules(touched)
        self.emit("scan", diff=d)
        if touched and self.settings.auto_level != "manual":
            todo = [r for r in touched if (n := self.index.get(r)) and (n.missing or r in d.added)]
            if todo:
                try:
                    self.run_ai(todo, only_unstaged=True, title=f"auto: {len(todo)} new/changed note(s)")
                except RuntimeError:
                    pass
        return d

    def flush(self) -> None:
        self.stage.flush()

    def save_all(self) -> None:
        self.stage.flush()
        self.taxonomy.save()
        self.rules.save()
        self.instructions.save()
        self.locks.save()
        write_json(self._triage_file, self.triage)

    async def aclose(self) -> None:
        for t in self._bg:
            t.cancel()
        self.jobs.cancel_all()
        await asyncio.sleep(0)
        self.save_all()
        llm = self._llm
        if llm is not None and hasattr(llm, "aclose"):
            try:
                await llm.aclose()
            except Exception:
                pass
        self.cache.close()

    # ------------------------------------------------------------------ note state
    def effective_folder(self, rel: str) -> str:
        c = self.stage.get(rel, "move")
        if c is not None and c.status != "rejected":
            return c.data.get("folder", "")
        n = self.index.get(rel)
        return n.folder if n else split_rel(rel)[0]

    def is_locked(self, rel: str) -> bool:
        return self.locks.is_locked(rel) or rel in self._rule_locked

    def no_ai(self, rel: str) -> bool:
        return rel in self._skip or matches_any(self.settings.no_ai_globs, rel)

    def note_text(self, rel: str) -> str:
        return self.index.text(rel)

    def effective_values(self, rel: str) -> dict[str, list[str]]:
        """Current header values with staged (non-rejected) header changes overlaid."""
        n = self.index.get(rel)
        vals = {k: list(v) for k, v in (n.values.items() if n else [])}
        c = self.stage.get(rel, "header")
        if c is not None and c.status != "rejected":
            over = {k.lower() for k in c.data.get("overwrite", [])}
            for k, v in c.data.get("fields", {}).items():
                have = next((x for x in vals if x.lower() == k.lower()), None)
                if have is None or k.lower() in over or not vals.get(have):
                    vals[k] = list(v) if isinstance(v, list) else [v]
        return vals

    def auto_fields(self, note: NoteInfo, keys: Iterable[str]) -> dict:
        """Deterministic header values (dates, static) — never guessed by the model."""
        out: dict = {}
        for k in keys:
            sp = self.settings.spec(k)
            if sp is None:
                continue
            if sp.source == "created":
                out[k] = note.created.strftime(sp.date_format)
            elif sp.source == "modified":
                out[k] = datetime.fromtimestamp(note.mtime_ns / 1e9).strftime(sp.date_format)
            elif sp.source == "static" and sp.default:
                out[k] = [sp.default] if sp.is_list else sp.default
        return out

    # ------------------------------------------------------------------ staging API
    def _default_status(self, source: str, confidence: float | None) -> str:
        if source == "manual":
            return "accepted"
        if source == "rule":
            return "accepted" if self.settings.rules_auto_accept else "proposed"
        if (self.settings.auto_level == "auto" and confidence is not None
                and confidence >= self.settings.auto_accept_threshold):
            return "accepted"
        return "proposed"

    def stage_header(self, rel: str, fields: dict, overwrite: Iterable[str] = (), *, source: str = "manual",
                     confidence: float | None = None, reason: str = "", status: str | None = None) -> Change | None:
        note = self.index.get(rel)
        if note is None or not fields:
            return None
        overwrite = list(overwrite)
        existing = self.stage.get(rel, "header")
        if existing is not None and source == "manual":
            merged = dict(existing.data.get("fields", {}))
            merged.update(fields)
            fields = merged
            overwrite = sorted(set(existing.data.get("overwrite", [])) | set(overwrite))
        ch = Change(new_id(), "header", rel, source, status or self._default_status(source, confidence),
                    {"fields": fields, "overwrite": overwrite}, confidence, reason, note.hash)
        return self.stage.upsert(ch)

    def stage_move(self, rel: str, folder: str, name: str | None = None, *, source: str = "manual",
                   confidence: float | None = None, reason: str = "", status: str | None = None) -> Change | None:
        note = self.index.get(rel)
        if note is None:
            return None
        folder = sanitize_folder(folder)
        ch = Change(new_id(), "move", rel, source, status or self._default_status(source, confidence),
                    {"folder": folder, "name": name or None}, confidence, reason, note.hash)
        out = self.stage.upsert(ch)
        if out is not None and source == "manual":
            self.triage.pop(rel, None)
        return out

    def reject(self, cids: Iterable[str]) -> None:
        self.stage.set_status_many(cids, "rejected")

    def accept(self, cids: Iterable[str]) -> None:
        self.stage.set_status_many(cids, "accepted")

    def accept_all(self, *, min_confidence: float | None = None, kind: str | None = None,
                   source: str | None = None) -> int:
        ids = []
        for c in self.stage.all(status="proposed", kind=kind, source=source):
            if min_confidence is None or c.confidence is None or c.confidence >= min_confidence:
                ids.append(c.id)
        return self.stage.set_status_many(ids, "accepted")

    def unstage_note(self, rel: str) -> None:
        for c in self.stage.for_note(rel):
            self.stage.remove(c.id)

    def _prune_stage(self) -> None:
        self.stage.remove_where(lambda c: self.index.get(c.note) is None)
        for rel in [r for r in self.triage if self.index.get(r) is None]:
            self.triage.pop(rel, None)
        return None

    def toggle_lock(self, rel: str) -> bool:
        locked = self.locks.toggle(rel)
        self.locks.save()
        self.emit("stage")
        return locked

    # ------------------------------------------------------------------ rules
    def apply_rules(self, rels: Iterable[str] | None = None) -> int:
        """(Re)evaluate rules for notes; replaces previously rule-sourced changes. Returns #changes staged."""
        targets = list(rels) if rels is not None else list(self.index.notes)
        tset = set(targets)
        # remember the user's rejections of rule changes so a re-run doesn't resurrect them
        rejected = {(c.note, c.kind, repr(c.data)) for c in self.stage.all(status="rejected", source="rule")}
        self.stage.remove_where(lambda c: c.source == "rule" and c.note in tset)
        if rels is None:
            self._skip.clear()
            self._rule_locked.clear()
        else:
            self._skip -= tset
            self._rule_locked -= tset
        n = 0
        for rel in targets:
            note = self.index.get(rel)
            if note is None:
                continue
            out = self.rules.evaluate(note, lambda r=rel: self.index.text(r))
            if not out:
                continue
            if out.skip:
                self._skip.add(rel)
            if out.lock:
                self._rule_locked.add(rel)
                continue
            if self.locks.is_locked(rel):
                continue
            if out.move is not None and out.move != note.folder:
                ch = self.stage_move(rel, out.move, source="rule", confidence=1.0, reason="rule")
                if ch is not None:
                    n += 1
                    if (rel, "move", repr(ch.data)) in rejected:
                        ch.status = "rejected"
            fields: dict = {}
            over: list[str] = []
            for key, (val, force) in out.sets.items():
                spec = self.settings.spec(key)
                have = next((v for k, v in note.values.items() if k.lower() == key.lower()), [])
                if force or not have:
                    fields[key] = [x.strip() for x in val.split(",")] if (spec and spec.is_list) else val
                    if force and have:
                        over.append(key)
            for key, items in out.adds.items():
                have = next((v for k, v in note.values.items() if k.lower() == key.lower()), [])
                low = {h.lower() for h in have}
                new = [i for i in items if i.lower() not in low]
                if new:
                    fields[key] = list(have) + new
                    over.append(key)
            if fields:
                ch = self.stage_header(rel, fields, over, source="rule", confidence=1.0, reason="rule")
                if ch is not None:
                    n += 1
                    if (rel, "header", repr(ch.data)) in rejected:
                        ch.status = "rejected"
        self.emit("rules")
        return n

    def rule_matches(self, rid: str) -> list[str]:
        """Which notes would this rule match right now (ignoring enabled/approval state)?"""
        r = self.rules.get(rid)
        if r is None or r.error:
            return []
        conds, _ = r.compiled()
        return [rel for rel, n in self.index.notes.items()
                if all(c.test(n, lambda rel=rel: self.index.text(rel)) for c in conds)]

    def add_rule(self, text: str, *, name: str = "", enabled: bool = True, proposed: bool = False) -> Rule:
        r = self.rules.add(text, name=name, enabled=enabled, proposed=proposed)
        self.rules.save()
        if enabled and not proposed:
            self.apply_rules(None)
        self.emit("rules")
        return r

    def update_rule(self, rid: str, text: str) -> Rule:
        r = self.rules.update(rid, text)
        self.rules.save()
        self.apply_rules(None)
        return r

    def toggle_rule(self, rid: str) -> None:
        r = self.rules.get(rid)
        if r and not r.error:
            r.enabled = not r.enabled
            if r.enabled:
                r.proposed = False
            self.rules.save()
            self.apply_rules(None)

    def remove_rule(self, rid: str) -> None:
        self.rules.remove(rid)
        self.rules.save()
        self.apply_rules(None)

    def move_rule(self, rid: str, delta: int) -> None:
        self.rules.move_up(rid, delta)
        self.rules.save()
        self.apply_rules(None)

    # ------------------------------------------------------------------ instructions
    def add_instruction(self, text: str, scope: str = "global", value="", task: str = "both",
                        rerun: bool | None = None) -> tuple[Instruction, Job | None]:
        ins = self.instructions.add(text, scope, value, task)
        self.instructions.save()
        job = None
        if rerun is None:
            rerun = self.settings.auto_level != "manual"
        if rerun:
            job = self.rerun_affected(ins)
        self.emit("rules")
        return ins, job

    def rerun_affected(self, ins: Instruction) -> Job | None:
        rels = self.instructions.affected(ins, self.index.notes.values())
        rels = [r for r in rels if not self.is_locked(r) and not self.no_ai(r)]
        if not rels:
            return None
        return self.run_ai(rels, title=f"re-run for instruction: {ins.text[:40]}")

    def toggle_instruction(self, iid: str) -> None:
        i = self.instructions.get(iid)
        if i:
            i.enabled = not i.enabled
            self.instructions.save()
            self.emit("rules")

    def remove_instruction(self, iid: str) -> None:
        self.instructions.remove(iid)
        self.instructions.save()
        self.emit("rules")

    async def translate_instruction(self, text: str) -> tuple[list[str], str]:
        """Ask the model to turn plain text into rules (returned, not yet added)."""
        return await planning.instruction_to_rules(self.settings, self.get_llm(), self.taxonomy, self.index, text)

    # ------------------------------------------------------------------ taxonomy
    def _retarget_stage(self, mapping: dict[str, str]) -> int:
        n = 0
        for c in self.stage.all(kind="move"):
            f = c.data.get("folder", "")
            for old, new in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
                if is_under(f, old):
                    c.data["folder"] = new + f[len(old):]
                    n += 1
                    break
        if n:
            self.stage._touch()
        return n

    def rename_folder(self, old: str, new: str) -> dict[str, str]:
        mapping = self.taxonomy.rename(old, new)
        if mapping:
            self._retarget_stage(mapping)
            self.rules.retarget_folders(mapping)
            self.instructions.retarget_folders(mapping)
            self.taxonomy.save()
            self.rules.save()
            self.instructions.save()
            self.apply_rules(None) if self.rules.rules else None
            self.emit("taxonomy")
        return mapping

    def add_folder(self, path: str, description: str = "", *, reroute: bool = True) -> Job | None:
        path = self.taxonomy.add(path, description)
        self.taxonomy.save()
        self.emit("taxonomy")
        if not reroute or self.settings.auto_level == "manual":
            return None
        cands = self.reroute_candidates(path)
        return self.run_ai(cands, header=False, route=True, title=f"re-route into new folder {path}") if cands else None

    def reroute_candidates(self, path: str) -> list[str]:
        """Notes that could belong in a newly added folder: those sitting in its parent folder,
        unassigned notes, and notes the AI suggested it for."""
        parent = split_rel(path)[0]
        sug = set(self.taxonomy.suggestions.get(path).notes) if path in self.taxonomy.suggestions else set()
        out = []
        for rel, n in self.index.notes.items():
            if self.is_locked(rel) or self.no_ai(rel):
                continue
            mv = self.stage.get(rel, "move")
            if mv is not None and mv.source == "manual":
                continue
            eff = self.effective_folder(rel)
            if rel in sug or rel in self.triage or eff == parent or eff not in self.taxonomy:
                out.append(rel)
        return out

    def remove_folder(self, path: str, *, with_children: bool = False) -> list[str]:
        removed = self.taxonomy.remove(path, with_children=with_children)
        gone = set(removed)
        self.stage.remove_where(lambda c: c.kind == "move" and c.source != "manual" and c.data.get("folder") in gone)
        self.taxonomy.save()
        self.emit("taxonomy")
        return removed

    def accept_suggestion(self, path: str) -> int:
        s = self.taxonomy.suggestions.get(path)
        if s is None:
            return 0
        notes = list(s.notes)
        path = self.taxonomy.add(path, s.reason or "")
        n = 0
        for rel in notes:
            if self.index.get(rel) is not None and self.stage_move(rel, path, source="ai", confidence=0.6,
                                                                    reason="suggested folder"):
                self.triage.pop(rel, None)
                n += 1
        self.taxonomy.save()
        self.emit("taxonomy")
        return n

    def dismiss_suggestion(self, path: str) -> None:
        self.taxonomy.dismiss_suggestion(path)
        self.taxonomy.save()
        self.emit("taxonomy")

    def set_frozen(self, frozen: bool) -> None:
        self.taxonomy.frozen = frozen
        self.taxonomy.save()
        self.emit("taxonomy")

    def import_current_folders(self) -> int:
        n = 0
        for f in self.index.folders():
            if f not in self.taxonomy:
                self.taxonomy.add(f)
                n += 1
        self.taxonomy.save()
        self.emit("taxonomy")
        return n

    async def propose_structure(self, preferences: str = "", feedback: str = "") -> tuple[list[Folder], str]:
        return await planning.propose_structure(self.settings, self.get_llm(), self.index, self.taxonomy,
                                                preferences, feedback)

    def adopt_structure(self, folders: list[Folder], *, replace: bool = True) -> None:
        if replace:
            old = {p: f for p, f in self.taxonomy.folders.items()}
            self.taxonomy.replace_all(folders)
            for f in folders:                       # keep hand-written descriptions the proposal left empty
                if not f.description and f.path in old:
                    self.taxonomy.set_description(f.path, old[f.path].description)
        else:
            for f in folders:
                self.taxonomy.add(f.path, f.description)
        self.taxonomy.save()
        self.emit("taxonomy")

    def plan_counts(self) -> dict[str, int]:
        """Notes per planned folder (by effective folder, i.e. including staged moves)."""
        counts: dict[str, int] = {p: 0 for p in self.taxonomy.folders}
        for rel in self.index.notes:
            f = self.effective_folder(rel)
            if f in counts:
                counts[f] += 1
        return counts

    def unassigned(self) -> list[str]:
        """Notes whose effective folder is not in the plan (only meaningful once a plan exists)."""
        if not len(self.taxonomy):
            return []
        return sorted(r for r in self.index.notes if self.effective_folder(r) not in self.taxonomy)

    def needs_attention(self) -> list[tuple[str, str]]:
        """(rel, why) — notes the AI could not place confidently, low-confidence proposals, unassigned."""
        out: dict[str, str] = {}
        for rel, info in self.triage.items():
            if self.index.get(rel):
                out[rel] = info.get("reason", "needs a folder")
        thr = self.settings.auto_accept_threshold
        for c in self.stage.all(kind="move", status="proposed"):
            if c.confidence is not None and c.confidence < thr:
                out.setdefault(c.note, f"low confidence ({c.confidence:.2f}) → {c.data.get('folder')}")
        for rel in self.unassigned():
            if self.stage.get(rel, "move") is None and not self.is_locked(rel):
                out.setdefault(rel, "not in the planned structure")
        return sorted(out.items())

    # ------------------------------------------------------------------ AI jobs
    def plan_work(self, rels: Iterable[str] | None, *, header: bool, route: bool, only_unstaged: bool) -> list[WorkItem]:
        llm_keys_all = [s.key for s in self.settings.llm_fields]
        have_route = route and len(self.taxonomy.routable()) > 0
        items = []
        for rel in (list(rels) if rels is not None else list(self.index.notes)):
            note = self.index.get(rel)
            if note is None or self.is_locked(rel) or self.no_ai(rel):
                continue
            keys: tuple[str, ...] = ()
            if header:
                keys = tuple(k for k in llm_keys_all if k in note.missing)
                if only_unstaged and self.stage.get(rel, "header") is not None:
                    keys = ()
            wr = have_route
            if wr:
                mv = self.stage.get(rel, "move")
                if mv is not None and (mv.source == "manual" or (only_unstaged and mv.status != "rejected")):
                    wr = False
            if keys or wr:
                items.append(WorkItem(rel, keys, wr))
        return items

    def fill_deterministic(self, rels: Iterable[str] | None = None) -> int:
        """Stage date/static fields that need no model (always cheap, always instant)."""
        n = 0
        det_keys = [s.key for s in self.settings.header_schema if s.source in ("created", "modified", "static")]
        for rel in (list(rels) if rels is not None else list(self.index.notes)):
            note = self.index.get(rel)
            if note is None or self.is_locked(rel):
                continue
            keys = [k for k in det_keys if k in note.missing]
            if not keys or self.stage.get(rel, "header") is not None:
                continue
            f = self.auto_fields(note, keys)
            if f and self.stage_header(rel, f, source="ai", confidence=1.0, reason="from file metadata"):
                n += 1
        return n

    def run_ai(self, rels: Iterable[str] | None = None, *, header: bool = True, route: bool = True,
               only_unstaged: bool = False, fresh: bool = False, title: str = "") -> Job | None:
        """Start a background job that proposes headers and/or folders. Returns the Job (or None if
        there is nothing to do)."""
        items = self.plan_work(rels, header=header, route=route, only_unstaged=only_unstaged)
        queued = {it.rel for it in items}
        if header:   # notes that only miss date/static fields need no model call
            self.fill_deterministic([r for r in (list(rels) if rels is not None else list(self.index.notes)) if r not in queued])
        if not items:
            self.say("nothing to do")
            return None
        groups = self.analyzer.make_groups(items, self.settings.batch_size)
        by_rel = {it.rel: it for it in items}
        units = [[it.rel for it in g] for g in groups]
        what = "tag + route" if (header and route and self.taxonomy.routable()) else ("route" if not header else "tag")
        job_holder: dict[str, Job] = {}

        async def handler(keys: list[str]) -> list[tuple[str, str, str]]:
            group = [by_rel[k] for k in keys]
            analyses = await self.analyzer.run_group(group, fresh=fresh)
            out: list[tuple[str, str, str]] = []
            for a in analyses:
                job_holder["j"].tokens += a.tokens
                if a.error:
                    out.append((a.rel, ERROR, a.error))
                    continue
                it = by_rel[a.rel]
                if self._changed_on_disk(a):
                    self.index.refresh([a.rel])
                    self.analyzer.invalidate()
                    a2 = (await self.analyzer.run_group([it], fresh=True))[0]
                    if a2.error or self._changed_on_disk(a2):
                        out.append((a.rel, STALE, "note kept changing while analysing"))
                        continue
                    a = a2
                msg = self._stage_analysis(a, it)
                out.append((a.rel, DONE, msg))
            return out

        job = self.jobs.submit(what.replace(" ", ""), title or f"{what}: {len(items)} note(s)", units, handler,
                               self.settings.workers)
        job_holder["j"] = job
        return job

    def _changed_on_disk(self, a: NoteAnalysis) -> bool:
        try:
            return content_hash(read_text(self.index.abs(a.rel))) != a.hash
        except (OSError, UnicodeDecodeError):
            return True

    def _stage_analysis(self, a: NoteAnalysis, it: WorkItem) -> str:
        note = self.index.get(a.rel)
        if note is None:
            return "gone"
        parts: list[str] = []
        # ---- header
        fields = {k: v for k, v in a.fields.items() if k in note.missing}
        det = self.auto_fields(note, [k for k in note.missing if k not in a.fields])
        fields.update({k: v for k, v in det.items() if k not in fields})
        if fields:
            conf = a.conf_fields if (a.fields and a.conf_fields is not None) else (1.0 if not a.fields else None)
            ch = self.stage_header(a.rel, fields, source="ai", confidence=conf, reason=a.reason)
            if ch is not None:
                parts.append(ch.summary()[:90])
        # ---- route
        if it.want_route:
            self.triage.pop(a.rel, None)
            if a.folder:
                if a.folder != note.folder:
                    ch = self.stage_move(a.rel, a.folder, source="ai", confidence=a.conf_folder, reason=a.reason)
                    if ch is not None:
                        parts.append(f"→ {a.folder} ({(a.conf_folder or 0):.2f})")
                else:
                    ex = self.stage.get(a.rel, "move")
                    if ex is not None and ex.source == "ai":
                        self.stage.remove(ex.id)
                    parts.append("stays")
            else:
                self.triage[a.rel] = {"reason": "no confident folder" + (f" — suggests '{a.new_folder}'" if a.new_folder else "")}
                parts.append("no folder")
            if a.new_folder:
                self.taxonomy.suggest(a.new_folder, a.rel, a.reason)
        return " · ".join(parts) or "no change"

    def _on_job_event(self, job: Job | None, event: str) -> None:
        if job is not None and event == "finished":
            self.flush()
            self.taxonomy.save()
            write_json(self._triage_file, self.triage)
            self.maybe_auto_apply()
        self.emit("job", job=job, event=event)

    # ------------------------------------------------------------------ apply / undo
    def plan_apply(self, only_ids: Iterable[str] | None = None) -> applymod.ApplyPlan:
        changes = self.stage.all(status="accepted")
        if only_ids is not None:
            ids = set(only_ids)
            changes = [c for c in changes if c.id in ids]
        return applymod.build_plan(self.index, self.settings, changes, self.is_locked)

    def apply(self, plan: applymod.ApplyPlan | None = None, label: str = "apply") -> applymod.ApplyResult:
        plan = plan or self.plan_apply()
        res = applymod.execute(self.vault, self.index, self.settings, plan, label)
        done = set(res.applied)
        self.stage.remove_where(lambda c: c.id in done)
        for src, dst in res.moved.items():
            self.stage.rename_note(src, dst)
            self.locks.rename(src, dst)
            if src in self.triage:
                self.triage[dst] = self.triage.pop(src)
        failed = dict(res.failed)
        for cid, msg in failed.items():
            c = self.stage.changes.get(cid)
            if c is not None:
                c.warnings = [msg]
        self.index.refresh(set(res.written) | set(res.moved) | set(res.moved.values()))
        self.index.dirs = self.index._walk()[1]
        self.analyzer.invalidate()
        self.locks.save()
        write_json(self._triage_file, self.triage)
        self.flush()
        self.emit("applied", result=res)
        return res

    def maybe_auto_apply(self) -> None:
        s = self.settings
        if s.auto_level != "auto" or not s.auto_apply or self.jobs.active():
            return
        n = len(self.stage.all(status="accepted"))
        if not n:
            return
        if n <= s.auto_apply_cap:
            res = self.apply(label="auto-apply")
            self.say(f"auto-applied {len(res.applied)} change(s)")
        else:
            self.emit("confirm_needed", count=n)
            self.say(f"{n} accepted changes exceed the auto-apply cap ({s.auto_apply_cap}) — review in Stage")

    def undo(self, batch_id: str | None = None) -> applymod.UndoResult:
        res = applymod.undo_batch(self.vault, batch_id)
        if res.touched:
            self.index.refresh(res.touched)
            self.index.dirs = self.index._walk()[1]
            self.analyzer.invalidate()
        self.emit("undone", result=res)
        return res

    def batches(self) -> list[applymod.BatchInfo]:
        return applymod.list_batches(self.vault)

    def save_note(self, rel: str, new_text: str) -> str:
        """Save a manually edited note (its own undoable batch)."""
        batch = applymod.save_note_text(self.vault, self.settings, rel, new_text)
        self.index.refresh([rel])
        self.analyzer.invalidate()
        self.emit("scan", diff=None)
        return batch

    # ------------------------------------------------------------------ previews
    def preview_change(self, c: Change) -> dict:
        """Everything the Stage tab shows for one change."""
        out: dict = {"change": c, "diff": [], "warnings": list(c.warnings), "lines": []}
        note = self.index.get(c.note)
        if note is None:
            out["warnings"].append("note no longer exists")
            return out
        if c.kind == "header":
            old = self.index.text(c.note)
            new = fm.apply_fields(old, self.settings.header_schema, c.data.get("fields", {}), c.data.get("overwrite", []))
            out["diff"] = fm.header_diff(old, new)
        else:
            folder = c.data.get("folder", "")
            name = c.data.get("name") or note.name
            dst = f"{folder}/{name}" if folder else name
            out["lines"] = [f"{c.note}", f"  → {dst}"]
            out["warnings"] += self.index.move_warnings(c.note, dst) if dst != c.note else []
        return out

    # ------------------------------------------------------------------ tag/value manager
    def value_usage(self, key: str) -> list[tuple[str, int]]:
        return tagvocab.usage(self.index, key)

    def value_groups(self, key: str) -> list[list[str]]:
        return tagvocab.near_duplicate_groups(self.value_usage(key))

    def rename_value(self, key: str, olds: Iterable[str], new: str | None) -> int:
        """Rename/merge `olds` into `new` (or delete them when new is None) across all notes — staged, not written."""
        olds_l = {o.lower() for o in olds}
        spec = self.settings.spec(key)
        n = 0
        for rel, note in list(self.index.notes.items()):
            if self.is_locked(rel):
                continue
            have = next((v for k, v in self.effective_values(rel).items() if k.lower() == key.lower()), [])
            if not any(v.lower() in olds_l for v in have):
                continue
            out: list[str] = []
            for v in have:
                if v.lower() in olds_l:
                    if new:
                        out.append(new)
                else:
                    out.append(v)
            seen, dedup = set(), []
            for v in out:
                if v.lower() not in seen:
                    seen.add(v.lower())
                    dedup.append(v)
            if dedup == have:
                continue
            value = dedup if (spec is None or spec.is_list) else (dedup[0] if dedup else "")
            if self.stage_header(rel, {key: value}, overwrite=[key], source="manual",
                                 reason=f"{key}: {', '.join(sorted(olds))} → {new or '(removed)'}"):
                n += 1
        return n

    # ------------------------------------------------------------------ misc
    def status(self) -> dict:
        st = self.index.stats()
        st["stage"] = self.stage.counts()
        st["jobs_active"] = len(self.jobs.active())
        st["tokens"] = self.analyzer.tokens_total
        st["cache"] = {"entries": self.cache.size(), "hits": self.cache.hits, "misses": self.cache.misses}
        return st
