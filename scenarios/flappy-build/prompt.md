# BUILD FLAPPSY — deterministic terminal flappy-bird

You are implementing **Flappsy**, a complete, playable, fully deterministic
terminal flappy-bird game, from the design reference provided in this
directory. This is a from-scratch build: the directory contains only the
reference document, a contract test suite, and this task.

## Deliverables (create these in the current directory)

1. `game/core.py` — pure game logic. Must expose module-level constants:
   `BIRD_X`, `PIPE_WIDTH`, `TICK_RATE`, and `LEVELS` (a tuple of exactly 4
   level definitions, each with attributes `name`, `speed`, `gravity`,
   `flap_velocity`, `gap`, `pipe_spacing`, `pipes_to_clear`). Immutable
   state, pure functions, seeded RNG only.
2. `game/controller.py` — `GameController`, the single API surface (see
   contract below). Tests, automation, and human play all drive this one
   object.
3. `game/render.py` — deterministic ASCII rendering, a pure function of
   state.
4. `game/__main__.py` — human play mode: `python3 -m game` starts a
   keyboard session using the SAME controller. Space/W/Up = flap,
   P = pause, R = restart, Q = quit.
5. `tests/test_game.py` — your own pytest test suite. It must pass and it
   must be meaningful (see reference.md §10.1).
6. `pyproject.toml` — project metadata, `requires-python >= 3.11`,
   stdlib only, zero runtime dependencies.
7. `README.md` — how to run, controls, architecture.
8. A **git repository**: `git init`, configure identity if needed, and
   commit your work in at least 3 logical commits (e.g. scaffold, core,
   controller+tests). The reference.md §10.2 applies.

## GameController contract (must match exactly)

```python
class GameController:
    def reset(self, level: int = 0, seed: int | None = None) -> None
        # Start a fresh world. Same (level, seed) -> identical world, always.
    def step(self, action: str = "NONE") -> dict
        # Advance exactly ONE physics tick (dt = 1/TICK_RATE).
        # action in: "FLAP" | "NONE" | "PAUSE" | "RESTART"; unknown -> NONE.
        # Returns the new state dict.
    def state(self) -> dict
        # {"bird": {"y", "velocity"}, "pipes": [{"x", "gap_y", "scored"}],
        #  "score", "status", "tick", "level", "width", "height"}
    def render(self) -> str
        # Deterministic ASCII map. Same state -> same string.
    def set_speed(self, factor: float) -> None
        # Debug/automation playback pacing only. Never affects physics.
    def play(self) -> None
        # Human keyboard mode; blocks until quit.
```

Status values: `RUNNING`, `PAUSED`, `LEVEL_COMPLETE`, `GAME_OVER`, `WON`.

The contract tests in `tests/test_contract.py` verify the shape and
behavior of this API. Run them as you build. Additional value-level checks
run later, against the reference document.

## Physics, levels, and rules

All physics constants, level definitions, collision rules, world
generation, and the exact tick order are in **`reference.md`** in this
directory. Extract values precisely — do not invent or guess them.

Where the document is self-contradictory, the **Current Tuning** section
(Section 4) is authoritative. History sections and the FAQ are noise.

## Requirements

- Deterministic: same (level, seed) → identical world, identical
  gameplay. All RNG seeded. The pipe layout is independent of bird
  actions (reference.md §4.6).
- Levels must be actually completable: every level's pipe gaps must be
  geometrically reachable by a valid flap sequence.
- Verify your work like an engineer:
  1. Run `tests/test_contract.py` and your own `tests/test_game.py` —
     iterate until green.
  2. Prove the game is playable via the API: drive level 0 through
     `GameController` (write a tiny script or autopilot) and confirm
     `LEVEL_COMPLETE` is reachable, not just claimed.
  3. Commit your work in logical commits.

## Constraints

- Python 3.11+ standard library only. No third-party packages, no network
  access, no external data files beyond what you create.
- Do not modify `reference.md` or `tests/test_contract.py`.

## Evaluation

Your submission is graded on:

1. Hidden contract + value suite (exact constants from reference.md §4).
2. Determinism (same seed → identical replay).
3. Level passability (automated solver) and API-driven completion
   replay of at least level 0.
4. Git structure (repo, >= 3 logical commits, clean working tree).

Tool-call trace (search, read, write, test, git usage; failures and
retries) is logged as a diagnostic report. Take your time and build it
properly — this is a craft task, not a sprint.
