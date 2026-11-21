"""This machine's Claude accounts: the config folders found, whether each is logged in, the command
that logs one in, adding one as a worker, the default worker, two loops on one account, and the
last usage each worker's sessions reported.

A worker's `claude` is the Claude half of it (docs/settings.md); the Overseer half is config's. The
script's side is the account handling at the top of run-agent-loop.ps1: `Account = N` is
`~/.claude-accountN`, `~/.claude` is the default account and no CLAUDE_CONFIG_DIR at all, and a
folder with no `.credentials.json` is only warned about.

Differences from the script, each deliberate:
- A login is checked with `claude auth status --json`, claude's own check, which makes no model
  call and so costs no usage. On macOS the login is in the keychain and `.credentials.json` means
  nothing; claude's check reads the keychain there. The file is looked at only when claude cannot
  answer, and not on macOS. Untried on macOS and Linux: this machine is Windows.
- `claude auth status` writes into the folder it is given (`.claude.json`, `backups/`), so it is
  never run on a folder that does not exist: that would make one.
- What it says beyond the login - the email, the organisation - is never kept, logged or shown.
- The command shown for logging in is `claude auth login`, where the script says to run claude and
  type /login.
- A worker may sign in with a token (`claude setup-token`) or an API key, held in an environment
  variable its `signIn` names (item claude-container), for a container where nobody runs
  `claude auth login` at a desktop. Its status is claude's, asked with that variable set; the
  secret is never shown, and a variable that is not set is said so with no claude call.
- `accounts login --run` runs the login in the terminal it is given, for `docker exec -it`: claude
  prints an address to open on any machine and takes the code back, with no browser here.
- The last usage a worker's sessions reported is kept, so the page can show it before a loop runs;
  the script only knows it from the stream of the run it is in.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from agentloop import config, session

# How long `claude auth status` may take, in seconds, before the answer counts as unknown.
STATUS_TIMEOUT = 30


def _same_folder(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def default_folder() -> Path:
    """The default account's folder: what claude uses with no CLAUDE_CONFIG_DIR."""
    return Path.home() / ".claude"


def normalize(claude: str | Path | None) -> str | None:
    """A worker's `claude` as the settings write it: `~/.claude` named is the default account,
    null, as the script treats it; any other folder is written under `~` when it is in the home
    folder."""
    if claude is None or str(claude).strip() in ("", "default"):
        return None
    folder = config.expand(str(claude)) if isinstance(claude, str) else claude
    if _same_folder(folder, default_folder()):
        return None
    return config.home_relative(folder.resolve())


def same_account(a: str | None, b: str | None) -> bool:
    """Whether two workers' `claude` are one account."""
    a, b = normalize(a), normalize(b)
    if a is None or b is None:
        return a is None and b is None
    return _same_folder(config.expand(a), config.expand(b))


# Finding them

@dataclass
class Found:
    """A Claude config folder on this PC that no worker names yet."""

    name: str
    claude: str | None
    folder: Path


def suggest_name(claude: str | None) -> str:
    """A worker's name for an account: `default`, or its folder's name less `.claude-`."""
    if normalize(claude) is None:
        return "default"
    return _suggest(config.expand(claude))


def _suggest(folder: Path) -> str:
    base = folder.name.lower()
    base = base[len(".claude-"):] if base.startswith(".claude-") else base.lstrip(".")
    return re.sub(r"[^a-z0-9_-]", "-", base) or "account"


def found(workers: list[config.Worker], home: Path | None = None) -> list[Found]:
    """`~/.claude` as the default account and every `~/.claude-account*` folder, less the ones a
    worker already names, each with a name to suggest."""
    home = home or Path.home()
    candidates: list[tuple[str, Path, bool]] = []
    if (home / ".claude").is_dir():
        candidates.append(("default", home / ".claude", True))
    for folder in sorted(home.glob(".claude-account*"), key=lambda p: p.name.lower()):
        if folder.is_dir():
            candidates.append((_suggest(folder), folder, False))
    taken = {w.name for w in workers}
    result = []
    for name, folder, is_default in candidates:
        claude = None if is_default else config.home_relative(folder)
        # same_account counts a worker naming ~/.claude by its path as the default account too.
        if any(same_account(w.claude, claude) for w in workers):
            continue
        suggested, n = name, 2
        while suggested in taken:
            suggested, n = f"{name}-{n}", n + 1
        taken.add(suggested)
        result.append(Found(suggested, claude, folder))
    return result


# Logged in, or not

