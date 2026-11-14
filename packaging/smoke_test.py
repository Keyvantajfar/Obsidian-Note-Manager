"""Smoke-test a built app:  python packaging/smoke_test.py PATH_TO_EXECUTABLE
Starts `<exe> serve --demo`, waits for /healthz, checks the demo vault opened and the page is served,
runs one AI job with the fake model, then quits through the API and checks the process really ends."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

exe = sys.argv[1]
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
base = f"http://127.0.0.1:{port}"
env = dict(os.environ, VAULTMGR_HOME=tempfile.mkdtemp(prefix="vaultmgr-smoke-"))
p = subprocess.Popen([exe, "serve", "--demo", "--port", str(port)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def get(path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.read()


def post(path, data=None):
    req = urllib.request.Request(base + path, data=json.dumps(data or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-VaultMgr": "1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fail(msg):
    p.kill()
    out = p.stdout.read().decode(errors="replace") if p.stdout else ""
    print(f"SMOKE TEST FAILED: {msg}\n{out}", file=sys.stderr)
    sys.exit(1)


try:
    for _ in range(60):
        try:
            if json.loads(get("/healthz"))["ok"]:
                break
        except Exception:
            if p.poll() is not None:
                fail(f"process exited early with code {p.returncode}")
            time.sleep(1)
    else:
        fail("server did not answer /healthz in 60 s")
    for _ in range(30):
        st = json.loads(get("/api/status"))
        if st.get("open") and st["stats"]["notes"] == 23:
            break
        time.sleep(0.5)
    else:
        fail(f"demo vault did not open: {st}")
    page = get("/").decode()
    if "window.__VM__" not in page:
        fail("index page was not served (static files missing from the bundle?)")
    if b"Vault Manager" not in get("/static/index.html"):
        fail("static file missing")
    job = post("/api/ai/run", {"header": True, "route": False})["job"]
    if not job:
        fail("the AI job did not start")
    for _ in range(60):
        jobs = json.loads(get("/api/jobs"))["jobs"]
        if jobs and not any(j["active"] for j in jobs):
            break
        time.sleep(0.5)
    else:
        fail("the AI job did not finish")
    if jobs[0]["errors"]:
        fail(f"the AI job had errors: {jobs[0]}")
    post("/api/quit")
    try:
        code = p.wait(timeout=20)
    except subprocess.TimeoutExpired:
        fail("process did not exit after /api/quit")
    if code != 0:
        fail(f"exit code {code}")
    print("smoke test OK:", exe)
finally:
    if p.poll() is None:
        p.kill()
