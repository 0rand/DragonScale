"""Dispatch a scenario to a coding agent (jcode or opencode).

jcode is the proven path on this box (provider profiles in ~/.jcode/config.toml
→ oMLX on :8000). opencode is implemented but experimental — verify with a
probe run before trusting its session format.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

JCODE = "/Users/xraan/.local/bin/jcode"
OPENCODE = "/opt/homebrew/bin/opencode"


def _run(cmd, cwd, timeout):
    try:
        r = subprocess.run([str(c) for c in cmd], cwd=str(cwd),
                           capture_output=True, text=True, timeout=timeout)
        return {"exit": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
    except subprocess.TimeoutExpired:
        return {"exit": -1, "stdout": "", "stderr": f"TIMEOUT {timeout}s"}
    except Exception as e:  # noqa: BLE001
        return {"exit": -2, "stdout": "", "stderr": str(e)}


def dispatch_jcode(sandbox: Path, prompt_path: Path, provider: str,
                   timeout: int = 3600) -> dict:
    prompt = prompt_path.read_text()
    cmd = [JCODE, "--provider-profile", provider, "run", "--json", "--quiet",
           "--trace", "-C", str(sandbox), prompt]
    return _run(cmd, sandbox, timeout)


def dispatch_opencode(sandbox: Path, prompt_path: Path, model: str,
                      timeout: int = 3600) -> dict:
    """EXPERIMENTAL. model is 'provider/model' per opencode run -m."""
    prompt = prompt_path.read_text()
    cmd = [OPENCODE, "run", "--format", "json", "-m", model, "--agent", "build",
           "--dir", str(sandbox), prompt]
    return _run(cmd, sandbox, timeout)
