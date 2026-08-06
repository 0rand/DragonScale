"""Passability solver for DragonScale.

Given a model's game package (imported as `game.core`) and controller,
prove that a level is actually completable:

  1. Probe — run the real game through the controller with a gap-aiming
     autopilot, recording per-tick pipe windows at BIRD_X plus score/status.
     The pipe layout is seed-determined and independent of bird actions,
     so the recorded timeline is valid for any action sequence.
  2. Growing horizon — the gap-aiming probe may die on brutal levels. When
     it does, BFS the known timeline, replay the surviving path, and keep
     probing past the previous death point. Each iteration extends the
     timeline; converges when the timeline reaches a completion status (LEVEL_COMPLETE|WON) or BFS
     can no longer survive it.
  3. BFS — exact (y, velocity) state-space search through the timeline
     using the documented integration model and the model's OWN constants.
     Parent-pointer paths, quantized dedupe (no list copying).
  4. Replay — run the final path through a FRESH controller and require
     COMPLETE (LEVEL_COMPLETE|WON). A replay mismatch means the model's physics deviates
     from the documented tick order or is nondeterministic.
"""

from __future__ import annotations

BAND = 13.5  # hover target when no pipe is approaching
MAX_TICKS = 3000
MAX_HORIZON_ITERATIONS = 14



COMPLETE_STATUSES = ("LEVEL_COMPLETE", "WON")


def _is_complete(status: str) -> bool:
    """Terminal success statuses per the GameController contract."""
    return status in COMPLETE_STATUSES

def _record_event(module, st2):
    window = None
    for p in st2["pipes"]:
        if int(p["x"]) <= module.BIRD_X <= int(p["x"]) + module.PIPE_WIDTH - 1:
            window = p["gap_y"]
            break
    return {"win": window, "score": st2["score"], "status": st2["status"]}


def _choose_action(module, st, band):
    """Gap-aiming autopilot: aim for the nearest approaching pipe's gap."""
    lookahead = max(45, module.BIRD_X + 40)
    approaching = [p for p in st["pipes"] if module.BIRD_X <= p["x"] < lookahead]
    if approaching:
        target = min(approaching, key=lambda p: p["x"])["gap_y"]
        return "FLAP" if st["bird"]["y"] > target else "NONE"
    return "FLAP" if st["bird"]["y"] > band else "NONE"


def _probe_from(module, ctl, band, max_ticks, events):
    """Record events from the controller's current position until death/completion."""
    for _t in range(max_ticks):
        st = ctl.state()
        if st["status"] != "RUNNING":
            break
        st2 = ctl.step(_choose_action(module, st, band))
        events.append(_record_event(module, st2))
        if _is_complete(st2["status"]):
            break
    return events


def _bfs(module, level_idx, events, y0, vel0, height):
    """Return (survives_all, completed, path|None).

    - survives_all: some trajectory survives every event in the timeline.
    - completed: that trajectory reaches a completion status (LEVEL_COMPLETE|WON) event.
    - path: the action sequence (length == len(events) when survives_all).

    Flappy is a TIME-INDEXED system: the same (y, vel) at different ticks
    faces different pipes, so first-arrival does NOT dominate later
    arrivals. Dedupe is per layer (one tick's frontier), never global.
    Parent pointers are kept per layer; the path is reconstructed by
    walking layers backward.
    """
    dt = 1.0 / module.TICK_RATE
    lvl = module.LEVELS[level_idx]
    g = lvl.gravity
    fv = lvl.flap_velocity
    half = lvl.gap // 2

    def key(y, v):
        return (round(y, 1), round(v, 1))

    start_key = key(y0, vel0)
    frontier = {start_key}
    exact = {start_key: (y0, vel0)}
    parent_layers = [{start_key: (None, None)}]  # layer i: child_key -> (prev_key, action)

    for ev in events:
        win = ev["win"]
        nxt = set()
        nxt_exact = {}
        nxt_parents = {}
        for k in frontier:
            y, v = exact[k]
            for action, vv in (("NONE", v), ("FLAP", fv)):
                y2 = y + vv * dt
                v2 = vv + g * dt
                if y2 < 1.5 or y2 >= height - 2:
                    continue
                if win is not None and not (win - half <= y2 <= win + half):
                    continue
                k2 = key(y2, v2)
                if k2 not in nxt_parents:
                    nxt_parents[k2] = (k, action)
                    nxt_exact[k2] = (y2, v2)
                    nxt.add(k2)
        frontier = nxt
        exact = nxt_exact
        parent_layers.append(nxt_parents)
        if not frontier:
            return False, False, None
        if _is_complete(ev["status"]):
            # the bird survives the tick that completes the level
            k = next(iter(frontier))
            path = []
            for layer in reversed(parent_layers):
                prev, action = layer[k]
                if action is not None:
                    path.append(action)
                k = prev
            path.reverse()
            return True, True, path
    return True, False, _reconstruct_path(parent_layers, next(iter(frontier)))


def _reconstruct_path(parent_layers, k):
    path = []
    for layer in reversed(parent_layers):
        prev, action = layer[k]
        if action is not None:
            path.append(action)
        k = prev
    path.reverse()
    return path


def solve_level(module, factory, level_idx, seed):
    """Full solve+replay for one level. Returns a result dict."""
    ctl = factory()
    ctl.reset(level_idx, seed)
    st0 = ctl.state()
    events: list = []
    _probe_from(module, ctl, BAND, MAX_TICKS, events)

    for _iter in range(MAX_HORIZON_ITERATIONS):
        if events and _is_complete(events[-1]["status"]):
            break
        prev_len = len(events)
        survives, completed, path = _bfs(module, level_idx, events,
                                         st0["bird"]["y"], st0["bird"]["velocity"],
                                         st0["height"])
        if completed:
            break  # path to completion found
        if not survives:
            break  # genuinely stuck: no trajectory survives the known timeline
        # extend: replay the surviving path, then continue probing past the
        # old death point. Pipe layout is deterministic, so the replayed
        # events match; the continuation records NEW ticks.
        ctl = factory()
        ctl.reset(level_idx, seed)
        events = []
        for a in path:
            events.append(_record_event(module, ctl.step(a)))
        _probe_from(module, ctl, BAND, MAX_TICKS, events)
        if len(events) <= prev_len:
            break  # no progress — stop the loop

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

    survives, completed, path = _bfs(module, level_idx, events,
                                     st0["bird"]["y"], st0["bird"]["velocity"],
                                     st0["height"])
    result["passable"] = bool(completed)
    if not completed:
        result.update({"replay_ok": False, "path_len": 0,
                       "final_status": None, "final_score": 0,
                       "survives_timeline": survives})
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
    result["replay_ok"] = bool(final and _is_complete(final["status"]))
    return result


def solve_all(module, factory, levels=(0, 1, 2, 3), seed=42):
    return {f"level_{lv}": solve_level(module, factory, lv, seed) for lv in levels}
