"""Unit tests for the tool-call trace parser (both harness shapes).

Covers: opencode part/state shape, jcode flat shape, failure marking,
token extraction (flat + nested), regex fallback.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from bench import trace

OC_LOG = "\n".join([
    '{"type":"tool_use","timestamp":1,"sessionID":"s","part":{"id":"p","type":"tool","tool":"read","callID":"c","state":{"status":"completed","input":{"filePath":"/tmp/x"},"output":"ok"}}}',
    '{"type":"tool_use","timestamp":2,"sessionID":"s","part":{"id":"p","type":"tool","tool":"bash","callID":"c2","state":{"status":"error","input":{"command":"pytest"},"output":"exit 1"}}}',
    '{"type":"usage","prompt_tokens":1200,"completion_tokens":900}',
])

JC_LOG = "\n".join([
    '{"type":"tool_use","name":"read_file","input":{"path":"reference.md"},"output":"x","is_error":false}',
    '{"type":"tool_use","name":"bash","input":{"command":"pytest"},"output":"err","is_error":true}',
])


def test_opencode_shape():
    ev = trace.extract_events(OC_LOG)
    assert len(ev) == 2
    assert [e["tool"] for e in ev] == ["read", "bash"]
    assert sum(1 for e in ev if e["is_error"]) == 1
    assert ev[0]["input"] == {"filePath": "/tmp/x"}


def test_jcode_shape():
    ev = trace.extract_events(JC_LOG)
    assert len(ev) == 2
    assert [e["tool"] for e in ev] == ["read_file", "bash"]
    assert sum(1 for e in ev if e["is_error"]) == 1


def test_token_extraction():
    s = trace.summarize(OC_LOG)
    assert s["tokens"] == {"in": 1200, "out": 900}
    assert s["total_calls"] == 2


def test_nested_tokens():
    s = trace.summarize('{"usage":{"prompt_tokens":500,"completion_tokens":200}}')
    assert s["tokens"] == {"in": 500, "out": 200}


def test_regex_fallback():
    s = trace.summarize("called bash twice and read_file once")
    assert s["parser"] == "regex"
    assert s["calls_by_tool"]["bash"] >= 1
