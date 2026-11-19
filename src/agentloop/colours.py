"""The colours the loop's lines are printed in: the script's `Write-Host -ForegroundColor`s, whose
meanings the page keeps - red a stop or a failure, yellow the iteration and a stop asked for, cyan
the item and its outcome, dark grey a tool call and a session's figures, dark cyan the usage line,
dark green a commit, dark yellow a warning, green the loop's end.

A coloured line goes out as ANSI SGR: the console's dark colours as 30-37, the bright ones as
90-97, then a reset. `agentloop run` writes them to a terminal, and always to the output.log the
runner gives it, from which the page draws them.
"""

from __future__ import annotations

import os
import re
from typing import IO

# The console colours by their .NET names, as the script gives them, and their SGR codes.
CODES = {
    "Black": 30, "DarkRed": 31, "DarkGreen": 32, "DarkYellow": 33,
    "DarkBlue": 34, "DarkMagenta": 35, "DarkCyan": 36, "Gray": 37,
    "DarkGray": 90, "Red": 91, "Green": 92, "Yellow": 93,
    "Blue": 94, "Magenta": 95, "Cyan": 96, "White": 97,
}

_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def plain(line: str, colour: str | None = None) -> str:
    return line


def ansi(line: str, colour: str | None = None) -> str:
    """The line in its colour, for a terminal or the output log; as it is when it has none, or is
    empty."""
    if not colour or not line:
        return line
    return f"\x1b[{CODES[colour]}m{line}\x1b[0m"


def strip(text: str) -> str:
    return _ESCAPE.sub("", text)


def wanted(choice: str, stream: IO) -> bool:
    """Whether to colour what goes to `stream`: `always`, `never`, or `auto` - a terminal, unless
    NO_COLOR is set (no-color.org)."""
    if choice == "always":
        return True
    if choice == "never":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    return os.name != "nt" or _windows_console(stream)


def _windows_console(stream: IO) -> bool:
    # A Windows console reads the codes only once asked to (ENABLE_VIRTUAL_TERMINAL_PROCESSING);
    # one too old to be asked is given plain text.
    try:
        import ctypes
        import msvcrt

        kernel32 = ctypes.windll.kernel32
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(mode.value & 0x0004 or kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError, ValueError):
        return False
