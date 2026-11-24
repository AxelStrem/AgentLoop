"""Starting `agentloop serve` at login, so that the loops nobody ended come back after a restart
without anyone opening anything (item loginstart). `agentloop login-start on|off|status`; the
installers offer `on`, and the page names it while loops wait to come back.

Each system has its own place, written for this user alone and needing no admin:

- Windows: a value under HKCU's `Run` key, which has pythonw run a small starter script,
  `<data>/loginstart/agentloop-serve.pyw`; the starter runs serve with a console of its own and no
  window (item loginstart-novbs). A hidden console rather than none because serve and its loops
  start git and claude, console programs, which would each open a window of their own under a
  process with no console. An older `on` wrote `agentloop-serve.vbs` to the Startup folder instead,
  which needs VBScript, which Windows is phasing out: `on` and `off` remove it, and `status` says
  when it is the one there.
- macOS: a LaunchAgent, `~/Library/LaunchAgents/<LABEL>.plist`, with `RunAtLoad`.
- Linux: a systemd user unit, `~/.config/systemd/user/agentloop-serve.service`, enabled for
  `default.target`. Where there is no systemd user session - a container, say - nothing is written.

`on` arranges the next login and starts nothing now: the installer starts serve itself right after,
and a second serve would only find the port taken. What runs is the absolute path of the
`agentloop` this command was started as - a build's launcher, when it is a build - with
`serve --log <data>/logs/serve.log`, never a shell line. On macOS and Linux the PATH and
AGENTLOOP_HOME of the shell that ran `on` go with it, since launchd and systemd start their programs
with a bare PATH that finds neither git nor claude.

The tests run the Windows starter for real, on a stand-in command, with the registry stood in for;
the files for macOS and Linux are tested as text, and their `systemctl` calls with stand-ins (item
other-os tries them).
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from agentloop import build, config

LABEL = "io.github.axelstrem.agentloop.serve"
UNIT = "agentloop-serve.service"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "AgentLoop serve"
STARTER = "agentloop-serve.pyw"
# What an older `on` wrote to the Startup folder, removed wherever it is found.
OLD_VBS = "agentloop-serve.vbs"
# The variables the started serve is given, from the shell that ran `on`, where the system would
# not pass them on.
CARRIED = ("PATH", "AGENTLOOP_HOME")


class LoginStartError(Exception):
    """Something that stops `login-start`, said so the owner can act on it."""


@dataclass
class Status:
    system: str
    on: bool
    path: Path | None
    # Why this machine cannot start serve at login, when it cannot.
    unsupported: str | None = None
    # Something more the owner should know, as that an older login start is the one there.
    note: str | None = None


# What runs

def log_path() -> Path:
    return config.paths().data / "logs" / "serve.log"


def command(argv0: str | None = None) -> list[str]:
    """The command that starts this agentloop: a build's launcher, which runs whichever build is
    current at login; else the console script this was started as; else this Python with
    `-m agentloop`."""
    record = build.read_build()
    if record is not None:
        # <to>/versions/<id>/agentloop is the package of a build.
        launcher = build.package_dir().parent.parent.parent / build.launcher_name()
        if launcher.is_file():
            return [str(launcher)]
    given = Path(argv0 if argv0 is not None else sys.argv[0])
    candidates = [given]
    if os.name == "nt" and given.suffix.lower() != ".exe":
        candidates.append(given.with_name(given.name + ".exe"))
    for candidate in candidates:
        if candidate.stem.lower() == "agentloop" and candidate.is_file():
            # Absolute, but not resolved: uv's ~/.local/bin/agentloop is a link into the tool's
            # environment, and the link is the name that outlives an upgrade.
            return [os.path.abspath(candidate)]
    return [sys.executable, "-m", "agentloop"]


def serve_args(argv0: str | None = None) -> list[str]:
    return [*command(argv0), "serve", "--log", str(log_path())]


def carried_env() -> dict[str, str]:
    return {name: os.environ[name] for name in CARRIED if os.environ.get(name)}


# Windows

def startup_folder() -> Path:
    roaming = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(roaming) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def starter_path() -> Path:
    return config.paths().data / "loginstart" / STARTER


def _pythonw() -> Path:
    """The windowless Python beside the one this runs on: python.org's installs, venvs and uv's
    tool environments all have one."""
    return Path(sys.executable).with_name("pythonw.exe")


def starter_text(args: list[str], env: dict[str, str]) -> str:
    """The starter pythonw runs at login: serve's command with a console of its own and no window
    (CREATE_NO_WINDOW), so that git and claude, which serve and its loops start, share that hidden
    console rather than each opening a window. The arguments and the environment are Python
    literals, so no command-line quoting applies to them. It needs nothing but the standard
    library, as `-I` gives it no other."""
    # PATH is the user's own at login already; AGENTLOOP_HOME goes on only where it was set.
    carried = {name: value for name, value in env.items() if name != "PATH"}
    return "\n".join([
        "# Written by agentloop login-start on: starts agentloop serve at login, with no window.",
        "# agentloop login-start off removes it, and the Run value that starts it.",
        "import os",
        "import subprocess",
        "",
        f"ARGS = {args!r}",
        f"ENV = {carried!r}",
        "",
        "os.environ.update(ENV)",
        "subprocess.Popen(ARGS, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,",
        "                 creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)",
        "",
    ])


def run_command(pythonw: Path, starter: Path) -> str:
    """The Run value: pythonw, isolated, on the starter. A Windows path cannot hold a quote, so
    quoting each whole is all the escaping it needs."""
    for path in (pythonw, starter):
        if '"' in str(path):
            raise LoginStartError("a path holds a quote, which a Windows command line cannot carry")
    return f'"{pythonw}" -I "{starter}"'


def _run_get() -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
    except FileNotFoundError:
        return None
    return str(value)


def _run_set(command_line: str) -> None:
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command_line)


def _run_delete() -> bool:
    """Removes the Run value; whether there was one."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except FileNotFoundError:
        return False
    return True


