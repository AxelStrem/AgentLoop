"""Each session's changes committed per repository, and only the session's own.

The script's side is run-agent-loop.ps1's `Get-Changed`, `Get-Snapshot`, `Measure-SessionLines`,
`Commit-Session`, `Get-RepoStatus` and the commit lock `Enter-CommitLock` takes. Before a session
the loop takes a snapshot of what is changed already - the owner's work in progress - and after it
commits, in each repository, only what changed since; a file changed before and again is the
owner's and the agent's together, and is left. Printing what was committed and recording it are
item loop's: these say what they did, and print nothing.

Differences from the script, each deliberate:
- The lock is a file lock under the app's data folder, which every OS has, since a second app on
  the PC must keep off the same repositories. On Windows it also takes the script's own named
  mutex, so that the app and the old script keep off each other's commits while they run side by
  side (item switchover); that part goes once the script is retired. The file lock is untried on
  macOS and Linux here: this machine is Windows.
- Trackers are on a server only (item simplify): no tracker is a file in a repository, so none is
  left out of the count of a session's lines, or committed for the items a session filed into
  another project's, and every commit record's `tracker` is false.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from agentloop import config

# run-agent-loop.ps1's commit mutex, `Local\run-agent-loop-commit`, as config.script_mutex names it.
SCRIPT_MUTEX = "commit"

# How many lines of a repository's status the prompt gives before it says how many more there are.
STATUS_LINES = 40

Snapshot = dict[Path, dict[str, str]]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    # git warns on stderr (line endings, for one); whether a command worked is its exit code.
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True)


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


# The lock

def lock_file() -> Path:
    return config.paths().data / "commit.lock"


@contextlib.contextmanager
def commit_lock(path: Path | None = None) -> Iterator[None]:
    """Held by any loop looking at or committing to its repositories - in this app, in another on
    the same PC, or in the script: two commits at once in a shared repository would collide on
    git's index lock. Released in the thread that took it."""
    with _script_mutex(), _file_lock(path or lock_file()):
        yield


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            # msvcrt has no wait of its own that lasts: LK_LOCK gives up after ten seconds.
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _script_mutex() -> Iterator[None]:
    if sys.platform != "win32":
        yield
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    mutex = kernel.CreateMutexW(None, False, config.script_mutex(SCRIPT_MUTEX))
    if not mutex:
        # The file lock still keeps the app's loops apart; only the script's would go unheeded.
        yield
        return
    try:
        # WAIT_OBJECT_0, or WAIT_ABANDONED: a loop that died holding it, taken as Enter-CommitLock
        # takes it - owned all the same.
        taken = kernel.WaitForSingleObject(mutex, 0xFFFFFFFF) in (0, 0x80)
        try:
            yield
        finally:
            if taken:
                kernel.ReleaseMutex(mutex)
    finally:
        kernel.CloseHandle(mutex)


# What is changed

def changed(repo: Path) -> dict[str, str]:
    """The files git sees as changed in a repository, each with a hash of what it holds now:
    `Get-Changed`."""
    found: dict[str, str] = {}
    done = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    entries = _text(done.stdout).split("\0")
    k = 0
    while k < len(entries):
        entry = entries[k]
        k += 1
        if len(entry) < 4:
            continue
        if "R" in entry[:2] or "C" in entry[:2]:
            k += 1   # a rename is followed by the name it had
        path = entry[3:]
        full = repo / path
        found[path] = hashlib.sha1(full.read_bytes()).hexdigest().upper() if full.is_file() else "gone"
    return found


def snapshot(repos: list[Path], lock: Path | None = None) -> Snapshot:
    """What is changed in each repository now, to tell later what a session changed: `Get-Snapshot`."""
    with commit_lock(lock):
        return {repo: changed(repo) for repo in repos}


def repo_status(repos: list[Path]) -> str:
    """What is uncommitted in each repository as the session begins, for the prompt: `Get-RepoStatus`."""
    parts = []
    for repo in repos:
        lines = [line for line in _text(_git(repo, "status", "--short").stdout).splitlines() if line]
        name = str(repo).replace("\\", "/")
        if not lines:
            parts.append(f"{name}: clean")
        elif len(lines) > STATUS_LINES:
            shown = "\n".join(lines[:STATUS_LINES])
            parts.append(f"{name}:\n{shown}\n... and {len(lines) - STATUS_LINES} more")
        else:
            parts.append(f"{name}:\n" + "\n".join(lines))
    return ("Uncommitted changes as the session begins, as git status --short prints them - the owner's "
            "work in progress unless they are this item's:\n\n" + "\n\n".join(parts) + "\n\n")


def _new(before: Snapshot, repo: Path, now: dict[str, str]) -> list[str]:
    was = before.get(repo, {})
    return [path for path in now if path not in was]


