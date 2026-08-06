"""Unit tests for the dispatcher: env sanitization + command construction.

No live dispatch — checks clean_env() and the command shapes only.
"""

import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from bench import dispatcher
from bench.dispatcher import clean_env, dispatch_jcode, dispatch_opencode


def test_clean_env_strips_hermes():
    os.environ["HERMES_SESSION_KEY"] = "leak"
    env = clean_env()
    assert "HERMES_SESSION_KEY" not in env
    assert "PATH" in env and "HOME" in env


def test_opencode_cmd_shape():
    src = inspect.getsource(dispatcher)
    for tok in ("--format", "json", "-m", "--agent", "build", "--dir",
                "TASK.md", "Read TASK.md"):
        assert tok in src, f"missing {tok!r}"


def test_jcode_fallback_has_no_selfdev():
    assert "--no-selfdev" in inspect.getsource(dispatch_jcode)
