"""Unit tests for the run.py self-destruct guard.

The guard refuses `--prebuilt <run-dir>/sandbox --label <same>` — the pattern
that once deleted a model's evidence. Covers refusal, non-destruction, and
that legitimate prebuilt grading still works (smoke-good, fresh label).
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
VENV_PY = ROOT / ".venv" / "bin" / "python"


def _run(label, prebuilt):
    return subprocess.run(
        [str(VENV_PY), "bench/run.py", "--scenario", "flappy-build",
         "--label", label, "--prebuilt", prebuilt],
        cwd=ROOT, capture_output=True, text=True, timeout=300)


def test_guard_refuses_self_prebuilt():
    """--prebuilt <run>/sandbox --label <run> must refuse and not delete."""
    label = "run-oc-35b-001"
    sandbox = ROOT / "runs" / label / "sandbox"
    if not sandbox.is_dir():
        # no evidence dir to protect; the guard's refusal path is untestable
        # without one — build a minimal fake run dir instead
        (ROOT / "runs" / label).mkdir(parents=True, exist_ok=True)
        (sandbox).mkdir(exist_ok=True)
        (sandbox / "marker.txt").write_text("x")
    before = sorted(str(p.relative_to(sandbox)) for p in sandbox.rglob("*") if p.is_file())

    r = _run(label, f"runs/{label}/sandbox")
    assert r.returncode != 0
    assert "refusing" in (r.stdout + r.stderr)

    after = sorted(str(p.relative_to(sandbox)) for p in sandbox.rglob("*") if p.is_file())
    assert before == after, "sandbox was deleted by the guard path"


def test_legit_prebuilt_still_grades():
    """A real grade with a fresh label still works (smoke-good → PASS)."""
    r = _run("verify-guard", "scripts/smoke_good")
    assert r.returncode == 0
    assert json.loads(r.stdout)["result"] == "PASS"
