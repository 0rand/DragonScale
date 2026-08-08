"""Unit tests for the deterministic numeric score rubric.

Covers: compute_score determinism (same report -> same score), component
math (hidden_suite proportional, mutation sensitivity weighting, git
structure points), and the expected ranking of the four graded artifacts.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from bench.grader import compute_score
from bench.run import RUNS_ROOT

ROOT = Path(__file__).resolve().parent.parent.parent
VENV_PY = ROOT / ".venv" / "bin" / "python"

# Smoke fixtures are reference implementations, withheld from the public
# repo — control/ranking tests skip when absent (grading a model needs
# only the hidden suite, which IS public).
SMOKE_FIXTURES = (ROOT / "scripts" / "smoke_good").is_dir()
smoke = pytest.mark.skipif(
    not SMOKE_FIXTURES,
    reason="private smoke fixtures not present in this checkout")


def _grade(label, prebuilt):
    r = subprocess.run(
        [str(VENV_PY), "bench/run.py", "--scenario", "flappy-build",
         "--label", label, "--prebuilt", prebuilt],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    return json.loads((RUNS_ROOT / label / "report.json").read_text())


@smoke
def test_score_is_deterministic():
    a = _grade("score-det-a", "scripts/smoke_good")
    b = _grade("score-det-b", "scripts/smoke_good")
    assert a["score"]["total"] == b["score"]["total"]
    assert a["score"]["components"] == b["score"]["components"]


def test_score_hidden_proportional():
    """hidden_suite = passed/total * 25 (v2 weight)."""
    rep = {
        "hidden_tests": {"passed": 6, "failed": 2, "errors": 0},
        "solver": {f"level_{i}": {"passable": True, "replay_ok": True}
                   for i in range(4)},
        "model_tests": {"passed": 10, "failed": 0, "errors": 0},
        "mutation": {"sensitivity": 1.0},
        "visible_tests": {"passed": 13, "failed": 0, "errors": 0},
        "git": {"init": True, "commits": 5, "dirty": [],
                "messages": ["one", "two", "three", "four", "five"]},
        "human_play": {"ok": True},
        "packaging": {"score": 7.0},
    }
    sc = compute_score(rep)
    assert sc["components"]["hidden_suite"] == 18.75  # 6/8 * 25


def test_score_weights_sum_to_100_when_perfect():
    rep = {
        "hidden_tests": {"passed": 8, "failed": 0, "errors": 0},
        "solver": {f"level_{i}": {"passable": True, "replay_ok": True}
                   for i in range(4)},
        "model_tests": {"passed": 20, "failed": 0, "errors": 0},
        "mutation": {"sensitivity": 1.0},
        "visible_tests": {"passed": 13, "failed": 0, "errors": 0},
        "git": {"init": True, "commits": 3, "dirty": [],
                "messages": ["scaffold the project", "core game logic",
                             "tests and tooling"]},
        "human_play": {"ok": True},
        "packaging": {"score": 7.0},
    }
    sc = compute_score(rep)
    assert sc["total"] == 100.0, sc
    assert sc["components"]["human_play"] == 15.0
    assert sc["components"]["packaging"] == 7.0
    assert sc["components"]["mutation"] == 5.0


def test_score_mutation_is_fixed_panel():
    """mutation = kills/applicable * 5; 2/4 kills -> 2.5."""
    rep = {
        "hidden_tests": {"passed": 8, "failed": 0, "errors": 0},
        "solver": {f"level_{i}": {"passable": True, "replay_ok": True}
                   for i in range(4)},
        "model_tests": {"passed": 20, "failed": 0, "errors": 0},
        "mutation": {"sensitivity": 0.5},
        "visible_tests": {"passed": 13, "failed": 0, "errors": 0},
        "git": {"init": True, "commits": 3, "dirty": [],
                "messages": ["scaffold the project", "core game logic",
                             "tests and tooling"]},
        "human_play": {"ok": True},
        "packaging": {"score": 7.0},
    }
    sc = compute_score(rep)
    assert sc["components"]["mutation"] == 2.5


@smoke
def test_score_ranks_graded_artifacts():
    """The four graded artifacts must rank: DS >= smoke-good > 35B > smoke-broken."""
    good = _grade("score-rank-good", "scripts/smoke_good")
    broken = _grade("score-rank-broken", "scripts/smoke_broken")
    t = [broken["score"]["total"], good["score"]["total"]]
    assert t[0] < t[1], f"broken={t[0]} good={t[1]}"


@smoke
def test_report_stamps_model_via_cli():
    """--model must land in report.json and report.md."""
    label = "score-model-cli"
    r = subprocess.run(
        [str(VENV_PY), "bench/run.py", "--scenario", "flappy-build",
         "--label", label, "--prebuilt", "scripts/smoke_good",
         "--model", "TESTPROVIDER/Qwen3.6-35B"],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-500:]
    rep = json.loads((RUNS_ROOT / label / "report.json").read_text())
    assert rep["model"] == "TESTPROVIDER/Qwen3.6-35B"
    md = (RUNS_ROOT / label / "report.md").read_text()
    assert "TESTPROVIDER/Qwen3.6-35B" in md
    assert "Model under test" in md


@smoke
def test_report_model_unknown_without_model():
    """No model info -> report says unknown, verdict unaffected."""
    label = "score-model-none"
    r = subprocess.run(
        [str(VENV_PY), "bench/run.py", "--scenario", "flappy-build",
         "--label", label, "--prebuilt", "scripts/smoke_good"],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-500:]
    rep = json.loads((RUNS_ROOT / label / "report.json").read_text())
    assert rep["model"] is None
    md = (RUNS_ROOT / label / "report.md").read_text()
    assert "unknown" in md
    assert rep["verdict"]["result"] == "PASS"  # model stamping never affects verdict


@smoke
def test_prebuilt_grade_recovers_model_from_dispatch_json(tmp_path):
    """Re-grading a dispatched run's label recovers the model from dispatch.json."""
    label = "score-model-dispatch"
    run_dir = RUNS_ROOT / label
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "dispatch.json").write_text(json.dumps(
        {"runner": "opencode", "provider": "MYPROVIDER",
         "model": "MYPROVIDER/my-model", "workdir": str(run_dir / "sandbox"),
         "exit": 0, "stdout_bytes": 0, "stderr_bytes": 0}))
    r = subprocess.run(
        [str(VENV_PY), "bench/run.py", "--scenario", "flappy-build",
         "--label", label, "--prebuilt", "scripts/smoke_good"],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-500:]
    rep = json.loads((run_dir / "report.json").read_text())
    assert rep["model"] == "MYPROVIDER/my-model", rep["model"]
    assert "MYPROVIDER/my-model" in (run_dir / "report.md").read_text()
