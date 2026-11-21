"""GitHub addresses in the form this machine signs in with, HTTPS or SSH (item git-protocol).

GitHub serves each repository both as https://github.com/<owner>/<repo>.git and as
git@github.com:<owner>/<repo>.git. A definition names one of the two; a machine signs in with an
HTTPS credential, an SSH key, or both. So:

- A GitHub address is cloned in its other form when this machine signs in with that one and not
  with the one given (clones.py).
- A clone whose fetch or push fails has its remote switched the same way, and tried once more
  (sync.py).

HTTPS signs in when git holds a credential for github.com (`git credential fill`, never asking);
SSH when `ssh -T git@github.com` greets a user, its first contact adding GitHub's host key to
known_hosts. An address whose own form signs in, or whose other one does not either, is kept. An
address with a user name or password in it, and any other host, are never touched.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable

HTTPS = "https"
SSH = "ssh"

# A probe that takes longer is taken as not signing in.
TIMEOUT = 20

_HTTPS_FORM = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", re.IGNORECASE)
_SSH_FORM = re.compile(r"^(?:ssh://)?git@github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", re.IGNORECASE)


def form(url: str) -> str | None:
    """HTTPS or SSH for a GitHub address this module may switch, None for any other."""
    if _HTTPS_FORM.match(url.strip()):
        return HTTPS
    if _SSH_FORM.match(url.strip()):
        return SSH
    return None


def other_form(url: str) -> str | None:
    """The same GitHub repository's address in the other form, or None."""
    text = url.strip()
    if found := _HTTPS_FORM.match(text):
        return f"git@github.com:{found[1]}/{found[2]}.git"
    if found := _SSH_FORM.match(text):
        return f"https://github.com/{found[1]}/{found[2]}.git"
    return None


def _env() -> dict[str, str]:
    # Nothing may ask: no terminal prompt, no askpass window, no Credential Manager window.
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}


def _run(args: list[str], stdin: bytes = b"") -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, input=stdin, capture_output=True, env=_env(), timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _https_signs_in() -> bool:
    done = _run(["git", "credential", "fill"], b"protocol=https\nhost=github.com\n\n")
    return done is not None and done.returncode == 0 and b"\npassword=" in b"\n" + done.stdout


def _ssh_signs_in() -> bool:
    done = _run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                 "-o", "StrictHostKeyChecking=accept-new", "git@github.com"])
    # GitHub answers a key it knows with "Hi <user>! You've successfully authenticated", exit 1.
    return done is not None and b"successfully authenticated" in done.stdout + done.stderr


def signs_in(kind: str) -> bool:
    """Whether this machine signs in to GitHub over HTTPS or SSH, asked afresh each time: a key or
    token may be added while serve runs."""
    return _https_signs_in() if kind == HTTPS else _ssh_signs_in()


def usable(url: str, check: Callable[[str], bool] | None = None) -> str:
    """`url`, or its other GitHub form when this machine signs in with that one and not with
    `url`'s own."""
    check = check or signs_in
    other = other_form(url)
    if other is None or check(form(url)) or not check(form(other)):
        return url
    return other


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=_env())


def switch(repo: Path, remote: str, say: Callable[[str, "str | None"], None]) -> bool:
    """The clone's remote switched to its other GitHub form when this machine signs in with that
    one and not with the one it has, said: True when it was switched. A push address of its own
    (`remote.<name>.pushurl`) is switched alike."""
    switched = False
    for push in (False, True):
        key = f"remote.{remote}.{'pushurl' if push else 'url'}"
        url = _git(repo, "config", "--get", key).stdout.decode("utf-8", errors="replace").strip()
        if not url:
            continue
        new = usable(url)
        if new == url:
            continue
        if _git(repo, "remote", "set-url", *(["--push"] if push else []), remote, new).returncode == 0:
            say(f"  {repo.name}: {remote} switched to {new}, the form this machine signs in to GitHub with.", "DarkYellow")
            switched = True
    return switched
