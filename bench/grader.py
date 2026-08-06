"""DragonScale grader — grade one sandbox against a scenario.

Gates: hidden suite green, level 0 passable + replayable, git structure.
Everything else (visible tests, model's own tests, trace) is diagnostic.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / ".venv" / "bin" / "python"


def _run(cmd, cwd=None, env=None, timeout=600):
    try:
        r = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                           env=env, capture_output=True, text=True, timeout=timeout)
        return {"exit": r.returncode, "output": r.stdout, "stderr": r.stderr}
    except subprocess.TimeoutExpired:
        return {"exit": -1, "output": "", "stderr": f"TIMEOUT {timeout}s"}
    except Exception as e:  # noqa: BLE001
        return {"exit": -2, "output": "", "stderr": str(e)}


def _sha(path: Path) -> str:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _pytest_result(sandbox: Path, test_path: Path, timeout=600):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(sandbox)
    r = _run([VENV_PY, "-m", "pytest", str(test_path), "-q", "--no-header",
              "-p", "no:cacheprovider", "--tb=line"],
             cwd=test_path.parent, env=env, timeout=timeout)
    out = r["output"]
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    errors = int(m.group(1)) if (m := re.search(r"(\d+) error", out)) else 0
    return {"passed": passed, "failed": failed, "errors": errors,
            "exit": r["exit"], "tail": out[-4000:]}


def git_info(sandbox: Path):
    if not (sandbox / ".git").is_dir():
        return {"init": False, "commits": 0, "messages": [], "dirty": None}
    log = _run(["git", "-C", str(sandbox), "log", "--oneline", "-n", "100"])
    status = _run(["git", "-C", str(sandbox), "status", "--porcelain"])
    messages = [ln.strip() for ln in log["output"].splitlines() if ln.strip()]
    dirty = [ln.strip() for ln in status["output"].splitlines() if ln.strip()][:20]
    return {"init": True, "commits": len(messages), "messages": messages, "dirty": dirty}


def solve_via_import(sandbox: Path, seed: int):
    """Import the model's package and run the solver. Isolated import env."""
    from bench import solver  # local import to keep grader self-contained

    sys.path.insert(0, str(sandbox))
    try:
        import game.core as core
        from game.controller import GameController
    except Exception as e:  # noqa: BLE001
        return {"import_error": f"{type(e).__name__}: {e}"}
    finally:
        sys.path.pop(0)
        for k in [k for k in list(sys.modules) if k == "game" or k.startswith("game.")]:
            del sys.modules[k]

    try:
        factory = GameController
        return solver.solve_all(core, factory, seed=seed)
    except Exception as e:  # noqa: BLE001
        return {"solver_error": f"{type(e).__name__}: {e}"}


