# FLAPPSY — Design Reference v2.4

Authoritative design document for the Flappsy terminal game project.
This document is the single source of truth for physics, levels, and API
behavior. When implementing, extract values and rules from here — do not
invent numbers from memory.

> **Spec compliance note:** this document is long and contains historical
> notes, design discussions, and FAQs. Some of those sections mention values
> that are no longer correct. The **Current Tuning** section (Section 4) is
> the only authoritative source for gameplay constants. If anything in this
> document contradicts Section 4, Section 4 wins.

---

## 1. Project Overview

Flappsy is a standalone Python 3.11+ terminal clone of the classic
"flappy bird" arcade game. It runs entirely on the standard library — no
third-party dependencies, no network access, no GPU. The entire game is
rendered as ASCII art in a terminal window.

The project started in 2025 as a weekend toy and grew into a platform for
testing deterministic game logic, seeded RNG discipline, and testable
controller design. The core design principle: **the game is a pure state
machine**. All randomness is seeded. Given the same level and the same
seed, the game world is byte-for-byte reproducible — the same pipe layout,
the same bird trajectory, the same outcome. This is what makes the game
automatically playable and testable.

### 1.1 Design Goals

1. Determinism. Same (level, seed) → identical world. No exceptions.
2. A single controller API used by three consumers: automated tests,
   an automation/API client, and a human at the keyboard.
3. Pure, immutable core logic separated from terminal rendering.
4. Playable but demanding. Four levels of increasing cruelty.

### 1.2 Controls

| Key | Action |
|-----|--------|
| Space / W / Up | Flap |
| P | Pause / resume |
| R | Restart current level |
| Enter | Advance to next level |
| M | Mute |
| Q | Quit |

Minimum terminal: 50x18. The game window is 80x24 by default.

---

## 2. Architecture

Three logical modules (see the task prompt for the required file layout):

- **Core** — pure game logic. Frozen dataclasses, immutable state, pure
  functions. Contains all physics, level definitions, collision, scoring.
  No I/O, no rendering, no input. The core is what the controller drives.
- **Controller** — the `GameController` object. The single API surface.
  Wraps the core, owns the current world state, exposes `reset`, `step`,
  `state`, `render`, `set_speed`, `play`. Tests, automation, and the
  keyboard all go through this one object.
- **Render / TUI** — deterministic ASCII rendering of a world state, and
  a keyboard loop for human play. Rendering must be a pure function of
  the state: the same state always renders to the same string.

The human keyboard loop is a thin adapter: key presses become controller
actions (`FLAP`, `PAUSE`, `RESTART`, ...). There is no game logic in the
keyboard loop.

### 2.1 The Controller Contract

The controller is the *only* way the world advances. Nobody calls the core
directly — not tests, not automation, not the keyboard loop.

- `reset(level=0, seed=None)` — start a fresh world. Same (level, seed)
  always produces the same world.
- `step(action="NONE")` — advance exactly one physics tick
  (1/20 second at nominal speed) and return the new state as a dict.
  Valid actions: `FLAP`, `NONE`, `PAUSE`, `RESTART`. Unknown actions are
  ignored (treated as `NONE`).
- `state()` — the current state as a dict: `bird` (y, velocity), `pipes`
  (list of x, gap_y, scored), `score`, `status`, `tick`, `level`, width,
  height.
- `render()` — deterministic ASCII map of the current state.
- `set_speed(factor)` — debug/automation playback speed control. Never
  affects physics, only pacing.
- `play()` — human keyboard mode; blocks until the player quits.

Status values: `RUNNING`, `PAUSED`, `LEVEL_COMPLETE`, `GAME_OVER`, `WON`.

---

## 3. Gameplay

The bird sits at a fixed horizontal position and can only move vertically.
Gravity constantly pulls it down. A flap sets its velocity upward; the
player must time flaps to thread the bird through the gaps in a
never-ending wall of pipes.

The bird advances through the level when it passes the required number of
pipes. Colliding with a pipe, the ground, or the ceiling ends the run.

### 3.1 What "Passing" a Pipe Means

A pipe is "scored" the moment its right edge has completely passed the
bird's horizontal position. Once a pipe is scored it can never be scored
again. The score is the number of scored pipes.

### 3.2 Level Completion

When the score reaches the level's target (see Section 4), the level is
complete — if the bird is still alive at that moment. A fireworks display
celebrates the milestone.

---

## 4. Current Tuning  ←  AUTHORITATIVE

These are the values in force. Everything else in this document is
history, discussion, or noise.

### 4.1 World constants

| Constant | Value | Meaning |
|----------|-------|---------|
| `BIRD_X` | 10 | Bird's fixed horizontal position (0-based column) |
| `PIPE_WIDTH` | 4 | Pipe thickness in columns |
| `TICK_RATE` | 20 | Physics ticks per second |
| `dt` | 1/20 | Seconds per tick (derived from TICK_RATE) |
| Screen width | 80 | Default terminal width |
| Screen height | 24 | Default terminal height (min 18) |
| `usable_h` | max(10, height − 4) | Vertical room available for pipes |
| Pipe margin | 3 | Minimum cells between pipe gap and top/bottom edge |
| Spawn offset | width + 8 | Where new pipes are born, off-screen right |

