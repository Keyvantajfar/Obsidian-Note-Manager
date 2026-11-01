"""Headless Textual pilot tests: boot, tabs, and an end-to-end workflow (route → tag → stage → apply → undo)."""
import pytest

pytest.importorskip("textual")

from vaultcore.demo import make_demo_vault
from vaultcore.mock import MockLLM
from vaultcore.session import Session
from vaulttui.app import VaultApp


@pytest.fixture
def app_session(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULTMGR_HOME", str(tmp_path / "cfg"))
    vault = make_demo_vault(tmp_path / "vault")
    return Session(vault, llm=MockLLM(delay=0.01, jitter=0.01)), vault


async def test_boot_and_all_tabs(app_session):
    s, _ = app_session
    app = VaultApp(s, workflow="all")
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause(0.5)
        for n in "123456789":
            await pilot.press(n)
            await pilot.pause(0.2)
        assert app.is_running


async def test_workflow_tag_stage_apply_undo(app_session):
    s, vault = app_session
    app = VaultApp(s, workflow="all")
    async with app.run_test(size=(190, 52)) as pilot:
        await pilot.pause(0.5)
        await pilot.press("3")
        await pilot.pause(0.3)
        await pilot.click("#og-propose")
        await pilot.pause(1.0)
        await pilot.click("#replace")
        await pilot.pause(0.4)
        await pilot.press("2")
        await pilot.pause(0.3)
        await pilot.click("#tg-run")
        for _ in range(80):                      # wait until the job exists, then until it finishes
            await pilot.pause(0.25)
            if s.jobs.jobs and not s.jobs.active():
                break
        assert len(s.stage.all()) > 0
        await pilot.press("5")                   # Stage is tab 5 in the ALL workspace
        await pilot.pause(0.4)
        await pilot.press("A")
        await pilot.pause(0.3)
        assert s.stage.all(status="accepted")
        notes = lambda: {p: p.read_bytes() for p in vault.rglob("*.md") if ".vaultmgr" not in p.parts}
        before = notes()
        await pilot.click("#st-apply")
        await pilot.pause(0.5)
        # dry run: nothing written yet
        assert notes() == before
        await pilot.click("#apply")
        await pilot.pause(0.6)
        assert s.batches()
        await pilot.press("escape")
        await pilot.pause(0.2)
        last = s.batches()[-1]
        s.undo(last.id)
        assert notes() == before


async def test_settings_llm_form_survives_refreshes_and_profile_switching(app_session):
    from textual.widgets import Input, Select
    from vaultcore.settings import LLMProfile
    s, _ = app_session
    s.profiles.upsert(LLMProfile(name="A", base_url="http://a/v1", model="ma"))
    s.profiles.upsert(LLMProfile(name="B", base_url="http://b/v1", model="mb"))
    s.profiles.active = "A"
    app = VaultApp(s, workflow="all")
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause(0.4)
        await pilot.press("9")
        await pilot.pause(0.5)
        url, name = app.query_one("#se-llm-url", Input), app.query_one("#se-llm-name", Input)
        assert (name.value, url.value) == ("A", "http://a/v1")
        url.value = "http://typed-but-unsaved/v1"           # user is editing
        for _ in range(5):                                   # unrelated settings events must not wipe or mix it
            s.emit("settings")
            await pilot.pause(0.7)
        assert (name.value, url.value) == ("A", "http://typed-but-unsaved/v1")
        app.query_one("#se-llm-profile", Select).value = "B"  # user picks another profile
        await pilot.pause(0.4)
        s.emit("settings")
        await pilot.pause(0.8)
        assert (name.value, url.value) == ("B", "http://b/v1")


async def test_settings_max_tokens_field_is_visible(app_session):
    from textual.widgets import Input
    s, _ = app_session
    app = VaultApp(s, workflow="all")
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause(0.5)                   # let the app finish booting before switching tabs
        await pilot.press("9")
        await pilot.pause(1.2)
        for wid in ("#se-llm-maxtok", "#se-llm-temp", "#se-llm-timeout", "#se-llm-retries"):
            w = app.query_one(wid, Input)
            for _ in range(40):                  # layout may lag on slow machines
                if w.region.height > 0 and w.region.width > 0:
                    break
                await pilot.pause(0.25)
            assert w.region.height > 0 and w.region.width > 0, wid



async def test_home_screen_and_workspaces(app_session):
    from textual.widgets import TabbedContent
    s, _ = app_session
    app = VaultApp(s)                                  # no workflow given → home first
    async with app.run_test(size=(170, 52)) as pilot:
        await pilot.pause(0.8)
        assert app.home_visible()
        expect = {"2": ("header", ["explorer", "tagger", "dates", "stage", "jobs", "tags", "rules", "settings"], False),
                  "3": ("organizer", ["explorer", "organizer", "stage", "jobs", "rules", "settings"], False),
                  "4": ("dates", ["explorer", "dates", "stage", "settings"], True),
                  "1": ("all", ["explorer", "tagger", "organizer", "dates", "stage", "jobs", "tags", "rules", "settings"], False)}
        for key, (wid, tabs, offline) in expect.items():
            await pilot.press(key)
            await pilot.pause(0.5)
            assert not app.home_visible() and app.workflow == wid and app.order == tabs
            assert s.offline is offline
            assert app.query_one("#tabs", TabbedContent).active in tabs
            await pilot.press("escape")
            await pilot.pause(0.3)
            assert app.home_visible()


async def test_date_fixer_workspace_is_offline_and_fixes_dates(tmp_path, monkeypatch):
    import os
    from datetime import datetime
    monkeypatch.setenv("VAULTMGR_HOME", str(tmp_path / "cfg"))
    v = tmp_path / "v"
    v.mkdir()
    n = v / "copied.md"
    n.write_text("---\ndate: 2026-10-30T22:00\n---\nbody\n", encoding="utf-8")
    ts = datetime(2023, 3, 1, 10, 0).timestamp()
    os.utime(n, (ts, ts))
    llm = MockLLM()
    s = Session(v, llm=llm)
    app = VaultApp(s, workflow="dates")
    async with app.run_test(size=(170, 52)) as pilot:
        await pilot.pause(0.8)
        assert s.offline
        await pilot.press("2")                           # Dates tab
        await pilot.pause(0.5)
        await pilot.press("F")                           # fix everything shown
        await pilot.pause(0.5)
        assert s.stage.all(status="accepted")
        await pilot.press("3")                           # Stage
        await pilot.pause(0.4)
        await pilot.click("#st-apply")
        await pilot.pause(0.5)
        await pilot.click("#apply")
        await pilot.pause(0.6)
    assert n.read_text(encoding="utf-8") == "---\ndate: 2023-03-01T10:00\n---\nbody\n"
    assert llm.calls == 0                                # no model call, ever
