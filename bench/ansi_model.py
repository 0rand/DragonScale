"""Minimal ANSI terminal screen model for bench checks.

Purpose: deterministically detect games whose play loop writes frames that
OVERFLOW the terminal they run in (the "3 parallel realities" bug — a
hardcoded 24-line frame in a 12-row terminal scrolls and stacks fragments).

The model tracks a cursor and counts CONTENT WRITES at rows beyond the
terminal height. Curses games (and any game that adapts to terminal size)
never write below the last row; hardcoded-frame games do, every frame.

Deliberately minimal — enough escape handling to be fair to both styles
(curses emits cursor-positioning + erase sequences; raw-ANSI games emit
clear-screen + plain lines). Not a full terminal emulator.

API: count_overflow_writes(data: bytes, rows: int, cols: int = 80) -> int
"""

from __future__ import annotations

import re

# CSI sequence: ESC [ params... final-byte (0x40-0x7E)
_CSI = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z@`~])")


def _final(c: str) -> bool:
    return 0x40 <= ord(c) <= 0x7E


def count_overflow_writes(data: bytes, rows: int, cols: int = 80) -> int:
    """Count content characters written at a row > `rows` (1-based).

    Scans the byte stream; strips ANSI CSI sequences (colors, cursor
    moves, erases, alt-screen toggles) and tracks the cursor. Only
    printable text writes are counted — cursor moves alone never count.
    """
    text = data.decode(errors="replace")
    row, col = 1, 1
    overflow = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b":
            m = _CSI.match(text, i)
            if m:
                params, final = m.group(1), m.group(2)
                if params.startswith("?"):
                    pass  # private mode (alt screen, autowrap) — no move
                else:
                    ps = [int(p) for p in params.split(";") if p]
                    p1 = ps[0] if ps else 1
                    p2 = ps[1] if len(ps) > 1 else 1
                    if final in ("H", "f"):
                        row, col = p1, p2
                    elif final == "G":
                        col = p1
                    elif final == "d":
                        row = p1
                    elif final == "A":
                        row = max(1, row - p1)
                    elif final == "B":
                        row = row + p1
                    elif final == "C":
                        col = col + p1
                    elif final == "D":
                        col = max(1, col - p1)
                    # E/F/G-erasures, J/K clears, P/@/L/M edits: no cursor move
                i = m.end()
                continue
            # bare ESC — skip
            i += 1
            continue
        if ch == "\n":
            row += 1
            col = 1
        elif ch == "\r":
            col = 1
        elif ch == "\b":
            col = max(1, col - 1)
        elif ch == "\t":
            col += 1
        elif ch.isprintable():
            if row > rows:
                overflow += 1
            if col > cols:
                # autowrap (default on): wrap to next row
                row += 1
                col = 1
                if row > rows:
                    overflow += 1
            col += 1
        i += 1
    return overflow
