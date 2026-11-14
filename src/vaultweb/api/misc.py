"""Tags vocabulary, rules + instructions, date fixer."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from vaultcore import rules as rulesmod
from vaultcore.datefmt import parse_date_loose
from vaultcore import dates as dt

from .. import ser
from ..common import body, sess, str_list
from ..state import ApiError

router = APIRouter()


# ------------------------------------------------------------------ tags
@router.get("/api/tags")
async def tags(request: Request, key: str = ""):
    s = sess(request)
    keys = [f.key for f in s.settings.header_schema]
    if not key:
        key = "tags" if "tags" in keys else (keys[0] if keys else "")
    if not key:
        return {"keys": [], "key": "", "values": [], "groups": []}
    usage = s.value_usage(key)
    return {"keys": keys, "key": key, "values": [{"value": v, "count": c} for v, c in usage],
            "groups": s.value_groups(key)}


@router.post("/api/tags/rename")
async def tags_rename(request: Request):
    """Rename / merge values (new = string) or delete them (new = null) — staged, nothing is written yet."""
    s = sess(request)
    b = await body(request)
    key, olds = str(b.get("key") or ""), str_list(b.get("olds"), "olds")
    new = b.get("new")
    if not key or not olds:
        raise ApiError(400, "key and olds are required")
    if new is not None and not str(new).strip():
        raise ApiError(400, "new value is empty (use null to delete)")
    n = s.rename_value(key, olds, str(new).strip() if new is not None else None)
    return {"staged": n}


# ------------------------------------------------------------------ rules & instructions
def rules_state(s) -> dict:
    return {
        "rules": [{**ser.rule_json(r), "target": (r.move_target() if not r.error else None)} for r in s.rules.rules],
        "instructions": [ser.instruction_json(i) for i in s.instructions.items],
        "locks": sorted(s.locks.paths),
        "syntax": rulesmod.__doc__ or "",
    }


@router.get("/api/rules")
async def rules(request: Request):
    return rules_state(sess(request))


def _rule(s, rid: str):
    r = s.rules.get(rid)
    if r is None:
        raise ApiError(404, "no such rule")
    return r


@router.post("/api/rules")
async def rule_add(request: Request):
    s = sess(request)
    b = await body(request)
    try:
        r = s.add_rule(str(b.get("text") or ""), name=str(b.get("name") or ""), enabled=bool(b.get("enabled", True)),
                       proposed=bool(b.get("proposed", False)))
    except rulesmod.RuleError as e:
        raise ApiError(400, str(e))
    return {"rule": ser.rule_json(r), "matches": len(s.rule_matches(r.id)), **rules_state(s)}


@router.put("/api/rules/{rid}")
async def rule_update(request: Request, rid: str):
    s = sess(request)
    b = await body(request)
    _rule(s, rid)
    try:
        r = s.update_rule(rid, str(b.get("text") or ""))
    except rulesmod.RuleError as e:
        raise ApiError(400, str(e))
    return {"rule": ser.rule_json(r), **rules_state(s)}


@router.post("/api/rules/{rid}/{action}")
async def rule_action(request: Request, rid: str, action: str):
    s = sess(request)
    b = await body(request)
    _rule(s, rid)
    if action == "toggle":
        s.toggle_rule(rid)
    elif action == "approve":
        r = _rule(s, rid)
        if not r.error:
            r.enabled, r.proposed = True, False
            s.rules.save()
            s.apply_rules(None)
    elif action == "delete":
        s.remove_rule(rid)
    elif action == "move":
        s.move_rule(rid, int(b.get("delta", -1)))
    else:
        raise ApiError(404, f"unknown rule action: {action}")
    return rules_state(s)


@router.get("/api/rules/{rid}/matches")
async def rule_matches(request: Request, rid: str):
    s = sess(request)
    _rule(s, rid)
    m = s.rule_matches(rid)
    return {"count": len(m), "notes": m[:200]}


@router.post("/api/rules-translate")
async def rule_translate(request: Request):
    s = sess(request)
    text = str((await body(request)).get("text") or "").strip()
    if not text:
        raise ApiError(400, "text is empty")
    if s.offline:
        raise ApiError(400, "AI is switched off in this workspace")
    try:
        rules, why = await s.translate_instruction(text)
    except Exception as e:  # noqa: BLE001
        raise ApiError(502, f"the model call failed: {e}")
    return {"rules": rules, "explanation": why}


@router.post("/api/instructions")
async def instr_add(request: Request):
    s = sess(request)
    b = await body(request)
    text = str(b.get("text") or "").strip()
    if not text:
        raise ApiError(400, "instruction is empty")
    ins, job = s.add_instruction(text, str(b.get("scope") or "global"), b.get("value", ""), str(b.get("task") or "both"),
                                 rerun=b.get("rerun"))
    return {"instruction": ser.instruction_json(ins), "job": ser.job_json(job) if job else None, **rules_state(s)}


@router.post("/api/instructions/{iid}/{action}")
async def instr_action(request: Request, iid: str, action: str):
    s = sess(request)
    ins = s.instructions.get(iid)
    if ins is None:
        raise ApiError(404, "no such instruction")
    job = None
    if action == "toggle":
        s.toggle_instruction(iid)
    elif action == "delete":
        s.remove_instruction(iid)
    elif action == "rerun":
        job = s.rerun_affected(ins)
        if job is None:
            raise ApiError(400, "nothing to re-run (locked / excluded / no notes affected)")
    else:
        raise ApiError(404, f"unknown instruction action: {action}")
    return {"job": ser.job_json(job) if job else None, **rules_state(s)}


# ------------------------------------------------------------------ dates (offline)
def _rows(s, q) -> list:
    return s.date_rows(key=q.get("key") or None, source=q.get("source") or None,
                       tolerance_days=float(q.get("tolerance") or 1.0), flag_earlier=str(q.get("flag_earlier")) in ("1", "true"))


@router.get("/api/dates")
async def dates(request: Request):
    s = sess(request)
    q = dict(request.query_params)
    try:
        rows = await asyncio.to_thread(_rows, s, q)
    except ValueError:
        raise ApiError(400, "tolerance must be a number")
    spec = dt.date_spec(s.settings, q.get("key") or None)
    return {"rows": [ser.date_row_json(r) for r in rows], "counts": dt.counts(rows),
            "spec": {"key": spec.key, "format": spec.date_format} if spec else None,
            "sources": dt.SOURCES, "source_help": dt.SOURCE_HELP,
            "source": q.get("source") or s.settings.date_source}


@router.post("/api/dates/fix")
async def dates_fix(request: Request):
    """Stage the proposed date for the given notes, or for every row matching `filter` ('problems'|'all'|status)."""
    s = sess(request)
    b = await body(request)
    q = {k: b.get(k) for k in ("key", "source", "tolerance", "flag_earlier")}
    rows = await asyncio.to_thread(_rows, s, q)
    if b.get("rels") is not None:
        want = set(str_list(b.get("rels"), "rels"))
        rows = [r for r in rows if r.rel in want]
    else:
        f = str(b.get("filter") or "problems")
        rows = [r for r in rows if r.status != "ok" and (f in ("problems", "all") or r.status == f)]
    return {"staged": s.fix_dates(rows)}


@router.post("/api/dates/set")
async def dates_set(request: Request):
    s = sess(request)
    b = await body(request)
    rel, text = str(b.get("rel") or ""), str(b.get("text") or "").strip()
    if s.index.get(rel) is None:
        raise ApiError(404, "no such note")
    spec = dt.date_spec(s.settings, b.get("key") or None)
    if spec is None:
        raise ApiError(400, "the header schema has no date field")
    parsed = parse_date_loose(text) if text else None
    if parsed is None:
        raise ApiError(400, "could not read that as a date — try 2024-09-16 or 2024-09-16T17:18")
    s.stage_header(rel, {spec.key: parsed.strftime(spec.date_format)}, overwrite=[spec.key], source="manual",
                   reason="date set by hand")
    return {"staged": 1, "value": parsed.strftime(spec.date_format)}
