"""A project's setup script: the tools its sessions need, installed on this machine (item project-setup).

A project's definition may tick one of its tools as the setup script for the systems that tool is
for (overseer:def-setup) - `setup.sh` for Linux and macOS, say, and `setup.ps1` for Windows. It is
fetched and hash-checked as every tool is, and only once the definition that brought it is
accepted: until then it waits in the pending folder, never here.

AgentLoop runs it before a loop's first session, and again whenever the script's sha256 changes or
its last run failed. It runs as the person running AgentLoop - never as root - in a folder of the
project's own, `<data>/projects/<project>/env`, which it is given as `AGENTLOOP_PROJECT_ENV`
along with `AGENTLOOP_SYSTEM` (windows, macos or linux) and `AGENTLOOP_PROJECT`. That folder is
shared by every loop of the project, and outlives upgrades and, under `/data`, a container's
redeploys. The script installs there, and leaves `session-env.txt` in it saying what every session
of the project then gets:

    # a comment
    PATH={env}/cargo/bin        a folder put in front of PATH, in the order written
    CARGO_HOME={env}/cargo      a variable set

`{env}` is the project's folder, so the file stays right if the data folder moves. Variables the
app gives a session itself - OVERSEER_*, CLAUDE_*, ANTHROPIC_*, AGENT_*, AGENTLOOP_* - are refused,
as is a line that is neither. The script should be safe to run again, skipping what is already
there: "Set up again" on the page, or `agentloop setup <loop> --again`, runs it on request.

What it did last is `setup.json` beside the folder: the script's sha256, the system, when, whether
it succeeded, and the end of its output, for the page to show. System packages are not the
script's to install, since that needs root.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from agentloop import config

# What the script leaves in the project's folder, and what is kept of its last run beside it.
ENV_FILE = "session-env.txt"
RECORD = "setup.json"
# How many of its last lines of output are kept, for the page and a failure's message.
TAIL = 40
# The variables a session's own environment holds, which a project's file may not set: the
# tracker's, Claude's and its sign-in, the loop's own.
REFUSED_PREFIXES = ("OVERSEER_", "CLAUDE_", "ANTHROPIC_", "AGENT_", "AGENTIC_", "AGENTLOOP_")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SetupError(Exception):
    """A setup that could not run, or did not succeed; its message is for the person, and names
    no secret."""


@dataclass
class SessionEnv:
    """What a project's setup gives its sessions: folders to put in front of PATH, in order, and
    variables to set."""

    paths: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    # The file it was read from, for the system prompt to name.
    file: Path | None = None

    def apply(self, env: dict[str, str | None], base: dict[str, str] | None = None) -> None:
        """Into a session's variables: each variable set, and PATH given these folders in front of
        the one in `base` (the app's own environment, by default)."""
        env.update(self.variables)
        if self.paths:
            base = os.environ if base is None else base
            # os.environ on Windows takes PATH in any case; a plain dict may hold it as Path.
            name = next((k for k in base if k.upper() == "PATH"), "PATH")
            rest = env.get(name) or base.get(name) or ""
            env[name] = os.pathsep.join([*self.paths, *([rest] if rest else [])])


def env_dir(project: str) -> Path:
    """The project's own folder for what its setup installs."""
    return config.paths().data / "projects" / project / "env"


def record_path(project: str) -> Path:
    return env_dir(project).parent / RECORD


def record(project: str) -> dict | None:
    """What the project's last setup did - {"sha256", "system", "at", "ok", "tail"} - or None."""
    try:
        found = json.loads(record_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def forget(project: str) -> None:
    """Forgets the last setup, so that the next start runs the script again."""
    try:
        record_path(project).unlink()
    except FileNotFoundError:
        pass


def due(project: str, tool: dict) -> bool:
    """Whether the setup `tool` is to run: never run, run for another script or system, or failed."""
    last = record(project)
    return (last is None or last.get("sha256") != tool["sha256"] or last.get("system") != config.this_system()
            or not last.get("ok"))


def state(project: str, tool: dict | None, mode: str) -> dict:
    """The setup as the page shows it: {"state", "file", "at", "tail"}. The state is "none" when
    the definition names no setup for this system, "off" when the loop's setting says so, else
    "due" when it is to run at the next start, "ok" or "failed"."""
    if tool is None:
        return {"state": "none"}
    shown = {"file": tool["file"]}
    if mode == "off":
        return {"state": "off", **shown}
    last = record(project)
    if last is None or last.get("sha256") != tool["sha256"] or last.get("system") != config.this_system():
        return {"state": "due", **shown}
    return {"state": "ok" if last.get("ok") else "failed", "at": last.get("at"), "tail": last.get("tail") or [], **shown}


# The file it leaves

def parse_env_file(path: Path, folder: Path) -> SessionEnv:
    """The file a setup leaves, read; none at all is nothing to apply. A line the format does not
    allow is a SetupError naming its number."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return SessionEnv()
    except (OSError, UnicodeDecodeError) as error:
        raise SetupError(f"{ENV_FILE} could not be read ({type(error).__name__})") from None
    found = SessionEnv(file=path)
    here = str(folder).replace("\\", "/")
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, equals, value = line.partition("=")
        name, value = name.strip(), value.strip().replace("{env}", here)
        if not equals or not NAME.match(name):
            raise SetupError(f"{ENV_FILE} line {number}: not NAME=value or PATH=<folder>")
        if name.upper() == "PATH":
            if not value:
                raise SetupError(f"{ENV_FILE} line {number}: PATH= names no folder")
            found.paths.append(str(Path(value)))
        elif name.upper().startswith(REFUSED_PREFIXES):
            raise SetupError(f"{ENV_FILE} line {number}: {name} is the app's to set for a session, not a project's")
        else:
            found.variables[name] = value
    return found


def session_env(project: str) -> SessionEnv:
    """What the project's last setup left for its sessions. Nothing when none ran; a SetupError
    when the file cannot be read as the format says."""
    folder = env_dir(project)
    return parse_env_file(folder / ENV_FILE, folder)


# Running it

def command(script: Path) -> list[str]:
    """How a script is started, by its kind: PowerShell for .ps1 (pwsh when there is one), bash or
    sh for .sh, this Python for .py, cmd for a batch file, and anything else as itself. Never a
    shell built into a command line."""
    suffix = script.suffix.lower()
    if suffix == ".ps1":
        shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        return [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    if suffix == ".sh":
        return [shutil.which("bash") or shutil.which("sh") or "sh", str(script)]
    if suffix == ".py":
        return [sys.executable, str(script)]
    if suffix in (".cmd", ".bat"):
        return [os.environ.get("COMSPEC") or "cmd.exe", "/d", "/c", str(script)]
    return [str(script)]


def _as_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return geteuid is not None and geteuid() == 0


def run(project: str, script: Path, tool: dict, say: Callable[[str], None],
        on_start: Callable[[subprocess.Popen], None] | None = None) -> SessionEnv:
    """Runs the setup script `script` - the fetched copy of `tool` - for `project`, each line of
    its output given to `say`, and records how it went. What it leaves for the sessions when it
    succeeds; a SetupError, with the end of its output, when it does not."""
    folder = env_dir(project)
    tail: deque[str] = deque(maxlen=TAIL)

    def done(ok: bool, why: str | None = None) -> None:
        if why:
            tail.append(why)
        config.save_json(record_path(project), {
            "sha256": tool["sha256"], "system": config.this_system(), "file": tool["file"], "ok": ok,
            "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), "tail": list(tail),
        })

    if _as_root():
        why = "the setup script is not run as root: run AgentLoop as the person whose tools these are"
        done(False, why)
        raise SetupError(why)
    folder.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"AGENTLOOP_PROJECT_ENV": str(folder).replace("\\", "/"), "AGENTLOOP_SYSTEM": config.this_system(),
                "AGENTLOOP_PROJECT": project})
    try:
        process = subprocess.Popen(command(script), cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as error:
        why = f"{tool['file']} could not be started ({type(error).__name__})"
        done(False, why)
        raise SetupError(why) from None
    # Left in the loop's own process group, not given one of its own as a session's claude is: a
    # kill that ends the loop's tree ends the script with it. `on_start` may name it sooner.
    if on_start:
        on_start(process)
    assert process.stdout is not None
    with process.stdout:
        for raw in process.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            tail.append(line)
            say(line)
    code = process.wait()
    if code != 0:
        why = f"{tool['file']} ended with exit code {code}"
        done(False, why)
        raise SetupError(why)
    try:
        found = session_env(project)
    except SetupError as error:
        done(False, str(error))
        raise
    done(True)
    return found


def tail_text(project: str, lines: int = 15) -> str:
    """The end of the last setup's output, for a failure's message."""
    last = record(project) or {}
    return "\n".join((last.get("tail") or [])[-lines:])
