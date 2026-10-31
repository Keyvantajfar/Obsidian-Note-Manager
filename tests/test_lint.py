"""Catches NameError-class mistakes in code paths the other tests don't reach (e.g. UI buttons that need a real server)."""
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyflakes")


def test_no_undefined_names():
    src = Path(__file__).parent.parent / "src"
    out = subprocess.run([sys.executable, "-m", "pyflakes", str(src)], capture_output=True, text=True).stdout
    bad = [l for l in out.splitlines() if "undefined name" in l]
    assert not bad, "\n".join(bad)
