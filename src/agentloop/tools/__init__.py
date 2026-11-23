"""The tools a session uses, as the app's own copies: the loop hands sessions these, not
AgenticLoop's.

    tracker.py      the command that reads and edits a tracker
    features.py     the command for a project's feature lists
    guard.py        the PreToolUse hook a session's settings file names
    review-run.py   the review of a run (item review)
    whisper-run.py  local Whisper's transcription, run in its own environment (item voice-engines)
    AUTOTASK.md     the workflow every session's system prompt starts with
    AUTOTASK-windows.md  what of it holds on Windows only, added there alone

They were copied byte for byte from AgenticLoop at commit 169e1c4. Since then AUTOTASK.md names
this machine's folders by placeholder, and its Windows part is AUTOTASK-windows.md (item
neutral); nothing else in them was changed. They stay scripts, run as subprocesses as run-agent-loop.ps1 runs them: the loop reads
their one line of JSON, and a refusal's sys.exit, a document's lock and the OVERSEER_* variables
each session is given stay in that process. AgenticLoop's copies are frozen until item
switchover; a fix made here is said in its item's comment, for the owner to copy back if the
running loop needs it.
"""

from __future__ import annotations

from pathlib import Path

NAMES = ("tracker.py", "features.py", "guard.py", "review-run.py", "whisper-run.py", "AUTOTASK.md", "AUTOTASK-windows.md")


def path(name: str) -> Path:
    """Where the tool named so is, beside this file - in the repository or in a build alike."""
    if name not in NAMES:
        raise ValueError(f"no tool named {name!r}")
    return Path(__file__).resolve().parent / name