### 4.2 Level definitions

| idx | Name | Speed (px/tick) | Gravity (px/s²) | Flap velocity (px/s) | Gap (cells) | Pipe spacing (px) | Pipes to clear |
|-----|------|-----------------|------------------|-----------------------|-------------|--------------------|----------------|
| 0 | Tutorial Puddle | 0.72 | 12.0 | −10.0 | 12 | 30 | 5 |
| 1 | Suburban Wind Tunnel | 1.05 | 18.0 | −9.8 | 9 | 27 | 7 |
| 2 | Volcano Tax Audit | 1.35 | 22.0 | −10.8 | 8 | 24 | 9 |
| 3 | Neon Bankruptcy | 1.70 | 26.0 | −11.8 | 7 | 21 | 11 |

`gap` is the vertical gap between pipe halves, in cells. `pipe_spacing`
is the horizontal distance between consecutive pipe centers, in pixels.

### 4.3 Physics model (exact)

One tick of `step()` performs, in this order:

1. If the action is `FLAP`, set the bird's velocity to the level's
   `flap_velocity` (instantaneous).
2. Integrate the bird: `y += velocity * dt`, then
   `velocity += gravity * dt`.
3. Advance every pipe left by `speed` pixels.
4. Replace any pipe whose `x` drops below −4 with a fresh pipe spawned at
   `max(width + 8, max_x + k * pipe_spacing)` where `max_x` is the largest
   `x` currently on the board and `k` counts replacements this tick.
5. Score: for any unscored pipe whose new `x + PIPE_WIDTH − 1 < BIRD_X`,
   increment score and mark it scored.
6. Check collisions (Section 4.4).
7. Check level completion (Section 3.2). If complete, emit fireworks.

`dt` is always `1 / TICK_RATE`. The controller calls `step()` with this
exact tick. There is no variable time step in gameplay.

### 4.4 Collision rules (exact)

The bird dies (status → `GAME_OVER`) if **any** of:

- `y < 1.5` (hit the ceiling);
- `y >= height − 2` (hit the ground — note the floor sits 2 rows above
  the bottom edge);
- a pipe occupies the bird's column — `int(x) <= BIRD_X <= int(x) + PIPE_WIDTH − 1` —
  and the bird's `y` is outside the gap band
  `[gap_y − gap // 2, gap_y + gap // 2]`.

Note the integer floor division: the gap band is `gap_y ± floor(gap/2)`.
The collision test uses the bird's position *after* integration and the
pipe positions *after* movement in the same tick.

### 4.5 World generation (exact)

- Initial pipes: exactly 3, first at `max(40, int(width * 0.60))`, then
  every `pipe_spacing` pixels. This gives a new player a runway of at
  least two seconds before the first pipe arrives.
- Pipe gap position is random per pipe, seeded: `random.Random(seed +
  level_index * 997)`, uniform between `3 + gap // 2` and
  `max(3 + gap // 2, usable_h − 3 − gap // 2)`.
- Tutorial special-case: on level 0, the first pipe's gap sits exactly at
  `usable_h // 2` and the second pipe's gap at `usable_h // 2 − 2`. This
  guarantees a fair opening for beginners. (This is a fixed rule, not
  random.)
- Replacement pipes during play use RNG seeded per-tick:
  `random.Random(seed + level_index * 1009 + tick)`.
- The bird starts at `y = height / 2` with velocity 0.
- Fireworks: 90 spark positions, seeded from the current tick.

### 4.6 Determinism mandate

Every random source is seeded. The world depends only on `(level, seed,
tick)`. Two runs with the same level and seed produce identical states at
every tick, no matter what actions are taken. **The pipe layout is
independent of bird actions** — it is a pure function of the seed.

The controller must reproduce this: `reset(0, 42)` followed by any action
sequence must be exactly reproducible by another `reset(0, 42)` followed
by the same actions.

---

## 5. Level Design Notes

These are historical design discussions. Treat as context, NOT as values.

Level 0 — **Tutorial Puddle**. The onboarding level. Generous gap,
gentle gravity, few pipes. The first two pipes are hand-placed to be
unmissable. Speed is a lazy 0.72.

Level 1 — **Suburban Wind Tunnel**. "A breezy 1.1 px/tick with a
10-cell gap" per the original design brief, but playtesting showed that
was too forgiving — the shipped gap is tighter and the speed slightly
lower. Gravity gets serious here.

Level 2 — **Volcano Tax Audit**. "We talked about pushing gravity to
24 and flap to −11.0," says the original designer's notes, "but the audit
metaphor demands precision, not chaos." The shipped values are harsher on
timing than the notes suggest. Red theme. The tax man cometh.

