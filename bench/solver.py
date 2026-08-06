"""Passability solver for DragonScale.

Given a model's game package (imported as `game.core`) and controller,
prove that a level is actually completable:

  1. Probe — run the real game through the controller with a survivable
     bang-bang autopilot, recording per-tick pipe windows at BIRD_X plus
     score/status. The pipe layout is seed-determined and independent of
     bird actions, so the probe timeline is valid for any action sequence.
  2. BFS — search the (y, velocity) state space through the recorded
     timeline, using the documented integration model and the model's OWN
     constants (imported from game.core).
  3. Replay — run the found action path through a FRESH controller and
     require LEVEL_COMPLETE. A replay mismatch means the model's physics
     deviates from the documented tick order or is nondeterministic.
"""

from __future__ import annotations

BAND_PROBES = [13.5, 12.0, 11.0, 10.5]  # flap-above thresholds, tried in order
MAX_TICKS = 3000


def _probe(module, ctl, level_idx, seed, band, max_ticks=MAX_TICKS):
    """Run the real game; return per-step observations (post-step state)."""
    ctl.reset(level_idx, seed)
    events = []
    for _t in range(max_ticks):
        st = ctl.state()
        if st["status"] != "RUNNING":
            break
        action = "FLAP" if st["bird"]["y"] > band else "NONE"
        st2 = ctl.step(action)
        window = None
        for p in st2["pipes"]:
            if int(p["x"]) <= module.BIRD_X <= int(p["x"]) + module.PIPE_WIDTH - 1:
                window = p["gap_y"]
                break
        events.append({
            "win": window,
            "score": st2["score"],
            "status": st2["status"],
        })
        if st2["status"] == "LEVEL_COMPLETE":
            break
    return events


def _bfs(module, level_idx, events, y0, vel0, height):
    """Return (found: bool, path: list[str]|None) surviving all events."""
    dt = 1.0 / module.TICK_RATE
    lvl = module.LEVELS[level_idx]
    g = lvl.gravity
    fv = lvl.flap_velocity
    half = lvl.gap // 2
    frontier = {(round(y0, 6), round(vel0, 6)): []}
    for ev in events:
        if ev["status"] == "LEVEL_COMPLETE":
            for path in frontier.values():
                return True, path
        win = ev["win"]
        nxt = {}
        for (y, vel), path in frontier.items():
            for action, v in (("NONE", vel), ("FLAP", fv)):
                y2 = y + v * dt
                v2 = v + g * dt
                if y2 < 1.5 or y2 >= height - 2:
                    continue
                if win is not None and not (win - half <= y2 <= win + half):
                    continue
                key = (round(y2, 6), round(v2, 6))
                if key not in nxt:
                    nxt[key] = path + [action]
        frontier = nxt
        if not frontier:
            return False, None
    return False, None


def solve_level(module, factory, level_idx, seed):
    """Full solve+replay for one level. Returns a result dict."""
    events = None
    best = None
    for band in BAND_PROBES:
        ctl = factory()
        ev = _probe(module, ctl, level_idx, seed, band)
        if best is None or len(ev) > len(best):
            best = ev
        if ev and ev[-1]["status"] == "LEVEL_COMPLETE":
            events = ev
            break
    if events is None:
        events = best or []

    result = {
        "level": level_idx,
        "probe_ticks": len(events),
        "probe_ended": events[-1]["status"] if events else "no-events",
        "probe_final_score": events[-1]["score"] if events else 0,
    }

    if not events:
        result.update({"passable": False, "replay_ok": False, "path_len": 0,
                       "final_status": None, "final_score": 0})
        return result

    ctl0 = factory()
    ctl0.reset(level_idx, seed)
    st0 = ctl0.state()
    found, path = _bfs(module, level_idx, events,
                       st0["bird"]["y"], st0["bird"]["velocity"], st0["height"])
    result["passable"] = bool(found)
    if not found:
        result.update({"replay_ok": False, "path_len": 0,
                       "final_status": None, "final_score": 0})
        return result

    # Replay the found path through a fresh controller: verifies the model's
    # actual physics accepts the path (spec compliance + determinism).
    ctl1 = factory()
    ctl1.reset(level_idx, seed)
    final = None
    for a in path:
        final = ctl1.step(a)
    result["path_len"] = len(path)
    result["final_status"] = final["status"] if final else None
    result["final_score"] = final["score"] if final else 0
    result["replay_ok"] = bool(final and final["status"] == "LEVEL_COMPLETE")
    return result


def solve_all(module, factory, levels=(0, 1, 2, 3), seed=42):
    return {f"level_{lv}": solve_level(module, factory, lv, seed) for lv in levels}
