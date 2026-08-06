"""Unit tests for the ANSI terminal screen model (overflow detection).

The model counts content writes at rows beyond the terminal height — the
"3 parallel realities" bug where a hardcoded frame taller than the terminal
scrolls and stacks. Curses games (and adaptive games) never write below the
last row; hardcoded-frame games do, every frame.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from bench.ansi_model import count_overflow_writes


def test_plain_lines_fit_exactly():
    """A frame that exactly matches the terminal height never overflows."""
    data = ("line1\nline2\nline3\nline4\nline5\n"
            "line6\nline7\nline8\nline9\nline10\n").encode()
    assert count_overflow_writes(data, rows=10) == 0


def test_frame_taller_than_terminal_overflows():
    """A 24-line frame in a 10-row terminal overflows every frame."""
    data = ("\n".join(f"row{i}" for i in range(1, 25)) + "\n").encode()
    n = count_overflow_writes(data, rows=10)
    # rows 11..24 written beyond the 10-row terminal
    assert n >= 14, n


def test_ansi_clear_and_home_do_not_count():
    """Clear-screen + home sequences are control, not content writes."""
    data = b"\x1b[2J\x1b[Hrow1\nrow2\n"
    assert count_overflow_writes(data, rows=10) == 0


def test_cursor_positioned_writes_respect_terminal():
    """curses-style absolute positioning within the terminal: no overflow."""
    data = b"\x1b[1;1Hpipe \x1b[2;1Hpipe \x1b[10;1Hground"
    assert count_overflow_writes(data, rows=10) == 0


def test_cursor_position_beyond_terminal_overflows():
    """curses positioning beyond the last row counts as overflow."""
    data = b"\x1b[12;1Hpipe"
    # 4 chars ('pipe') all written at row 12 in a 10-row terminal
    assert count_overflow_writes(data, rows=10) == 4


def test_sgr_color_sequences_ignored():
    data = b"\x1b[96mcyan\x1b[0m\nrow2"
    assert count_overflow_writes(data, rows=10) == 0
