"""DragonScale grader — grade one sandbox against a scenario.

Gates: hidden suite green, level 0 passable + replayable, git structure.
Everything else (visible tests, model's own tests, trace) is diagnostic.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import termios
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


# --------------------------------------------------------------------------
# Deterministic numeric score (no LLM in the loop).
# Weights sum to 100. Every component is computed from artifacts.
# --------------------------------------------------------------------------

def _mutation_sensitivity(sandbox: Path, model_tests_path: Path) -> dict:
    """Run the model's own tests against a MUTATED copy of its core.

    Mutation: level-0 gravity 12.0 -> 14.0 (the smoke_broken sabotage).
    Tests that fail under mutation have teeth; a suite that still passes
    against sabotaged physics is vacuous. sensitivity = (failed+errors)/total.
    """
    import shutil
    import tempfile

    if not model_tests_path.exists():
        return {"applicable": False, "reason": "no model tests",
                "sensitivity": 0.0, "total": 0, "failed": 0}
    tmp = Path(tempfile.mkdtemp(prefix="dragonscale-mut-"))
    shutil.copytree(sandbox, tmp, dirs_exist_ok=True)
    try:
        core_p = tmp / "game" / "core.py"
        if not core_p.exists():
            return {"applicable": False, "reason": "no game/core.py",
                    "sensitivity": 0.0, "total": 0, "failed": 0}
        src = core_p.read_text()
        # Sabotage: level-0 gravity 12.0 -> 14.0. Works for positional Level()
        # tuples (flappsy), dict LEVELS, and dataclass forms — the value
        # 12.0 must appear literally (hidden suite asserts it).
        m = re.search(r"12\.0", src)
        if not m:
            return {"applicable": False, "reason": "12.0 not found",
                    "sensitivity": 0.0, "total": 0, "failed": 0}
        mutated = src[:m.start()] + "14.0" + src[m.end():]
        core_p.write_text(mutated)
        res = _pytest_result(tmp, tmp / "tests" / "test_game.py")
        total = res["passed"] + res["failed"] + res["errors"]
        failed = res["failed"] + res["errors"]
        return {"applicable": True,
                "sensitivity": round(failed / total, 4) if total else 0.0,
                "total": total, "failed": failed}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _human_play_smoke(sandbox: Path) -> dict:
    """Launch `python3 -m game` in a PTY, prove keyboard input + screen, quit.

    Sequence (with continuous output drain — a pty buffer fills and blocks
    the child's write if we don't read, so the child never reaches its input
    read; a real human terminal consumes output continuously):
      0. idle progression: 1s with NO input — a live game must advance time
         (bird falls, pipes move -> rendered frame changes). A game whose
         loop only steps on keypress renders the SAME frame forever (frozen)
         and is unplayable for a human.
      1. send 'w' (flap) — the key loop must consume it and stay alive
      2. send 'q' — clean quit, exit 0
    Plus a SMALL-TERMINAL (10 rows) pass whose raw output is scanned for
    frame overflow (frames taller than the terminal scroll and stack —
    the "3 parallel realities" bug). Curses games adapt; hardcoded-frame
    games overflow. Proves: curses/keyboard init, a live key loop, time
    progression, a working quit path, and that the screen fits the terminal.
    """
    import pty
    import select
    import time

    from bench import ansi_model

    # Don't let the grader's own game launch pollute the sandbox: python3 -m game
    # would write __pycache__/*.pyc INTO the sandbox, which then dirties the git
    # tree we grade (untracked files → git gate loses points). Bytecode must not
    # be written by the grader.
    smoke_env = {**os.environ, "TERM": "xterm-256color", "PYTHONDONTWRITEBYTECODE": "1"}

    start = time.monotonic()
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            [str(VENV_PY), "-m", "game"],
            cwd=str(sandbox), stdin=slave, stdout=slave, stderr=slave,
            env=smoke_env, close_fds=True)
    except Exception as e:  # noqa: BLE001
        os.close(master)
        os.close(slave)
        return {"ok": False, "error": f"spawn failed: {e}", "ms": int((time.monotonic() - start) * 1000)}
    os.close(slave)

    sent_w = sent_q = False
    alive_after_flap = False
    drained = 0
    raw = b""
    sig1 = sig2 = b""
    idle_progressed = False
    while time.monotonic() - start < 15:
        r, _, _ = select.select([master], [], [], 0.1)
        if r:
            try:
                chunk = os.read(master, 65536)
                drained += len(chunk)
                raw += chunk
            except OSError:
                break
        t = time.monotonic() - start
        # Idle-progression window: capture frame at 0.5s and 1.5s, NO input.
        if t > 0.5 and not sig1:
            sig1 = raw[-1200:]
        if t > 1.5 and sig1 and not sig2:
            sig2 = raw[-1200:]
            idle_progressed = sig1 != sig2
        if t > 1.8 and not sent_w:
            try:
                os.write(master, b"w")
                sent_w = True
            except OSError:
                pass
        if t > 2.4 and sent_w and not sent_q:
            alive_after_flap = proc.poll() is None
            try:
                os.write(master, b"q")
                sent_q = True
            except OSError:
                pass
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    if proc.poll() is None:
        proc.kill()
        proc.wait()
    rc = proc.poll()
    os.close(master)

    # Small-terminal overflow check: 10 rows, short run.
    overflow = 0
    try:
        master2, slave2 = pty.openpty()
        fcntl.ioctl(slave2, termios.TIOCSWINSZ, struct.pack("HHHH", 10, 80, 0, 0))
        proc2 = subprocess.Popen(
            [str(VENV_PY), "-m", "game"],
            cwd=str(sandbox), stdin=slave2, stdout=slave2, stderr=slave2,
            env=smoke_env, close_fds=True)
        os.close(slave2)
        out2 = b""
        end2 = time.monotonic() + 2.0
        while time.monotonic() < end2:
            r2, _, _ = select.select([master2], [], [], 0.1)
            if r2:
                try:
                    out2 += os.read(master2, 65536)
                except OSError:
                    break
            time.sleep(0.02)
        proc2.kill()
        os.close(master2)
        overflow = ansi_model.count_overflow_writes(out2, rows=10)
    except Exception as e:  # noqa: BLE001
        overflow = -1  # check unavailable

    elapsed = int((time.monotonic() - start) * 1000)
    return {"ok": rc == 0 and alive_after_flap and sent_q and overflow == 0 and idle_progressed,
            "exit": rc, "sent_w": sent_w, "alive_after_flap": alive_after_flap,
            "sent_q": sent_q, "drained_bytes": drained, "ms": elapsed,
            "overflow_writes": overflow, "small_terminal_ok": overflow == 0,
            "idle_progressed": idle_progressed, "sig1_len": len(sig1), "sig2_len": len(sig2)}


def compute_score(report: dict) -> dict:
    """Deterministic 0-100 rubric from the graded report. No LLM."""
    c = {}

    hid = report["hidden_tests"]
    hid_total = hid.get("passed", 0) + hid.get("failed", 0) + hid.get("errors", 0)
    c["hidden_suite"] = round(hid.get("passed", 0) / hid_total * 30, 2) if hid_total else 0.0

    sol = report["solver"]
    if "import_error" in sol or "solver_error" in sol:
        c["passability"], c["replay"] = 0.0, 0.0
    else:
        lvls = [sol[f"level_{i}"] for i in range(4) if f"level_{i}" in sol]
        n = max(len(lvls), 1)
        c["passability"] = round(sum(1 for l in lvls if l.get("passable")) / n * 15, 2)
        c["replay"] = round(sum(1 for l in lvls if l.get("replay_ok")) / n * 10, 2)

    mt = report["model_tests"]
    if mt.get("missing"):
        c["own_tests"], c["mutation"] = 0.0, 0.0
    else:
        mt_total = mt.get("passed", 0) + mt.get("failed", 0) + mt.get("errors", 0)
        c["own_tests"] = round(mt.get("passed", 0) / mt_total * 8, 2) if mt_total else 0.0
        c["mutation"] = round(report.get("mutation", {}).get("sensitivity", 0.0) * 7, 2)

    vis = report["visible_tests"]
    vis_total = vis.get("passed", 0) + vis.get("failed", 0) + vis.get("errors", 0)
    c["contract"] = round(vis.get("passed", 0) / vis_total * 10, 2) if vis_total else 0.0

    g = report["git"]
    if not g.get("init"):
        c["git"] = 0.0
    else:
        commits = g.get("commits", 0)
        msgs = g.get("messages", [])
        trivial = sum(1 for m in msgs
                      if len(m.strip()) < 8 or m.strip().lower() in ("wip", "init", "update", "commit"))
        nontriv_ratio = 1.0 if not msgs else (len(msgs) - min(trivial, len(msgs))) / len(msgs)
        c["git"] = round(
            3.0                                # repo initialized
            + 6.0 * min(commits, 3) / 3        # >= 3 logical commits
            + (3.0 if not g.get("dirty") else 0.0)   # clean working tree
            + (3.0 if nontriv_ratio >= 0.5 else 0.0),  # meaningful messages
            2)

    hp = report.get("human_play", {})
    c["human_play"] = 5.0 if hp.get("ok") else 0.0

    total = round(sum(c.values()), 2)
    return {"total": total, "components": c}


def grade(sandbox: Path, scenario: Path, label: str, seed: int = 42,
          model: str | None = None):
    ts = datetime.now(timezone.utc).isoformat()
    report = {
        "label": label, "model": model, "timestamp": ts, "seed": seed,
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

    # Mutation sensitivity: do the model's OWN tests catch a physics sabotage?
    report["mutation"] = _mutation_sensitivity(sandbox, sandbox / "tests" / "test_game.py")

    # Human-play smoke: can a human actually launch + quit the game?
    report["human_play"] = _human_play_smoke(sandbox)

    # Trace (only when a dispatch happened)
    dispatch_log = sandbox.parent / "dispatch.stdout.log"
    if dispatch_log.exists():
        from bench import trace
        report["trace"] = trace.summarize(dispatch_log.read_text(errors="replace"))
    else:
        report["trace"] = None

    # Deterministic numeric score (rubric, no LLM)
    report["score"] = compute_score(report)

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
             f"**Model under test:** {report.get('model') or 'unknown'}",
             "",
             f"## Verdict: **{report['verdict']['result']}**",
             ""]
    if report["verdict"]["reasons"]:
        lines += ["Reasons:"]
        lines += [f"- {r}" for r in report["verdict"]["reasons"]]
        lines += [""]
    else:
        lines += ["All gates green.", ""]

    sc = report.get("score", {})
    lines += ["## Score (deterministic rubric, 0-100, no LLM)",
              f"- **total: {sc.get('total')} / 100**", ""]
    for k, v in sc.get("components", {}).items():
        lines += [f"- {k}: {v}"]
    lines += [""]

    lines += ["## Versions", "```json", json.dumps(report["versions"], indent=2), "```", ""]
    lines += ["## Git", f"- init: {report['git'].get('init')}", 
              f"- commits: {report['git'].get('commits')}",
              f"- messages: {report['git'].get('messages')}",
              f"- dirty: {report['git'].get('dirty')}", ""]

    hp = report.get("human_play", {})
    lines += ["## Human-play smoke",
              f"- ok: {hp.get('ok')} (exit {hp.get('exit')}, drained {hp.get('drained_bytes')}B, "
              f"{hp.get('ms')}ms)",
              f"- flap key ('w'): sent={hp.get('sent_w')}, alive after flap="
              f"{hp.get('alive_after_flap')}",
              f"- quit key ('q'): sent={hp.get('sent_q')}",
              f"- idle time progression: {hp.get('idle_progressed')} "
              f"(frame {'changed' if hp.get('idle_progressed') else 'SAME'} with no input)",
              f"- small-terminal overflow: {hp.get('overflow_writes')} writes "
              f"(ok={hp.get('small_terminal_ok')})",
              ""]

    mut = report.get("mutation", {})
    lines += ["## Mutation sensitivity",
              f"- applicable: {mut.get('applicable')} ({mut.get('reason', 'n/a')})",
              f"- sensitivity: {mut.get('sensitivity')} ({mut.get('failed')}/{mut.get('total')} "
              f"own tests fail under gravity 12->14)",
              ""]

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
