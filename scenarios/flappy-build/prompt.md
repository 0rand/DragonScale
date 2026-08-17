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
   P = pause, R = restart, Q = quit. The loop must advance time at
   TICK_RATE even when no key is pressed, and render to the actual
   terminal size (see Requirements).
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
- **Terminal size is variable.** The viewport must adapt to the actual
  terminal dimensions at runtime; a hardcoded 80x24 frame is not
  acceptable. On every terminal from 50x18 up, the whole playfield must
  remain visible and usable with no frame stacking, no scroll artifacts,
  and no clipped pipes. reference.md's "80x24 by default" is a DEFAULT
  size, not a fixed one.
- **Time advances continuously.** The world must advance at TICK_RATE
  (`dt = 1/TICK_RATE`) on every tick, whether or not any key is pressed.
  A game that only advances when the player presses a key is not
  playable. Key presses steer (flap/pause/restart/quit); they must never
  be the sole driver of time.
- Verify your work like an engineer:
  1. Run `tests/test_contract.py` and your own `tests/test_game.py` —
     iterate until green.
  2. Prove the game is playable via the API: drive level 0 through
     `GameController` (write a tiny script or autopilot) and confirm
     `LEVEL_COMPLETE` is reachable, not just claimed.
  3. Prove human playability: run `python3 -m game` in a real terminal
     and confirm the bird falls and pipes move with NO key pressed, and
     that a resized (narrow/short) terminal still renders cleanly.
  4. Commit your work in logical commits.

## Constraints

- Python 3.11+ standard library only. No third-party packages, no network
  access, no external data files beyond what you create.
- Do not modify `reference.md` or `tests/test_contract.py`.

## Working method (mandatory)

Work in a loop of SMALL steps — never one giant response. Your output
per turn is limited; a single huge response will be cut off mid-file and
the work will be lost.

1. **Plan first.** Read `reference.md` and the contract. Write a short
   plan: which files, in what order, what to verify. Keep it brief.
2. **Implement one file per step.** Use your file-writing tool for each
   file. Do NOT paste large amounts of code into chat — write files with
   tools, then move on.
3. **Verify each step.** Run `tests/test_contract.py` and your own tests
   as you go. Fix failures before moving on.
4. **Track progress.** Keep a short checklist of done / next. Update it
   after every step.

If a response starts getting long, stop and let the next turn continue —
the harness will prompt you to keep going.

## Completion

When ALL deliverables are complete and verified (tests green, game
playable, git committed), end your final message with the exact line:

    JOB COMPLETE

Do NOT say `JOB COMPLETE` before the work is actually done. If you are
prompted to continue, keep working until you can honestly say it.

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
