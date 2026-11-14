"""OpenAI-compatible async chat client (llama.cpp server, vLLM, Ollama /v1, LM Studio, OpenAI, OpenRouter...).

Everything provider-specific is data in the profile: base URL, key (or env var), model,
``json_mode`` (auto/on/off), ``extra_body`` (e.g. vLLM ``guided_json`` or
``chat_template_kwargs``), ``extra_headers``. Includes retries with backoff, `<think>` stripping
and tolerant JSON extraction.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass

import httpx

from .settings import LLMProfile


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency: float = 0.0
    finish_reason: str = ""
    reasoning: str = ""

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_think(text: str) -> str:
    text = _THINK_RE.sub("", text)
    if "<think>" in text.lower() and "</think>" not in text.lower():
        text = re.split(r"<think>", text, flags=re.IGNORECASE)[0]
    return text.strip()


def parse_json_loose(raw: str):
    """Extract the first JSON object/array from model output (fences, prose, think blocks tolerated).
    Returns None if nothing parses."""
    raw = strip_think(raw or "")
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    try:
        return json.loads(raw)
    except ValueError:
        pass
    dec = json.JSONDecoder()
    tried = 0
    for m in re.finditer(r"[{\[]", raw):
        try:
            obj, _ = dec.raw_decode(raw[m.start():])
            return obj
        except ValueError:
            tried += 1
            if tried > 25:
                break
    return None


def clean_key(raw: str) -> str:
    """Make a pasted API key safe: drop whitespace/newlines/zero-width chars, quotes, 'Bearer ' and 'KEY=' prefixes."""
    k = re.sub(r"[\s\u200b-\u200f\u2060\ufeff]+", "", raw or "").strip("\"'`“”‘’")
    k = re.sub(r"^(?i:bearer)", "", k)
    k = re.sub(r"^[A-Za-z_]*(?:KEY|key|Key)=", "", k)
    return k.strip("\"'`“”‘’")


def describe_key(key: str) -> str:
    if not key:
        return "NO API KEY is set"
    return f"key sent: {key[:9]}…{key[-4:]} ({len(key)} chars)" if len(key) > 16 else f"key sent: {len(key)} chars (looks too short)"


def auth_hint(message: str) -> str:
    """Human advice for fatal provider errors, or '' if the message isn't one."""
    m = re.match(r"HTTP (401|402|403)", message or "")
    if not m:
        return ""
    return {"401": "authentication failed: the provider did not accept the API key (empty, mistyped, revoked or for another provider). "
                   "Re-paste it in Settings → LLM connection and press Test.",
            "402": "the provider says you're out of credits.",
            "403": "the provider refused this key/model (permissions, region or moderation)."}[m.group(1)]


