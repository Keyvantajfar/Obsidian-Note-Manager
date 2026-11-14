import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vaultcore.llm import LLMClient, LLMError, parse_json_loose, strip_think
from vaultcore.settings import LLMProfile


class Handler(BaseHTTPRequestHandler):
    script: list = []
    seen: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "m1"}, {"id": "m2"}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n))
        Handler.seen.append((payload, dict(self.headers)))
        status, body = Handler.script.pop(0) if Handler.script else (200, {"choices": [{"message": {"content": "{}"}}]})
        data = json.dumps(body).encode()
        self.send_response(status); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)


@pytest.fixture
def server():
    Handler.script, Handler.seen = [], []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def ok(text):
    return 200, {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}


async def test_chat_basic_strips_think_and_sends_auth(server):
    Handler.script = [ok("<think>hmm</think>{\"a\": 1}")]
    c = LLMClient(LLMProfile(base_url=server, model="m", api_key="sek", extra_body={"top_k": 7}))
    r = await c.chat("sys", "usr", json_mode=True)
    assert r.text == '{"a": 1}' and r.tokens == 8
    payload, headers = Handler.seen[0]
    assert payload["response_format"] == {"type": "json_object"} and payload["top_k"] == 7
    assert headers["Authorization"] == "Bearer sek"
    await c.aclose()


async def test_retries_on_5xx_then_succeeds(server, monkeypatch):
    Handler.script = [(503, {"e": 1}), (429, {"e": 1}), ok("{}")]
    import vaultcore.llm as m
    async def nosleep(_): return None
    monkeypatch.setattr(m.asyncio, "sleep", nosleep)
    c = LLMClient(LLMProfile(base_url=server, model="m", max_retries=3))
    assert (await c.chat("s", "u")).text == "{}"
    assert len(Handler.seen) == 3
    await c.aclose()


async def test_non_retryable_error_raises_immediately(server):
    Handler.script = [(401, {"error": "bad key"})]
    c = LLMClient(LLMProfile(base_url=server, model="m"))
    with pytest.raises(LLMError) as e:
        await c.chat("s", "u")
    assert e.value.status == 401 and not e.value.retryable and len(Handler.seen) == 1
    await c.aclose()


async def test_json_mode_auto_falls_back_without_response_format(server):
    Handler.script = [(400, {"error": "response_format not supported"}), ok("{}"), ok("{}")]
    c = LLMClient(LLMProfile(base_url=server, model="m", json_mode="auto"))
    await c.chat("s", "u", json_mode=True)
    assert "response_format" in Handler.seen[0][0] and "response_format" not in Handler.seen[1][0]
    await c.chat("s", "u", json_mode=True)                      # remembered: not even tried
    assert "response_format" not in Handler.seen[2][0]
    await c.aclose()


async def test_json_mode_off_and_list_models(server):
    c = LLMClient(LLMProfile(base_url=server, model="m", json_mode="off"))
    await c.chat("s", "u", json_mode=True)
    assert "response_format" not in Handler.seen[0][0]
    assert await c.list_models() == ["m1", "m2"]
    await c.aclose()


async def test_connection_refused_is_llm_error():
    c = LLMClient(LLMProfile(base_url="http://127.0.0.1:9/v1", model="m", max_retries=0))
    with pytest.raises(LLMError) as e:
        await c.chat("s", "u")
    assert e.value.retryable
    await c.aclose()


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("MY_KEY", "fromenv")
    assert LLMProfile(api_key_env="MY_KEY", api_key="x").resolved_key() == "fromenv"
    assert LLMProfile(api_key="x").resolved_key() == "x"


def test_parse_json_loose():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('Sure! Here: {"a": {"b": [1,2]}} hope it helps') == {"a": {"b": [1, 2]}}
    assert parse_json_loose("<think>{\"x\":1}</think>{\"y\":2}") == {"y": 2}
    assert parse_json_loose("nope") is None
    assert strip_think("a<think>unclosed") == "a"
