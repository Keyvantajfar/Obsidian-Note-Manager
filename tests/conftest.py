import hashlib
import os
from pathlib import Path

import pytest

from vaultcore.demo import make_demo_vault
from vaultcore.mock import MockLLM
from vaultcore.session import Session


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULTMGR_HOME", str(tmp_path / "cfg"))


@pytest.fixture
def vault(tmp_path) -> Path:
    return make_demo_vault(tmp_path / "vault")


@pytest.fixture
def llm() -> MockLLM:
    return MockLLM()


@pytest.fixture
def session(vault, llm):
    s = Session(vault, llm=llm)
    s.scan()
    yield s
    s.cache.close()


def snapshot(root: Path) -> dict[str, str]:
    """rel path -> sha of bytes, for every file outside the .vaultmgr state dir (also empty dirs)."""
    out = {}
    for cur, dirs, files in os.walk(root):
        rel = os.path.relpath(cur, root).replace(os.sep, "/")
        if rel.startswith(".vaultmgr") or rel == ".vaultmgr":
            dirs[:] = []
            continue
        if not files and not dirs and rel != ".":
            out[rel + "/"] = "dir"
        for f in files:
            p = Path(cur) / f
            out[(rel + "/" if rel != "." else "") + f] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out
