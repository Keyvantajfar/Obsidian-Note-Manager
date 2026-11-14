"""Settings, header schema, prompts and LLM profiles."""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, fields

from fastapi import APIRouter, Request

from vaultcore.llm import LLMClient, LLMError, auth_hint, clean_key, describe_key, parse_json_loose
from vaultcore.prompts import DEFAULT_PROMPTS
from vaultcore.settings import (AUTO_LEVELS, COLLISION_POLICIES, DATE_SOURCES, FIELD_KINDS, FIELD_SOURCES, PRESETS,
                                FieldSpec, LLMProfile, Settings, default_schema)

from .. import ser
from ..common import body, sess
from ..state import ApiError

router = APIRouter()

_SKIP = {"header_schema", "prompts", "theme"}


def settings_state(s) -> dict:
    return {
        "settings": {k: v for k, v in asdict(s.settings).items() if k not in ("theme",)},
        "options": {"auto_levels": AUTO_LEVELS, "collision_policies": COLLISION_POLICIES, "date_sources": DATE_SOURCES,
                    "field_kinds": FIELD_KINDS, "field_sources": FIELD_SOURCES},
        "default_prompts": DEFAULT_PROMPTS,
        "default_schema": [asdict(f) for f in default_schema()],
        "presets": PRESETS,
    }


@router.get("/api/settings")
async def get_settings(request: Request):
    return settings_state(sess(request))


def _coerce(cur, val):
    if isinstance(cur, bool):
        if isinstance(val, str):
            return val.strip().lower() in ("1", "true", "yes", "on")
        return bool(val)
    if isinstance(cur, int):
        return int(val)
    if isinstance(cur, float):
        return float(val)
    if isinstance(cur, list):
        if isinstance(val, str):
            return [x.strip() for x in val.replace("\n", ",").split(",") if x.strip()]
        return [str(x) for x in val]
    return str(val)


@router.put("/api/settings")
async def put_settings(request: Request):
    s = sess(request)
    b = await body(request)
    st = s.settings
    names = {f.name for f in fields(Settings)} - _SKIP
    rescan = False
    for k, v in b.items():
        if k == "prompts":
            if not isinstance(v, dict):
                raise ApiError(400, "'prompts' must be an object")
            st.prompts = {pk: str(pv) for pk, pv in v.items() if pk in DEFAULT_PROMPTS and str(pv).strip()
                          and str(pv).strip() != DEFAULT_PROMPTS[pk].strip()}
            continue
        if k not in names:
            continue
        old = getattr(st, k)
        try:
            new = _coerce(old, v)
        except (TypeError, ValueError):
            raise ApiError(400, f"bad value for {k}")
        if new != old and k in ("ignore_globs", "date_source"):
            rescan = True
        setattr(st, k, new)
    try:
        s.save_settings()
    except (ValueError, OSError) as e:
        raise ApiError(400, str(e))
    if rescan:
        await asyncio.to_thread(s.index.reload_schema)
        diff = await asyncio.to_thread(s.index.scan)
        s.after_scan(diff)
    for j in s.jobs.active():
        if "workers" in b:
            j.set_workers(st.workers)
    return settings_state(s)


@router.put("/api/settings/schema")
async def put_schema(request: Request):
    s = sess(request)
    b = await body(request)
    raw = b.get("fields")
    if not isinstance(raw, list) or not raw:
        raise ApiError(400, "the header needs at least one field")
    names = {f.name for f in fields(FieldSpec)}
    out, seen = [], set()
    for d in raw:
        if not isinstance(d, dict):
            raise ApiError(400, "every field must be an object")
        key = str(d.get("key") or "").strip()
        if not key or any(c in key for c in ":#[]\n"):
            raise ApiError(400, f"invalid field key: {key!r}")
        if key.lower() in seen:
            raise ApiError(400, f"duplicate field key: {key}")
        seen.add(key.lower())
        kw = {k: v for k, v in d.items() if k in names}
        kw["key"] = key
        spec = FieldSpec(**kw)
        if spec.kind not in FIELD_KINDS:
            raise ApiError(400, f"{key}: kind must be one of {', '.join(FIELD_KINDS)}")
        if spec.source not in FIELD_SOURCES:
            raise ApiError(400, f"{key}: source must be one of {', '.join(FIELD_SOURCES)}")
        try:
            time.strftime(spec.date_format)
        except ValueError:
            raise ApiError(400, f"{key}: invalid date format")
        spec.allowed = [str(x) for x in (spec.allowed or [])]
        spec.max_items, spec.min_items = int(spec.max_items), int(spec.min_items)
        out.append(spec)
    s.settings.header_schema = out
    s.save_settings()
    await asyncio.to_thread(s.index.reload_schema)
    s.analyzer.invalidate()
    s.emit("scan", diff=None)
    return settings_state(s)


# ------------------------------------------------------------------ LLM profiles
def profiles_state(s) -> dict:
    pf = s.profiles
    return {"profiles": [ser.profile_json(p) for p in pf.profiles], "active": pf.get_active().name,
            "presets": PRESETS, "model": s.model_label}


def _profile_from(b: dict, existing: LLMProfile | None) -> LLMProfile:
    names = {f.name for f in fields(LLMProfile)}
    d = asdict(existing) if existing else asdict(LLMProfile())
    for k, v in b.items():
        if k in names and k != "api_key":
            d[k] = v
    if "api_key" in b and b["api_key"] is not None:
        d["api_key"] = str(b["api_key"])
    for k in ("temperature", "timeout"):
        d[k] = float(d[k])
    for k in ("max_tokens", "max_retries"):
        d[k] = int(d[k])
    for k in ("extra_body", "extra_headers"):
        if not isinstance(d.get(k), dict):
            raise ApiError(400, f"{k} must be a JSON object")
    d["name"] = str(d["name"]).strip()
    if not d["name"]:
        raise ApiError(400, "profile name is required")
    if d["json_mode"] not in ("auto", "on", "off"):
        raise ApiError(400, "json_mode must be auto, on or off")
    if d["reasoning"] not in ("auto", "off", "on"):
        raise ApiError(400, "reasoning must be auto, off or on")
    return LLMProfile(**d)