def grade(sandbox: Path, scenario: Path, label: str, seed: int = 42):
    ts = datetime.now(timezone.utc).isoformat()
    report = {
        "label": label, "timestamp": ts, "seed": seed,
        "versions": {
            "prompt": _sha(scenario / "prompt.md"),
            "reference": _sha(scenario / "fixture" / "reference.md"),
            "visible_suite": _sha(scenario / "fixture" / "tests" / "test_contract.py"),
            "hidden_suite": _sha(scenario / "hidden" / "test_hidden.py"),
        },
    }

    report["git"] = git_info(sandbox)

    visible = _pytest_result(sandbox, scenario / "fixture" / "tests" / "test_contract.py")
    report["visible_tests"] = {"passed": visible["passed"], "failed": visible["failed"],
                               "errors": visible["errors"], "exit": visible["exit"],
                               "tail": visible["tail"]}

    model_tests_path = sandbox / "tests" / "test_game.py"
    if model_tests_path.exists():
        mt = _pytest_result(sandbox, model_tests_path)
        report["model_tests"] = {"passed": mt["passed"], "failed": mt["failed"],
                                 "errors": mt["errors"], "exit": mt["exit"],
                                 "tail": mt["tail"]}
    else:
        report["model_tests"] = {"missing": True}

    hidden = _pytest_result(sandbox, scenario / "hidden" / "test_hidden.py")
    report["hidden_tests"] = {"passed": hidden["passed"], "failed": hidden["failed"],
                              "errors": hidden["errors"], "exit": hidden["exit"],
                              "tail": hidden["tail"]}

    report["solver"] = solve_via_import(sandbox, seed)

    # Trace (only when a dispatch happened)
    dispatch_log = sandbox.parent / "dispatch.stdout.log"
    if dispatch_log.exists():
        from bench import trace
        report["trace"] = trace.summarize(dispatch_log.read_text(errors="replace"))
    else:
        report["trace"] = None

    # ---- verdict ---------------------------------------------------------
    reasons = []
    hid = report["hidden_tests"]
    if hid.get("missing") or hid.get("errors") or hid.get("failed") or hid.get("passed", 0) == 0:
        reasons.append(f"hidden suite not green (passed={hid.get('passed')}, "
                       f"failed={hid.get('failed')}, errors={hid.get('errors')})")
    sol = report["solver"]
    if "import_error" in sol or "solver_error" in sol:
        reasons.append(f"solver could not load the game ({sol.get('import_error') or sol.get('solver_error')})")
    else:
        l0 = sol.get("level_0", {})
        if not l0.get("passable"):
            reasons.append(f"level 0 not passable (probe ended {l0.get('probe_ended')} "
                           f"at score {l0.get('probe_final_score')})")
        elif not l0.get("replay_ok"):
            reasons.append(f"level 0 path found but replay failed "
                           f"(final {l0.get('final_status')}, score {l0.get('final_score')})")
    git = report["git"]
    if not git.get("init"):
        reasons.append("no git repository")
    elif git.get("commits", 0) < 3:
        reasons.append(f"only {git.get('commits')} commits (< 3)")

    report["verdict"] = {"result": "PASS" if not reasons else "FAIL", "reasons": reasons}
    return report


def render_markdown(report: dict) -> str:
    lines = [f"# DragonScale run: {report['label']}",
             f"`{report['timestamp']}` · seed {report['seed']}",
             "",
             f"## Verdict: **{report['verdict']['result']}**",
             ""]
    if report["verdict"]["reasons"]:
        lines += ["Reasons:"]
        lines += [f"- {r}" for r in report["verdict"]["reasons"]]
        lines += [""]
    else:
        lines += ["All gates green.", ""]

    lines += ["## Versions", "```json", json.dumps(report["versions"], indent=2), "```", ""]
    lines += ["## Git", f"- init: {report['git'].get('init')}", 
              f"- commits: {report['git'].get('commits')}",
              f"- messages: {report['git'].get('messages')}",
              f"- dirty: {report['git'].get('dirty')}", ""]

    for name in ("visible_tests", "model_tests", "hidden_tests"):
        t = report[name]
        if "missing" in t:
            lines += [f"## {name}: MISSING", ""]
        else:
            lines += [f"## {name}: {t.get('passed')} passed, {t.get('failed')} failed, "
                      f"{t.get('errors')} errors (exit {t.get('exit')})", ""]
            if t.get("tail"):
                lines += ["```", t["tail"][-2500:], "```", ""]

    sol = report["solver"]
    if "import_error" in sol or "solver_error" in sol:
        lines += [f"## Solver: ERROR {sol.get('import_error') or sol.get('solver_error')}", ""]
    else:
        lines += ["## Solver (passability + replay)", ""]
        lines += ["| level | passable | path | replay | final status | score | probe |",
                  "|-------|----------|------|--------|--------------|-------|-------|"]
        for lv in ("level_0", "level_1", "level_2", "level_3"):
            s = sol[lv]
            lines += [f"| {lv} | {s['passable']} | {s['path_len']} | {s['replay_ok']} | "
                      f"{s.get('final_status')} | {s.get('final_score')} | "
                      f"{s.get('probe_ticks')} ticks → {s.get('probe_ended')} |"]
        lines += [""]

    if report.get("trace"):
        tr = report["trace"]
        lines += ["## Tool-call trace",
                  f"- total tool calls: {tr.get('total_calls')}",
                  f"- parser: {tr.get('parser')}",
                  f"- tokens: {tr.get('tokens')}",
                  f"- calls by tool: {tr.get('calls_by_tool')}",
                  f"- failures by tool: {tr.get('failures_by_tool')}",
                  ""]
    return "\n".join(lines)
