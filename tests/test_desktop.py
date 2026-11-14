"""Desktop launcher: window lifecycle with a fake browser, CLI, `serve` smoke test."""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
from pathlib import Path

import pytest

from vaultweb import launcher
from vaultweb.server import free_port

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="uses a shell script as the fake browser")


def fake_browser(body: str) -> str:
    p = Path(tempfile.mkdtemp()) / "fakebrowser"
    p.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


FETCH = f'{sys.executable} -c "import sys,urllib.request;open(sys.argv[1],\'w\').write(urllib.request.urlopen(sys.argv[2]).read().decode())"'


def test_find_chromium_override_and_command(monkeypatch):
    monkeypatch.setenv("VAULTMGR_BROWSER", sys.executable)
    assert launcher.find_chromium() == sys.executable
    cmd = launcher.chromium_cmd("/x/chrome", "http://127.0.0.1:5000/", Path("/p"))
    assert "--app=http://127.0.0.1:5000/" in cmd and f"--user-data-dir={Path('/p')}" in cmd


@posix_only
def test_window_process_exit_ends_server_and_page_knows_it_is_desktop(monkeypatch):
    out = Path(tempfile.mkdtemp()) / "page.html"
    fake = fake_browser(f'''
        url="${{1#--app=}}"
        {FETCH} "{out}" "$url"
        sleep 5
    ''')
    monkeypatch.setenv("VAULTMGR_BROWSER", fake)
    t0 = time.time()
    assert launcher.run(port=free_port(), mode="app") == 0
    assert 4 <= time.time() - t0 < 15
    html = out.read_text()
    assert '"desktop": true' in html and re.search(r'"token": "[\w-]{10,}"', html)


@posix_only
def test_goodbye_beacon_ends_a_lingering_browser(monkeypatch):
    monkeypatch.setattr(launcher, "BYE_GRACE", 0.5)
    out = Path(tempfile.mkdtemp()) / "page.html"
    fake = fake_browser(f'''
        url="${{1#--app=}}"
        {FETCH} "{out}" "$url"
        tok=$({sys.executable} -c "import re,sys;print(re.search(r'\\"token\\": \\"([^\\"]+)',open(sys.argv[1]).read()).group(1))" "{out}")
        {sys.executable} -c "import sys,urllib.request;urllib.request.urlopen(urllib.request.Request(sys.argv[1]+'desktop/bye?t='+sys.argv[2],method='POST'))" "$url" "$tok"
        sleep 60
    ''')
    monkeypatch.setenv("VAULTMGR_BROWSER", fake)
    t0 = time.time()
    assert launcher.run(port=free_port(), mode="app") == 0
    assert time.time() - t0 < 20                       # did not wait for the 60 s browser (it was terminated)


@posix_only
def test_idle_window_is_given_up_on(monkeypatch):
    monkeypatch.setattr(launcher, "IDLE_LIMIT", 1.0)
    fake = fake_browser("sleep 60\n")
    monkeypatch.setenv("VAULTMGR_BROWSER", fake)
    t0 = time.time()
    assert launcher.run(port=free_port(), mode="app") == 0
    assert time.time() - t0 < 20


@posix_only
def test_quit_button_ends_the_app(monkeypatch):
    fake = fake_browser(f'''
        url="${{1#--app=}}"
        {sys.executable} -c "import sys,urllib.request;urllib.request.urlopen(urllib.request.Request(sys.argv[1]+'api/quit',data=b'{{}}',method='POST',headers={{'X-VaultMgr':'1'}}))" "$url"
        sleep 60
    ''')
    monkeypatch.setenv("VAULTMGR_BROWSER", fake)
    t0 = time.time()
    assert launcher.run(port=free_port(), mode="app") == 0
    assert time.time() - t0 < 20


@posix_only
def test_browser_that_fails_to_start_falls_back_to_tab(monkeypatch):
    monkeypatch.setenv("VAULTMGR_BROWSER", fake_browser("exit 3\n"))
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda u: opened.append(u))
    monkeypatch.setattr(launcher, "IDLE_LIMIT", 1.0)
    assert launcher.run(port=free_port(), mode="auto") == 0
    assert opened and opened[0].startswith("http://127.0.0.1:")


def test_cli_version_and_unknown_vault(capsys):
    from vaultweb.cli import main
    assert main(["version"]) == 0 and "vaultmgr" in capsys.readouterr().out
    assert main(["serve", "/definitely/not/a/vault"]) == 2
    assert main(["gui", "--import-config", "/nope.json"]) == 2


def test_cli_delegates(monkeypatch):
    import vaultcore.cli as vc
    seen = {}
    monkeypatch.setattr(vc, "main", lambda argv: seen.setdefault("argv", argv) and 0)
    from vaultweb.cli import main
    assert main(["cli", "somevault", "status"]) == 0 and seen["argv"] == ["somevault", "status"]


def test_serve_subprocess_smoke(tmp_path):
    """What the CI does on every built binary: start it, ask /healthz, open the demo vault, quit via the API."""
    port = free_port()
    env = dict(os.environ, VAULTMGR_HOME=str(tmp_path / "cfg"))
    p = subprocess.Popen([sys.executable, "-m", "vaultweb", "serve", "--demo", "--port", str(port)], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                if json.load(urllib.request.urlopen(base + "/healthz", timeout=1))["ok"]:
                    break
            except Exception:
                time.sleep(0.1)
        else:
            raise AssertionError("server did not start: " + (p.stdout.read1().decode() if p.poll() is not None else ""))
        for _ in range(50):
            st = json.load(urllib.request.urlopen(base + "/api/status"))
            if st["open"] and st["stats"]["notes"] == 23:
                break
            time.sleep(0.1)
        else:
            raise AssertionError(st)
        urllib.request.urlopen(urllib.request.Request(base + "/api/quit", data=b"{}", method="POST", headers={"X-VaultMgr": "1"}))
        assert p.wait(timeout=15) == 0
    finally:
        if p.poll() is None:
            p.kill()
