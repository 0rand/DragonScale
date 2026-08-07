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
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import tomllib
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

# Fixed semantic mutant panel (Sol review 2026-08-07): each mutant is an
# independent sabotage anchored on an exact constant the hidden suite asserts
# (reference.md §4 values). One kill per mutant = the model's OWN tests catch
# it. A mutant whose anchor is absent from the model's core is "n/a" and
# excluded from the denominator. This replaces the old failed/total ratio,
# which rewarded test parametrization and cascading failures.
MUTANT_PANEL = [
    {"name": "gravity",         "find": r"12\.0", "repl": "14.0"},
    {"name": "flap_velocity",   "find": r"-10\.0", "repl": "-8.0"},
    {"name": "collision_ceiling", "find": r"1\.5", "repl": "2.5"},
    {"name": "rng_seed_mix",    "find": r"1009",  "repl": "1013"},
]


def _mutation_sensitivity(sandbox: Path, model_tests_path: Path) -> dict:
    """Run the model's own tests against a FIXED PANEL of core mutants.

    Each mutant is applied to a clean copy of the sandbox; a mutant is
    KILLED if the baseline suite passes and the mutant suite fails (>=1
    failure/error). sensitivity = kills / applicable_mutants.
    """
    if not model_tests_path.exists():
        return {"applicable": False, "reason": "no model tests",
                "kills": 0, "applicable_mutants": 0, "kills_by_mutant": {},
                "sensitivity": 0.0}
    core_p = sandbox / "game" / "core.py"
    if not core_p.exists():
        return {"applicable": False, "reason": "no game/core.py",
                "kills": 0, "applicable_mutants": 0, "kills_by_mutant": {},
                "sensitivity": 0.0}
    # Baseline must be green or mutation is vacuous.
    base = _pytest_result(sandbox, model_tests_path)
    if base["failed"] or base["errors"] or base["passed"] == 0:
        return {"applicable": False,
                "reason": f"baseline tests not green ({base['passed']}p/{base['failed']}f/{base['errors']}e)",
                "kills": 0, "applicable_mutants": 0, "kills_by_mutant": {},
                "sensitivity": 0.0}

    src = core_p.read_text()
    kills = 0
    applicable = 0
    kills_by_mutant = {}
    for mutant in MUTANT_PANEL:
        m = re.search(mutant["find"], src)
        if not m:
            kills_by_mutant[mutant["name"]] = "n/a"
            continue
        applicable += 1
        tmp = Path(tempfile.mkdtemp(prefix="dragonscale-mut-"))
        shutil.copytree(sandbox, tmp, dirs_exist_ok=True)
        try:
            mutated = src[:m.start()] + mutant["repl"] + src[m.end():]
            (tmp / "game" / "core.py").write_text(mutated)
            res = _pytest_result(tmp, tmp / "tests" / "test_game.py")
            killed = res["failed"] + res["errors"] > 0
            if killed:
                kills += 1
            kills_by_mutant[mutant["name"]] = "killed" if killed else "survived"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    sensitivity = round(kills / applicable, 4) if applicable else 0.0
    return {"applicable": True, "reason": "n/a",
            "kills": kills, "applicable_mutants": applicable,
            "kills_by_mutant": kills_by_mutant, "sensitivity": sensitivity}


