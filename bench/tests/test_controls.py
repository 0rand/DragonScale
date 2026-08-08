"""Integration: the two smoke controls through the real grader.

smoke_good (real flappsy core + adapter) must PASS all gates;
smoke_broken (gravity 14 + unseeded RNG) must FAIL the hidden suite.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
VENV_PY = ROOT / ".venv" / "bin" / "python"
sys.path.insert(0, str(ROOT))
from bench.run import RUNS_ROOT  # noqa: E402

# The smoke fixtures are reference implementations, withheld from the
# public repo (benchmark integrity). On a fresh clone they are absent —
# the control tests skip; grading a model does not need them.
SMOKE_FIXTURES = (ROOT / "scripts" / "smoke_good").is_dir()
smoke = pytest.mark.skipif(
    not SMOKE_FIXTURES,
    reason="private smoke fixtures not present in this checkout")


def _grade(label, prebuilt):
    r = subprocess.run(
        [str(VENV_PY), "bench/run.py", "--scenario", "flappy-build",
         "--label", label, "--prebuilt", prebuilt],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    return json.loads(r.stdout)


@smoke
def test_smoke_good_passes():
    assert _grade("pytest-good", "scripts/smoke_good")["result"] == "PASS"


@smoke
def test_smoke_broken_fails():
    assert _grade("pytest-broken", "scripts/smoke_broken")["result"] == "FAIL"


@smoke
def test_smoke_good_lc_probe_cached_module():
    """Regression: the level-complete probe must not reuse a cached
    'game' module from a previous sandbox (same-process trap). Running
    smoke_good twice in a row must still report lc=True (freezes)."""
    from bench.grader import _human_play_smoke
    hp1 = _human_play_smoke(Path("scripts/smoke_good"))
    hp2 = _human_play_smoke(Path("scripts/smoke_good"))
    assert hp1["lc_advances"] is not False
    assert hp2["lc_advances"] is not False
    assert hp1["ok"] and hp2["ok"]


@smoke
def test_smoke_stuck_lc_fails_progression():
    """A game that freezes at LEVEL_COMPLETE but cannot advance on Enter
    must FAIL the level-complete progression gate (35B v3 defect class).

    Regression: the gate used a source-only binding scan
    (_advance_binding_in), which dead code fools — the 35B v3 game
    mapped Enter -> 'ENTER' and even called step('ENTER'), but step()
    early-returned before the ENTER branch. Manual playtest found the
    game stuck at level complete (only R/Q work). The gate now
    exercises the Enter path behaviorally (_lc_progress_check) and must
    catch this fixture, which passes everything else (hidden suite,
    physics, git, overflow-free render)."""
    r = _grade("pytest-stuck-lc", "scripts/smoke_stuck_lc")
    assert r["result"] == "FAIL"
    reasons = " ".join(r["reasons"])
    assert "level-complete" in reasons, r["reasons"]
    assert "Enter does not advance" in reasons, r["reasons"]
    # The ONLY failure must be the progression gate — the fixture passes
    # the hidden suite, physics, git, and is overflow-free.
    assert "overflow" not in reasons, r["reasons"]
    assert "hidden" not in reasons and "suite" not in reasons, r["reasons"]


def test_bird_row_in_tail():
    """_bird_row_in_tail finds the bird glyph near column 10 in a frame."""
    from bench.grader import _bird_row_in_tail

    # 24-row frame; bird 'B' at row 12, col 10; pipes elsewhere.
    lines = [" " * 24 for _ in range(24)]
    lines[12] = " " * 10 + "B" + " " * 13
    frame = "\x1b[2J\x1b[H" + "\n".join(lines) + "\n"
    assert _bird_row_in_tail(frame.encode()) == 12

    # Unicode bird glyphs (DS GA / Laguna).
    lines2 = [" " * 24 for _ in range(24)]
    lines2[8] = " " * 10 + "\u15A7" + " " * 13  # ᗧ
    frame2 = "\x1b[2J\x1b[H" + "\n".join(lines2) + "\n"
    assert _bird_row_in_tail(frame2.encode()) == 8

    # Header/ground skipped — glyph in top 2 lines is NOT the bird.
    lines3 = [" " * 10 + "B" + " " * 13] + [" " * 24 for _ in range(23)]
    frame3 = "\x1b[2J\x1b[H" + "\n".join(lines3) + "\n"
    assert _bird_row_in_tail(frame3.encode()) is None

    # No bird at all -> None.
    assert _bird_row_in_tail(b"no bird here") is None


def test_report_kind_rendering():
    """Model-run reports lead with score + defect profile (NO aggregate
    verdict headline); control reports keep the PASS/FAIL verdict.

    The verdict is a deployment gate, not a capability measurement —
    a single binary made 35B 'FAIL' while Primo played it and called it
    'really good'. Controls (smoke fixtures) keep the verdict because
    it is the load-bearing grader self-check."""
    from bench.grader import render_markdown

    base = {
        "label": "x", "model": "m", "timestamp": "t", "seed": 42,
        "kind": "model",
        "score": {"total": 79.5,
                  "components": {"hidden_suite": 25.0, "human_play": 0.0}},
        "verdict": {"result": "FAIL",
                    "reasons": ["game not human-playable: level-complete "
                                "progression: Enter does not advance"]},
        "versions": {}, "git": {}, "human_play": {}, "mutation": {},
        "packaging": {}, "visible_tests": {"missing": True},
        "model_tests": {"missing": True}, "hidden_tests": {"missing": True},
        "solver": {lv: {"passable": True, "path_len": 0, "replay_ok": True,
                        "final_status": "LEVEL_COMPLETE", "final_score": 0,
                        "probe_ticks": 0, "probe_ended": "x"}
                   for lv in ("level_0", "level_1", "level_2", "level_3")},
        "trace": None,
    }

    md = render_markdown(base)
    assert "## Score: **79.5 / 100**" in md
    assert "## Verdict:" not in md
    assert "### Defect profile" in md
    assert "Enter does not advance" in md
    # Score components still present, just not headed by a verdict.
    assert "## Score components" in md
    assert "- hidden_suite: 25.0" in md

    base["kind"] = "control"
    md2 = render_markdown(base)
    assert "## Verdict: **FAIL**" in md2
    assert "### Defect profile" not in md2


@smoke
def test_smoke_broken_seeded_fails_stably():
    """Seeded physics-wrong control: FAIL (hidden suite) with STABLE score.

    Unlike smoke_broken (unseeded RNG -> score wobbles by design), the
    seeded variant must grade identically every time — it is the
    determinism oracle for the grader.
    """
    a = _grade("pytest-broken-seed-a", "scripts/smoke_broken_seeded")
    b = _grade("pytest-broken-seed-b", "scripts/smoke_broken_seeded")
    sa = json.loads((RUNS_ROOT / "pytest-broken-seed-a" / "report.json").read_text())
    sb = json.loads((RUNS_ROOT / "pytest-broken-seed-b" / "report.json").read_text())
    assert a["result"] == "FAIL"
    assert b["result"] == "FAIL"
    assert sa["score"]["total"] == sb["score"]["total"], (sa["score"], sb["score"])
