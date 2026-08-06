"""Visible contract tests for the GameController API.

These tests are VALUE-AGNOSTIC: they verify the shape and behavior of the
controller contract, not the specific physics constants (those come from
reference.md and are checked by a separate suite). They exist so you can
iterate against the API while building.

Run:  python3 -m pytest tests/test_contract.py -q
"""

import pytest

from game import core
from game.controller import GameController


@pytest.fixture
def ctl():
    c = GameController()
    c.reset(level=0, seed=42)
    return c


def test_contract_surface():
    c = GameController()
    for m in ("reset", "step", "state", "render", "set_speed", "play"):
        assert callable(getattr(c, m, None)), f"GameController.{m} missing"


def test_levels_shape_attributes():
    """LEVELS entries must expose their values as ATTRIBUTES (.name, .speed,
    ...), not dict keys. The task prompt specifies 'attributes'; the hidden
    suite and the passability solver access them as attributes."""
    assert isinstance(core.LEVELS, tuple)
    assert len(core.LEVELS) == 4
    for L in core.LEVELS:
        for attr in ("name", "speed", "gravity", "flap_velocity",
                     "gap", "pipe_spacing", "pipes_to_clear"):
            assert hasattr(L, attr), f"LEVELS entry missing attribute {attr!r}"
            assert not isinstance(getattr(L, attr), dict), \
                f"LEVELS entry attribute {attr!r} must not be a dict"


def test_reset_returns_none_and_state_ok(ctl):
    assert ctl.reset(level=0, seed=42) is None
    st = ctl.state()
    assert st["status"] == "RUNNING"
    assert st["tick"] == 0
    assert st["level"] == 0


def test_state_shape(ctl):
    st = ctl.state()
    assert set(st) >= {"bird", "pipes", "score", "status", "tick", "level"}
    assert set(st["bird"]) >= {"y", "velocity"}
    for p in st["pipes"]:
        assert set(p) >= {"x", "gap_y", "scored"}


def test_step_advances_tick(ctl):
    t0 = ctl.state()["tick"]
    ctl.step("NONE")
    assert ctl.state()["tick"] == t0 + 1


def test_step_returns_state_dict(ctl):
    st = ctl.step("NONE")
    assert isinstance(st, dict)
    assert st["tick"] == 1


def test_flap_changes_velocity(ctl):
    c1 = GameController(); c1.reset(0, 42)
    c2 = GameController(); c2.reset(0, 42)
    v_none = c1.step("NONE")["bird"]["velocity"]
    v_flap = c2.step("FLAP")["bird"]["velocity"]
    assert v_none != v_flap, "FLAP must change the bird's velocity"


def test_unknown_action_ignored(ctl):
    c1 = GameController(); c1.reset(0, 42)
    c2 = GameController(); c2.reset(0, 42)
    s_unknown = c1.step("TELEPORT")
    s_none = c2.step("NONE")
    assert s_unknown == s_none, "unknown action must behave like NONE"


def test_pause_freezes_world(ctl):
    ctl.step("PAUSE")
    assert ctl.state()["status"] == "PAUSED"
    frozen = ctl.state()
    ctl.step("NONE")
    ctl.step("NONE")
    assert ctl.state()["tick"] == frozen["tick"], "paused world must not advance"
    assert ctl.state()["status"] == "PAUSED"


def test_restart_resets_world(ctl):
    ctl.step("FLAP")
    ctl.step("FLAP")
    assert ctl.state()["tick"] > 0
    ctl.step("RESTART")
    assert ctl.state()["tick"] == 0
    assert ctl.state()["status"] == "RUNNING"


def test_render_deterministic(ctl):
    r1 = ctl.render()
    r2 = ctl.render()
    assert r1 == r2, "render() must be a pure function of state"


def test_render_stable_width(ctl):
    lines = ctl.render().splitlines()
    assert lines, "render must produce output"
    widths = {len(ln) for ln in lines}
    assert len(widths) == 1, "render must produce a stable-width frame"


def test_set_speed_does_not_break_physics(ctl):
    c1 = GameController(); c1.reset(0, 42)
    c2 = GameController(); c2.reset(0, 42)
    c2.set_speed(0.5)
    s1 = c1.step("FLAP")
    s2 = c2.step("FLAP")
    assert s1 == s2, "set_speed must not affect physics or state"
