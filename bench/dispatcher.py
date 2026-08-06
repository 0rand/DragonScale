"""Dispatch a scenario to a coding agent.

opencode is the PRIMARY runner (Primo's preference — proven, distributed,
clean one-shot sessions). jcode is retained as a fallback.

Both runners get a SANITIZED environment: all HERMES_* vars are stripped so
the bench model never inherits the parent agent session's persona/context
(learned the hard way: jcode ambient mode leaked the full Hermes persona +
history into a bench request via the environment).

The prompt is copied into the sandbox as TASK.md and the agent is told to
read it — the proven task-file pattern (avoids quoting issues, agent can
re-read mid-session, file stays version-controlled).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

JCODE = "/Users/xraan/.local/bin/jcode"
OPENCODE = "/opt/homebrew/bin/opencode"

# Env vars that carry the parent Hermes session's identity/context.
# Strip them so the bench model runs clean-room.
HERMES_ENV_PREFIXES = ("HERMES_",)
# Also drop jcode ambient/selfdev inheritance knobs explicitly.
DROP_ENV = {
    "JCODE_AMBIENT", "JCODE_AMBIENT_MODE", "OPENCODE_AMBIENT",
}


def clean_env() -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(HERMES_ENV_PREFIXES) or key in DROP_ENV:
            env.pop(key, None)
    # keep the essentials for a working shell + python
    for key in ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR",
                "JCODE_NO_TELEMETRY", "JCODE_STREAM_IDLE_TIMEOUT_SECS"):
        if key in os.environ and key not in env:
            env[key] = os.environ[key]
    return env


def _prepare_task(sandbox: Path, prompt_path: Path) -> str:
    """Copy prompt.md into the sandbox as TASK.md; return the dispatch message."""
    shutil.copyfile(prompt_path, sandbox / "TASK.md")
    return "Read TASK.md in this directory and follow its instructions. Work in this directory."


def _run(cmd, cwd, timeout):
    try:
        r = subprocess.run([str(c) for c in cmd], cwd=str(cwd),
                           capture_output=True, text=True, timeout=timeout,
                           env=clean_env())
        return {"exit": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
    except subprocess.TimeoutExpired:
        return {"exit": -1, "stdout": "", "stderr": f"TIMEOUT {timeout}s"}
    except Exception as e:  # noqa: BLE001
        return {"exit": -2, "stdout": "", "stderr": str(e)}


def dispatch_opencode(sandbox: Path, prompt_path: Path, model: str,
                      timeout: int = 3600) -> dict:
    """opencode one-shot run. model is 'Provider/Model' (e.g. MEDIABRIDGE/main).

    Fresh session per invocation; --dir roots it in the sandbox; build agent
    has full read/edit/bash permissions in the user config.
    """
    msg = _prepare_task(sandbox, prompt_path)
    cmd = [OPENCODE, "run", "--format", "json", "-m", model,
           "--agent", "build", "--dir", str(sandbox), msg]
    return _run(cmd, sandbox, timeout)


def dispatch_jcode(sandbox: Path, prompt_path: Path, provider: str,
                   timeout: int = 3600) -> dict:
    """jcode one-shot run (fallback). provider is a profile in config.toml.

    --no-selfdev + clean env disable jcode's parent-session inheritance.
    """
    prompt = prompt_path.read_text()
    cmd = [JCODE, "--provider-profile", provider, "run", "--json", "--quiet",
           "--trace", "--no-selfdev", "-C", str(sandbox), prompt]
    return _run(cmd, sandbox, timeout)