def _packaging_check(sandbox: Path) -> dict:
    """Deliverable packaging (7 pts): pyproject valid + requires-python >= 3.11
    + zero runtime deps, README present, package imports cleanly."""
    detail = {}
    score = 0.0
    data = None
    pyproject = sandbox / "pyproject.toml"
    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        req = (data.get("project", {}) or {}).get("requires-python", "")
        detail["requires_python"] = req
        if re.match(r">=\s*3\.1[1-9]", req or ""):
            score += 2.0
        else:
            detail["requires_python_ok"] = False
        deps = (data.get("project", {}) or {}).get("dependencies", []) or []
        detail["deps"] = deps
        if not deps:
            score += 2.0
    except FileNotFoundError:
        detail["error"] = "no pyproject.toml"
    except Exception as e:  # noqa: BLE001
        detail["error"] = f"{type(e).__name__}: {e}"

    readme = sandbox / "README.md"
    detail["readme"] = readme.exists() and readme.stat().st_size > 0
    if detail["readme"]:
        score += 1.0

    try:
        sys.path.insert(0, str(sandbox))
        import game.core  # noqa: F401
        import game.controller  # noqa: F401
        import game.render  # noqa: F401
        detail["import"] = True
        score += 2.0
    except Exception as e:  # noqa: BLE001
        detail["import"] = False
        detail["import_error"] = f"{type(e).__name__}: {e}"
    finally:
        sys.path.pop(0)
        for k in [k for k in list(sys.modules) if k == "game" or k.startswith("game.")]:
            del sys.modules[k]

    return {"score": round(score, 2), "detail": detail}


# Bird glyphs across known renderers (reference 'B', DS GA/Laguna 'ᗧᗤ',
# ASCII fallbacks '>','v'). Renderer-agnostic flap detection.
_BIRD_GLYPHS = ("B", "\u15A7", "\u15A4", ">", "v")
_BIRD_COL = 10  # BIRD_X in the reference contract


def _bird_row_in_tail(tail: bytes) -> int | None:
    """Row index of the bird in the last frame of a byte tail, or None.

    Used for flap efficacy: compare the bird row before vs after sending
    'w'. Unparseable tails return None (caller treats as unverifiable, not
    a failure) so exotic renderers don't get false-FAILed.
    """
    text = tail.decode("utf-8", errors="replace")
    for sep in ("\x1b[2J\x1b[H", "\x1b[H\x1b[J", "\x1b[2J", "\x1b[H\x1b[2J"):
        if sep in text:
            text = text.rsplit(sep, 1)[-1]
    return _bird_row_in_lines(text.splitlines())


def _bird_row_in_lines(lines: list[str]) -> int | None:
    """Row index of the bird glyph in a frame's lines, or None."""
    for i, ln in enumerate(lines[2:-2]):  # skip header/ground lines
        for col in (_BIRD_COL, _BIRD_COL - 1, _BIRD_COL + 1):
            if len(ln) > col and ln[col] in _BIRD_GLYPHS:
                return i + 2
    return None


# Characters that are NOT pipe wall: sky, dots, fireworks, bird glyphs.
_BG_CHARS = set(" .*+x\u2022\u2726\u2739@\u1767\u1724>vB|")


def _next_pipe_gap_center(lines: list[str]) -> int | None:
    """Midpoint row of the next pipe's gap ahead of the bird, or None.

    Scans columns right of the bird for a column that has solid wall above
    AND below a contiguous background band (the gap). Background chars are
    space/dots/fireworks; anything else counts as wall. Header (rows 0-1),
    overlay (h-2) and ground (h-3+) are excluded.
    """
    if len(lines) < 8:
        return None
    width = len(lines[0])
    for col in range(_BIRD_COL + 2, width):
        band = [lines[r][col] if col < len(lines[r]) else " "
                for r in range(2, len(lines) - 3)]
        gaps = []
        i = 0
        while i < len(band):
            if band[i] in _BG_CHARS:
                j = i
                while j < len(band) and band[j] in _BG_CHARS:
                    j += 1
                gaps.append((i, j))
                i = j
            else:
                i += 1
        for a, b in gaps:
            if a > 0 and b < len(band):
                above = any(c not in _BG_CHARS for c in band[:a])
                below = any(c not in _BG_CHARS for c in band[b:])
                if above and below:
                    return 2 + (a + b) // 2
    return None


