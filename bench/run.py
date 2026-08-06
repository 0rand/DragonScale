"""DragonScale runner — dispatch (optional) + grade one sandbox.

Usage:
  # grade an existing directory (no dispatch — smoke tests, manual runs)
  python3 bench/run.py --scenario flappy-build --label smoke-good \
      --prebuilt scripts/smoke_good

  # full run: dispatch to jcode/omlx-35b, then grade
  python3 bench/run.py --scenario flappy-build --label run-35b-001 \
      --runner jcode --provider omlx-35b --timeout 3600
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--prebuilt", help="grade an existing directory instead of dispatching")
    ap.add_argument("--runner", choices=["jcode", "opencode"], default="jcode")
    ap.add_argument("--provider", default="omlx-35b", help="jcode provider profile")
    ap.add_argument("--model", help="opencode model (provider/model)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args()

    scenario = ROOT / "scenarios" / args.scenario
    if not scenario.is_dir():
        sys.exit(f"scenario not found: {scenario}")

    runs = ROOT / "runs" / args.label
    runs.mkdir(parents=True, exist_ok=True)
    sandbox = runs / "sandbox"

    if args.prebuilt:
        if sandbox.exists():
            shutil.rmtree(sandbox)
        shutil.copytree(ROOT / args.prebuilt, sandbox)
    else:
        if sandbox.exists():
            shutil.rmtree(sandbox)
        shutil.copytree(scenario / "fixture", sandbox)
        from bench.dispatcher import dispatch_jcode, dispatch_opencode

        if args.runner == "jcode":
            res = dispatch_jcode(sandbox, scenario / "prompt.md", args.provider,
                                 timeout=args.timeout)
        else:
            if not args.model:
                sys.exit("--model required for opencode runner")
            res = dispatch_opencode(sandbox, scenario / "prompt.md", args.model,
                                    timeout=args.timeout)
        (runs / "dispatch.json").write_text(json.dumps(
            {"runner": args.runner, "exit": res["exit"],
             "stdout_bytes": len(res["stdout"]), "stderr_bytes": len(res["stderr"])},
            indent=2))
        (runs / "dispatch.stdout.log").write_text(res["stdout"])
        (runs / "dispatch.stderr.log").write_text(res["stderr"])

    from bench.grader import grade, render_markdown

    report = grade(sandbox, scenario, args.label, seed=args.seed)
    (runs / "report.json").write_text(json.dumps(report, indent=2))
    (runs / "report.md").write_text(render_markdown(report))

    print(json.dumps(report["verdict"], indent=2))
    print(f"report: {runs / 'report.md'}", file=sys.stderr)


if __name__ == "__main__":
    main()
