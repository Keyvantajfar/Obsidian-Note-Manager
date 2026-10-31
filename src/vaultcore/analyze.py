"""Per-note AI analysis: fills missing header fields and picks a destination folder, in one
LLM call per note (or per micro-batch), with a content-addressed cache.

Cache keys are split so that changing one thing only re-queries what it affects:
  * header part  — note content + the requested fields' definitions + header instructions
  * route part   — note content + folder plan (paths+descriptions) + route instructions
Adding one folder therefore re-routes notes but never re-tags them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from . import frontmatter as fm
from .cache import Cache, make_key
from .guidance import Instructions
from .index import NoteInfo, VaultIndex, content_hash
from .llm import LLMError, parse_json_loose
from .prompts import PROMPT_VERSION, analyze_system, analyze_user, prompt_text
from .settings import FieldSpec, Settings
from .taxonomy import Taxonomy


@dataclass
class WorkItem:
    rel: str
    want_fields: tuple[str, ...] = ()       # LLM-filled header keys still needed
    want_route: bool = False


@dataclass
class NoteAnalysis:
    rel: str
    hash: str = ""
    fields: dict = field(default_factory=dict)
    conf_fields: float | None = None
    folder: str | None = None               # validated taxonomy path
    conf_folder: float | None = None
    new_folder: str = ""
    reason: str = ""
    error: str | None = None
    cached_fields: bool = False
    cached_route: bool = False
    tokens: int = 0


def s_floor(llm) -> int:
    return int(getattr(getattr(llm, 'profile', None), 'max_tokens', 0) or 1024)


def _conf(v, default: float = 0.6) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, f))


def _schema_sig(specs: list[FieldSpec]) -> str:
    return "|".join(f"{s.key}:{s.kind}:{s.instruction}:{s.min_items}:{s.max_items}:{','.join(s.allowed)}" for s in specs)


class Analyzer:
    def __init__(self, *, settings: Settings, index: VaultIndex, taxonomy: Taxonomy, instructions: Instructions,
                 cache: Cache, llm_getter: Callable[[], object], folder_of: Callable[[str], str]):
        self.settings = settings
        self.index = index
        self.taxonomy = taxonomy
        self.instructions = instructions
        self.cache = cache
        self.llm_getter = llm_getter
        self.folder_of = folder_of
        self.tokens_total = 0
        self.calls = 0
        self._vocab: dict[str, list[str]] | None = None
        self._names: dict[str, list[str]] | None = None

    def invalidate(self) -> None:
        self._vocab = None
        self._names = None

    # ---- helpers -----------------------------------------------------------------
    def _model(self) -> str:
        return getattr(getattr(self.llm_getter(), "profile", None), "model", "")

    def vocab(self, specs: list[FieldSpec]) -> dict[str, list[str]]:
        if self._vocab is None:
            self._vocab = {}
        out = {}
        for s in specs:
            if s.key not in self._vocab:
                self._vocab[s.key] = [v for v, _ in self.index.vocab(s.key, self.settings.vocab_size)]
            out[s.key] = self._vocab[s.key]
        return out

    def _context(self, note: NoteInfo) -> dict:
        names = self._names if self._names is not None else self.index.name_index()
        self._names = names
        sibs = [n.title for n in self.index.notes.values() if n.folder == note.folder and n.rel != note.rel]
        sibs = sorted(sibs)[: self.settings.context_siblings]
        links = []
        for target in note.links:
            rel = self.index.resolve_link(target, names)
            if rel and rel != note.rel:
                f = self.folder_of(rel)
                links.append(f"{self.index.notes[rel].title} → {f or '(root)'}")
            if len(links) >= self.settings.context_links:
                break
        return {"folder": note.folder, "siblings": sibs, "links": links}

    def group_key(self, it: WorkItem) -> tuple:
        note = self.index.get(it.rel)
        hi = ri = ""
        if note is not None:
            hi = Instructions.signature(self.instructions.for_note(note, "header")) if it.want_fields else ""
            ri = Instructions.signature(self.instructions.for_note(note, "route")) if it.want_route else ""
        return (it.want_fields, it.want_route, hi, ri)

    def make_groups(self, items: list[WorkItem], batch_size: int) -> list[list[WorkItem]]:
        buckets: dict[tuple, list[WorkItem]] = {}
        for it in items:
            buckets.setdefault(self.group_key(it), []).append(it)
        groups: list[list[WorkItem]] = []
        n = max(1, batch_size)
        for lst in buckets.values():
            for i in range(0, len(lst), n):
                groups.append(lst[i:i + n])
        return groups

    # ---- main entry ----------------------------------------------------------------
    async def run_group(self, group: list[WorkItem], fresh: bool = False) -> list[NoteAnalysis]:
        if not group:
            return []
        s = self.settings
        spec_keys = list(group[0].want_fields)
        specs = [sp for sp in (s.spec(k) for k in spec_keys) if sp is not None]
        want_route = group[0].want_route and len(self.taxonomy.routable()) > 0
        model = self._model()
        tsig, frozen = self.taxonomy.signature(), self.taxonomy.frozen
        ssig = _schema_sig(specs)
        psig = make_key(prompt_text(s, "analyze_base"))[:12]            # editing the prompt invalidates cached answers

        results: dict[str, NoteAnalysis] = {}
        pending: list[dict] = []
        for it in group:
            a = NoteAnalysis(it.rel)
            results[it.rel] = a
            note = self.index.get(it.rel)
            if note is None:
                a.error = "note no longer exists"
                continue
            try:
                text = self.index.text(it.rel)
            except (OSError, UnicodeDecodeError) as e:
                a.error = f"cannot read: {e}"
                continue
            a.hash = content_hash(text)
            h_ins = self.instructions.for_note(note, "header") if specs else []
            r_ins = self.instructions.for_note(note, "route") if want_route else []
            hkey = make_key("h", PROMPT_VERSION, psig, model, a.hash, ssig, Instructions.signature(h_ins)) if specs else None
            rkey = make_key("r", PROMPT_VERSION, psig, model, a.hash, tsig, str(frozen), Instructions.signature(r_ins)) if want_route else None
            need_f, need_r = bool(specs), want_route
            if hkey and not fresh and (c := self.cache.get(hkey)) is not None:
                a.fields, a.conf_fields, a.cached_fields = c["fields"], c.get("conf"), True
                a.reason = c.get("reason", "")
                need_f = False
            if rkey and not fresh and (c := self.cache.get(rkey)) is not None:
                a.folder, a.conf_folder, a.new_folder = c.get("folder"), c.get("conf"), c.get("new_folder", "")
                a.reason = a.reason or c.get("reason", "")
                a.cached_route = True
                need_r = False
            if need_f or need_r:
                trimmed = text if len(text) <= s.max_note_chars else text[:s.max_note_chars] + "\n...[truncated]..."
                pending.append({"a": a, "note": note, "text": trimmed, "need_f": need_f, "need_r": need_r,
                                "hkey": hkey, "rkey": rkey,
                                "ins": sorted({i.text for i in h_ins + r_ins})})
        if pending:
            await self._query(pending, specs, want_route)
        return [results[it.rel] for it in group]

    # ---- LLM call ------------------------------------------------------------------
    async def _query(self, pending: list[dict], specs: list[FieldSpec], want_route: bool) -> None:
        # a pending item may need only fields or only route; ask for the union required by the group
        need_f = any(p["need_f"] for p in pending)
        need_r = any(p["need_r"] for p in pending)
        qspecs = specs if need_f else []
        route = want_route and need_r
        raw = await self._call(pending, qspecs, route)
        missing = [p for p in pending if p["a"].rel not in raw]
        if missing and len(pending) > 1:
            for p in missing:                                    # retry the dropped ones individually
                raw.update(await self._call([p], qspecs, route))
        for p in pending:
            a: NoteAnalysis = p["a"]
            r = raw.get(a.rel)
            if r is None:
                a.error = a.error or "the model did not return a usable answer"
                continue
            if isinstance(r, str):
                a.error = r
                continue
            conf = r.get("confidence")
            cf = cr = None
            if isinstance(conf, dict):
                cf, cr = conf.get("fields"), conf.get("folder")
            elif conf is not None:
                cf = cr = conf
            a.reason = a.reason or str(r.get("reason") or "")[:300]
            a.tokens += r.get("_tokens", 0)
            if p["need_f"] and qspecs:
                fields, penal = self._norm_fields(r.get("fields") or {}, qspecs)
                a.fields = fields
                a.conf_fields = _conf(cf) * penal
                if p["hkey"]:
                    self.cache.put(p["hkey"], {"fields": fields, "conf": a.conf_fields, "reason": a.reason})
            if p["need_r"] and route:
                folder, new_folder, penal = self._norm_folder(r.get("folder"), r.get("new_folder"))
                a.folder, a.new_folder = folder, new_folder
                a.conf_folder = _conf(cr) * penal if folder else (_conf(cr) if cr is not None else 0.0)
                if p["rkey"]:
                    self.cache.put(p["rkey"], {"folder": folder, "conf": a.conf_folder,
                                               "new_folder": new_folder, "reason": a.reason})

    async def _call(self, pending: list[dict], specs: list[FieldSpec], route: bool) -> dict:
        """Returns {rel: result dict | error string}."""
        s = self.settings
        llm = self.llm_getter()
        ids = {f"n{i + 1}": p for i, p in enumerate(pending)}
        items = [{"id": nid, "title": p["note"].title, "text": p["text"],
                  "context": self._context(p["note"]) if route else {}} for nid, p in ids.items()]
        instr = sorted({t for p in pending for t in p["ins"]})
        system = analyze_system(s, specs, route, self.taxonomy.frozen)
        user = analyze_user(s, items, specs, route, self.taxonomy.prompt_lines(), self.vocab(specs), instr)
        out: dict[str, object] = {}
        err = "no answer"
        budget = max(s_floor(llm), 400 * len(pending))
        user_now = user
        for attempt in range(3):                                  # retries that change something: bigger budget, firmer nudge
            try:
                resp = await llm.chat(system, user_now, json_mode=True, max_tokens=budget)
            except LLMError as e:
                err = str(e)
                if not e.retryable:
                    break
                continue
            self.calls += 1
            self.tokens_total += resp.tokens
            data = parse_json_loose(resp.text)
            results = None
            if isinstance(data, dict):
                results = data.get("results")
                if results is None and ("fields" in data or "folder" in data):
                    results = [dict(data, id="n1")]
            elif isinstance(data, list):
                results = data
            if not isinstance(results, list):
                if not resp.text.strip():
                    llm.note_empty_answer()                       # reasoning model out of tokens: stop it thinking, give it room
                    budget = min(budget * 2, 32000)
                    err = (f"model returned an empty answer (finish_reason={resp.finish_reason or '?'}"
                           + (", it spent its tokens on hidden reasoning" if resp.reasoning or resp.finish_reason == "length" else "") + ")")
                else:
                    if resp.finish_reason == "length":
                        budget = min(budget * 2, 32000)
                    snippet = " ".join(resp.text.split())[:90]
                    err = f"model did not return valid JSON (finish_reason={resp.finish_reason or '?'}): {snippet!r}"
                    user_now = user + "\n\nIMPORTANT: your previous reply was not valid JSON. Reply with ONE JSON object only — no prose, no markdown."
                continue
            per_note_tokens = resp.tokens // max(1, len(pending))
            for r in results:
                if isinstance(r, dict):
                    nid = str(r.get("id", ""))
                    if nid not in ids and len(results) == len(pending) == 1:
                        nid = "n1"
                    if nid in ids:
                        r["_tokens"] = per_note_tokens
                        out[ids[nid]["a"].rel] = r
            if out:
                return out
            err = "model returned JSON but with no result for the note(s) (wrong ids?)"
        for p in pending:
            out.setdefault(p["a"].rel, err)
        return out

    # ---- normalisation -------------------------------------------------------------
    def _norm_fields(self, raw: dict, specs: list[FieldSpec]) -> tuple[dict, float]:
        out: dict = {}
        penalty = 1.0
        rl = {str(k).lower(): v for k, v in raw.items()} if isinstance(raw, dict) else {}
        for sp in specs:
            val = fm.normalize_value(sp, rl.get(sp.key.lower()))
            if sp.allowed:
                allowed = {a.lower(): a for a in sp.allowed}
                if isinstance(val, list):
                    val = [allowed[v.lower()] for v in val if v.lower() in allowed]
                else:
                    val = allowed.get(val.lower(), "")
            empty = (not val) if not isinstance(val, list) else (len(val) == 0)
            if empty:
                if sp.default:
                    val = [sp.default] if sp.is_list else sp.default
                    penalty = min(penalty, 0.35)
                else:
                    penalty = min(penalty, 0.2)
                    continue
            elif isinstance(val, list) and len(val) < sp.min_items:
                penalty = min(penalty, 0.8)
            out[sp.key] = val
        return out, penalty

    def _norm_folder(self, folder, new_folder) -> tuple[str | None, str, float]:
        claimed = str(folder or "").strip().strip("/")
        new_folder = str(new_folder or "").strip().strip("/")
        if not claimed:
            return None, new_folder, 1.0
        matched = self.taxonomy.match(claimed)
        if matched is None:
            return None, new_folder or claimed, 1.0          # unknown folder → treat as a suggestion
        return matched, new_folder, (1.0 if matched == claimed else 0.85)