def _advance_binding_in(sandbox: Path) -> str:
    """Static scan for a level-advance key binding in the human loop.

    Reference.md §2: Enter advances to the next level. Reports whether the
    sandbox's human-play code binds a key (Enter/newline, NEXT_LEVEL, or an
    advance call) to progress past LEVEL_COMPLETE. Informational — the hard
    gate is behavioral (_lc_progress_check exercises the actual Enter path
    in-process; source markers alone can be dead code). Values:
    'enter-bind', 'next-level', 'advance-call', 'none', 'unreadable'.
    """
    try:
        hits = []
        for fn in ("game/__main__.py", "game/controller.py", "game/render.py"):
            p = sandbox / fn
            if not p.exists():
                continue
            src = p.read_text(encoding="utf-8", errors="replace")
            if re.search(r"ord\((10|13)\)|343|[\"']\\r[\"']|[\"']\\n[\"']", src):
                hits.append("enter-bind")
            if "NEXT_LEVEL" in src or "next_level" in src:
                hits.append("next-level")
            if re.search(r"\.advance\s*\(|advance_level|advance\s*\(", src):
                hits.append("advance-call")
        if not hits:
            return "none"
        return "+".join(dict.fromkeys(hits))
    except Exception:  # noqa: BLE001
        return "unreadable"


