"""The Stage: every pending change, dry-run, apply, undo."""
from __future__ import annotations

from fastapi import APIRouter, Request

from vaultcore.pathutil import sanitize_folder
from vaultcore.stage import STATUSES

from .. import ser
from ..common import body, sess, str_list
from ..state import ApiError

router = APIRouter()


@router.get("/api/stage")
async def stage_list(request: Request, status: str | None = None, kind: str | None = None, source: str | None = None):
    s = sess(request)
    ch = s.stage.all(status=status or None, kind=kind or None, source=source or None)
    return {"changes": [ser.change_json(s, c) for c in ch], "counts": s.stage.counts(),
            "threshold": s.settings.auto_accept_threshold}


@router.get("/api/stage/preview")
async def stage_preview(request: Request, id: str):
    s = sess(request)
    c = s.stage.changes.get(id)
    if c is None:
        raise ApiError(404, "no such change")
    p = s.preview_change(c)
    return {"diff": [[t, l] for t, l in p["diff"]], "warnings": p["warnings"], "lines": p["lines"],
            "change": ser.change_json(s, c)}


@router.post("/api/stage/status")
async def stage_status(request: Request):
    s = sess(request)
    b = await body(request)
    status, ids = b.get("status"), str_list(b.get("ids"), "ids")
    if status not in STATUSES:
        raise ApiError(400, f"status must be one of {', '.join(STATUSES)}")
    n = s.stage.set_status_many(ids, status)
    return {"changed": n}


@router.post("/api/stage/accept-all")
async def accept_all(request: Request):
    s = sess(request)
    b = await body(request)
    mc = b.get("min_confidence")
    if mc == "threshold":
        mc = s.settings.auto_accept_threshold
    n = s.accept_all(min_confidence=float(mc) if mc is not None else None, kind=b.get("kind"), source=b.get("source"))
    return {"accepted": n, "min_confidence": mc}


@router.post("/api/stage/remove")
async def stage_remove(request: Request):
    s = sess(request)
    ids = str_list((await body(request)).get("ids"), "ids")
    for i in ids:
        s.stage.remove(i)
    return {"removed": len(ids)}


@router.post("/api/stage/clear-rejected")
async def clear_rejected(request: Request):
    s = sess(request)
    n = s.stage.remove_where(lambda c: c.status == "rejected")
    return {"removed": n}


@router.post("/api/stage/edit")
async def stage_edit(request: Request):
    """Edit a staged change the way the TUI does: re-stage it as a manual change (manual wins over AI/rule)."""
    s = sess(request)
    b = await body(request)
    c = s.stage.changes.get(str(b.get("id") or ""))
    if c is None:
        raise ApiError(404, "no such change")
    if s.index.get(c.note) is None:
        raise ApiError(400, "the note no longer exists")
    if c.kind == "header":
        fields = b.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise ApiError(400, "'fields' must be a non-empty object")
        vals = s.effective_values(c.note)
        over = [k for k in fields if any(h.lower() == k.lower() and vals[h] for h in vals)]
        new = s.stage_header(c.note, fields, overwrite=over, source="manual")
    else:
        folder = str(b.get("folder") if b.get("folder") is not None else c.data.get("folder", "")).strip().strip("/")
        try:
            sanitize_folder(folder)
        except ValueError as e:
            raise ApiError(400, str(e))
        if folder and len(s.taxonomy) and folder not in s.taxonomy:
            s.add_folder(folder, reroute=False)
        name = b.get("name")
        name = None if not name or not str(name).strip() else str(name).strip()
        note = s.index.get(c.note)
        if name and (name == note.title or name == note.name):
            name = None
        new = s.stage_move(c.note, folder, name=name, source="manual")
    if new is None:
        raise ApiError(400, "could not stage that change")
    return {"change": ser.change_json(s, new)}


@router.post("/api/apply/plan")
async def apply_plan(request: Request):
    s = sess(request)
    b = await body(request)
    ids = b.get("ids")
    plan = s.plan_apply(str_list(ids, "ids") if ids is not None else None)
    return ser.plan_json(plan)


@router.post("/api/apply")
async def apply(request: Request):
    s = sess(request)
    b = await body(request)
    ids = b.get("ids")
    plan = s.plan_apply(str_list(ids, "ids") if ids is not None else None)
    if not plan.runnable() and not any(i.noop for i in plan.items):
        raise ApiError(400, "nothing to apply — accept some changes first")
    res = s.apply(plan, label=str(b.get("label") or "apply"))
    return {"result": ser.result_json(res), "counts": plan.counts()}


@router.post("/api/undo")
async def undo(request: Request):
    s = sess(request)
    b = await body(request)
    res = s.undo(b.get("batch_id") or None)
    if not res.batch_id:
        raise ApiError(400, "nothing to undo")
    return ser.undo_json(res)


@router.get("/api/batches")
async def batches(request: Request):
    s = sess(request)
    return {"batches": [ser.batch_json(b) for b in reversed(s.batches())]}