def _has_vbscript() -> bool:
    """Whether Windows Script Host could still run the .vbs an older login-start on wrote: VBScript
    is a feature on demand from Windows 11 24H2, to be off by default and then removed."""
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return (Path(root) / "System32" / "vbscript.dll").is_file()


# macOS

def launch_agent() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def plist_bytes(args: list[str], env: dict[str, str]) -> bytes:
    agent = {"Label": LABEL, "ProgramArguments": args, "RunAtLoad": True, "ProcessType": "Interactive"}
    if env:
        agent["EnvironmentVariables"] = env
    return plistlib.dumps(agent)


# Linux

def _systemd_user_folder() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base and Path(base).is_absolute() else Path.home() / ".config"
    return root / "systemd" / "user"


def unit_path() -> Path:
    return _systemd_user_folder() / UNIT


def wants_link() -> Path:
    return _systemd_user_folder() / "default.target.wants" / UNIT


def _unit_quote(arg: str) -> str:
    """One word of ExecStart=: in double quotes, with backslash and quote escaped as C does, and
    `%` doubled, which systemd would read as a specifier, and `$`, which it would expand."""
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def unit_text(args: list[str], env: dict[str, str]) -> str:
    for arg in [*args, *env.values()]:
        if "\n" in arg or "\r" in arg:
            raise LoginStartError("an argument holds a line break, which a systemd unit cannot carry")
    lines = [
        "# Written by agentloop login-start on: starts agentloop serve at login.",
        "# agentloop login-start off removes it.",
        "[Unit]",
        "Description=AgentLoop's page, which starts again the loops nobody ended",
        "",
        "[Service]",
        "Type=simple",
        # Stopping the service ends serve alone: the loops it started go on, as they do when serve
        # is ended anywhere else.
        "KillMode=process",
        f"ExecStart={' '.join(_unit_quote(arg) for arg in args)}",
    ]
    for name, value in env.items():
        lines.append(f"Environment={_unit_quote(f'{name}={value}')}")
    lines += ["", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=60)


def _no_systemd() -> str | None:
    """Why there is no systemd here, from the files alone (as sd_booted does): the page asks on
    every refresh, and starts no process for it."""
    if not Path("/run/systemd/system").is_dir():
        return "this system does not run systemd, which starts serve at login on Linux"
    return None


def _check_user_session() -> None:
    reason = _no_systemd()
    if reason is None and shutil.which("systemctl") is None:
        reason = "systemctl is not on PATH"
    if reason is None:
        try:
            done = _systemctl("show-environment")
        except (OSError, subprocess.SubprocessError) as error:
            raise LoginStartError(f"systemctl --user could not be run ({type(error).__name__})") from None
        if done.returncode != 0:
            reason = "there is no systemd user session here (systemctl --user show-environment failed)"
    if reason is not None:
        raise LoginStartError(f"{reason}: nothing was written. Start agentloop serve some other way at login.")


# The three