def _lc_progress_check(ctl) -> str:
    """Behavioral: can the game progress past LEVEL_COMPLETE via Enter?

    Source markers are NOT enough — a dead binding fools a source scan
    (35B v3: ``_key_to_action`` maps Enter → 'ENTER', but ``step('ENTER')``
    is unreachable dead code; the game froze "conforming" yet could never
    advance — found by manual playtest). Exercise the game's own Enter
    path in-process and require a real state change.

    Ladder of conventions: ``advance()`` method (smoke_good / reference),
    ``_map_key(stdscr, key)`` method (curses — DS GA), module-level
    ``_key_to_action`` resolver (35B), module-level ``key_map`` dict,
    then common ``step()`` action names. Returns 'advanced' | 'stuck'.
    """
    try:
        _before = ctl.state()
        _st_before = _before.get("status")
        _lv_before = _before.get("level", 0)
    except Exception:  # noqa: BLE001
        return "stuck"

    def _progressed() -> bool:
        try:
            _after = ctl.state()
            return (_after.get("status") != _st_before
                    or _after.get("level", 0) != _lv_before)
        except Exception:  # noqa: BLE001
            return False

    # 1. controller.advance() method (smoke_good / reference convention)
    try:
        _adv = getattr(ctl, "advance", None)
        if callable(_adv):
            _adv()
            if _progressed():
                return "advanced"
    except Exception:  # noqa: BLE001
        pass

    # 2. _map_key(stdscr, key) method (curses convention — DS GA)
    try:
        _mk = getattr(ctl, "_map_key", None)
        if callable(_mk):
            for _k in (10, 13, 343):
                _mk(None, _k)
                if _progressed():
                    return "advanced"
    except Exception:  # noqa: BLE001
        pass

    # 3. module-level key resolver (35B convention)
    try:
        import game.controller as _cm  # noqa: PLC0415

        _resolver = getattr(_cm, "_key_to_action", None)
        if callable(_resolver):
            for _k in ("\x0d", "\r", "\n", 10, 13, 343):
                try:
                    _act = _resolver(_k)
                except Exception:  # noqa: BLE001
                    continue
                if _act and _act not in ("NONE", "QUIT", "RESTART",
                                         "PAUSE", "FLAP"):
                    ctl.step(_act)
                    if _progressed():
                        return "advanced"
                    # Resolver produced an advance-ish action that did
                    # nothing — the binding is dead (35B defect class).
                    return "stuck"
    except Exception:  # noqa: BLE001
        pass

    # 4. module-level key map dict
    try:
        for _mod in (sys.modules.get("game.controller"),
                     sys.modules.get("game.__main__")):
            if _mod is None:
                continue
            for _attr in ("key_map", "KEY_MAP", "_key_map", "keys"):
                _km = getattr(_mod, _attr, None)
                if not isinstance(_km, dict):
                    continue
                for _k in (10, 13, 343, "\x0d", "\r", "\n"):
                    if _k not in _km:
                        continue
                    _h = _km[_k]
                    try:
                        if callable(_h):
                            _h(ctl)
                        else:
                            ctl.step(str(_h))
                    except Exception:  # noqa: BLE001
                        continue
                    if _progressed():
                        return "advanced"
    except Exception:  # noqa: BLE001
        pass

    # 5. fallback step() conventions
    for _act in ("ENTER", "NEXT_LEVEL", "ADVANCE", 10):
        try:
            ctl.step(_act)
            if _progressed():
                return "advanced"
        except Exception:  # noqa: BLE001
            continue

    return "stuck"


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

    # Ctrl+C responsiveness: a game that puts the terminal in raw mode
    # (ISIG off) TRAPS the human — Ctrl+C/Ctrl+Z are delivered as literal
    # bytes and ignored; the game cannot be interrupted and the terminal is
    # left broken. Check the pty's lflag directly (keep the slave fd open)
    # AND require the process to exit within 3s of a 0x03 byte.
    ctrlc_ok = False
    ctrlc_rc = None
    ctrlc_isig = False
    try:
        master3, slave3 = pty.openpty()
        proc3 = subprocess.Popen(
            [str(VENV_PY), "-m", "game"],
            cwd=str(sandbox), stdin=slave3, stdout=slave3, stderr=slave3,
            env=smoke_env, close_fds=True)
        end3 = time.monotonic() + 0.6
        while time.monotonic() < end3:
            r3, _, _ = select.select([master3], [], [], 0.1)
            if r3:
                try:
                    os.read(master3, 65536)
                except OSError:
                    break
            time.sleep(0.02)
        # Inspect the terminal the GAME is running in (slave side).
        attrs = termios.tcgetattr(slave3)
        ctrlc_isig = bool(attrs[3] & termios.ISIG)  # lflag index 3
        try:
            os.write(master3, b"\x03")
        except OSError:
            pass
        end3 = time.monotonic() + 3.0
        while time.monotonic() < end3 and proc3.poll() is None:
            r3, _, _ = select.select([master3], [], [], 0.1)
            if r3:
                try:
                    os.read(master3, 65536)
                except OSError:
                    break
            time.sleep(0.05)
        if proc3.poll() is None:
            proc3.kill()
            proc3.wait()
        ctrlc_rc = proc3.poll()
        ctrlc_ok = ctrlc_isig and ctrlc_rc is not None
        os.close(slave3)
        os.close(master3)
    except Exception as e:  # noqa: BLE001
        ctrlc_ok = False
        ctrlc_rc = None  # check unavailable

    # Flap efficacy: in a FRESH launch (bird alive), send 'w' EARLY and
    # verify the bird moves UP. The main launch's flap check only proves
    # the process didn't crash — it can't see whether the bird moved, and
    # by 1.8s the bird may already be dead (gravity 12-14 from center hits
    # the ground at ~1.2-1.3s). Input-dead games (Laguna v2: per-read raw
    # toggle leaves the tty in cooked mode, keys never delivered) fail
    # here: the bird keeps falling, so post_flap row > pre_flap row.
    flap_worked = None
    try:
        master4, slave4 = pty.openpty()
        proc4 = subprocess.Popen(
            [str(VENV_PY), "-m", "game"],
            cwd=str(sandbox), stdin=slave4, stdout=slave4, stderr=slave4,
            env=smoke_env, close_fds=True)
        os.close(slave4)
        out4 = b""
        pre4 = post4 = b""
        sent_w4 = False
        t0 = time.monotonic()
        while time.monotonic() - t0 < 2.0:
            r4, _, _ = select.select([master4], [], [], 0.05)
            if r4:
                try:
                    out4 += os.read(master4, 65536)
                except OSError:
                    break
            t4 = time.monotonic() - t0
            if t4 > 0.55 and not pre4:
                pre4 = out4[-3000:]
            if t4 > 0.65 and not sent_w4:
                try:
                    os.write(master4, b"w")
                    sent_w4 = True
                except OSError:
                    pass
            if t4 > 1.05 and sent_w4 and not post4:
                post4 = out4[-3000:]
        if proc4.poll() is None:
            proc4.kill()
            proc4.wait()
        os.close(master4)
        pre_row = _bird_row_in_tail(pre4)
        post_row = _bird_row_in_tail(post4)
        if pre_row is not None and post_row is not None:
            flap_worked = post_row < pre_row  # bird moved UP after 'w'
    except Exception:  # noqa: BLE001
        flap_worked = None  # unverifiable — do not fail on parse/spawn miss

    # ── Level-complete progression: does the game ever END? ──────────
    # reference.md: step() must NOT advance a non-RUNNING world; the human
    # loop advances a completed level on Enter. A game that KEEPS
    # SIMULATING at LEVEL_COMPLETE (like the 27B crown game: physics keeps
    # ticking under LEVEL_COMPLETE, score climbs, 90 random fireworks
    # reshuffled every frame = the user's "hailstorm") violates the
    # contract and NEVER settles — the "never ends" defect. Probe
    # (deterministic, in-process): autopilot the sandbox's OWN controller
    # to LEVEL_COMPLETE, then verify the world freezes (conforming) or
    # auto-advances. None = unverifiable (autopilot can't complete) —
    # never a failure.
    lc_advances = lc_how = None
    lc_detail = "unverifiable (autopilot did not reach LEVEL_COMPLETE)"
    try:
        # Purge any cached 'game' modules — the probe runs in-process and
        # a PREVIOUS sandbox's controller would otherwise be reused
        # (sys.path insert does NOT clear sys.modules), silently grading
        # the wrong game (the smoke_good-false-positive trap).
        for _k in [k for k in list(sys.modules)
                   if k == "game" or k.startswith("game.")]:
            del sys.modules[_k]
        sys.path.insert(0, str(sandbox))
        from game.controller import GameController  # noqa: PLC0415
        sys.path.pop(0)
        for _seed in (0, 42):
            try:
                _ctl = GameController()
                try:
                    _ctl.reset(level=0, seed=_seed)
                except TypeError:
                    _ctl.reset(0)
                _done = False
                _st = None
                for _t in range(900):
                    _st = _ctl.state()
                    _status = _st.get("status")
                    if _status in ("LEVEL_COMPLETE", "WON"):
                        _done = True
                        break
                    if _status != "RUNNING":
                        break
                    _nxt = min((p for p in _st.get("pipes", [])
                                if p["x"] + 4 > 10),
                               key=lambda p: p["x"], default=None)
                    _ctl.step("FLAP" if (_nxt is not None
                                         and _st["bird"]["y"] > _nxt["gap_y"])
                               else "NONE")
                if not _done or _st is None:
                    continue
                if _status == "WON":
                    lc_advances, lc_how = True, "won"
                    lc_detail = "game ends with WON"
                    break
                # LEVEL_COMPLETE reached — does the world settle?
                _snap = (_st["tick"], _st["score"],
                         round(_st["bird"]["y"], 3),
                         tuple(round(p["x"], 1) for p in _st["pipes"]))
                _settled = True
                for _ in range(120):  # 6s at 20Hz
                    _st2 = _ctl.step("NONE")
                    _s2 = _st2.get("status")
                    if _s2 not in ("LEVEL_COMPLETE", "WON"):
                        lc_advances, lc_how = True, "auto"
                        lc_detail = f"auto-advanced ({_s2})"
                        _settled = False
                        break
                    _snap2 = (_st2["tick"], _st2["score"],
                              round(_st2["bird"]["y"], 3),
                              tuple(round(p["x"], 1) for p in _st2["pipes"]))
                    if _snap2 != _snap:
                        _settled = False
                        break
                if lc_advances is not None:
                    break
                if not _settled:
                    lc_advances, lc_how = False, None
                    lc_detail = ("world keeps simulating at LEVEL_COMPLETE "
                                 "(never settles; step() must not advance a "
                                 "non-RUNNING world per reference.md)")
                else:
                    # Freezes at completion (conforming settle). Now
                    # verify the game can PROGRESS past it — Enter must
                    # advance to the next level (reference.md §2). Source
                    # markers alone are not enough: a dead binding fools
                    # _advance_binding_in (35B v3 froze "conforming" but
                    # its Enter handler was unreachable dead code — found
                    # by manual playtest, 2026-08-07).
                    _prog = _lc_progress_check(_ctl)
                    if _prog == "advanced":
                        lc_advances, lc_how = True, "freeze"
                        lc_detail = ("freezes at LEVEL_COMPLETE (conforming); "
                                     "Enter advances (behavioral)")
                    else:
                        _adv_binding = _advance_binding_in(sandbox)
                        lc_advances, lc_how = False, None
                        lc_detail = ("freezes at LEVEL_COMPLETE but Enter does "
                                     "not advance (game cannot progress; "
                                     f"source markers: {_adv_binding})")
                break
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        lc_advances, lc_how = None, None
        lc_detail = "unverifiable (probe error)"

    elapsed = int((time.monotonic() - start) * 1000)
    return {"ok": rc == 0 and alive_after_flap and sent_q and overflow == 0
                  and idle_progressed and ctrlc_ok
                  and flap_worked is not False
                  and lc_advances is not False,
            "exit": rc, "sent_w": sent_w, "alive_after_flap": alive_after_flap,
            "sent_q": sent_q, "drained_bytes": drained, "ms": elapsed,
            "overflow_writes": overflow, "small_terminal_ok": overflow == 0,
            "idle_progressed": idle_progressed, "sig1_len": len(sig1), "sig2_len": len(sig2),
            "ctrlc_ok": ctrlc_ok, "ctrlc_exit": ctrlc_rc, "ctrlc_isig": ctrlc_isig,
            "flap_worked": flap_worked,
            "lc_advances": lc_advances, "lc_how": lc_how, "lc_detail": lc_detail}


