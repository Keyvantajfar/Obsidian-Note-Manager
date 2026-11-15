"""Things that break silently: the static UI's wiring, the tab registry, and the crash log."""
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vaultcore.workflows import WORKFLOWS
from vaultweb import crashlog
from vaultweb.app import create_app, static_dir
from vaultweb.state import AppState

H = {"X-VaultMgr": "1"}
STATIC = Path(static_dir())


def test_index_references_exist():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="/static/([^"]+)"', html)
    assert refs, "index.html should reference its assets under /static/"
    for r in refs:
        assert (STATIC / r).is_file(), f"index.html references a missing file: {r}"


def test_js_imports_resolve():
    for f in STATIC.rglob("*.js"):
        for rel in re.findall(r"""from\s+['"](\.{1,2}/[^'"]+)['"]""", f.read_text(encoding="utf-8")):
            assert (f.parent / rel).resolve().is_file(), f"{f.relative_to(STATIC)} imports a missing module: {rel}"


def test_every_workflow_tab_is_registered_and_built():
    src = (STATIC / "js" / "tabs" / "index.js").read_text(encoding="utf-8")
    registered = dict(re.findall(r"^\s{2}(\w+): \{[^\n]*create: (\w+)", src, re.M))
    for wid, w in WORKFLOWS.items():
        for tab in w["tabs"]:
            assert tab in registered, f"workflow {wid} lists tab {tab} that the UI does not register"
    assert not any(v.startswith("stub") for v in registered.values()), "a tab is still a placeholder"


def test_internal_error_is_logged_and_returned(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULTMGR_HOME", str(tmp_path / "cfg"))
    crashlog._installed = False
    crashlog.logger().handlers.clear()
    assert crashlog.install() == tmp_path / "cfg" / "logs" / "vaultmgr.log"
    app = create_app(AppState(mock=True))

    @app.get("/api/_boom")
    async def boom():
        raise RuntimeError("kaboom")

    with TestClient(app, base_url="http://127.0.0.1", headers=H, raise_server_exceptions=False) as c:
        r = c.get("/api/_boom")
    assert r.status_code == 500 and "kaboom" in r.json()["error"]
    for h in crashlog.logger().handlers:
        h.flush()
    text = crashlog.log_path().read_text(encoding="utf-8")
    assert "kaboom" in text and "/api/_boom" in text
    for h in list(crashlog.logger().handlers):
        h.close()
        crashlog.logger().removeHandler(h)
    crashlog._installed = False


def test_app_info_reports_log_path(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULTMGR_HOME", str(tmp_path / "cfg"))
    with TestClient(create_app(AppState(mock=True)), base_url="http://127.0.0.1", headers=H) as c:
        assert c.get("/api/app").json()["log"].endswith("vaultmgr.log")


def test_dashed_subcommand_aliases(capsys):
    from vaultweb.cli import main
    assert main(["--version"]) == 0
    with pytest.raises(SystemExit) as e:
        main(["--cli", "--help"])
    assert e.value.code == 0 and "vaultmgr-cli" in capsys.readouterr().out