def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def status() -> Status:
    """Whether serve starts at login, from the files alone."""
    system = config.this_system()
    if system == "windows":
        if _run_get() is not None:
            return Status(system, True, starter_path())
        old = startup_folder() / OLD_VBS
        if not old.is_file():
            return Status(system, False, starter_path())
        if _has_vbscript():
            return Status(system, True, old, note="this is the VBScript start of an older agentloop, which Windows is "
                                                  "phasing out: agentloop login-start on replaces it")
        return Status(system, False, old, note=f"{OLD_VBS} is in the Startup folder, but this Windows has no VBScript "
                                               "to run it: agentloop login-start on replaces it")
    if system == "macos":
        path = launch_agent()
        return Status(system, path.is_file(), path)
    path = unit_path()
    on = path.is_file() and (wants_link().is_symlink() or wants_link().exists())
    return Status(system, on, path, None if on else _no_systemd())


def turn_on(argv0: str | None = None) -> tuple[Path, list[str]]:
    """Writes this system's login start, replacing one written before; the file and what it runs."""
    args = serve_args(argv0)
    env = carried_env()
    system = config.this_system()
    if system == "windows":
        pythonw = _pythonw()
        if not pythonw.is_file():
            raise LoginStartError("there is no pythonw.exe beside the Python agentloop runs on, which starts serve "
                                  "with no window: nothing was written")
        path = starter_path()
        line = run_command(pythonw, path)
        _write(path, starter_text(args, env).encode("utf-8"))
        _run_set(line)
        old = startup_folder() / OLD_VBS
        if old.is_file():
            old.unlink()
        return path, args
    if system == "macos":
        path = launch_agent()
        _write(path, plist_bytes(args, env))
        # Loaded at the next login, as launchd loads every file in LaunchAgents; lint it now.
        if shutil.which("plutil"):
            try:
                done = subprocess.run(["plutil", "-lint", str(path)], capture_output=True, text=True, timeout=60)
            except (OSError, subprocess.SubprocessError):
                done = None
            if done is not None and done.returncode != 0:
                path.unlink()
                raise LoginStartError("plutil found the LaunchAgent malformed; it was removed")
        return path, args
    _check_user_session()
    path = unit_path()
    _write(path, unit_text(args, env).encode("utf-8"))
    for step in (("daemon-reload",), ("enable", UNIT)):
        done = _systemctl(*step)
        if done.returncode != 0:
            raise LoginStartError(f"systemctl --user {' '.join(step)} failed: {done.stderr.strip() or done.returncode}")
    return path, args


def turn_off() -> Path | None:
    """Removes this system's login start; the file removed, or None when there was none. A serve
    started at this login goes on until it is ended."""
    system = config.this_system()
    if system == "windows":
        removed = None
        if _run_delete():
            removed = starter_path()
        for path in (starter_path(), startup_folder() / OLD_VBS):
            if path.is_file():
                path.unlink()
                removed = removed or path
        return removed
    if system == "macos":
        # Not booted out of launchd, which would end a serve it started at this login: without
        # the file, the next login does not load it.
        path = launch_agent()
    else:
        path = unit_path()
        if path.is_file() and shutil.which("systemctl"):
            try:
                _systemctl("disable", UNIT)
            except (OSError, subprocess.SubprocessError):
                pass
        # Where systemctl could not, the link is removed by hand: enable made only that.
        link = wants_link()
        if link.is_symlink() or link.exists():
            link.unlink()
    if not path.is_file():
        return None
    path.unlink()
    if system == "linux" and shutil.which("systemctl"):
        try:
            _systemctl("daemon-reload")
        except (OSError, subprocess.SubprocessError):
            pass
    return path


# The command

def run(action: str) -> int:
    """`agentloop login-start on|off|status`. status exits 0 when on and 1 when off, so that an
    installer can ask it."""
    try:
        if action == "on":
            path, args = turn_on()
            print(f"on       serve starts at login, from {path}")
            if config.this_system() == "windows":
                print(f"         run by pythonw, from the value \"{RUN_VALUE}\" under HKCU\\{RUN_KEY}")
            print(f"runs     {' '.join(args)}")
            print("Nothing was started now: agentloop serve --open starts the page for this login.")
            return 0
        if action == "off":
            path = turn_off()
            print(f"off      removed {path}" if path else "off      serve was not started at login")
            return 0
        found = status()
        if found.on:
            print(f"on       {found.path}")
            if found.note:
                print(f"         {found.note}")
            return 0
        reason = found.unsupported or found.note
        print("off" + (f"      {reason}" if reason else ""))
        return 1
    except LoginStartError as error:
        print(f"agentloop login-start: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"agentloop login-start: {type(error).__name__}: {error.strerror or error}", file=sys.stderr)
        return 2