def compute_score(report: dict) -> dict:
    """Deterministic 0-100 rubric from the graded report. No LLM.

    v2 weights (Sol review 2026-08-07): terminal usability is now 15pts and
    a HARD gate; packaging/harness integration 7pts; mutation is a fixed
    mutant panel (kills/applicable). hidden 25 / passability 12 / replay 8 /
    contract 8 / own tests 5 / mutation 5 / git 15 / human play 15 /
    packaging 7 = 100.
    """
    c = {}

    hid = report["hidden_tests"]
    hid_total = hid.get("passed", 0) + hid.get("failed", 0) + hid.get("errors", 0)
    c["hidden_suite"] = round(hid.get("passed", 0) / hid_total * 25, 2) if hid_total else 0.0

    sol = report["solver"]
    if "import_error" in sol or "solver_error" in sol:
        c["passability"], c["replay"] = 0.0, 0.0
    else:
        lvls = [sol[f"level_{i}"] for i in range(4) if f"level_{i}" in sol]
        n = max(len(lvls), 1)
        c["passability"] = round(sum(1 for l in lvls if l.get("passable")) / n * 12, 2)
        c["replay"] = round(sum(1 for l in lvls if l.get("replay_ok")) / n * 8, 2)

    mt = report["model_tests"]
    if mt.get("missing"):
        c["own_tests"], c["mutation"] = 0.0, 0.0
    else:
        mt_total = mt.get("passed", 0) + mt.get("failed", 0) + mt.get("errors", 0)
        c["own_tests"] = round(mt.get("passed", 0) / mt_total * 5, 2) if mt_total else 0.0
        c["mutation"] = round(report.get("mutation", {}).get("sensitivity", 0.0) * 5, 2)

    vis = report["visible_tests"]
    vis_total = vis.get("passed", 0) + vis.get("failed", 0) + vis.get("errors", 0)
    c["contract"] = round(vis.get("passed", 0) / vis_total * 8, 2) if vis_total else 0.0

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
    c["human_play"] = 15.0 if hp.get("ok") else 0.0

    c["packaging"] = round(report.get("packaging", {}).get("score", 0.0), 2)

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

    # Mutation: do the model's OWN tests catch the fixed mutant panel?
    report["mutation"] = _mutation_sensitivity(sandbox, sandbox / "tests" / "test_game.py")

    # Human-play smoke: can a human actually launch + quit the game?
    report["human_play"] = _human_play_smoke(sandbox)

    # Packaging / harness integration: pyproject, README, clean import.
    report["packaging"] = _packaging_check(sandbox)

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

    # v2 hard gate (Sol review 2026-08-07): an unplayable terminal game must
    # FAIL even with perfect physics. Human-play smoke covers launch, idle
    # time progression, quit, small-terminal overflow, and the Ctrl+C trap.
    hp = report.get("human_play", {})
    if not hp.get("ok"):
        if hp.get("error"):
            reasons.append(f"game not human-playable (spawn failed: {hp.get('error')})")
        else:
            bits = []
            if not hp.get("idle_progressed"):
                bits.append("frozen (time only advances on keypress)")
            if hp.get("overflow_writes"):
                bits.append(f"small-terminal overflow ({hp.get('overflow_writes')} writes)")
            if hp.get("ctrlc_isig") is False:
                bits.append("Ctrl+C trap (raw mode, ISIG off)")
            if hp.get("flap_worked") is False:
                bits.append("flap key dead (bird did not move up on 'w')")
            if hp.get("lc_advances") is False:
                bits.append("level-complete progression: "
                            + hp.get("lc_detail", "game never advances past LEVEL_COMPLETE"))
            if hp.get("exit") != 0:
                bits.append(f"no clean quit (exit {hp.get('exit')})")
            reasons.append("game not human-playable: " + "; ".join(bits) if bits
                           else "game not human-playable")

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
              f"- flap efficacy: {hp.get('flap_worked')} "
              f"(bird moved UP after 'w'; None = unverifiable render)",
              f"- level-complete progression: {hp.get('lc_advances')} "
              f"({hp.get('lc_detail')})",
              f"- quit key ('q'): sent={hp.get('sent_q')}",
              f"- idle time progression: {hp.get('idle_progressed')} "
              f"(frame {'changed' if hp.get('idle_progressed') else 'SAME'} with no input)",
              f"- Ctrl+C responsiveness: {hp.get('ctrlc_ok')} "
              f"(ISIG {'on' if hp.get('ctrlc_isig') else 'OFF=raw-mode trap'}, "
              f"exit {hp.get('ctrlc_exit')})",
              f"- small-terminal overflow: {hp.get('overflow_writes')} writes "
              f"(ok={hp.get('small_terminal_ok')})",
              ""]

    mut = report.get("mutation", {})
    lines += ["## Mutation sensitivity (fixed panel)",
              f"- applicable: {mut.get('applicable')} ({mut.get('reason', 'n/a')})",
              f"- kills: {mut.get('kills')} / {mut.get('applicable_mutants')} "
              f"applicable mutants",
              f"- by mutant: {mut.get('kills_by_mutant')}",
              f"- sensitivity: {mut.get('sensitivity')} (×5 pts)",
              ""]

    pkg = report.get("packaging", {})
    lines += ["## Packaging / harness integration",
              f"- score: {pkg.get('score')} / 7",
              f"- detail: {pkg.get('detail')}",
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
