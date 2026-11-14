from __future__ import annotations

from fastapi import Request

from .state import ApiError, AppState


def vm(request: Request) -> AppState:
    return request.app.state.vm


def sess(request: Request):
    return vm(request).need()


async def body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = await request.json()
    except ValueError:
        raise ApiError(400, "request body is not valid JSON")
    if not isinstance(data, dict):
        raise ApiError(400, "request body must be a JSON object")
    return data


def str_list(v, name: str = "paths") -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ApiError(400, f"'{name}' must be a list of strings")
    return v


def need_note(s, rel: str):
    n = s.index.get(rel)
    if n is None:
        raise ApiError(404, f"no such note: {rel}")
    return n
