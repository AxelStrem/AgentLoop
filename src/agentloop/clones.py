"""A project's repositories cloned where the person adding a loop says (item clone).

A definition's repository may name the address `git clone` takes, and a branch
(overseer:repo-url). When it does, the page's form and `agentloop add` offer to clone it into the
folder asked for, instead of asking where a clone already is. That folder is one of three things:
missing or empty, and then it is cloned into; already a clone of that address, and then it is used
as it is; or anything else, which is refused by name and never touched.

The clone is the person's own git with their own credentials. One that fails - no access to a
private repository, say - says which repository and stops; nothing is retried another way. The
default folder offered is beside the others the person picked, or `<home>/<project>/<repo>` under
this machine's home: never a path from anyone else's machine.
"""

from __future__ import annotations

import os
import re
import subprocess
import urllib.parse
from pathlib import Path

from agentloop import config

# Long enough for a large repository on a slow line; a clone that takes longer is said to have.
TIMEOUT = 600.0

EMPTY = "empty"
CLONE = "clone"


def _git(*args: str, interactive: bool = False, timeout: float = 60.0) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if not interactive:
        # Nobody answers a prompt for a password on the page: git fails instead of waiting, and so
        # does Git Credential Manager's window. A credential the person keeps is still used.
        env.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
    try:
        return subprocess.run(["git", *args], capture_output=True, env=env, timeout=timeout,
                              stdin=None if interactive else subprocess.DEVNULL)
    except FileNotFoundError:
        raise config.ConfigError("git", "", "git is not installed, or not on PATH") from None
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, b"", b"timed out")


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()


def without_credentials(url: str) -> str:
    """An address with any `user:password@` taken out, for saying it."""
    return re.sub(r"(://)[^/@]*@", r"\1", url)


def _comparable(url: str) -> str:
    """An address as two that mean the same repository both read: no credentials, no trailing `/`
    or `.git`; for a remote one its host in lower case and its path, whether it is written as a URL
    or as ssh's `user@host:path`, so that an https clone and an ssh one of a repository are the
    same; a local path resolved, and on Windows in lower case too."""
    text = without_credentials(url.strip()).rstrip("/")
    text = text.removesuffix(".git").rstrip("/")
    parts = urllib.parse.urlsplit(text)
    if parts.scheme and parts.scheme != "file" and parts.hostname:
        return f"{parts.hostname.lower()}/{parts.path.strip('/')}"
    # `user@host:path`, as ssh takes it; a one-letter host is a Windows drive instead.
    scp = re.match(r"^(?:[^/@:]+@)?([^/:]{2,}):(.*)$", text)
    if scp and parts.scheme != "file":
        return f"{scp.group(1).lower()}/{scp.group(2).strip('/')}"
    if parts.scheme == "file":
        text = urllib.parse.unquote(parts.path)
        if re.match(r"^/[A-Za-z]:", text):
            text = text[1:]
    try:
        local = Path(text).expanduser().resolve().as_posix()
    except (OSError, ValueError):
        return text
    return local.lower() if os.name == "nt" else local


def same_address(a: str, b: str) -> bool:
    return _comparable(a) == _comparable(b)


def _remotes(folder: Path) -> list[str]:
    done = _git("-C", str(folder), "config", "--get-regexp", r"^remote\..*\.url$")
    return [line.split(" ", 1)[1] for line in _text(done.stdout).splitlines() if " " in line]


def state(name: str, folder: Path, url: str) -> str:
    """EMPTY for a folder that is missing or empty, CLONE for the top of a clone of `url`; a
    ConfigError on `repos.<name>` for anything else, saying what it holds."""
    field = f"repos.{name}"
    if not folder.exists():
        return EMPTY
    if not folder.is_dir():
        raise config.ConfigError("clone", field, f"{folder} is a file, not a folder")
    if not any(folder.iterdir()):
        return EMPTY
    top = config.repo_top(folder)
    if top is None or top != folder.resolve():
        raise config.ConfigError("clone", field, f"{folder} holds something that is not a clone of {without_credentials(url)}: "
                                 "pick an empty folder, or one where it is cloned already")
    remotes = _remotes(folder)
    if not remotes:
        raise config.ConfigError("clone", field, f"{folder} is a git repository that is not a clone of {without_credentials(url)}")
    if not any(same_address(remote, url) for remote in remotes):
        raise config.ConfigError("clone", field, f"{folder} is a clone of another repository than {without_credentials(url)}")
    return CLONE


def clone(name: str, folder: Path, url: str, branch: str = "", *, interactive: bool = False,
          timeout: float = TIMEOUT) -> bool:
    """`url` cloned into `folder`, on `branch` when one is given: True when it was cloned, False
    when the folder was a clone of it already and is used as it is. Refuses, on `repos.<name>`, a
    folder that holds anything else and a clone that fails, saying which repository and git's own
    last line; never retries."""
    if not url:
        raise config.ConfigError("clone", f"repos.{name}", f"the definition gives {name} no address to clone")
    if state(name, folder, url) == CLONE:
        return False
    folder.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", *(["--branch", branch] if branch else []), "--", url, str(folder)]
    done = _git(*args, interactive=interactive, timeout=timeout)
    if done.returncode != 0:
        lines = [line for line in _text(done.stderr).splitlines() if line.strip()]
        why = without_credentials(lines[-1]) if lines else f"git exited with {done.returncode}"
        why = why.removeprefix("fatal: ")
        raise config.ConfigError("clone", f"repos.{name}", f"{name} could not be cloned from {without_credentials(url)}: {why}")
    return True


def default_folder(home: Path, project: str, name: str, picked: list[str] | None = None) -> Path:
    """Where `name` is offered to go: beside the first folder the person picked for another of the
    project's repositories, else `<home>/<project>/<name>`."""
    for other in picked or []:
        if other and other.strip():
            return config.expand(other.strip()).parent / name
    return home / project / name
