"""vaultweb API: guards, vault open, the whole tag → stage → apply → undo loop, organizer, rules, dates, settings."""
import time

import pytest
from starlette.testclient import TestClient

from vaultcore.demo import make_demo_vault
from vaultweb.app import create_app
from vaultweb.state import AppState

H = {"X-VaultMgr": "1"}


@pytest.fixture
def state():
    st = AppState(mock=True)
    st.mock_timing = (0.0, 0.0)
    return st


@pytest.fixture
def client(state):
    with TestClient(create_app(state), base_url="http://127.0.0.1", headers=H) as c:
        yield c


@pytest.fixture
def open_client(client, tmp_path):
    root = make_demo_vault(tmp_path / "vault")
    r = client.post("/api/vault/open", json={"path": str(root), "mock": True})
    assert r.status_code == 200, r.text
    client.root = root
    return client


def md_snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.md")
            if not any(part.startswith(".") for part in p.relative_to(root).parts)}


def wait_jobs(c, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        jobs = c.get("/api/jobs").json()["jobs"]
        if jobs and not any(j["active"] for j in jobs):
            return jobs
        time.sleep(0.05)
    raise AssertionError("jobs did not finish")


# ------------------------------------------------------------------ guards
def test_guard_host_origin_header(client):
    assert client.get("/healthz").json()["ok"] is True
    assert client.get("/healthz", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/healthz", headers={"Origin": "http://evil.example"}).status_code == 403
    r = client.post("/api/scan", headers={"X-VaultMgr": ""})
    assert r.status_code == 403 and "X-VaultMgr" in r.json()["error"]
    assert client.get("/api/app", headers={"Origin": "http://127.0.0.1"}).status_code == 200


def test_errors_are_json(client):
    r = client.get("/api/notes")
    assert r.status_code == 409 and r.json()["error"] == "no vault is open"
    assert client.get("/nope").status_code == 404
    assert "error" in client.get("/nope").json()
    assert client.post("/api/vault/open", json={"path": "/definitely/not/here"}).status_code == 400


def test_index_page_gets_config(client):
    html = client.get("/").text
    assert "window.__VM__" in html and "Vault Manager" in html


# ------------------------------------------------------------------ vault / app
def test_app_info_and_open(open_client):
    c = open_client
    info = c.get("/api/app").json()
    assert set(info["workflows"]) == {"all", "header", "organizer", "dates"} and info["status"]["open"]
    st = c.get("/api/status").json()
    assert st["stats"]["notes"] == 23 and st["model"] == "mock-1" and st["mode"] == "assist"
    assert st["tabs"][0] == "explorer"
    assert c.get("/api/app").json()["recent"]


def test_demo_vault_and_close(client):
    r = client.post("/api/vault/demo").json()
    assert r["open"] and r["stats"]["notes"] == 23 and r["mock"]
    assert client.post("/api/vault/close").json()["open"] is False
    assert client.get("/api/notes").status_code == 409


def test_workflow_switches_ai(open_client):
    c = open_client
    st = c.post("/api/workflow", json={"id": "dates"}).json()
    assert st["offline"] is True and st["tabs"] == ["explorer", "dates", "stage", "settings"]
    st = c.post("/api/workflow", json={"id": "organizer"}).json()
    assert st["offline"] is False and st["header_enabled"] is False and st["route_enabled"] is True
    assert c.post("/api/workflow", json={"id": "bogus"}).status_code == 400


def test_fs_browser(client, tmp_path):
    (tmp_path / "a" / ".obsidian").mkdir(parents=True)
    (tmp_path / "b").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "x.md").write_text("x")
    r = client.get("/api/fs", params={"path": str(tmp_path)}).json()
    names = {d["name"]: d for d in r["dirs"]}
    assert set(names) == {"a", "b"} and names["a"]["is_vault"] and r["md_count"] == 1
    assert ".hidden" in {d["name"] for d in client.get("/api/fs", params={"path": str(tmp_path), "hidden": 1}).json()["dirs"]}
    assert client.get("/api/fs", params={"path": str(tmp_path / "nope")}).status_code == 400
    assert client.post("/api/fs/mkdir", json={"path": str(tmp_path), "name": "new"}).status_code == 200
    assert (tmp_path / "new").is_dir()
    assert client.post("/api/fs/mkdir", json={"path": str(tmp_path), "name": "a/b"}).status_code == 400


# ------------------------------------------------------------------ notes
def test_notes_and_detail(open_client):
    c = open_client
    notes = c.get("/api/notes").json()["notes"]
    assert len(notes) == 23
    r = c.get("/api/note", params={"path": "Zettelkasten/Complete header.md"}).json()
    assert r["has_fm"] and r["missing"] == [] and any(h["key"] == "tags" for h in r["header"]) and "transformers" in r["body"]
    assert c.get("/api/note", params={"path": "nope.md"}).status_code == 404
    link = c.get("/api/note", params={"path": "Zettelkasten/Attention.md"}).json()
    assert "Inbox/Link note.md" in link["backlinks"]


def test_save_note_with_conflict_check(open_client):
    c = open_client
    rel = "Zettelkasten/idea.md"
    d = c.get("/api/note", params={"path": rel}).json()
    r = c.put("/api/note", json={"path": rel, "text": d["text"] + "more\n", "base_hash": d["hash"]})
    assert r.status_code == 200 and "more" in (c.root / rel).read_text()
    r = c.put("/api/note", json={"path": rel, "text": "x", "base_hash": d["hash"]})
    assert r.status_code == 409
    assert c.get("/api/batches").json()["batches"][0]["label"] == "edit note"


def test_manual_header_move_rename_lock(open_client):
    c = open_client
    rel = "Zettelkasten/idea.md"
    assert c.post("/api/note/header", json={"paths": [rel], "fields": {"status": "Draft"}}).json()["staged"] == 1
    assert c.post("/api/note/move", json={"paths": [rel], "folder": "Ideas"}).json()["staged"] == 1
    assert c.post("/api/note/rename", json={"path": rel, "name": "Big idea"}).json()["staged"] == 1
    ch = c.get("/api/stage").json()["changes"]
    assert {x["kind"] for x in ch} == {"header", "move"} and all(x["status"] == "accepted" for x in ch)
    mv = next(x for x in ch if x["kind"] == "move")
    assert mv["data"] == {"folder": "Ideas", "name": "Big idea"}
    n = c.get("/api/notes").json()["notes"]
    assert next(x for x in n if x["rel"] == rel)["eff_folder"] == "Ideas"
    assert c.post("/api/note/lock", json={"paths": [rel]}).json()["locked"][rel] is True
    assert c.post("/api/note/lock", json={"paths": [rel], "locked": False}).json()["locked"][rel] is False
    c.post("/api/note/unstage", json={"paths": [rel]})
    assert c.get("/api/stage").json()["changes"] == []
    assert c.post("/api/note/header", json={"paths": ["nope.md"], "fields": {"a": "b"}}).status_code == 400


# ------------------------------------------------------------------ the loop: AI → stage → apply → undo
def test_full_loop(open_client):
    c = open_client
    before = md_snapshot(c.root)
    r = c.post("/api/ai/run", json={"header": True, "route": False, "only_unstaged": True}).json()
    assert r["job"] and r["job"]["total"] > 0
    jobs = wait_jobs(c)
    assert jobs[0]["errors"] == 0 and jobs[0]["done"] >= 1
    d = c.get(f"/api/jobs/{jobs[0]['id']}").json()
    assert d["items"] and d["log"]
    st = c.get("/api/stage").json()
    assert st["counts"]["proposed"] + st["counts"]["accepted"] > 0
    ch = next(x for x in st["changes"] if x["kind"] == "header")
    pv = c.get("/api/stage/preview", params={"id": ch["id"]}).json()
    assert any(t == "+" for t, _ in pv["diff"])
    n = c.post("/api/stage/accept-all", json={}).json()["accepted"]
    assert n > 0
    plan = c.post("/api/apply/plan", json={}).json()
    assert plan["counts"]["headers"] > 0 and all(not i["blocked"] for i in plan["items"] if not i["noop"])
    res = c.post("/api/apply", json={}).json()
    assert res["result"]["batch_id"] and res["result"]["failed"] == []
    after = md_snapshot(c.root)
    assert after != before
    from vaultcore import frontmatter as fm
    for rel, data in before.items():                       # note bodies are never touched
        assert fm.body_of(data.decode()) == fm.body_of(after[rel].decode()), rel
    u = c.post("/api/undo", json={}).json()
    assert u["restored"] > 0 and not u["conflicts"]
    assert md_snapshot(c.root) == before
    assert c.post("/api/undo", json={}).status_code == 400            # nothing left to undo


def test_stage_edit_reject_clear_remove(open_client):
    c = open_client
    c.post("/api/ai/run", json={"header": True, "route": False})
    wait_jobs(c)
    ch = c.get("/api/stage").json()["changes"]
    h = next(x for x in ch if x["kind"] == "header")
    assert c.post("/api/stage/status", json={"ids": [h["id"]], "status": "rejected"}).json()["changed"] == 1
    assert c.post("/api/stage/status", json={"ids": [h["id"]], "status": "nope"}).status_code == 400
    r = c.post("/api/stage/edit", json={"id": h["id"], "fields": {"status": "Edited"}}).json()
    assert r["change"]["source"] == "manual" and r["change"]["data"]["fields"]["status"] == "Edited"
    assert c.post("/api/stage/clear-rejected").status_code == 200
    ids = [x["id"] for x in c.get("/api/stage").json()["changes"]]
    assert c.post("/api/stage/remove", json={"ids": ids}).json()["removed"] == len(ids)
    assert c.get("/api/stage").json()["changes"] == []


def test_retry_and_job_controls(open_client):
    c = open_client
    r = c.post("/api/ai/run", json={"rels": ["Zettelkasten/Attention.md"], "header": True, "route": False}).json()
    jid = r["job"]["id"]
    wait_jobs(c)
    assert c.post(f"/api/jobs/{jid}/retry").status_code == 400            # nothing failed
    assert c.post(f"/api/jobs/{jid}/workers", json={"n": 3}).status_code == 200
    assert c.get("/api/settings").json()["settings"]["workers"] == 3
    assert c.post(f"/api/jobs/{jid}/workers", json={"n": "x"}).status_code == 400
    assert c.post("/api/jobs-all/clear").status_code == 200
    assert c.get("/api/jobs").json()["jobs"] == []
    assert c.get("/api/jobs/999").status_code == 404


# ------------------------------------------------------------------ organizer
def test_plan_lifecycle(open_client):
    c = open_client
    assert c.post("/api/plan/import-current").json()["added"] > 0
    p = c.post("/api/plan/folder", json={"path": "Archive/Old", "description": "old stuff"}).json()
    paths = {f["path"] for f in p["folders"]}
    assert {"Archive", "Archive/Old"} <= paths
    p = c.post("/api/plan/describe", json={"path": "Archive", "description": "x", "llm": False}).json()
    assert next(f for f in p["folders"] if f["path"] == "Archive")["llm"] is False
    p = c.post("/api/plan/rename", json={"old": "Archive", "new": "Attic"}).json()
    assert p["mapping"]["Archive/Old"] == "Attic/Old"
    assert c.post("/api/plan/rename", json={"old": "Attic", "new": "Attic/Sub"}).status_code == 400
    assert c.post("/api/plan/freeze", json={}).json()["frozen"] is True
    assert c.post("/api/plan/propose", json={}).status_code == 400          # frozen
    assert c.post("/api/plan/freeze", json={"frozen": False}).json()["frozen"] is False
    p = c.post("/api/plan/remove", json={"path": "Attic", "with_children": True}).json()
    assert not any(f["path"].startswith("Attic") for f in p["folders"])


def test_propose_adopt_route(open_client):
    c = open_client
    r = c.post("/api/plan/propose", json={"preferences": "keep it small"})
    assert r.status_code == 200, r.text
    folders = r.json()["folders"]
    if not folders:                                                       # the mock may propose nothing: adopt by hand
        folders = [{"path": "AI", "description": "neural networks, transformers"}, {"path": "Personal", "description": "journal, recipes"}]
    assert c.post("/api/plan/adopt", json={"folders": folders}).status_code == 200
    assert c.post("/api/plan/adopt", json={"folders": []}).status_code == 400
    j = c.post("/api/plan/route", json={}).json()
    assert j["job"] is not None
    wait_jobs(c)
    assert any(x["kind"] == "move" for x in c.get("/api/stage").json()["changes"])
    plan = c.get("/api/plan").json()
    assert plan["folders"] and "attention" in plan and "real" in plan


def test_suggestion_endpoints(open_client):
    c = open_client
    s = open_client.app.state.vm.session
    s.taxonomy.add("Existing")
    s.taxonomy.suggest("New/Place", "Zettelkasten/idea.md", "because")
    r = c.get("/api/plan").json()
    assert r["suggestions"][0]["path"] == "New/Place"
    out = c.post("/api/plan/suggestion", json={"path": "New/Place", "action": "accept"}).json()
    assert out["staged"] == 1 and any(f["path"] == "New/Place" for f in out["folders"])
    assert c.post("/api/plan/suggestion", json={"path": "zzz", "action": "accept"}).status_code == 404


# ------------------------------------------------------------------ tags / rules / instructions / dates
def test_tags_rename_merge_delete(open_client):
    c = open_client
    t = c.get("/api/tags", params={"key": "tags"}).json()
    assert t["key"] == "tags" and any(v["value"] == "Calculus" for v in t["values"])
    assert c.post("/api/tags/rename", json={"key": "tags", "olds": ["Calculus"], "new": "Calc"}).json()["staged"] == 1
    assert c.post("/api/tags/rename", json={"key": "tags", "olds": ["Math"], "new": None}).json()["staged"] >= 1
    assert c.post("/api/tags/rename", json={"key": "tags", "olds": [], "new": "x"}).status_code == 400
    assert c.post("/api/tags/rename", json={"key": "tags", "olds": ["a"], "new": " "}).status_code == 400


def test_rules_crud_and_matches(open_client):
    c = open_client
    r = c.post("/api/rules", json={"text": "title~homework -> move:3_University/Homework"})
    assert r.status_code == 200 and r.json()["matches"] == 2
    rid = r.json()["rule"]["id"]
    assert c.get(f"/api/rules/{rid}/matches").json()["count"] == 2
    assert any(x["kind"] == "move" and x["source"] == "rule" for x in c.get("/api/stage").json()["changes"])
    assert c.post("/api/rules", json={"text": "garbage"}).status_code == 400
    r2 = c.post("/api/rules", json={"text": "tag:ML -> skip"}).json()["rule"]["id"]
    assert [x["id"] for x in c.post(f"/api/rules/{r2}/move", json={"delta": -1}).json()["rules"]][0] == r2
    assert c.put(f"/api/rules/{rid}", json={"text": "title~homework -> move:Elsewhere"}).status_code == 200
    assert c.put(f"/api/rules/{rid}", json={"text": "bad"}).status_code == 400
    assert c.post(f"/api/rules/{rid}/toggle").json()["rules"][1]["enabled"] is False
    assert c.post(f"/api/rules/{rid}/delete").status_code == 200
    assert c.post(f"/api/rules/nope/toggle").status_code == 404
    assert "syntax" in c.get("/api/rules").json()


def test_instructions_and_translate(open_client):
    c = open_client
    r = c.post("/api/instructions", json={"text": "keep calculus under Math", "scope": "folder", "value": "Zettelkasten", "rerun": False})
    assert r.status_code == 200 and r.json()["instructions"][0]["scope_label"] == "folder: Zettelkasten"
    iid = r.json()["instruction"]["id"]
    assert c.post(f"/api/instructions/{iid}/toggle").json()["instructions"][0]["enabled"] is False
    assert c.post(f"/api/instructions/{iid}/delete").json()["instructions"] == []
    assert c.post("/api/instructions", json={"text": " "}).status_code == 400
    assert c.post("/api/rules-translate", json={"text": "put homework in Homework"}).status_code in (200, 502)


def test_dates_flow(open_client):
    c = open_client
    d = c.get("/api/dates").json()
    assert d["spec"]["key"] == "date" and d["counts"]["missing"] > 0 and d["sources"]
    n = c.post("/api/dates/fix", json={"filter": "missing"}).json()["staged"]
    assert n == d["counts"]["missing"]
    assert c.post("/api/dates/set", json={"rel": "Zettelkasten/idea.md", "text": "not a date"}).status_code == 400
    ok = c.post("/api/dates/set", json={"rel": "Zettelkasten/idea.md", "text": "2024-05-06"}).json()
    assert ok["value"].startswith("2024-05-06")
    assert c.get("/api/dates", params={"source": "mtime", "flag_earlier": 1}).status_code == 200
    assert c.get("/api/dates", params={"tolerance": "x"}).status_code == 400


# ------------------------------------------------------------------ settings / schema / profiles
def test_settings_roundtrip(open_client):
    c = open_client
    s = c.get("/api/settings").json()
    assert "analyze_base" in s["default_prompts"] and "textual-dark" not in str(s["settings"])
    r = c.put("/api/settings", json={"workers": "6", "auto_accept_threshold": 0.7, "auto_level": "auto",
                                     "no_ai_globs": "Daily/**, Inbox/*", "prompts": {"analyze_base": "custom", "bogus": "x"}})
    st = r.json()["settings"]
    assert st["workers"] == 6 and st["auto_level"] == "auto" and st["no_ai_globs"] == ["Daily/**", "Inbox/*"]
    assert st["prompts"] == {"analyze_base": "custom"}
    assert c.put("/api/settings", json={"workers": "abc"}).status_code == 400
    r = c.put("/api/settings", json={"prompts": {"analyze_base": s["default_prompts"]["analyze_base"]}}).json()
    assert r["settings"]["prompts"] == {}                                   # same as default = reset
    assert c.get("/api/status").json()["mode"] == "auto"


def test_schema_edit_recomputes_missing(open_client):
    c = open_client
    fields = c.get("/api/settings").json()["settings"]["header_schema"]
    fields.append({"key": "area", "kind": "text", "source": "llm", "instruction": "area of life"})
    r = c.put("/api/settings/schema", json={"fields": fields})
    assert r.status_code == 200
    note = c.get("/api/note", params={"path": "Zettelkasten/Complete header.md"}).json()
    assert note["missing"] == ["area"]
    bad = [dict(fields[0], key="x:y")]
    assert c.put("/api/settings/schema", json={"fields": bad}).status_code == 400
    assert c.put("/api/settings/schema", json={"fields": [fields[0], dict(fields[0])]}).status_code == 400
    assert c.put("/api/settings/schema", json={"fields": [dict(fields[0], kind="weird")]}).status_code == 400
    assert c.put("/api/settings/schema", json={"fields": []}).status_code == 400


def test_profiles_crud_never_leaks_keys(open_client):
    c = open_client
    p = c.get("/api/profiles").json()
    assert p["profiles"] and p["profiles"][0]["api_key"] == ""
    r = c.put("/api/profiles", json={"profile": {"name": "Mine", "base_url": "http://x/v1", "api_key": "sk-secret-123", "model": "m"}, "activate": True})
    out = r.json()
    assert out["active"] == "Mine" and "sk-secret-123" not in r.text
    mine = next(x for x in out["profiles"] if x["name"] == "Mine")
    assert mine["api_key_set"] is True
    s = open_client.app.state.vm.session
    assert s.profiles.get_active().api_key == "sk-secret-123"
    c.put("/api/profiles", json={"profile": {"name": "Mine", "model": "m2"}})            # key omitted: kept
    assert s.profiles.get_active().api_key == "sk-secret-123" and s.profiles.get_active().model == "m2"
    c.put("/api/profiles", json={"profile": {"name": "Mine2"}, "original_name": "Mine"})  # rename
    assert s.profiles.active == "Mine2"
    assert c.put("/api/profiles", json={"profile": {"name": "Mine2", "json_mode": "bad"}}).status_code == 400
    assert c.post("/api/profiles/delete", json={"name": "Mine2"}).status_code == 200
    assert c.post("/api/profiles/activate", json={"name": "zzz"}).status_code == 404


def test_profile_test_reports_failure(open_client):
    r = open_client.post("/api/profiles/test", json={"profile": {"name": "t", "base_url": "http://127.0.0.1:9/v1", "timeout": 2}})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert open_client.post("/api/profiles/models", json={"profile": {"name": "t", "base_url": "http://127.0.0.1:9/v1", "timeout": 2}}).status_code == 502


# ------------------------------------------------------------------ command bar
def test_command_bar(open_client):
    c = open_client
    assert c.post("/api/command", json={"text": "/mode manual"}).json()["message"] == "mode: manual"
    assert c.post("/api/command", json={"text": "/threshold 0.5"}).status_code == 200
    assert c.post("/api/command", json={"text": "/goto dat"}).json()["goto"] == "dates"
    assert c.post("/api/command", json={"text": "/goto zzz"}).status_code == 400
    assert c.post("/api/command", json={"text": "/apply"}).json()["action"] == "apply"
    assert c.post("/api/command", json={"text": "/bogus"}).status_code == 400
    assert c.post("/api/command", json={"text": "/workers x"}).status_code == 400
    r = c.post("/api/command", json={"text": "title~journal -> skip"}).json()
    assert "rule added" in r["message"]
    r = c.post("/api/command", json={"text": "@Zettelkasten: calculus goes in Math"}).json()
    assert "folder: Zettelkasten" in r["message"]
    r = c.post("/api/command", json={"text": "/run tag"}).json()
    assert r["job"]
    wait_jobs(c)


# ------------------------------------------------------------------ events hub
def test_hub_coalesces_and_streams():
    import asyncio
    from vaultweb.hub import EventHub

    async def go():
        hub = EventHub()
        hub.bind(asyncio.get_running_loop())
        got = []

        async def reader():
            async for frame in hub.stream(keepalive=0.2):
                if "data:" in frame:
                    got.append(frame)
                if len(got) >= 3:
                    return
        t = asyncio.create_task(reader())
        await asyncio.sleep(0.05)
        for _ in range(50):
            hub.publish("stage")
        hub.publish("message", text="hi")
        await asyncio.wait_for(t, 2)
        return got
    got = asyncio.run(go())
    assert len(got) == 3 and '"hello"' in got[0]
    assert sum('"stage"' in g for g in got) == 1 and any('"hi"' in g for g in got)


def test_events_endpoint_over_real_server(tmp_path):
    """SSE needs a real socket (TestClient cannot stream an endless response). Also covers ServerThread start/stop."""
    import threading
    import httpx
    from vaultweb.server import ServerThread

    st = AppState(mock=True)
    st.mock_timing = (0.0, 0.0)
    srv = ServerThread(create_app(st)).start()
    try:
        base = srv.url.rstrip("/")
        with httpx.Client(base_url=base, headers=H, timeout=10) as c:
            assert c.get("/healthz").json()["ok"]
            root = make_demo_vault(tmp_path / "v")
            seen = []
            with c.stream("GET", "/api/events") as r:
                assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
                threading.Timer(0.3, lambda: httpx.post(base + "/api/vault/open", json={"path": str(root), "mock": True}, headers=H)).start()
                for line in r.iter_lines():
                    if line.startswith("data:"):
                        seen.append(line)
                    if any('"state": "open"' in x for x in seen):
                        break
            assert any('"hello"' in x for x in seen) and any('"vault"' in x for x in seen)
            assert c.get("/api/status").json()["stats"]["notes"] == 23
    finally:
        t0 = time.time()
        srv.stop()
        assert time.time() - t0 < 6, "server did not shut down promptly with an open stream"
    assert not srv.thread.is_alive()