def session_lines(before: Snapshot, repos: list[Path], lock: Path | None = None) -> int:
    """How many lines a session added and took out, in the files it changed - every line of a file
    it made - but any file changed before it began, which is left uncommitted:
    `Measure-SessionLines`. Counted before the commit, so the count can go into it."""
    total = 0
    with commit_lock(lock):
        for repo in repos:
            for path in _new(before, repo, changed(repo)):
                stat = _text(_git(repo, "diff", "--numstat", "HEAD", "--", path).stdout).splitlines()
                # Nothing to compare against at HEAD: a file the session made, compared with nothing.
                if not stat:
                    stat = _text(_git(repo, "diff", "--no-index", "--numstat", "--", "/dev/null", path).stdout).splitlines()
                for row in stat:
                    # A binary file says - for both.
                    found = re.match(r"^(\d+)\s+(\d+)\s", row)
                    if found:
                        total += int(found[1]) + int(found[2])
    return total


# Committing

@dataclass
class Committed:
    """What `commit_session` did: whether every commit worked, a record of each - the script's
    `$script:Commits` - and the lines to print."""

    ok: bool = True
    commits: list[dict] = field(default_factory=list)
    said: list[str] = field(default_factory=list)


def _shortstat(repo: Path) -> dict[str, int]:
    stat = _text(_git(repo, "show", "--shortstat", "--format=", "HEAD").stdout)

    def count(word: str) -> int:
        found = re.search(rf"(\d+) {word}", stat)
        return int(found[1]) if found else 0

    return {"files": count("files? changed"), "insertions": count("insertions?"), "deletions": count("deletions?")}


def _head(repo: Path, form: str) -> str:
    return _text(_git(repo, "log", "-1", f"--format={form}").stdout).strip()


def _concluding(repo: Path) -> bool:
    """A merge or a cherry-pick stopped before its commit, as `--no-commit` leaves one."""
    return any(_git(repo, "rev-parse", "-q", "--verify", head).returncode == 0
               for head in ("MERGE_HEAD", "CHERRY_PICK_HEAD"))


def commit_session(before: Snapshot, message: str, repos: list[Path], *, lock: Path | None = None) -> Committed:
    """Commit, in each repository, what changed since `before` was taken - and only that:
    `Commit-Session`. A file already changed before the session, and changed again, holds the
    owner's work and the agent's together; it is left for the owner, and said so."""
    result = Committed()
    with commit_lock(lock):
        for repo in repos:
            now = changed(repo)
            was = before.get(repo, {})
            mine = _new(before, repo, now)
            mixed = [path for path in now if path in was and was[path] != now[path]]
            if mixed:
                result.said.append(f"left uncommitted in {repo.name}, already changed before this session: {', '.join(mixed)}")
            if not mine:
                continue
            # Before anything is added: adding a file still in conflict marks it resolved, and its
            # conflict markers would be committed.
            unmerged = [path for path in _text(_git(repo, "diff", "--name-only", "-z", "--diff-filter=U").stdout).split("\0") if path]
            if unmerged:
                result.said.append(f"not committed in {repo.name}: unmerged files, still in conflict: {', '.join(unmerged)}")
                result.ok = False
                return result
            concluding = _concluding(repo)
            _git(repo, "add", "--", *mine)
            if concluding:
                # git commits a merge or cherry-pick whole, never some of its files. A session's
                # own is all its own: git begins one only on an index that matches HEAD, so the
                # owner's work is unstaged and stays out. One that holds files changed before the
                # session was begun before it, and is the owner's to finish.
                staged = _text(_git(repo, "diff", "--cached", "--name-only", "-z").stdout).split("\0")
                theirs = [path for path in staged if path in was]
                if theirs:
                    result.said.append(f"not committed in {repo.name}: a merge in progress holds files changed before this session: {', '.join(theirs)}")
                    result.ok = False
                    return result
                done = _git(repo, "commit", "-q", "--no-edit", "-m", message)
            else:
                # --only: these files and nothing else, whatever the owner has staged.
                done = _git(repo, "commit", "-q", "--only", "-m", message, "--", *mine)
            if done.returncode != 0:
                reason = next((line for line in _text(done.stderr).splitlines() if line.strip()), "git gave no reason")
                result.said.append(f"not committed in {repo.name}: {reason}")
                result.ok = False
                return result
            result.said.append(f"committed in {repo.name}: {_head(repo, '%h %s')} ({len(mine)} files)")
            # How much it changed, for weighing a session's cost against its work. `tracker` is
            # the script's mark on its tracker's repository, whose lines are notes: no repository
            # holds a tracker now, and it is kept so that the records read as the script's do.
            result.commits.append({"repo": repo.name, "hash": _head(repo, "%h"), "tracker": False, **_shortstat(repo)})
    return result
