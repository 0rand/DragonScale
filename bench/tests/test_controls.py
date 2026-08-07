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


def test_smoke_broken_seeded_fails_stably():
    """Seeded physics-wrong control: FAIL (hidden suite) with STABLE score.

    Unlike smoke_broken (unseeded RNG -> score wobbles by design), the
    seeded variant must grade identically every time — it is the
    determinism oracle for the grader.
    """
    a = _grade("pytest-broken-seed-a", "scripts/smoke_broken_seeded")
    b = _grade("pytest-broken-seed-b", "scripts/smoke_broken_seeded")
    sa = json.loads((ROOT / "runs" / "pytest-broken-seed-a" / "report.json").read_text())
    sb = json.loads((ROOT / "runs" / "pytest-broken-seed-b" / "report.json").read_text())
    assert a["result"] == "FAIL"
    assert b["result"] == "FAIL"
    assert sa["score"]["total"] == sb["score"]["total"], (sa["score"], sb["score"])
