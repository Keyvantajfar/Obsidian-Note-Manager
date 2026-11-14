"""Notes, folders, manual header/move/rename/lock staging, note body saving."""
from __future__ import annotations

from fastapi import APIRouter, Request

from vaultcore.pathutil import split_rel

from .. import ser
from ..common import body, need_note, sess, str_list
from ..state import ApiError

router = APIRouter()


@router.get("/api/notes")
async def notes(request: Request):
    s = sess(request)
    return {"notes": [ser.note_brief(s, n) for n in sorted(s.index.notes.values(), key=lambda n: n.rel.lower())]}


@router.get("/api/note")
async def note(request: Request, path: str):
    s = sess(request)
    need_note(s, path)
    try:
        return ser.note_detail(s, path)
    except (OSError, UnicodeDecodeError) as e:
        raise ApiError(500, f"cannot read note: {e}")


@router.put("/api/note")
async def save_note(request: Request):
    """Save the whole note text (an undoable batch). Refuses if the file changed since it was loaded."""
    s = sess(request)
    b = await body(request)
    rel, text = str(b.get("path") or ""), b.get("text")
    n = need_note(s, rel)
    if not isinstance(text, str):
        raise ApiError(400, "'text' must be a string")
    base = b.get("base_hash")
    if base and base != n.hash:
        raise ApiError(409, "the note changed on disk since you opened it — reload it first")
    batch = s.save_note(rel, text)
    return {"batch_id": batch, "note": ser.note_detail(s, rel)}


@router.get("/api/folders")
async def folders(request: Request):
    s = sess(request)
    counts = s.plan_counts()
    real_counts: dict[str, int] = {}
    for n in s.index.notes.values():
        real_counts[n.folder] = real_counts.get(n.folder, 0) + 1
    return {
        "real": [{"path": p, "count": real_counts.get(p, 0)} for p in s.index.folders()],
        "root_count": real_counts.get("", 0),
        "planned": [ser.folder_json(f, counts.get(f.path, 0)) for f in sorted(s.taxonomy.folders.values(), key=lambda f: f.path.lower())],
        "frozen": s.taxonomy.frozen,
    }


def _targets(s, b: dict) -> list[str]:
    rels = [r for r in str_list(b.get("paths")) if s.index.get(r)]
    if not rels:
        raise ApiError(400, "no (existing) notes selected")
    return rels


@router.post("/api/note/header")
async def stage_header(request: Request):
    """Stage a manual header edit on one or many notes (only the given fields are touched)."""
    s = sess(request)
    b = await body(request)
    rels = _targets(s, b)
    fields = b.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ApiError(400, "'fields' must be a non-empty object")
    explicit = b.get("overwrite")
    n = 0
    for rel in rels:
        have = s.effective_values(rel)
        over = list(explicit) if isinstance(explicit, list) else [
            k for k in fields if any(h.lower() == k.lower() and have[h] for h in have)]
        if s.stage_header(rel, fields, overwrite=over, source="manual"):
            n += 1
    return {"staged": n}


@router.post("/api/note/move")
async def stage_move(request: Request):
    s = sess(request)
    b = await body(request)
    rels = _targets(s, b)
    folder = str(b.get("folder") or "").strip().strip("/")
    if folder and len(s.taxonomy) and folder not in s.taxonomy and b.get("add_to_plan", True):
        try:
            s.add_folder(folder, reroute=False)
        except ValueError as e:
            raise ApiError(400, str(e))
    n = 0
    for rel in rels:
        try:
            if s.stage_move(rel, folder, source="manual"):
                n += 1
        except ValueError as e:
            raise ApiError(400, str(e))
    return {"staged": n, "folder": folder}


@router.post("/api/note/rename")
async def stage_rename(request: Request):
    s = sess(request)
    b = await body(request)
    rel = str(b.get("path") or "")
    n = need_note(s, rel)
    new = str(b.get("name") or "").strip()
    if new.lower().endswith(".md"):
        new = new[:-3]
    if not new:
        raise ApiError(400, "name is empty")
    if new == n.title:
        return {"staged": 0}
    try:
        s.stage_move(rel, s.effective_folder(rel), name=new, source="manual")
    except ValueError as e:
        raise ApiError(400, str(e))
    return {"staged": 1}


@router.post("/api/note/lock")
async def lock(request: Request):
    s = sess(request)
    b = await body(request)
    rels = _targets(s, b)
    if "locked" in b:
        for rel in rels:
            s.locks.set(rel, bool(b["locked"]))
        s.locks.save()
        s.emit("stage")
    else:
        for rel in rels:
            s.toggle_lock(rel)
    return {"locked": {r: s.is_locked(r) for r in rels}}


@router.post("/api/note/unstage")
async def unstage(request: Request):
    s = sess(request)
    b = await body(request)
    for rel in _targets(s, b):
        s.unstage_note(rel)
    return {"ok": True}


@router.get("/api/note/folder-of")
async def folder_of(request: Request, path: str):
    s = sess(request)
    need_note(s, path)
    return {"folder": s.effective_folder(path), "name": split_rel(path)[1]}
