"""Hidden evaluation suite — exact values from reference.md §4.

Harness-owned. NOT shipped to the model's sandbox. Imported with
PYTHONPATH pointing at the model's working directory.

Checks:
- exact world constants and level definitions (reference.md §4.1-4.2)
- determinism: same (level, seed) -> identical replay (reference.md §4.6)
- seed sensitivity: different seeds -> different worlds
- render determinism
- floor/ceiling death semantics (reference.md §4.4)
"""

import sys

import game.core as core
from game.controller import GameController

# --- exact constants from reference.md §4.1 ---------------------------------

def test_world_constants():
    assert core.BIRD_X == 10
    assert core.PIPE_WIDTH == 4
    assert core.TICK_RATE == 20


# --- exact level definitions from reference.md §4.2 -------------------------

LEVEL_EXPECT = [
    # (name, speed, gravity, flap_velocity, gap, pipe_spacing, pipes_to_clear)
    ("Tutorial Puddle",        0.72, 12.0, -10.0, 12, 30, 5),
    ("Suburban Wind Tunnel",   1.05, 18.0,  -9.8,  9, 27, 7),
    ("Volcano Tax Audit",      1.35, 22.0, -10.8,  8, 24, 9),
    ("Neon Bankruptcy",        1.70, 26.0, -11.8,  7, 21, 11),
]


def test_level_count_and_order():
    assert len(core.LEVELS) == 4


def test_level_definitions_exact():
    for i, (name, speed, grav, flap, gap, spacing, pipes) in enumerate(LEVEL_EXPECT):
        L = core.LEVELS[i]
        assert L.name == name, f"level {i} name: {L.name!r} != {name!r}"
        assert L.speed == speed, f"level {i} speed: {L.speed} != {speed}"
        assert L.gravity == grav, f"level {i} gravity: {L.gravity} != {grav}"
        assert L.flap_velocity == flap, f"level {i} flap_velocity: {L.flap_velocity} != {flap}"
        assert L.gap == gap, f"level {i} gap: {L.gap} != {gap}"
        assert L.pipe_spacing == spacing, f"level {i} pipe_spacing: {L.pipe_spacing} != {spacing}"
        assert L.pipes_to_clear == pipes, f"level {i} pipes_to_clear: {L.pipes_to_clear} != {pipes}"


# --- determinism (reference.md §4.6) ----------------------------------------

def _trace(seed, steps=120, flap_mod=9, level=0):
    c = GameController()
    c.reset(level=level, seed=seed)
    out = []
    for i in range(steps):
        action = "FLAP" if i % flap_mod == 0 else "NONE"
        out.append(c.step(action))
    return out


def test_determinism_same_seed():
    t1 = _trace(seed=42)
    t2 = _trace(seed=42)
    assert t1 == t2, "same (level, seed) must replay identically"


def test_seed_changes_world():
    # Level 1 (no tutorial-forced pipes) with seeds 1/2: verified to differ.
    c1 = GameController(); c1.reset(1, seed=1)
    c2 = GameController(); c2.reset(1, seed=2)
    p1 = c1.state()["pipes"]
    p2 = c2.state()["pipes"]
    assert p1 != p2, "different seeds must produce different pipe layouts"


def test_render_deterministic_across_resets():
    def snap(seed):
        c = GameController(); c.reset(0, seed=seed)
        for i in range(30):
            c.step("FLAP" if i % 8 == 0 else "NONE")
        return c.render()
    assert snap(7) == snap(7)


# --- survival semantics (reference.md §4.4) ---------------------------------

def test_no_flap_ends_in_game_over():
    c = GameController()
    c.reset(0, seed=42)
    st = None
    for _ in range(2000):
        st = c.step("NONE")
        if st["status"] != "RUNNING":
            break
    assert st is not None and st["status"] == "GAME_OVER", (
        "a bird that never flaps must eventually hit the ground"
    )


def test_ceiling_death_possible():
    # Flap aggressively: the bird must rise and, with sustained flapping,
    # hit the ceiling (y < 1.5) rather than the ground.
    c = GameController()
    c.reset(0, seed=42)
    st = None
    ceiling_reached = False
    for _ in range(2000):
        st = c.step("FLAP")
        if st["bird"]["y"] < 1.5:
            ceiling_reached = True
            break
        if st["status"] != "RUNNING":
            break
    assert ceiling_reached, "sustained flapping must push the bird to the ceiling"