@dataclass
class Login:
    """Whether an account is logged in.

    state is `in`, `out`, `no-folder` (the folder is not there: nobody has logged in to it) or
    `unknown`. source says how that was found: `claude` (`claude auth status`), `file` (its
    `.credentials.json`, when claude could not answer) or `folder`. method and plan are claude's
    `authMethod` and `subscriptionType`; detail says why the state is unknown."""

    state: str
    source: str
    method: str | None = None
    plan: str | None = None
    detail: str | None = None

    def line(self) -> str:
        if self.state == "in":
            how = ", ".join(x for x in (self.method, self.plan) if x)
            said = " (by its .credentials.json)" if self.source == "file" else ""
            return f"logged in{f' ({how})' if how else ''}{said}"
        if self.state == "out":
            if self.source == "variable":
                return f"not signed in: {self.detail}"
            said = " (no .credentials.json)" if self.source == "file" else ""
            return f"not logged in{said}"
        if self.state == "no-folder":
            return "no such folder: not logged in"
        return f"unknown: {self.detail}" if self.detail else "unknown"


def status(claude_exe: str, claude: str | None, timeout: float = STATUS_TIMEOUT, macos: bool | None = None,
           sign_in: dict | None = None, environ: dict[str, str] | None = None) -> Login:
    """Whether the account in `claude` (a worker's: a folder, or None for the default) is logged in,
    asked of claude itself in the environment its sessions get. A worker that signs in with a token
    or a key (`sign_in`) is asked with it set, and needs no folder: claude makes one as a session
    would."""
    macos = sys.platform == "darwin" if macos is None else macos
    folder = session.config_dir(claude)
    variables: dict[str, str | None] = {"CLAUDE_CONFIG_DIR": str(folder) if claude else None}
    # A token or key the app was started with reaches every worker with no signIn, as a container's
    # CLAUDE_CODE_OAUTH_TOKEN does: claude is asked with it, folder or none.
    inherited = any((os.environ if environ is None else environ).get(name) for name in config.SIGN_IN.values())
    if sign_in:
        try:
            variables.update(session.sign_in_variables(sign_in, environ))
        except session.SessionError:
            return Login("out", "variable", detail=f"{next(iter(sign_in.values()))} is not set")
    elif not folder.is_dir() and not inherited:
        return Login("no-folder", "folder")
    try:
        stdout = _run_ended(
            [claude_exe, "auth", "status", "--json"], session.build_env(variables), timeout,
        )
        said = json.loads(stdout.decode("utf-8", errors="replace"))
        logged = said.get("loggedIn") if isinstance(said, dict) else None
        if not isinstance(logged, bool):
            raise ValueError("no loggedIn")
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        # The error's type only: its text could name a path or what claude printed.
        if sign_in:
            # The folder's .credentials.json says nothing of a token or a key.
            return Login("unknown", "claude", detail=f"claude auth status failed ({type(error).__name__})")
        return _by_file(folder, type(error).__name__, macos)
    if not logged:
        return Login("out", "claude")
    method, plan = said.get("authMethod"), said.get("subscriptionType")
    return Login("in", "claude", method if isinstance(method, str) else None, plan if isinstance(plan, str) else None)


def _run_ended(argv: list[str], env: dict[str, str], timeout: float) -> bytes:
    """What the command printed, or TimeoutExpired once it has been ended. On Windows claude is a
    .cmd whose node outlives cmd.exe and holds the pipe open, so ending only the process found
    would leave this waiting for the child: the whole tree is ended, as the OS ends one."""
    process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, **session._popen_options())
    try:
        stdout, _ = process.communicate(timeout=timeout)
        return stdout
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate()
        raise


def _by_file(folder: Path, why: str, macos: bool) -> Login:
    """The script's check, for when claude cannot answer. On macOS the login is in the keychain, and
    the file says nothing."""
    detail = f"claude auth status failed ({why})"
    if macos:
        return Login("unknown", "claude", detail=detail)
    state = "in" if (folder / ".credentials.json").is_file() else "out"
    return Login(state, "file", detail=detail)


def login_command(claude: str | None, windows: bool | None = None) -> str:
    """The command a person runs in a terminal to log the account in, for this OS's shell:
    PowerShell's on Windows, sh's on macOS and Linux."""
    windows = os.name == "nt" if windows is None else windows
    if claude is None:
        if windows:
            return "Remove-Item Env:CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue; claude auth login"
        return "env -u CLAUDE_CONFIG_DIR claude auth login"
    folder = str(session.config_dir(claude))
    if windows:
        return "$env:CLAUDE_CONFIG_DIR = '" + folder.replace("'", "''") + "'; claude auth login"
    return f"CLAUDE_CONFIG_DIR={shlex.quote(folder)} claude auth login"


def sign_in_line(claude: str | None, sign_in: dict | None, environ: dict[str, str] | None = None) -> str:
    """How a worker's sessions sign in to Claude, naming the variable a secret is in and never the
    secret: its token or key, else one the app's environment gives every worker, else the login
    in its folder."""
    environ = os.environ if environ is None else environ
    if sign_in:
        kind, variable = next(iter(sign_in.items()))
        unset = "" if environ.get(variable) else f" - {variable} is not set"
        return f"signs in with {_WHAT[kind]} from {variable}{unset}"
    inherited = [(kind, name) for kind, name in config.SIGN_IN.items() if environ.get(name)]
    if inherited:
        kind, name = inherited[0]
        return f"signs in with {_WHAT[kind]} from {name}, as the app was started"
    return f"signs in by the login in {_slashes(session.config_dir(claude))}"