Level 3 — **Neon Bankruptcy**. The final exam. Near-miss gaps, brutal
gravity, pipes arriving every fraction of a second. Originally prototyped
at 1.8 px/tick with a 6-cell gap; that was deemed "cruel and unusual" and
pulled back slightly before shipping. Magenta theme.

---

## 6. Rendering & UX

Rendering is a pure function of state. The same state renders to the same
string, character for character.

- Background: sparse dot field.
- Ground: `▄` across the bottom two rows.
- Pipes: `█` edges, `▓` fill.
- Bird: `ᗧ` rising, `ᗤ` falling.
- Fireworks: `* + x • ✦ ✹ @`.
- Header: `FLAPPSY | <level name> | score <n>`.
- Overlay messages on PAUSED / GAME OVER / LEVEL COMPLETE / WON.
- Palette per level: cyan, green, red, magenta (see Section 4.2).

The renderer must be safe at any terminal size: truncate long lines,
never crash on narrow terminals. Unicode cells fall back to ASCII when
the terminal cannot display them.

### 6.1 Message rotation

Sarcastic flavor messages rotate on a fixed cadence — a new message every
17 ticks, cycling through 20 messages. The message shown is purely a
function of the tick counter.

---

## 7. Audio (decorative, optional)

Eight-bit style sound effects via the system audio player, with a
terminal BEL fallback. Five effect types:

| Effect | Frequency sweep |
|--------|-----------------|
| flap | 880 → 1320 Hz |
| crash | 120 → 80 → 45 Hz |
| score | 660 → 990 → 1320 Hz |
| level complete | 523 → 659 → 784 → 1046 Hz |
| pause | 300 Hz |

Sound is strictly cosmetic. It must never affect physics or scoring, and
missing audio support must never crash the game. A mute toggle exists.

---

## 8. Version History

Useful context for why things are the way they are. Values listed here
are HISTORICAL and almost certainly not current.

- **v0.9 (early prototype):** gravity was a flat 14.0 px/s² on every
  level, flap −9.0 everywhere. Felt like flying through custard.
- **v1.0:** four-level structure introduced. Tutorial gap was 13 cells;
  speed 0.65.
- **v1.1:** Tutorial gap tightened from 13 to 12; flap velocities
  differentiated per level (previously −10.0 flat).
- **v1.2:** gravity rebalance — Tutorial 14→12, Suburban 16→18,
  Volcano 20→22, Neon 24→26. "Custard" complaint finally resolved.
- **v1.3:** pipe spacing reworked from 32/28/25/22 to the current values.
- **v1.4:** Neon speed 1.60 → 1.70 after the autopilot solver cleared it
  too easily. Also added the level-0 tutorial pipe placement.
- **v2.0:** controller API introduced; gameplay fully deterministic;
  old `step(dt)`-style free-for-all removed.
- **v2.2:** fireworks added. v2.3: message rotation cadence fixed at 17
  ticks. v2.4: this document.

---

## 9. FAQ

*Collected from the issues tracker. Some answers are outdated — see
Section 4 for what actually ships.*

**Q: What's the fastest pipe speed in the game?**
A: 1.6 px/tick, on Neon Bankruptcy. (Outdated — see Section 4.2.)

**Q: What's the Tutorial Puddle gap size?**
A: 13 cells. It's the friendliest gap in the game. (Outdated.)

**Q: Is flap velocity the same on every level?**
A: Yes, a flat −10.0 px/s across all levels. The physics doesn't change;
the gaps do. (Outdated.)

**Q: What's the default gravity?**
A: Gravity is uniform — 14 px/s² everywhere. Tuning per level was
abandoned in v1.2. (Outdated.)

**Q: How are pipes generated?**
A: Pipes are random every run. There's no seed — every game is a unique
snowflake. (Wrong. See Section 4.5.)

**Q: Can the bird hit the top of the screen?**
A: No, it bounces off the ceiling. (Wrong — see Section 4.4.)

**Q: How many pipes does level 3 require?**
A: Nine. (Wrong — see Section 4.2.)

**Q: Is the game playable without a network connection?**
A: Yes. It's stdlib-only and fully offline. (Correct.)

---

## 10. Engineering Notes

### 10.1 Testing philosophy

- The controller API is the contract. Tests drive the game exclusively
  through the controller — never by poking internal state.
- Determinism tests are mandatory: same seed, same action sequence, same
  result.
- Test the unhappy paths: floor death, ceiling death, pipe collision,
  pause freezing the world, restart resetting the world.
- A test suite that only covers the happy path is not a test suite.

### 10.2 Git practice

- Logical commits: scaffold, core, controller, tests — not one giant
  "everything" commit.
- Commit messages say WHAT and WHY, not just "fix".

### 10.3 Seeded RNG discipline

- Never call the global `random` module functions. Always use a
  `random.Random(seed)` instance derived from the world seed.
- The seed math in Section 4.5 is exact. Changing it changes the world.