@router.get("/api/profiles")
async def get_profiles(request: Request):
    return profiles_state(sess(request))


@router.put("/api/profiles")
async def put_profile(request: Request):
    """Create/update a profile. `original_name` renames. api_key omitted/null keeps the stored key, '' clears it."""
    s = sess(request)
    b = await body(request)
    pf = s.profiles
    prof_in = b.get("profile")
    if not isinstance(prof_in, dict):
        raise ApiError(400, "'profile' must be an object")
    orig = b.get("original_name") or prof_in.get("name")
    existing = next((p for p in pf.profiles if p.name == orig), None)
    try:
        p = _profile_from(prof_in, existing)
    except (TypeError, ValueError) as e:
        raise ApiError(400, f"invalid profile: {e}")
    if existing is not None and orig != p.name:
        if any(x.name == p.name for x in pf.profiles):
            raise ApiError(400, f"a profile named '{p.name}' already exists")
        pf.profiles = [x for x in pf.profiles if x.name != orig]
        if pf.active == orig:
            pf.active = p.name
    if b.get("activate") or pf.get_active().name == p.name:
        s.use_profile(p)
    else:
        pf.upsert(p)
        pf.save()
        s.emit("settings")
    return profiles_state(s)


@router.post("/api/profiles/activate")
async def activate_profile(request: Request):
    s = sess(request)
    name = str((await body(request)).get("name") or "")
    p = next((x for x in s.profiles.profiles if x.name == name), None)
    if p is None:
        raise ApiError(404, "no such profile")
    s.use_profile(p)
    return profiles_state(s)


@router.post("/api/profiles/delete")
async def delete_profile(request: Request):
    s = sess(request)
    name = str((await body(request)).get("name") or "")
    pf = s.profiles
    if not any(x.name == name for x in pf.profiles):
        raise ApiError(404, "no such profile")
    was_active = pf.get_active().name == name
    pf.remove(name)
    if was_active:
        pf.active = pf.profiles[0].name
        s._llm = None
    pf.save()
    s.emit("settings")
    return profiles_state(s)


@router.post("/api/profiles/import")
async def import_profile(request: Request):
    s = sess(request)
    path = str((await body(request)).get("path") or "").strip().strip('"')
    try:
        prof = s.profiles.import_legacy(path)
    except (OSError, ValueError) as e:
        raise ApiError(400, f"cannot import {path}: {e}")
    s.use_profile(prof)
    return profiles_state(s)


def _test_profile(s, b: dict) -> LLMProfile:
    prof_in = b.get("profile")
    if not isinstance(prof_in, dict):
        raise ApiError(400, "'profile' must be an object")
    existing = next((p for p in s.profiles.profiles if p.name == (b.get("original_name") or prof_in.get("name"))), None)
    try:
        return _profile_from(prof_in, existing)
    except (TypeError, ValueError) as e:
        raise ApiError(400, f"invalid profile: {e}")


@router.post("/api/profiles/test")
async def test_profile(request: Request):
    s = sess(request)
    p = _test_profile(s, await body(request))
    p.max_retries = 0
    c = LLMClient(p)
    lines = [{"level": "info", "text": describe_key(clean_key(p.resolved_key()))}]
    try:
        try:
            models = await c.list_models()
            lines.append({"level": "ok", "text": f"server reachable — {len(models)} model(s)" + (f": {', '.join(models[:5])}" if models else "")})
        except LLMError as e:
            lines.append({"level": "warn", "text": f"/models failed ({e}) — trying a chat request anyway"})
        t0 = time.monotonic()
        r = await c.chat("You are a connection test.", 'Reply with exactly this JSON: {"ok": true}', json_mode=True, max_tokens=600)
        if not r.text.strip():
            c.note_empty_answer()
            lines.append({"level": "error", "text": f"the server answered but with EMPTY text (finish_reason={r.finish_reason or '?'}"
                          + (", the model spent its tokens on hidden reasoning — set Thinking to 'off' or raise max tokens" if r.reasoning or r.finish_reason == "length" else "") + ")"})
        elif parse_json_loose(r.text) is None:
            lines.append({"level": "warn", "text": f"chat works ({time.monotonic() - t0:.2f}s) but the reply is not JSON: {r.text.strip()[:80]!r} — set JSON mode to 'off' or choose another model"})
        else:
            lines.append({"level": "ok", "text": f"chat + JSON works — {time.monotonic() - t0:.2f}s, reply: {r.text.strip()[:60]}"})
    except LLMError as e:
        lines.append({"level": "error", "text": str(e)})
        if auth_hint(str(e)):
            lines.append({"level": "warn", "text": f"{auth_hint(str(e))} (listing models can work without a valid key — it proves nothing about the key)"})
    except Exception as e:  # noqa: BLE001
        lines.append({"level": "error", "text": f"{type(e).__name__}: {e}"})
    finally:
        await c.aclose()
    return {"lines": lines, "ok": not any(l["level"] == "error" for l in lines)}


@router.post("/api/profiles/models")
async def list_models(request: Request):
    s = sess(request)
    p = _test_profile(s, await body(request))
    p.max_retries = 0
    c = LLMClient(p)
    try:
        models = await c.list_models()
    except Exception as e:  # noqa: BLE001
        raise ApiError(502, f"could not list models: {e}")
    finally:
        await c.aclose()
    return {"models": models}
