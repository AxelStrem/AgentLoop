"""Homebrew for a project's sessions, on a machine that turns it on (item brew-tools).

The container image sets AGENTLOOP_BREW=1 and keeps Homebrew in a volume of its own, shared by
every project. A project lists what its sessions need in a `Brewfile`, Homebrew's own format, at
the top of its first repository, so the list is versioned with the work. Before each session the
loop installs what it lists and is not installed yet; a session that needs a tool adds its line and
runs the same `brew bundle install` itself (the guard refuses `brew install`), so the Brewfile stays
the one record. Anywhere AGENTLOOP_BREW is not 1 - a person's own Mac or Windows PC - none of this
runs, and sessions are not told of it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from agentloop import config

SWITCH = "AGENTLOOP_BREW"
BREWFILE = "Brewfile"


class BrewError(Exception):
    """An install that could not run or did not succeed; its message is for the person."""


def enabled(environ: dict[str, str] | None = None) -> bool:
    environ = os.environ if environ is None else environ
    return environ.get(SWITCH) == "1" and config.this_system() != "windows"


def brewfile(repo: Path) -> Path:
    return repo / BREWFILE


def install_command(file: Path) -> list[str]:
    """What installs a Brewfile's tools: the loop's, and the one sessions are told to run."""
    return ["brew", "bundle", "install", "--no-upgrade", f"--file={str(file).replace(chr(92), '/')}"]


def _brew() -> str:
    found = shutil.which("brew")
    if not found:
        raise BrewError(f"{SWITCH} is 1, but brew is not on PATH")
    return found


def satisfied(file: Path) -> bool:
    """Whether everything the Brewfile lists is installed."""
    done = subprocess.run([_brew(), "bundle", "check", "--no-upgrade", f"--file={file}"], stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return done.returncode == 0


def install(file: Path, say: Callable[[str], None], on_start: Callable[[subprocess.Popen], None] | None = None) -> None:
    """Installs what the Brewfile lists, each line of brew's output given to `say`; a BrewError
    when brew fails; what it printed is in the loop's output already."""
    argv = [_brew(), *install_command(file)[1:]]
    try:
        process = subprocess.Popen(argv, cwd=file.parent, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
    except OSError as error:
        raise BrewError(f"brew could not be started ({type(error).__name__})") from None
    if on_start:
        on_start(process)
    assert process.stdout is not None
    with process.stdout:
        for raw in process.stdout:
            say(raw.decode("utf-8", "replace").rstrip("\r\n"))
    code = process.wait()
    if code != 0:
        raise BrewError(f"brew bundle install ended with exit code {code}")
