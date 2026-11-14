"""Organizer: the planned folder tree, suggestions, structure proposal, routing."""
from __future__ import annotations

from fastapi import APIRouter, Request

from vaultcore.taxonomy import Folder

from .. import ser
from ..common import body, sess, str_list
from ..state import ApiError

router = APIRouter()


def plan_state(s) -> dict:
    counts = s.plan_counts()
    real = s.index.folders()
    return {
        "folders": [ser.folder_json(f, counts.get(f.path, 0)) for f in sorted(s.taxonomy.folders.values(), key=lambda f: f.path.lower())],
        "frozen": s.taxonomy.frozen,
        "suggestions": [{"path": x.path, "notes": x.notes, "reason": x.reason} for x in s.taxonomy.suggestions.values()],
        "unassigned": s.unassigned(),
        "attention": [{"rel": r, "why": w} for r, w in s.needs_attention()],
        "real": [{"path": p, "in_plan": p in s.taxonomy} for p in real],
    }


@router.get("/api/plan")
async def plan(request: Request):
    return plan_state(sess(request))


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except (ValueError, KeyError) as e:
        raise ApiError(400, str(e).strip("'\""))


@router.post("/api/plan/folder")
async def add_folder(request: Request):
    s = sess(request)
    b = await body(request)
    path = str(b.get("path") or "").strip().strip("/")
    if not path:
        raise ApiError(400, "folder path is empty")
    job = _wrap(s.add_folder, path, str(b.get("description") or ""), reroute=bool(b.get("reroute", False)))
    return {"job": ser.job_json(job) if job else None, **plan_state(s)}


@router.post("/api/plan/rename")
async def rename(request: Request):
    s = sess(request)
    b = await body(request)
    mapping = _wrap(s.rename_folder, str(b.get("old") or "").strip("/"), str(b.get("new") or ""))
    return {"mapping": mapping, **plan_state(s)}


@router.post("/api/plan/remove")
async def remove(request: Request):
    s = sess(request)
    b = await body(request)
    removed = s.remove_folder(str(b.get("path") or "").strip("/"), with_children=bool(b.get("with_children", False)))
    return {"removed": removed, **plan_state(s)}


@router.post("/api/plan/describe")
async def describe(request: Request):
    s = sess(request)
    b = await body(request)
    path = str(b.get("path") or "").strip("/")
    if path not in s.taxonomy:
        raise ApiError(404, "not a planned folder")
    if "description" in b:
        s.taxonomy.set_description(path, str(b["description"] or ""))
    if "llm" in b:
        s.taxonomy.set_llm(path, bool(b["llm"]))
    s.taxonomy.save()
    s.emit("taxonomy")
    return plan_state(s)


@router.post("/api/plan/freeze")
async def freeze(request: Request):
    s = sess(request)
    b = await body(request)
    s.set_frozen(bool(b["frozen"]) if "frozen" in b else not s.taxonomy.frozen)
    return plan_state(s)


@router.post("/api/plan/import-current")
async def import_current(request: Request):
    s = sess(request)
    b = await body(request)
    paths = b.get("paths")
    if paths is None:
        n = s.import_current_folders()
    else:
        n = 0
        for p in str_list(paths, "paths"):
            if p not in s.taxonomy:
                s.add_folder(p, reroute=False)
                n += 1
    return {"added": n, **plan_state(s)}


@router.post("/api/plan/suggestion")
async def suggestion(request: Request):
    s = sess(request)
    b = await body(request)
    path, act = str(b.get("path") or ""), b.get("action")
    if path not in s.taxonomy.suggestions:
        raise ApiError(404, "no such suggestion")
    if act == "accept":
        n = s.accept_suggestion(path)
        return {"staged": n, **plan_state(s)}
    if act == "dismiss":
        s.dismiss_suggestion(path)
        return plan_state(s)
    raise ApiError(400, "action must be 'accept' or 'dismiss'")


@router.post("/api/plan/propose")
async def propose(request: Request):
    s = sess(request)
    b = await body(request)
    if s.taxonomy.frozen:
        raise ApiError(400, "the plan is frozen — unfreeze it before asking for a new structure")
    if s.offline:
        raise ApiError(400, "AI is switched off in this workspace")
    try:
        folders, note = await s.propose_structure(str(b.get("preferences") or ""), str(b.get("feedback") or ""))
    except Exception as e:  # noqa: BLE001
        raise ApiError(502, f"the model call failed: {e}")
    return {"folders": [ser.folder_json(f) for f in folders], "note": note}


@router.post("/api/plan/adopt")
async def adopt(request: Request):
    s = sess(request)
    b = await body(request)
    raw = b.get("folders")
    if not isinstance(raw, list) or not raw:
        raise ApiError(400, "'folders' must be a non-empty list")
    folders = []
    for f in raw:
        if not isinstance(f, dict) or not str(f.get("path") or "").strip():
            raise ApiError(400, "every folder needs a path")
        folders.append(Folder(str(f["path"]).strip().strip("/"), str(f.get("description") or ""), bool(f.get("llm", True))))
    _wrap(s.adopt_structure, folders, replace=bool(b.get("replace", True)))
    return plan_state(s)


@router.post("/api/plan/route")
async def route(request: Request):
    """Route notes into the plan with the AI (all unrouted, or the given notes)."""
    s = sess(request)
    b = await body(request)
    if not len(s.taxonomy):
        raise ApiError(400, "no planned folders yet — propose or add some first")
    rels = b.get("rels")
    if rels is None:
        job = s.run_ai(None, header=False, route=True, only_unstaged=True, title="route notes into the plan")
    else:
        rels = str_list(rels, "rels")
        job = s.run_ai(rels, header=False, route=True, fresh=True, title=f"re-route {len(rels)} note(s)")
    return {"job": ser.job_json(job) if job else None, "message": s.last_message}
