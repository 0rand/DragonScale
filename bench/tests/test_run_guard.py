"""Unit tests for the run.py self-destruct guard + --workdir CLI.

The guard refuses `--prebuilt <run-dir>/sandbox --label <same>` — the pattern
that once deleted a model's evidence. Covers refusal, non-destruction, and
that legitimate prebuilt grading still works (smoke-good, fresh label).
`--workdir` must create the sandbox at the requested path.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
VENV_PY = ROOT / ".venv" / "bin" / "python"

# Smoke fixtures are reference implementations, withheld from the public
# repo — tests that grade them skip when absent (grading a model needs
# only the hidden suite, which IS public).
SMOKE_FIXTURES = (ROOT / "scripts" / "smoke_good").is_dir()
smoke = pytest.mark.skipif(
    not SMOKE_FIXTURES,
    reason="private smoke fixtures not present in this checkout")


def _run(label, prebuilt, extra=None):
    cmd = [str(VENV_PY), "bench/run.py", "--scenario", "flappy-build",
           "--label", label, "--prebuilt", prebuilt]
    if extra:
        cmd += extra
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)


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


@smoke
def test_legit_prebuilt_still_grades():
    """A real grade with a fresh label still works (smoke-good → PASS)."""
    r = _run("verify-guard", "scripts/smoke_good")
    assert r.returncode == 0
    assert json.loads(r.stdout)["result"] == "PASS"


@smoke
def test_workdir_creates_sandbox_at_requested_path(tmp_path):
    """--workdir must create the sandbox there, not under runs/<label>/."""
    target = tmp_path / "sandbox-here"
    assert not target.exists()
    r = _run("verify-workdir", "scripts/smoke_good",
             extra=["--workdir", str(target)])
    assert r.returncode == 0, r.stderr[-500:]
    assert target.is_dir()
    assert (target / "reference.md").exists()
    # report still lands under runs/<label>/
    assert (ROOT / "runs" / "verify-workdir" / "report.json").exists()
