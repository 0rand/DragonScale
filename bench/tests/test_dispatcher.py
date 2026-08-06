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


def test_find_binary_env_override(monkeypatch, tmp_path):
    fake = tmp_path / "opencode"
    fake.write_text("#!/bin/sh\necho fake\n")
    fake.chmod(0o755)
    monkeypatch.setenv("OPENCODE_BIN", str(fake))
    assert dispatcher._find_binary("opencode", "OPENCODE_BIN") == str(fake)


def test_find_binary_path_lookup(monkeypatch, tmp_path):
    fake = tmp_path / "jcode"
    fake.write_text("#!/bin/sh\necho fake\n")
    fake.chmod(0o755)
    monkeypatch.delenv("JCODE_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert dispatcher._find_binary("jcode", "JCODE_BIN") == str(fake)


def test_find_binary_missing_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENCODE_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))  # empty dir: nothing on PATH
    try:
        dispatcher._find_binary("opencode", "OPENCODE_BIN")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        msg = str(e)
        assert "not found" in msg
        assert "does not install" in msg or "does not install" in msg.lower()
        assert "OPENCODE_BIN" in msg
