# DragonScale

Sovereign coding-model bench. One prompt, one target directory, one
deterministic gate — built for the way we actually work: code surgery,
terminal ops, git discipline, testing.

**Scenario 1: `flappy-build`** — build Flappsy, a deterministic terminal
flappy-bird, from a design reference. The reference contains all physics
constants (plus historical noise and red herrings). The hidden suite
checks exact values, determinism, and survival semantics. A passability
solver proves the levels are actually completable, then replays the found
path through the model's own physics to catch spec violations.

## Layout

```
scenarios/<name>/
  prompt.md          # the task given to the model
  fixture/           # copied into the sandbox (reference + visible tests)
  hidden/            # harness-owned grading suite — never shipped
bench/
  run.py             # dispatch + grade
  dispatcher.py      # jcode / opencode one-shot runner
  solver.py          # passability BFS + replay verification
  grader.py          # gates, versions, report
  trace.py           # tool-call log parser (diagnostic)
scripts/             # smoke fixtures (known-good / broken) for grader tests
runs/<label>/        # sandbox, dispatch logs, report.md/json (gitignored)
```

## Usage

```bash
# grade an existing directory (smoke / offline)
python3 bench/run.py --scenario flappy-build --label smoke-good \
    --prebuilt scripts/smoke_good

# full run via jcode → oMLX (35B)
python3 bench/run.py --scenario flappy-build --label run-35b-001 \
    --runner jcode --provider omlx-35b --timeout 3600

# experimental: opencode
python3 bench/run.py --scenario flappy-build --label run-oc-001 \
    --runner opencode --model UNOBTANIUM/<model> --timeout 3600
```

## Gates (PASS requires all)

1. Hidden suite green (exact constants from reference.md §4 + determinism)
2. Level 0 passable (solver BFS) AND replayable (model's own physics
   accepts the path → LEVEL_COMPLETE)
3. Git: repo initialized, >= 3 logical commits

Trace (tool calls, failures, tokens) is reported, never scored.

## Versioning discipline

Every report stamps sha256 of prompt, reference, visible suite, hidden
suite. **Never change a scenario's reference or hidden suite without
bumping its version** — scores stop being comparable the moment the
rubric moves (we learned this the hard way with tool-eval-bench
v2.1.0 → v2.3.0). A changed scenario is a new scenario.

## Roadmap

- Scenario 2: `fix-flappsy-bugs` — plant broken physics (impassable
  walls, off-by-one scoring, unseeded RNG) in a working build; the model
  must find and fix via tests + solver.
- More runners/harness param as first-class matrix axis.
- Multi-seed trials for flaky verdicts.
