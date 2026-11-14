"""JSON views of vaultcore objects (the browser never sees Python objects)."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from vaultcore import frontmatter as fm
from vaultcore import jobs as J
from vaultcore.llm import clean_key, describe_key
from vaultcore.settings import LLMProfile


def iso(d) -> str | None:
    return d.isoformat(timespec="seconds") if isinstance(d, datetime) else d


def note_brief(s, n) -> dict:
    return {
        "rel": n.rel, "name": n.name, "title": n.title, "folder": n.folder,
        "eff_folder": s.effective_folder(n.rel), "size": n.size, "mtime": n.mtime_ns // 10**9,
        "created": iso(n.created), "has_fm": n.has_fm, "values": n.values,
        "eff_values": s.effective_values(n.rel), "missing": n.missing, "words": n.word_count,
        "locked": s.is_locked(n.rel), "no_ai": s.no_ai(n.rel),
        "staged": [{"id": c.id, "kind": c.kind, "status": c.status, "source": c.source, "confidence": c.confidence}
                   for c in s.stage.for_note(n.rel)],
        "triage": (s.triage.get(n.rel) or {}).get("reason"),
    }


def note_detail(s, rel: str) -> dict:
    n = s.index.get(rel)
    text = s.index.text(rel)
    p = fm.parse(text)
    out = note_brief(s, n)
    out.update({
        "text": text, "hash": n.hash, "newline": "crlf" if "\r\n" in text else "lf",
        "header": [{"key": e.key, "values": e.values(), "raw": e.first_value} for e in p.entries if e.key] if p.has_fm else [],
        "body": fm.body_of(text),
        "links": n.links, "md_links": n.md_links,
        "backlinks": backlinks(s, n),
    })
    return out


def backlinks(s, n, limit: int = 50) -> list[str]:
    out = []
    stem = n.rel[:-3].lower()
    title = n.title.lower()
    for rel, o in s.index.notes.items():
        if rel == n.rel:
            continue
        for l in o.links:
            t = l[:-3] if l.lower().endswith(".md") else l
            t = t.lower()
            if t == title or t == stem or stem.endswith("/" + t):
                out.append(rel)
                break
        if len(out) >= limit:
            break
    return sorted(out)


def change_json(s, c) -> dict:
    n = s.index.get(c.note)
    return {
        "id": c.id, "kind": c.kind, "note": c.note, "title": n.title if n else c.note.rsplit("/", 1)[-1],
        "folder_now": n.folder if n else "", "source": c.source, "status": c.status, "data": c.data,
        "confidence": c.confidence, "reason": c.reason, "warnings": list(c.warnings), "summary": c.summary(),
        "created_at": c.created_at, "locked": s.is_locked(c.note), "exists": n is not None,
    }


def job_json(j, detail: bool = False, log_from: int = 0) -> dict:
    out = {
        "id": j.id, "kind": j.kind, "title": j.title, "state": j.state, "active": j.active,
        "total": j.total, "finished": j.finished_count, "done": j.count(J.DONE), "errors": len(j.errors),
        "skipped": j.count(J.SKIPPED, J.STALE), "pending": j.count(J.QUEUED, J.RUNNING),
        "running": j.count(J.RUNNING), "workers": j.workers, "tokens": j.tokens, "rate": round(j.rate, 3),
        "created": j.created, "started": j.started, "ended": j.finished,
    }
    if detail:
        out["items"] = [{"key": i.key, "state": i.state, "message": i.message} for i in list(j.items.values())[:5000]]
        log = list(j.log)
        out["log"] = log[log_from:]
        out["log_len"] = len(log)
    return out


def rule_json(r) -> dict:
    return {"id": r.id, "text": r.text, "name": r.name, "enabled": r.enabled, "proposed": r.proposed, "error": r.error}


def instruction_json(i) -> dict:
    return {"id": i.id, "text": i.text, "scope": i.scope, "value": i.value, "task": i.task, "enabled": i.enabled,
            "scope_label": i.scope_label()}


def plan_json(plan) -> dict:
    items = []
    for it in plan.items:
        diff = fm.header_diff(it.preview_old, it.preview_new) if it.kind == "header" and it.preview_new else []
        items.append({
            "change_id": it.change_id, "kind": it.kind, "note": it.note, "dst": it.dst, "fields": it.fields,
            "overwrite": it.overwrite, "noop": it.noop, "blocked": it.blocked, "warned": it.warned,
            "issues": [{"level": i.level, "text": i.text} for i in it.issues],
            "diff": [[t, l] for t, l in diff],
        })
    return {"items": items, "mkdirs": plan.mkdirs, "counts": plan.counts()}


def result_json(res) -> dict:
    return {"batch_id": res.batch_id, "applied": len(res.applied), "failed": [[c, m] for c, m in res.failed],
            "moved": res.moved, "written": res.written, "removed_dirs": res.removed_dirs}


def undo_json(res) -> dict:
    return {"batch_id": res.batch_id, "restored": res.restored, "moved_back": res.moved_back,
            "conflicts": res.conflicts, "touched": res.touched}


def batch_json(b) -> dict:
    return asdict(b)


def folder_json(f, count: int = 0) -> dict:
    return {"path": f.path, "description": f.description, "llm": f.llm, "count": count}


def date_row_json(r) -> dict:
    return {"rel": r.rel, "key": r.key, "current": r.current, "parsed": iso(r.parsed), "file_date": iso(r.file_date),
            "status": r.status, "proposed": r.proposed, "delta_days": r.delta_days}


def profile_json(p: LLMProfile) -> dict:
    d = asdict(p)
    key = clean_key(p.resolved_key())
    d["api_key"] = ""
    d["api_key_set"] = bool(p.api_key)
    d["key_hint"] = describe_key(key) if key else "no key"
    return d
