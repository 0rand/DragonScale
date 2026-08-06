# DragonScale

Sovereign coding-model benchmark. One prompt, one target directory, a
deterministic gate **and a deterministic 0-100 score — no LLM judge anywhere
in the loop.**

Built for the way we actually work: code surgery, terminal ops, git
discipline, testing. Not a knowledge exam (that's Bencher), not a generic
tool-hygiene suite (that's tool-eval-bench).

**Scenario 1: `flappy-build`** — build Flappsy, a deterministic terminal
flappy-bird, from a design reference. The reference contains all physics
constants (plus historical noise and red herrings — a model that skims and
guesses fails the exact-value checks). The hidden suite checks exact values,
determinism, and survival semantics. A passability solver proves the levels
are actually completable with the model's own physics, then replays the found
path to catch spec violations. A PTY smoke test proves the game is playable
by a human (launch, flap key, quit key, screen output).

## Layout

```
scenarios/<name>/
  prompt.md          # the task given to the model
  fixture/           # copied into the sandbox (reference + visible tests)
  hidden/            # harness-owned grading suite — never shipped
bench/
  run.py             # CLI: dispatch + grade
  dispatcher.py      # opencode (primary) / jcode (fallback) one-shot runner
  solver.py          # passability BFS + growing-horizon probe + replay
  grader.py          # gates, deterministic score, report
  trace.py           # tool-call log parser (diagnostic)
  tests/             # canonical pytest suite
scripts/             # smoke fixtures (known-good / broken) for grader tests
runs/<label>/        # sandbox, dispatch logs, report.md/json (gitignored)
```

## Prerequisites

- Python 3.11+ (stdlib only at runtime — zero runtime dependencies)
- `pytest` (dev only) — `pip install pytest` or `pip install -e .[dev]`
- A coding-agent runner, **installed and configured by you** (the bench does
  not install or configure runners):
  - **opencode** (primary): `npm i -g opencode-ai` — https://opencode.ai/docs
  - **jcode** (fallback): https://github.com/jcode-ai/jcode

  The runner's provider config must define the models you dispatch to
  (e.g. a local MLX server or a cloud gateway). Binary resolution:
  `OPENCODE_BIN` / `JCODE_BIN` env override, then `PATH`.

## Usage

```bash
cd dragonscale
.venv/bin/python -m pytest          # canonical suite (16 tests)

# grade an existing directory (smoke / offline); --model stamps the report
python3 bench/run.py --scenario flappy-build --label smoke-good \
    --prebuilt scripts/smoke_good --model "MYPROVIDER/model-name"

# full run: dispatch opencode -> model, then grade
python3 bench/run.py --scenario flappy-build --label run-oc-001 \
    --runner opencode --provider UNOBTANIUM --model Qwen3.6-35B-... --timeout 3600

# model as one string, custom workdir (created for the sandbox)
python3 bench/run.py --scenario flappy-build --label run-ds-001 \
    --runner opencode --model MEDIABRIDGE/main --workdir /tmp/ds-sandbox --timeout 3600

# jcode fallback
python3 bench/run.py --scenario flappy-build --label run-jc-001 \
    --runner jcode --provider omlx-35b --timeout 3600
```

The runner env is sanitized (`HERMES_*` stripped, jcode gets `--no-selfdev`)
so the bench model never inherits the parent agent's persona or history.

## Verdict + Score

**Verdict** is a hard gate: PASS requires hidden suite green, level 0
passable AND replayable, and git repo with >= 3 logical commits.

**Score** is a deterministic 0-100 rubric computed purely from artifacts:

| component | pts | source |
|-----------|-----|--------|
| hidden_suite | 30 | exact constants + determinism pass rate |
| passability | 15 | fraction of 4 levels BFS-passable |
| replay | 10 | fraction replay-verified |
| own_tests | 8 | model's own test suite pass rate |
| mutation | 7 | do the model's tests catch a gravity sabotage? |
| contract | 10 | visible suite pass rate |
| git | 15 | repo + >=3 commits + clean tree + meaningful messages |
| human_play | 5 | PTY: launch, flap key, quit key, exit 0 |

Tool-call trace (calls, failures by tool, tokens) is a diagnostic column —
never a score. Speed is not graded (1 AI second = 1 human hour; slow-correct
beats fast-wrong).

## First results (2026-08-06, flappy-build, seed 42)

| run | model | verdict | score |
|-----|-------|---------|-------|
| run-oc-ds-001 | DeepSeek v4-flash (cloud) | PASS | 93.5 |
| smoke-good | reference implementation | PASS | 93.0 |
| run-oc-35b-001 | Qwen3.6-35B (local) | FAIL (no git) | 78.6 |
| smoke-broken | unseeded RNG + gravity 14 | FAIL | 65.2 |

Lesson: code quality and process discipline are orthogonal — the 35B built a
flawless artifact (45 tests, exact constants) but never ran `git init`; the
cloud model committed in a textbook sequence. Mutation sensitivity is a real
discriminator: the reference build's own tests are value-shallow (0/4 catch
the sabotage), DeepSeek's catch it (2/26).

## Versioning discipline

Every report stamps sha256 of prompt, reference, visible suite, hidden
suite. **Never change a scenario's reference or hidden suite without bumping
its version** — scores stop being comparable the moment the rubric moves.
A changed scenario is a new scenario.

## Roadmap

- Scenario 2: `fix-flappsy-bugs` — plant broken physics (impassable walls,
  off-by-one scoring, unseeded RNG) in a working build; the model must find
  and fix via tests + solver.
- Multi-seed trials for flaky verdicts.
- Comparison-report generator: auto-emit head-to-head tables from two runs.
