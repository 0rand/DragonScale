"""Dispatch a scenario to a coding agent.

opencode is the PRIMARY runner (Primo's preference — proven, distributed,
clean one-shot sessions). jcode is retained as a fallback.

Runners are NOT installed by the bench — they must be installed and
configured by the operator (see README). Binaries resolve via env override
(OPENCODE_BIN / JCODE_BIN) then PATH; a clear error names the missing tool
and how to install it.

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

# Env vars that carry the parent Hermes session's identity/context.
# Strip them so the bench model runs clean-room.
HERMES_ENV_PREFIXES = ("HERMES_",)
# Also drop jcode ambient/selfdev inheritance knobs explicitly.
DROP_ENV = {
    "JCODE_AMBIENT", "JCODE_AMBIENT_MODE", "OPENCODE_AMBIENT",
}


def _find_binary(name: str, env_var: str) -> str:
    """Resolve a runner binary: env override -> PATH -> clear error.

    The bench does not install runners. If the operator hasn't installed
    the tool, say so plainly with the install hint instead of letting
    subprocess raise a bare FileNotFoundError.
    """
    override = os.environ.get(env_var)
    if override and Path(override).exists():
        return override
    found = shutil.which(name)
    if found:
        return found
    hint = {
        "opencode": "install + configure opencode first (see README): "
                    "`npm i -g opencode-ai` or https://opencode.ai/docs",
        "jcode": "install jcode first (see README): https://github.com/jcode-ai/jcode",
    }.get(name, "install it")
    raise RuntimeError(
        f"{name} not found on PATH (override with {env_var}). {hint}. "
        f"The bench does not install or configure runners.")


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


def _extract_session_id(stdout: str) -> str | None:
    """Pull the opencode session ID from NDJSON run output."""
    import json as _json
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        sid = ev.get("sessionID")
        if sid:
            return sid
    return None


def _has_job_complete(stdout: str) -> bool:
    """True only if the model's FINAL assistant message ends with JOB COMPLETE.

    A naive substring match false-positives: the prompt echo (TASK.md's
    Completion section, read via the read tool) and planning text ("I will
    say JOB COMPLETE when done") both contain the codeword. Only a
    standalone `JOB COMPLETE` as the LAST non-empty line of the last
    assistant text part counts (Primo's rule: "tell it to say JOB COMPLETE
    as the last line of its output").
    """
    import json as _json

    last_text = ""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if ev.get("type") == "text":
            part = ev.get("part", {})
            if isinstance(part, dict) and part.get("text"):
                last_text = part["text"]
    if not last_text:
        return False
    non_empty = [l.strip() for l in last_text.splitlines() if l.strip()]
    return bool(non_empty) and non_empty[-1] == "JOB COMPLETE"


NUDGE_MESSAGE = (
    "Your previous response was cut off by the output token limit — that "
    "is normal and NOT a failure. Your work is preserved: read plan.md "
    "to see exactly where you are, then continue from there. Do NOT "
    "restart from scratch and do NOT re-read files you have already read. "
    "WRITE the next file now using your file-writing tool (game/core.py, "
    "then controller.py, render.py, __main__.py, tests, pyproject.toml, "
    "README.md), update plan.md, run the tests, and commit. Keep each "
    "response SHORT — write files with tools, not long prose in chat. When "
    "the task is genuinely complete, end your final message with the exact "
    "line: JOB COMPLETE"
)


def dispatch_opencode(sandbox: Path, prompt_path: Path, model: str,
                      timeout: int = 3600, max_nudges: int = 5) -> dict:
    """opencode run with a nudge loop. model is 'Provider/Model'.

    Fresh session per invocation; --dir roots it in the sandbox; build agent
    has full read/edit/bash permissions in the user config.

    Some models (Qwen 3.8 27B) try to emit the whole implementation in ONE
    response and hit the output-token cap mid-file (reason: length) — the
    session ends with nothing written. The nudge loop re-opens the SAME
    session (`-s <session_id>`) with a continuation prompt until the model
    says JOB COMPLETE or max_nudges is exhausted. The codeword is defined
    in prompt.md; the grader never trusts it — it only stops the nudging.
    """
    msg = _prepare_task(sandbox, prompt_path)
    cmd = [_find_binary("opencode", "OPENCODE_BIN"),
           "run", "--format", "json", "-m", model,
           "--agent", "build", "--dir", str(sandbox), msg]
    result = _run(cmd, sandbox, timeout)
    session_id = _extract_session_id(result["stdout"])
    nudges = 0
    while (session_id and not _has_job_complete(result["stdout"])
           and nudges < max_nudges):
        nudge_cmd = [_find_binary("opencode", "OPENCODE_BIN"),
                     "run", "--format", "json", "-m", model,
                     "--agent", "build", "--dir", str(sandbox),
                     "-s", session_id, NUDGE_MESSAGE]
        nudge = _run(nudge_cmd, sandbox, timeout)
        result["stdout"] += nudge["stdout"]
        result["stderr"] += nudge["stderr"]
        result["exit"] = nudge["exit"] if nudge["exit"] != 0 else result["exit"]
        nudges += 1
    result["nudges"] = nudges
    return result


def dispatch_jcode(sandbox: Path, prompt_path: Path, provider: str,
                   timeout: int = 3600) -> dict:
    """jcode one-shot run (fallback). provider is a profile in config.toml.

    --no-selfdev + clean env disable jcode's parent-session inheritance.
    """
    prompt = prompt_path.read_text()
    cmd = [_find_binary("jcode", "JCODE_BIN"),
           "--provider-profile", provider, "run", "--json", "--quiet",
           "--trace", "--no-selfdev", "-C", str(sandbox), prompt]
    return _run(cmd, sandbox, timeout)
