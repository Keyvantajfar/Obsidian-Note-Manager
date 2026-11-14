"""App info, vault open/close, folder picker, workspaces, status, rescan, command bar."""
from __future__ import annotations

import os
import platform
import re
import string
import sys
from pathlib import Path

from fastapi import APIRouter, Request

from vaultcore.settings import AUTO_LEVELS, Profiles
from vaultcore.workflows import TAB_LABELS, WORKFLOWS

from .. import __version__
from ..common import body, sess, vm
from ..state import ApiError

router = APIRouter()


def status_json(v) -> dict:
    s = v.session
    out: dict = {"open": s is not None, "version": __version__, "workflow": v.workflow, "mock": v.mock,
                 "scanning": v.scanning, "scan_error": v.scan_error}
    if s is None:
        return out
    st = s.status()
    jobs = s.jobs.active()
    out.update({
        "vault": {"path": s.vault, "name": Path(s.vault).name},
        "stats": {k: st[k] for k in ("notes", "no_header", "incomplete", "complete", "folders")},
        "stage": st["stage"], "tokens": st["tokens"], "cache": st["cache"],
        "mode": s.settings.auto_level, "model": s.model_label, "offline": s.offline,
        "header_enabled": s.header_enabled, "route_enabled": s.route_enabled,
        "paused": bool(jobs) and all(j.state == "paused" for j in jobs),
        "jobs": {"active": len(jobs), "total": sum(j.total for j in jobs),
                 "finished": sum(j.finished_count for j in jobs), "rate": round(sum(j.rate for j in jobs), 2)},
        "frozen": s.taxonomy.frozen, "plan_size": len(s.taxonomy), "threshold": s.settings.auto_accept_threshold,
        "workers": s.settings.workers, "message": s.last_message,
        "tabs": WORKFLOWS[v.workflow]["tabs"],
    })
    return out


@router.get("/healthz")
async def healthz(request: Request):
    return {"ok": True, "app": "vaultmgr", "version": __version__, "vault": vm(request).session is not None}


@router.get("/api/app")
async def app_info(request: Request):
    v = vm(request)
    try:
        recent = [r for r in Profiles.load().recent_vaults if os.path.isdir(r)]
    except Exception:  # noqa: BLE001
        recent = []
    return {"version": __version__, "platform": sys.platform, "python": platform.python_version(),
            "workflows": WORKFLOWS, "tab_labels": TAB_LABELS, "auto_levels": AUTO_LEVELS, "recent": recent,
            "home": str(Path.home()), "desktop": bool(v.desktop.get("enabled")), "status": status_json(v)}


@router.get("/api/status")
async def status(request: Request):
    return status_json(vm(request))


@router.post("/api/vault/open")
async def vault_open(request: Request):
    b = await body(request)
    path = str(b.get("path") or "").strip().strip('"')
    if not path:
        raise ApiError(400, "path is required")
    await vm(request).open_vault(path, workflow=b.get("workflow"), mock=b.get("mock"))
    return status_json(vm(request))


@router.post("/api/vault/demo")
async def vault_demo(request: Request):
    await vm(request).open_demo()
    return status_json(vm(request))


@router.post("/api/vault/close")
async def vault_close(request: Request):
    await vm(request).close_vault()
    return status_json(vm(request))


@router.post("/api/workflow")
async def set_workflow(request: Request):
    b = await body(request)
    vm(request).set_workflow(str(b.get("id") or ""))
    return status_json(vm(request))


@router.post("/api/scan")
async def rescan(request: Request):
    import asyncio
    s = sess(request)
    diff = await asyncio.to_thread(s.index.scan)
    s.after_scan(diff)
    s.say("rescanned")
    return {"added": len(diff.added), "changed": len(diff.changed), "removed": len(diff.removed)}


@router.post("/api/quit")
async def quit_app(request: Request):
    v = vm(request)
    s = v.session
    if s is not None:
        s.save_all()
    v.quit_requested = True
    return {"ok": True}


# ---------------------------------------------------------------- folder picker
def _roots() -> list[str]:
    if os.name == "nt":
        return [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
    return ["/"]


@router.get("/api/fs")
async def fs_list(request: Request, path: str = "", hidden: int = 0):
    p = Path(path).expanduser() if path else Path.home()
    try:
        p = p.resolve()
    except OSError as e:
        raise ApiError(400, str(e))
    if not p.is_dir():
        raise ApiError(400, f"not a folder: {p}")
    dirs, md = [], 0
    try:
        with os.scandir(p) as it:
            for e in it:
                try:
                    if e.is_dir():
                        if e.name.startswith(".") and not hidden:
                            continue
                        dirs.append({"name": e.name, "path": e.path, "is_vault": os.path.isdir(os.path.join(e.path, ".obsidian"))})
                    elif e.name.lower().endswith(".md"):
                        md += 1
                except OSError:
                    continue
    except PermissionError:
        raise ApiError(400, f"permission denied: {p}")
    dirs.sort(key=lambda d: d["name"].lower())
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "home": str(Path.home()),
            "roots": _roots(), "dirs": dirs, "md_count": md, "is_vault": (p / ".obsidian").is_dir()}


