"""Integration: the two smoke controls through the real grader.

smoke_good (real flappsy core + adapter) must PASS all gates;
smoke_broken (gravity 14 + unseeded RNG) must FAIL the hidden suite.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
VENV_PY = ROOT / ".venv" / "bin" / "python"


def _grade(label, prebuilt):
    r = subprocess.run(
        [str(VENV_PY), "bench/run.py", "--scenario", "flappy-build",
         "--label", label, "--prebuilt", prebuilt],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    return json.loads(r.stdout)


def test_smoke_good_passes():
    assert _grade("pytest-good", "scripts/smoke_good")["result"] == "PASS"


def test_smoke_broken_fails():
    assert _grade("pytest-broken", "scripts/smoke_broken")["result"] == "FAIL"
