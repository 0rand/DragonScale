"""Tool-call trace parser for DragonScale.

Parses session output (jcode --json / opencode --format json) into
tool-call events. Lenient: walks nested JSON for tool-shaped records,
falls back to regex scanning of known tool names.

Diagnostic only — the trace is the report card, never the score.
"""

from __future__ import annotations

import json
import re
from collections import Counter

TOOL_TYPES = {"tool_use", "tool_call", "function_call", "tool"}
NAME_KEYS = ("name", "tool_name", "function", "tool")
INPUT_KEYS = ("input", "arguments", "args")
OUTPUT_KEYS = ("output", "result", "content", "response")
KNOWN_TOOLS = [
    "read_file", "write_file", "edit_file", "bash", "terminal", "execute",
    "grep", "rg", "find", "git", "python3", "python", "pytest", "ls", "cat",
    "mkdir", "mv", "cp", "rm", "cd", "chmod", "curl", "pip",
]


def _is_tool_call(node: dict) -> bool:
    t = str(node.get("type", "")).lower()
    if t in TOOL_TYPES:
        return True
    # shape heuristic: has a name-ish key AND an input-ish key
    has_name = any(k in node for k in NAME_KEYS)
    has_input = any(k in node for k in INPUT_KEYS)
    return bool(has_name and has_input and ("tool" in t or "call" in t or "function" in t))


def _normalize(node: dict) -> dict:
    tool = next((node[k] for k in NAME_KEYS if k in node), None)
    inp = next((node[k] for k in INPUT_KEYS if k in node), None)
    out = next((node[k] for k in OUTPUT_KEYS if k in node), None)
    err = node.get("is_error", False) or node.get("error", False)
    if isinstance(err, dict):
        err = bool(err)
    return {"tool": str(tool) if tool is not None else "?", "input": inp,
            "output": out, "is_error": bool(err)}


def _walk(node, events: list):
    if isinstance(node, dict):
        if _is_tool_call(node):
            events.append(_normalize(node))
        for v in node.values():
            _walk(v, events)
    elif isinstance(node, list):
        for v in node:
            _walk(v, events)


def extract_events(text: str) -> list:
    events: list = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        _walk(obj, events)
    if not events:
        try:
            _walk(json.loads(text), events)
        except Exception:  # noqa: BLE001
            pass
    return events


def _extract_tokens(text: str):
    seen = {}
    for line in text.splitlines():
        if "prompt_tokens" not in line:
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        for v in obj.values():
            if isinstance(v, dict) and "prompt_tokens" in v:
                seen = {"in": v["prompt_tokens"], "out": v.get("completion_tokens")}
    return seen or None


def _regex_fallback(text: str):
    counts = Counter()
    for tool in KNOWN_TOOLS:
        counts[tool] = len(re.findall(rf"\b{tool}\b", text))
    return {k: v for k, v in counts.items() if v > 0}


def summarize(text: str) -> dict:
    events = extract_events(text)
    if events:
        calls = Counter()
        fails = Counter()
        for e in events:
            calls[e["tool"]] += 1
            if e["is_error"]:
                fails[e["tool"]] += 1
        return {
            "parser": "structured",
            "total_calls": len(events),
            "calls_by_tool": dict(calls),
            "failures_by_tool": dict(fails),
            "tokens": _extract_tokens(text),
        }
    return {
        "parser": "regex",
        "total_calls": None,
        "calls_by_tool": _regex_fallback(text),
        "failures_by_tool": {},
        "tokens": None,
    }
