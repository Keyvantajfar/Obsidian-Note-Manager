"""Simulated OpenAI-compatible servers reproducing real-world misbehaviour (reasoning models, 200+error bodies, ...)."""
import json
import re
from pathlib import Path

import httpx
import pytest

from vaultcore.llm import LLMClient, LLMError, chat_json
from vaultcore.session import Session
from vaultcore.settings import LLMProfile, Profiles



def _answer_for(user: str) -> str:
    ids = re.findall(r'"id":\s*"(n\d+)"', user) or re.findall(r"\bid[=: ]+\"?(n\d+)", user) or ["n1"]
    res = [{"id": i, "fields": {"parent": "[[Fleeting MOC]]", "status": "#FleetingNote", "tags": ["Topic A", "Topic B", "Topic C", "Topic D", "Topic E"]},
            "confidence": {"fields": 0.8}, "reason": "ok"} for i in dict.fromkeys(ids)]
    return json.dumps({"results": res}, ensure_ascii=False)


def make_transport(mode: str):
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["n"] += 1
        body = json.loads(req.content)
        user = body["messages"][-1]["content"]
        ans = _answer_for(user)
        ok = lambda content, **kw: httpx.Response(200, json={"choices": [{"message": {"content": content, **kw.get("msg", {})},
                                                                           "finish_reason": kw.get("fr", "stop")}],
                                                             "usage": {"prompt_tokens": 10, "completion_tokens": 20}})
        if mode == "reasoning":                  # thinking model: empty content unless thinking is disabled or the budget is big
            off = body.get("reasoning") == {"enabled": False} or body.get("chat_template_kwargs", {}).get("enable_thinking") is False
            if not off and body["max_tokens"] < 6000:
                return ok(None, msg={"reasoning": "Let me think about this note..."}, fr="length")
            return ok(ans)
        if mode == "fenced":
            return ok("Sure! Here is the JSON:\n```json\n" + ans + "\n```\nHope that helps.")
        if mode == "think_tags":
            return ok("<think>hmm hmm {not json}</think>\n" + ans)
        if mode == "err200":
            if state["n"] == 1:
                return httpx.Response(200, json={"error": {"code": 429, "message": "Provider returned error"}})
            return ok(ans)
        if mode == "nojson404":
            if "response_format" in body:
                return httpx.Response(404, json={"error": {"message": "No endpoints found that support response_format"}})
            return ok(ans)
        if mode == "parts":
            return ok([{"type": "text", "text": ans}])
        if mode == "prose_then_ok":              # first reply is prose, second (after the nudge) is JSON
            if "previous reply was not valid JSON" not in user:
                return ok("I think this note is about statistics.")
            return ok(ans)
        if mode == "plain":
            return ok(ans)
        raise AssertionError(mode)
    return httpx.MockTransport(handler)


def client(mode: str, **kw) -> LLMClient:
    prof = LLMProfile(name="t", base_url="https://openrouter.ai/api/v1", model="x/y", max_retries=2, max_tokens=1024, **kw)
    return LLMClient(prof, transport=make_transport(mode))


@pytest.fixture
def vault(tmp_path):
    """Notes shaped like a real vault: Persian text + LaTeX, a 70 KB note, odd headers like `tags: [[A]], [[]]`."""
    root = tmp_path / "v"
    (root / "000 Zettelkasten").mkdir(parents=True)
    (root / "200 Researches").mkdir()
    (root / "000 Zettelkasten" / "AmarAzad.md").write_text("### سوال ۱\n\n**آزمون t وابسته**\n\n$$D = \\{2, 4, -3\\}$$\n" * 50, encoding="utf-8")
    (root / "200 Researches" / "Fast R-CNN.md").write_text(
        '\n---\nparent: "[[Fleeting MOC]]"\nstatus: #FleetingNote\ntags: [[CNN]], [[Artificial Intelligence]], [[]]\n'
        "date: 2024-09-16T17:18\n\n---\n# Fast R-CNN\n\nSummary text here.\n", encoding="utf-8")
    (root / "200 Researches" / "Internship Programs.md").write_text("# Big\n" + "line of text about internships\n" * 2500, encoding="utf-8")
    (root / "000 Zettelkasten" / "crlf.md").write_bytes(b"---\r\nstatus: #Idea\r\n---\r\nbody\r\n")
    return root


@pytest.mark.parametrize("mode", ["plain", "reasoning", "fenced", "think_tags", "err200", "nojson404", "parts", "prose_then_ok"])
async def test_every_note_gets_a_proposal(vault, mode):
    s = Session(vault, llm=client(mode))
    s.scan()
    job = s.run_ai(None, header=True, route=False, only_unstaged=True)
    assert job is not None
    await job.wait()
    errs = [(e.key, e.message) for e in job.errors]
    assert not errs, errs
    assert len(s.stage.all()) >= 1
    await s.aclose()


async def test_reasoning_error_message_is_actionable():
    c = client("reasoning", reasoning="on")           # user forced thinking on and a tiny budget
    data, err = await chat_json(c, "sys", "user", max_tokens=300)
    assert data is None and "empty answer" in err and "finish_reason=length" in err


async def test_chat_json_recovers_from_reasoning_model():
    data, err = await chat_json(client("reasoning"), "sys", "user ids: \"id\": \"n1\"", max_tokens=300)
    assert err == "" and data["results"][0]["id"] == "n1"


def test_import_legacy_config(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULTMGR_HOME", str(tmp_path / "cfg"))
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"ai_base_url": "https://openrouter.ai/api/v1", "ai_model": "m/x", "ai_api_key": "k", "ai_timeout": 120, "theme": "auto"}),
                 encoding="utf-8-sig")
    pr = Profiles.load()
    prof = pr.import_legacy(f)
    again = Profiles.load()
    assert again.get_active().base_url == "https://openrouter.ai/api/v1" and again.get_active().model == "m/x"
    assert prof.timeout == 120 and again.get_active().api_key == "k"
    with pytest.raises(ValueError):
        Profiles.profile_from_legacy({"theme": "auto"})


def test_clean_key_and_hints():
    from vaultcore.llm import auth_hint, clean_key, describe_key
    assert clean_key('  "Bearer sk-or-v1-abc\n def" ') == "sk-or-v1-abcdef"
    assert clean_key("OPENROUTER_API_KEY=sk-or-v1-x") == "sk-or-v1-x"
    assert "NO API KEY" in describe_key("")
    assert "re-paste" in auth_hint("HTTP 401: Missing Authentication header").lower() or "Re-paste" in auth_hint("HTTP 401: x")


async def test_auth_failure_pauses_job_instead_of_failing_everything(vault):
    def handler(req):
        return httpx.Response(401, json={"error": {"message": "Missing Authentication header", "code": 401}})
    prof = LLMProfile(name="t", base_url="https://openrouter.ai/api/v1", model="m", max_retries=0)
    s = Session(vault, llm=LLMClient(prof, transport=httpx.MockTransport(handler)))
    s.settings.workers = 1
    s.scan()
    job = s.run_ai(None, header=True, route=False, only_unstaged=True)
    import asyncio
    for _ in range(50):
        await asyncio.sleep(0.1)
        if job.state == "paused":
            break
    assert job.state == "paused"
    assert job.count("error") <= 2 and job.count("queued") >= 1
    assert any("authentication failed" in l for l in job.log)
    job.cancel()
    await s.aclose()