@router.post("/api/fs/mkdir")
async def fs_mkdir(request: Request):
    b = await body(request)
    parent, name = str(b.get("path") or ""), str(b.get("name") or "").strip()
    if not name or re.search(r'[\\/:*?"<>|]', name):
        raise ApiError(400, "invalid folder name")
    target = Path(parent).expanduser() / name
    try:
        target.mkdir(parents=False, exist_ok=False)
    except OSError as e:
        raise ApiError(400, str(e))
    return {"path": str(target)}


# ---------------------------------------------------------------- command bar
@router.post("/api/command")
async def command(request: Request):
    """The command bar: /slash commands, rules (cond -> action) and free-text AI instructions."""
    s = sess(request)
    v = vm(request)
    text = str((await body(request)).get("text") or "").strip()
    if not text:
        raise ApiError(400, "empty command")
    res: dict = {"message": "", "job": None, "goto": None, "action": None, "level": "info"}

    if text.startswith("/"):
        parts = text[1:].split()
        if not parts:
            raise ApiError(400, "empty command")
        name, args = parts[0].lower(), parts[1:]
        try:
            if name == "mode" and args and args[0] in AUTO_LEVELS:
                s.settings.auto_level = args[0]
                s.save_settings()
                res["message"] = f"mode: {args[0]}"
            elif name == "workers" and args:
                s.settings.workers = int(args[0])
                s.save_settings()
                for j in s.jobs.active():
                    j.set_workers(s.settings.workers)
                res["message"] = f"workers: {s.settings.workers}"
            elif name == "batch" and args:
                s.settings.batch_size = int(args[0])
                s.save_settings()
                res["message"] = f"notes per request: {s.settings.batch_size} (applies to new jobs)"
            elif name == "threshold" and args:
                s.settings.auto_accept_threshold = float(args[0])
                s.save_settings()
                res["message"] = f"auto-accept threshold: {s.settings.auto_accept_threshold}"
            elif name == "run":
                what = args[0] if args else "all"
                job = s.run_ai(header=what in ("tag", "all"), route=what in ("route", "all"), only_unstaged=True)
                res["job"] = job.id if job else None
                res["message"] = f"job #{job.id} started" if job else "nothing to do"
            elif name == "apply":
                res.update(goto="stage", action="apply")
            elif name == "undo":
                res.update(goto="stage", action="undo")
            elif name == "pause":
                s.jobs.pause_all()
                res["message"] = "paused"
            elif name == "resume":
                s.jobs.resume_all()
                res["message"] = "resumed"
            elif name == "stop":
                s.jobs.cancel_all()
                res["message"] = "stopped"
            elif name == "import" and args:
                prof = s.profiles.import_legacy(" ".join(args).strip('"'))
                s.use_profile(prof)
                res["message"] = f"imported + activated LLM profile '{prof.name}' (check Settings → LLM connection → Test)"
            elif name == "scan":
                res["action"] = "scan"
            elif name in ("freeze", "unfreeze"):
                s.set_frozen(name == "freeze")
                res["message"] = "plan frozen: AI may only suggest new folders" if name == "freeze" else "plan unfrozen"
            elif name == "goto" and args:
                tabs = WORKFLOWS[v.workflow]["tabs"]
                match = next((t for t in tabs if t.startswith(args[0].lower())), None)
                if not match:
                    raise ApiError(400, f"no tab '{args[0]}' in this workspace")
                res["goto"] = match
            elif name == "help":
                res["action"] = "help"
            else:
                raise ApiError(400, f"unknown command: /{parts[0]}  (try /help)")
        except (ValueError, IndexError, OSError) as e:
            raise ApiError(400, f"bad argument: {e}")
        return res

    if "->" in text:
        try:
            r = s.add_rule(text)
        except Exception as e:  # noqa: BLE001
            raise ApiError(400, str(e))
        res["message"] = f"rule added — matches {len(s.rule_matches(r.id))} note(s). Review the results in the Stage tab."
        res["goto"] = "stage" if "stage" in WORKFLOWS[v.workflow]["tabs"] else None
        return res

    scope, value, instr = "global", "", text
    m = re.match(r"^@(\S+?):\s*(.+)$", text)
    m2 = re.match(r"^#(\S+?):\s*(.+)$", text)
    if m:
        scope, value, instr = "folder", m.group(1).strip("/"), m.group(2)
    elif m2:
        scope, value, instr = "tag", m2.group(1), m2.group(2)
    ins, job = s.add_instruction(instr, scope, value)
    res["job"] = job.id if job else None
    res["message"] = f"instruction saved ({ins.scope_label()})" + (
        f" — re-running AI on affected notes (job #{job.id})" if job else " — use Run on all missing / /run to apply it")

    async def translate() -> None:
        try:
            rules, _ = await s.translate_instruction(instr)
        except Exception as e:  # noqa: BLE001
            s.say(f"could not turn it into rules: {e}")
            return
        n = 0
        for r in rules:
            try:
                s.add_rule(r, name="from instruction", enabled=False, proposed=True)
                n += 1
            except Exception:  # noqa: BLE001
                pass
        if n:
            s.say(f"{n} rule suggestion(s) added (disabled) — approve them in Rules")

    if not s.offline:
        v.spawn(translate())
    return res