_WHAT = {"token": "a token", "apiKey": "an API key, billed per use,"}


def _slashes(path: Path) -> str:
    return str(path).replace("\\", "/")


def sign_in_hint(sign_in: dict) -> str:
    """What a person does to give a token or key worker its secret: the command that makes a token,
    or where an API key comes from, and the variable to put it in where the app runs."""
    kind, variable = next(iter(sign_in.items()))
    if kind == "token":
        return f"claude setup-token, then set {variable} to the token it prints, where agentloop runs"
    return f"set {variable} to an API key from the Claude Console, where agentloop runs"


def login_argv(claude_exe: str, sign_in: dict | None) -> list[str]:
    """What `accounts login --run` runs: `claude setup-token` for a token worker, which prints the
    token for the person to keep, else `claude auth login`."""
    return [claude_exe, "setup-token"] if sign_in else [claude_exe, "auth", "login"]


def run_login(claude_exe: str, claude: str | None, sign_in: dict | None) -> int:
    """Runs the login in this terminal, with the worker's CLAUDE_CONFIG_DIR, its folder made first so
    that a container's volume holds it. claude prints an address to open on any machine and reads
    the code back, so no browser is needed here."""
    if sign_in and next(iter(sign_in)) != "token":
        raise session.SessionError("An API key is made in the Claude Console, not by claude: " + sign_in_hint(sign_in) + ".")
    variables: dict[str, str | None] = {"CLAUDE_CONFIG_DIR": None}
    if claude and not sign_in:
        folder = session.config_dir(claude)
        folder.mkdir(parents=True, exist_ok=True)
        variables["CLAUDE_CONFIG_DIR"] = str(folder)
    return subprocess.run(login_argv(claude_exe, sign_in), env=session.build_env(variables)).returncode


# Adding one, and the default

def add_worker(name: str, claude: str | None, path: Path | None = None) -> config.Settings:
    """A worker on a Claude account, with no Overseer half, added to the settings. A second worker
    of the name, or on the same account, is refused; the folder need not exist yet, since logging
    in makes it."""
    settings = config.load_settings(path)
    where = settings.path.name
    if not config.NAME.match(name):
        raise config.ConfigError(where, "workers", f'"{name}" is not a name: lower case letters, digits, - and _')
    if settings.worker(name):
        raise config.ConfigError(where, "workers", f'there is a worker named "{name}" already')
    claude = normalize(claude)
    other = next((w for w in settings.workers if same_account(w.claude, claude)), None)
    if other:
        raise config.ConfigError(where, "workers", f"worker {other.name} is on that account already")
    raw = settings.raw
    raw.setdefault("workers", []).append({"name": name, "claude": claude, "overseer": None})
    return config.save_settings(raw, settings.path)


def set_default(name: str, path: Path | None = None) -> config.Settings:
    """The worker a loop made on the page starts with."""
    settings = config.load_settings(path)
    if not settings.worker(name):
        raise config.ConfigError(settings.path.name, "defaultWorker", f'no worker named "{name}"')
    settings.raw["defaultWorker"] = name
    return config.save_settings(settings.raw, settings.path)


# Two loops on one account

def sharing(worker: str, running: dict[str, str], settings: config.Settings) -> list[str]:
    """The running loops, of `running` (a loop's name to its worker's), already on the account a
    loop on `worker` would run on, by name."""
    mine = settings.worker(worker)
    if mine is None:
        return []
    return [loop for loop, theirs in running.items()
            if (w := settings.worker(theirs)) is not None and same_account(w.claude, mine.claude)]


def sharing_line(worker: str, loops: list[str]) -> str | None:
    if not loops:
        return None
    named = ", ".join(loops)
    verb = "is" if len(loops) == 1 else "are"
    return f"{named} {verb} already running on {worker}'s account; the loops share its 5-hour and weekly usage."


# The last usage reported

def usage_file(worker: str) -> Path:
    return config.paths().data / "workers" / worker / "usage.json"


def save_usage(worker: str, rate: dict, at: float | None = None) -> None:
    """The last `rate_limit_info` a session on this worker reported, and when. Written to a
    temporary file and renamed over the old one; several loops in one process may write it, so the
    temporary name is the OS's, not the process's."""
    target = usage_file(worker)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {"at": time.time() if at is None else at, "rate": rate}
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".usage.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(json.dumps(data, indent=2) + "\n")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def last_usage(worker: str) -> tuple[float, dict] | None:
    """When the worker's sessions last reported their usage, and what; None if they never did, or
    the file is not what this writes."""
    try:
        data = json.loads(usage_file(worker).read_text(encoding="utf-8"))
        at, rate = data["at"], data["rate"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(at, (int, float)) or not isinstance(rate, dict):
        return None
    return float(at), rate