class LLMClient:
    def __init__(self, profile: LLMProfile, transport: httpx.AsyncBaseTransport | None = None):
        self.profile = profile
        self._http: httpx.AsyncClient | None = None
        self._transport = transport                 # tests inject httpx.MockTransport here
        self._json_unsupported = False
        self._reasoning_off = False                 # set after a model burned its budget "thinking"
        self.last_debug: str = ""

    def note_empty_answer(self) -> None:
        """Called when a reply had no usable content (typically a reasoning model that ran out of tokens)."""
        if self.profile.reasoning == "auto":
            self._reasoning_off = True

    # ---- plumbing ----------------------------------------------------------------
    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(self.profile.timeout, connect=10.0),
                limits=httpx.Limits(max_connections=128, max_keepalive_connections=32),
                transport=self._transport,
            )
        return self._http

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        key = clean_key(self.profile.resolved_key())
        if key:
            h["Authorization"] = f"Bearer {key}"
        h.update(self.profile.extra_headers or {})
        return h

    def _url(self, path: str) -> str:
        return self.profile.base_url.rstrip("/") + path

    async def aclose(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    # ---- API ---------------------------------------------------------------------
    async def list_models(self) -> list[str]:
        try:
            r = await self._client().get(self._url("/models"), headers=self._headers())
        except httpx.HTTPError as e:
            raise LLMError(f"cannot reach {self.profile.base_url}: {e}", retryable=True)
        if r.status_code != 200:
            raise LLMError(f"/models returned HTTP {r.status_code}: {r.text[:200]}", status=r.status_code)
        data = r.json()
        items = data.get("data", data if isinstance(data, list) else [])
        return [str(m.get("id", m)) if isinstance(m, dict) else str(m) for m in items]

    async def chat(self, system: str, user: str, *, json_mode: bool = False,
                   max_tokens: int | None = None) -> LLMResponse:
        p = self.profile
        use_json = json_mode and p.json_mode != "off" and not (p.json_mode == "auto" and self._json_unsupported)
        last: Exception | None = None
        for attempt in range(p.max_retries + 1):
            payload = {
                "model": p.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": p.temperature,
                "max_tokens": max_tokens or p.max_tokens,
                "stream": False,
            }
            if use_json:
                payload["response_format"] = {"type": "json_object"}
            if p.reasoning == "off" or (p.reasoning == "auto" and self._reasoning_off):
                payload.update(self._no_reasoning_body())
            payload.update(p.extra_body or {})
            t0 = time.monotonic()
            delay = min(30.0, 0.8 * (2 ** attempt)) + random.random() * 0.3
            try:
                r = await self._client().post(self._url("/chat/completions"), json=payload, headers=self._headers())
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = LLMError(f"{type(e).__name__}: {e or 'connection problem'}", retryable=True)
            else:
                if r.status_code == 200:
                    try:
                        return self._parse(r, time.monotonic() - t0)
                    except LLMError as e:
                        if not e.retryable:
                            raise
                        last = e
                        delay = max(delay, 1.0)
                        if attempt < p.max_retries:
                            await asyncio.sleep(delay)
                        continue
                body = r.text[:400]
                if r.status_code in (400, 404, 415, 422) and use_json and re.search(r"response_format|json|structured", body, re.I):
                    # backend doesn't support JSON mode: remember and retry immediately without it
                    if p.json_mode == "auto":
                        self._json_unsupported = True
                    use_json = False
                    continue
                if r.status_code in (408, 409, 425, 429, 500, 502, 503, 504):
                    last = LLMError(f"HTTP {r.status_code}: {body}", retryable=True, status=r.status_code)
                    ra = r.headers.get("retry-after")
                    if ra and ra.isdigit():
                        delay = max(delay, min(60.0, float(ra)))
                else:
                    raise LLMError(f"HTTP {r.status_code}: {body}", status=r.status_code)
            if attempt < p.max_retries:
                await asyncio.sleep(delay)
        assert last is not None
        raise last

    def _no_reasoning_body(self) -> dict:
        if "openrouter" in self.profile.base_url.lower():
            return {"reasoning": {"enabled": False}}
        return {"chat_template_kwargs": {"enable_thinking": False}}

    @staticmethod
    def _parse(r: httpx.Response, latency: float) -> LLMResponse:
        try:
            data = r.json()
        except ValueError:
            raise LLMError(f"server returned non-JSON (HTTP 200): {r.text[:200]!r}", retryable=True)
        if not isinstance(data, dict):
            raise LLMError(f"unexpected response shape: {str(data)[:200]}")
        if data.get("error") and not data.get("choices"):
            # OpenRouter (and some proxies) report upstream failures as HTTP 200 + {"error": {...}}
            err = data["error"]
            msg = err.get("message", err) if isinstance(err, dict) else err
            code = err.get("code") if isinstance(err, dict) else None
            retry = code in (408, 429, 500, 502, 503, 504, "rate_limit_exceeded") or code is None
            raise LLMError(f"provider error {code}: {str(msg)[:300]}", retryable=retry,
                           status=code if isinstance(code, int) else None)
        try:
            choice = data["choices"][0]
            msg = choice.get("message") or {}
            content = msg.get("content")
            usage = data.get("usage") or {}
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            raise LLMError(f"unexpected response shape: {e}: {r.text[:200]}")
        if isinstance(content, list):                    # content as a list of parts
            content = "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
        text = content or choice.get("text") or ""
        reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)
        return LLMResponse(strip_think(text), int(usage.get("prompt_tokens") or 0),
                           int(usage.get("completion_tokens") or 0), latency,
                           str(choice.get("finish_reason") or ""), reasoning)


async def chat_json(llm, system: str, user: str, *, max_tokens: int = 2048) -> tuple[object | None, str]:
    """Chat expecting JSON. Retries with a bigger budget / firmer nudge. Returns (data, error_message)."""
    budget = max(max_tokens, int(getattr(getattr(llm, "profile", None), "max_tokens", 0) or 0))
    err = "no answer"
    user_now = user
    for _ in range(3):
        try:
            resp = await llm.chat(system, user_now, json_mode=True, max_tokens=budget)
        except LLMError as e:
            err = str(e)
            if not e.retryable:
                break
            continue
        data = parse_json_loose(resp.text)
        if data is not None:
            return data, ""
        if not resp.text.strip():
            llm.note_empty_answer()
            budget = min(budget * 2, 32000)
            err = f"model returned an empty answer (finish_reason={resp.finish_reason or '?'})"
        else:
            if resp.finish_reason == "length":
                budget = min(budget * 2, 32000)
            err = f"model did not return valid JSON (finish_reason={resp.finish_reason or '?'}): {' '.join(resp.text.split())[:90]!r}"
            user_now = user + "\n\nIMPORTANT: your previous reply was not valid JSON. Reply with ONE JSON object only — no prose, no markdown."
    return None, err
