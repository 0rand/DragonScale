"""DragonScale runner — dispatch (optional) + grade one sandbox.

Usage:
  # grade an existing directory (no dispatch — smoke tests, manual runs)
  python3 bench/run.py --scenario flappy-build --label smoke-good \
      --prebuilt scripts/smoke_good

  # full run: dispatch opencode -> model, then grade
  python3 bench/run.py --scenario flappy-build --label run-oc-001 \
      --runner opencode --provider MYPROVIDER --model my-model --timeout 3600

  # opencode model as one string (Provider/Model), custom workdir
  python3 bench/run.py --scenario flappy-build --label run-ds-001 \
      --runner opencode --model MYPROVIDER/my-model --workdir /tmp/ds-sandbox --timeout 3600

  # jcode fallback
  python3 bench/run.py --scenario flappy-build --label run-jc-001 \
      --runner jcode --provider my-provider --timeout 3600
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Runs live OUTSIDE the project tree (default ~/DragoScaleRuns) so a
# model's git commands inside a sandbox can never walk up and commit
# into this repo (luna v4: no nested .git -> its commits landed in
# dragonscale main). Override with $DRAGONSCALE_RUNS.
RUNS_ROOT = Path(
    os.environ.get("DRAGONSCALE_RUNS", str(Path.home() / "DragoScaleRuns"))
).expanduser().resolve()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--prebuilt", help="grade an existing directory instead of dispatching")
    ap.add_argument("--runner", choices=["jcode", "opencode"], default="opencode")
    ap.add_argument("--provider", default=None,
                    help="jcode provider profile, or opencode provider name "
                         "(combined with --model into Provider/Model)")
    ap.add_argument("--model", help="opencode model name (with --provider, "
                                    "or full 'Provider/Model' string)")
    ap.add_argument("--workdir", help="directory to create for the sandbox "
                                      "(default: runs/<label>/sandbox)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args()

    scenario = ROOT / "scenarios" / args.scenario
    if not scenario.is_dir():
        sys.exit(f"scenario not found: {scenario}")

    runs = RUNS_ROOT / args.label
    runs.mkdir(parents=True, exist_ok=True)
    sandbox = Path(args.workdir).expanduser().resolve() if args.workdir \
        else runs / "sandbox"

    # Model identity for the report: explicit --model wins; else the
    # dispatched model; else whatever dispatch.json records (re-grades).
    model_label = args.model

    if args.prebuilt:
        src = (ROOT / args.prebuilt).resolve()
        if src == sandbox.resolve():
            sys.exit(f"refusing: --prebuilt {args.prebuilt} is the run dir itself "
                     f"(--label {args.label}) — would delete the evidence")
        if sandbox.exists():
            shutil.rmtree(sandbox)
        shutil.copytree(src, sandbox)
        if not model_label:
            dj = runs / "dispatch.json"
            if dj.exists():
                model_label = json.loads(dj.read_text()).get("model")
    else:
        if sandbox.exists():
            shutil.rmtree(sandbox)
        shutil.copytree(scenario / "fixture", sandbox)
        from bench.dispatcher import dispatch_jcode, dispatch_opencode

        if args.runner == "jcode":
            res = dispatch_jcode(sandbox, scenario / "prompt.md", args.provider,
                                 timeout=args.timeout)
            model_label = model_label or args.provider
        else:
            model = args.model
            if not model:
                sys.exit("--model required for opencode runner "
                         "(--provider NAME --model NAME, or --model Provider/Model)")
            if "/" not in model and args.provider:
                model = f"{args.provider}/{model}"
            res = dispatch_opencode(sandbox, scenario / "prompt.md", model,
                                    timeout=args.timeout)
            model_label = model
        (runs / "dispatch.json").write_text(json.dumps(
            {"runner": args.runner, "provider": args.provider, "model": model_label,
             "workdir": str(sandbox), "exit": res["exit"],
             "stdout_bytes": len(res["stdout"]), "stderr_bytes": len(res["stderr"])},
            indent=2))
        (runs / "dispatch.stdout.log").write_text(res["stdout"])
        (runs / "dispatch.stderr.log").write_text(res["stderr"])

    from bench.grader import grade, render_markdown

    # Controls = --prebuilt pointing at a smoke fixture (scripts/smoke_*):
    # the PASS/FAIL verdict is load-bearing grader self-validation.
    # Everything else (dispatched runs, re-grades of model sandboxes) is
    # a capability measurement — report leads with score + defect profile.
    kind = "control" if (args.prebuilt
                         and args.prebuilt.replace("\\", "/").startswith("scripts/")) \
        else "model"

    report = grade(sandbox, scenario, args.label, seed=args.seed, model=model_label,
                   kind=kind)
    (runs / "report.json").write_text(json.dumps(report, indent=2))
    (runs / "report.md").write_text(render_markdown(report))

    print(json.dumps(report["verdict"], indent=2))
    print(f"score: {report.get('score', {}).get('total')} / 100", file=sys.stderr)
    print(f"report: {runs / 'report.md'}", file=sys.stderr)


if __name__ == "__main__":
    main()
