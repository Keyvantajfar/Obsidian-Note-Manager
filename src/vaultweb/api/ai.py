"""AI runs and the job queue."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import ser
from ..common import body, sess, str_list
from ..state import ApiError

router = APIRouter()


def _job(s, jid: int):
    j = s.jobs.get(jid)
    if j is None:
        raise ApiError(404, f"no such job: #{jid}")
    return j


@router.post("/api/ai/run")
async def ai_run(request: Request):
    """Start a tag/route job. `rels` omitted = every note (use only_unstaged for 'run on all missing')."""
    s = sess(request)
    b = await body(request)
    rels = b.get("rels")
    job = s.run_ai(str_list(rels, "rels") if rels is not None else None,
                   header=bool(b.get("header", True)), route=bool(b.get("route", True)),
                   only_unstaged=bool(b.get("only_unstaged", False)), fresh=bool(b.get("fresh", False)),
                   title=str(b.get("title") or ""))
    return {"job": ser.job_json(job) if job else None, "message": s.last_message}


@router.get("/api/jobs")
async def jobs(request: Request):
    s = sess(request)
    return {"jobs": [ser.job_json(j) for j in reversed(s.jobs.jobs)],
            "paused": bool(s.jobs.active()) and all(j.state == "paused" for j in s.jobs.active())}


@router.get("/api/jobs/{jid}")
async def job_detail(request: Request, jid: int, log_from: int = 0):
    return ser.job_json(_job(sess(request), jid), detail=True, log_from=log_from)


@router.post("/api/jobs/{jid}/{action}")
async def job_action(request: Request, jid: int, action: str):
    s = sess(request)
    j = _job(s, jid)
    b = await body(request)
    if action == "pause":
        j.pause()
    elif action == "resume":
        j.resume()
    elif action == "cancel":
        j.cancel()
    elif action == "workers":
        try:
            n = max(1, int(b.get("n")))
        except (TypeError, ValueError):
            raise ApiError(400, "enter a whole number")
        if j.active:
            j.set_workers(n)
        s.settings.workers = n
        s.save_settings()
    elif action == "retry":
        rels = [i.key for i in j.errors]
        if not rels:
            raise ApiError(400, "no failed items in this job")
        new = s.run_ai(rels, header="tag" in j.kind, route="route" in j.kind, fresh=True,
                       title=f"retry of #{j.id}: {len(rels)} note(s)")
        return {"job": ser.job_json(new) if new else None, "retrying": len(rels)}
    else:
        raise ApiError(404, f"unknown job action: {action}")
    return {"job": ser.job_json(j)}


@router.post("/api/jobs-all/{action}")
async def jobs_all(request: Request, action: str):
    s = sess(request)
    if action == "pause":
        s.jobs.pause_all()
    elif action == "resume":
        s.jobs.resume_all()
    elif action == "toggle":
        act = s.jobs.active()
        (s.jobs.resume_all if act and all(j.state == "paused" for j in act) else s.jobs.pause_all)()
    elif action == "cancel":
        s.jobs.cancel_all()
    elif action == "clear":
        s.jobs.clear_finished()
    else:
        raise ApiError(404, f"unknown action: {action}")
    return {"ok": True}
