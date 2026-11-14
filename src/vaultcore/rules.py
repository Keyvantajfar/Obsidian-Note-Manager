"""User rules: deterministic "criteria → action" lines you can type, applied before any LLM call.

Syntax — conditions, then ``->``, then actions (shell-style quoting, ``&`` / ``and`` optional):

    tag:ML -> move:2_Research/AI
    title~homework|assignment -> move:3_University/Homework
    path:Daily/** -> skip
    folder:Inbox status:Idea -> set:parent="Inbox MOC" tag+:Inbox
    noheader text~"#recipe" -> set!:status=Reference move:Cooking

Conditions (prefix ``!`` negates):
    path:GLOB  folder:GLOB  name:GLOB      glob on the note path / current folder / file name
    title:TEXT title~REGEX                 note title (substring / regex, case-insensitive)
    text~REGEX                             note body contains a match (slowest, evaluated last)
    tag:NAME   tag~REGEX                   header ``tags`` contains / matches
    <key>:VALUE  <key>~REGEX               any other header key equals VALUE / matches
    noheader   hasheader   missing:KEY     header state
Actions:
    move:FOLDER          route the note to FOLDER
    set:KEY=VALUE        fill KEY if empty        set!:KEY=VALUE   overwrite KEY
    add:KEY=a,b          add values to a list     tag+:a,b         shorthand for add:tags=a,b
    skip                 the AI never proposes for this note
    lock                 nothing automated touches this note
"""
from __future__ import annotations

import re
import shlex
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

from .index import NoteInfo
from .pathutil import matches_glob, sanitize_folder
from .settings import STATE_DIR
from .store import read_json, write_json


class RuleError(ValueError):
    pass


@dataclass
class Condition:
    kind: str                 # path folder name title title_re text_re tag tag_re header header_re noheader hasheader missing
    arg: str = ""
    key: str = ""
    negate: bool = False
    rx: re.Pattern | None = None

    def needs_text(self) -> bool:
        return self.kind == "text_re"

    def test(self, note: NoteInfo, text: Callable[[], str]) -> bool:
        r = self._test(note, text)
        return (not r) if self.negate else r

    def _test(self, note: NoteInfo, text: Callable[[], str]) -> bool:
        k = self.kind
        if k == "path":
            return matches_glob(self.arg, note.rel)
        if k == "folder":
            return matches_glob(self.arg, note.folder) if note.folder else (self.arg in ("", "/"))
        if k == "name":
            return matches_glob(self.arg, note.name)
        if k == "title":
            return self.arg.lower() in note.title.lower()
        if k == "title_re":
            return bool(self.rx.search(note.title))
        if k == "text_re":
            return bool(self.rx.search(text()))
        if k == "noheader":
            return not note.has_fm
        if k == "hasheader":
            return note.has_fm
        if k == "missing":
            return self.arg.lower() in [m.lower() for m in note.missing]
        if k in ("tag", "tag_re", "header", "header_re"):
            key = "tags" if k.startswith("tag") else self.key
            vals = _values_ci(note, key)
            if k in ("tag", "header"):
                return self.arg.lower() in [v.lower() for v in vals]
            return any(self.rx.search(v) for v in vals)
        return False


def _values_ci(note: NoteInfo, key: str) -> list[str]:
    for k, v in note.values.items():
        if k.lower() == key.lower():
            return v
    return []


@dataclass
class Action:
    kind: str                 # move set set! add skip lock
    key: str = ""
    value: str = ""

    def describe(self) -> str:
        if self.kind == "move":
            return f"move → {self.value}"
        if self.kind in ("set", "set!", "add"):
            return f"{self.kind} {self.key}={self.value}"
        return self.kind


_COND_SPLIT = re.compile(r"[:~=]")


def parse_rule(text: str) -> tuple[list[Condition], list[Action]]:
    try:
        toks = shlex.split(text, posix=True)
    except ValueError as e:
        raise RuleError(f"bad quoting: {e}")
    if "->" not in toks:
        raise RuleError("a rule needs '->' between conditions and actions")
    i = toks.index("->")
    left = [t for t in toks[:i] if t.lower() not in ("&", "and", "&&")]
    right = toks[i + 1:]
    if not left:
        raise RuleError("no conditions before '->'")
    if not right:
        raise RuleError("no actions after '->'")
    conds = [_parse_cond(t) for t in left]
    acts = [_parse_action(t) for t in right]
    conds.sort(key=lambda c: c.needs_text())     # cheap conditions first
    return conds, acts


def _parse_cond(tok: str) -> Condition:
    neg = tok.startswith("!")
    t = tok[1:] if neg else tok
    tl = t.lower()
    if tl in ("noheader", "hasheader"):
        return Condition(tl, negate=neg)
    m = _COND_SPLIT.search(t)
    if not m:
        raise RuleError(f"can't read condition {tok!r} (expected name:value or name~regex)")
    name, op, arg = t[:m.start()].strip(), t[m.start()], t[m.end():]
    nl = name.lower()
    if not arg:
        raise RuleError(f"condition {tok!r} has no value")
    if op == "=":
        op = ":"
    if nl in ("path", "folder", "name"):
        if op != ":":
            raise RuleError(f"{nl} takes a glob: {nl}:GLOB")
        return Condition(nl, arg, negate=neg)
    if nl == "missing":
        return Condition("missing", arg, negate=neg)
    if op == "~":
        try:
            rx = re.compile(arg, re.IGNORECASE | re.MULTILINE)
        except re.error as e:
            raise RuleError(f"bad regex in {tok!r}: {e}")
        if nl == "title":
            return Condition("title_re", arg, negate=neg, rx=rx)
        if nl == "text":
            return Condition("text_re", arg, negate=neg, rx=rx)
        if nl == "tag":
            return Condition("tag_re", arg, key="tags", negate=neg, rx=rx)
        return Condition("header_re", arg, key=name, negate=neg, rx=rx)
    if nl == "title":
        return Condition("title", arg, negate=neg)
    if nl == "text":
        return Condition("text_re", re.escape(arg), negate=neg, rx=re.compile(re.escape(arg), re.IGNORECASE))
    if nl == "tag":
        return Condition("tag", arg, key="tags", negate=neg)
    return Condition("header", arg, key=name, negate=neg)


