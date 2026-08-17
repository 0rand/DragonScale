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
                "TASK.md", "Read TASK.md", "-s", "JOB COMPLETE"):
        assert tok in src, f"missing {tok!r}"


def test_extract_session_id_from_ndjson():
    out = ('{"type":"step_start","sessionID":"ses_abc123"}\n'
           '{"type":"text","sessionID":"ses_abc123","part":{"text":"hi"}}\n')
    assert dispatcher._extract_session_id(out) == "ses_abc123"


def test_extract_session_id_none_when_empty():
    assert dispatcher._extract_session_id("") is None
    assert dispatcher._extract_session_id("not json at all") is None


def test_has_job_complete():
    assert dispatcher._has_job_complete("... JOB COMPLETE ...")
    assert not dispatcher._has_job_complete("... still working ...")
    assert not dispatcher._has_job_complete("")


def test_nudge_loop_continues_session(monkeypatch, tmp_path):
    """dispatch_opencode must re-open the SAME session until JOB COMPLETE."""
    calls = []

    def fake_run(cmd, cwd, timeout):
        calls.append(cmd)
        if len(calls) == 1:
            return {"exit": 0,
                    "stdout": '{"type":"step_start","sessionID":"ses_nudge1"}\n'
                              '{"type":"text","sessionID":"ses_nudge1",'
                              '"part":{"text":"still working"}}\n',
                    "stderr": ""}
        return {"exit": 0,
                "stdout": '{"type":"text","sessionID":"ses_nudge1",'
                          '"part":{"text":"done now JOB COMPLETE"}}\n',
                "stderr": ""}

    monkeypatch.setattr(dispatcher, "_run", fake_run)
    monkeypatch.setattr(dispatcher, "_find_binary", lambda *a: "opencode")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# task\n")
    res = dispatch_opencode(sandbox, prompt, "TESTPROVIDER/model", max_nudges=5)
    assert res["nudges"] == 1
    assert "JOB COMPLETE" in res["stdout"]
    # second call must carry -s <session_id>
    assert "-s" in calls[1] and "ses_nudge1" in calls[1]


def test_nudge_loop_stops_at_max(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, cwd, timeout):
        calls.append(cmd)
        return {"exit": 0,
                "stdout": '{"type":"step_start","sessionID":"ses_nudge2"}\n'
                          '{"type":"text","sessionID":"ses_nudge2",'
                          '"part":{"text":"never finishing"}}\n',
                "stderr": ""}

    monkeypatch.setattr(dispatcher, "_run", fake_run)
    monkeypatch.setattr(dispatcher, "_find_binary", lambda *a: "opencode")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# task\n")
    res = dispatch_opencode(sandbox, prompt, "TESTPROVIDER/model", max_nudges=3)
    assert res["nudges"] == 3
    assert len(calls) == 4  # 1 initial + 3 nudges


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