def _parse_action(tok: str) -> Action:
    tl = tok.lower()
    if tl in ("skip", "lock"):
        return Action(tl)
    if ":" not in tok:
        raise RuleError(f"can't read action {tok!r}")
    name, _, arg = tok.partition(":")
    nl = name.lower()
    if nl == "move":
        try:
            return Action("move", value=sanitize_folder(arg))
        except ValueError as e:
            raise RuleError(f"bad folder in {tok!r}: {e}")
    if nl in ("set", "set!", "add"):
        if "=" not in arg:
            raise RuleError(f"{nl} needs KEY=VALUE, got {tok!r}")
        key, _, val = arg.partition("=")
        if not key.strip():
            raise RuleError(f"{nl} needs a key in {tok!r}")
        return Action(nl, key.strip(), val.strip())
    if nl == "tag+":
        return Action("add", "tags", arg.strip())
    raise RuleError(f"unknown action {name!r}")


@dataclass
class Rule:
    id: str
    text: str
    name: str = ""
    enabled: bool = True
    proposed: bool = False        # produced from a free-text instruction; waiting for approval
    error: str = ""
    _parsed: tuple | None = field(default=None, repr=False, compare=False)

    def compiled(self) -> tuple[list[Condition], list[Action]]:
        if self._parsed is None:
            self._parsed = parse_rule(self.text)
        return self._parsed

    def move_target(self) -> str | None:
        for a in self.compiled()[1]:
            if a.kind == "move":
                return a.value
        return None


@dataclass
class Outcome:
    move: str | None = None
    sets: dict[str, tuple[str, bool]] = field(default_factory=dict)    # key -> (value, overwrite)
    adds: dict[str, list[str]] = field(default_factory=dict)
    skip: bool = False
    lock: bool = False
    matched: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.matched)


class RuleSet:
    def __init__(self, vault: str | Path):
        self.file = Path(vault) / STATE_DIR / "rules.json"
        self.rules: list[Rule] = []
        self.load()

    def load(self) -> None:
        self.rules = []
        for d in (read_json(self.file, []) or []):
            if isinstance(d, dict) and d.get("text"):
                r = Rule(d.get("id") or uuid.uuid4().hex[:8], d["text"], d.get("name", ""),
                         bool(d.get("enabled", True)), bool(d.get("proposed", False)))
                try:
                    r.compiled()
                except RuleError as e:
                    r.error = str(e)
                    r.enabled = False
                self.rules.append(r)

    def save(self) -> None:
        write_json(self.file, [{"id": r.id, "text": r.text, "name": r.name, "enabled": r.enabled,
                                "proposed": r.proposed} for r in self.rules])

    def add(self, text: str, *, name: str = "", enabled: bool = True, proposed: bool = False) -> Rule:
        parse_rule(text)                      # raises RuleError on bad input
        r = Rule(uuid.uuid4().hex[:8], text.strip(), name, enabled, proposed)
        self.rules.append(r)
        return r

    def update(self, rid: str, text: str) -> Rule:
        parse_rule(text)
        r = self.get(rid)
        if r is None:
            raise KeyError(rid)
        r.text, r._parsed, r.error = text.strip(), None, ""
        return r

    def get(self, rid: str) -> Rule | None:
        return next((r for r in self.rules if r.id == rid), None)

    def remove(self, rid: str) -> None:
        self.rules = [r for r in self.rules if r.id != rid]

    def move_up(self, rid: str, delta: int = -1) -> None:
        for i, r in enumerate(self.rules):
            if r.id == rid:
                j = max(0, min(len(self.rules) - 1, i + delta))
                self.rules.insert(j, self.rules.pop(i))
                return

    def retarget_folders(self, mapping: dict[str, str]) -> int:
        """A taxonomy folder was renamed: rewrite ``move:`` targets in rule text."""
        n = 0
        for r in self.rules:
            conds, acts = r.compiled() if not r.error else ([], [])
            for a in acts:
                if a.kind == "move" and a.value in mapping:
                    old, new = a.value, mapping[a.value]
                    r.text = re.sub(r"move:(['\"]?)" + re.escape(old) + r"\1(?=\s|$)", lambda m: f"move:{m.group(1)}{new}{m.group(1)}", r.text)
                    r._parsed = None
                    n += 1
        return n

    def evaluate(self, note: NoteInfo, text: Callable[[], str]) -> Outcome:
        out = Outcome()
        cache: list[str] = []

        def lazy() -> str:
            if not cache:
                cache.append(text())
            return cache[0]

        for r in self.rules:
            if not r.enabled or r.proposed or r.error:
                continue
            conds, acts = r.compiled()
            if all(c.test(note, lazy) for c in conds):
                out.matched.append(r.id)
                for a in acts:
                    if a.kind == "move" and out.move is None:
                        out.move = a.value
                    elif a.kind in ("set", "set!"):
                        out.sets.setdefault(a.key, (a.value, a.kind == "set!"))
                    elif a.kind == "add":
                        lst = out.adds.setdefault(a.key, [])
                        lst.extend(v.strip() for v in a.value.split(",") if v.strip())
                    elif a.kind == "skip":
                        out.skip = True
                    elif a.kind == "lock":
                        out.lock = True
        return out
