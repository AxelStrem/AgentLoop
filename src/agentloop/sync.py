"""A loop's clones kept in step with their upstream: pulled before each session, pushed after it
(item git-sync).

The script never pushes - run-agent-loop.ps1 commits each session in the loop's own clones and
leaves the rest to the owner - so none of this is a port. With the run setting `push` on, loops on
several machines work one project from one branch:

- Before a session, `pull` fetches the branch's upstream and fast-forwards a clone that is only
  behind it, so the session starts from everyone's work. `git merge --ff-only` carries the owner's
  uncommitted changes along, and refuses - changing nothing - when one of them is in a file the
  pull would change; the clone is then left as it is, and said so.
- After the session's commit, `push` fetches again, replays the clone's own commits onto the
  upstream when it has moved on, and pushes, never with --force. A push refused because another
  loop pushed first is fetched and replayed again, `pushTries` times in all.

The replay never runs in the clone's own working tree, which may hold the owner's uncommitted work:
a plain rebase refuses it, and --autostash would put it through a stash pop that can conflict. It
runs in a worktree of its own, detached, under the loop's folder, and the clone is moved to its
result with `git reset --keep`, which changes only the files that differ between the two commits,
and refuses, changing nothing, if one of them has local changes.

A replay that conflicts is handed to `resolve`: the loop resumes the session that made the commit,
which resolves the files in the worktree and says MERGED, or leaves them and says UNMERGED. The
replay goes on only when it said MERGED and no conflict markers are left; otherwise it is aborted,
the commit is left local, and the loop stops: `push` returns a `Stopped` that says which clone,
which files and why, and the loop tells the owner what to do (item push-conflict).

A fetch or push that fails on a GitHub remote in the form this machine does not sign in with,
HTTPS or SSH, has the remote switched to the other form and is tried once more (gitauth.py).

What git prints is not passed on: an error of fetch or push names the remote's URL. Its exit code
is said instead.

Only Windows has been tried: this machine is Windows.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agentloop import commits, gitauth

# How long a fetch or a push may take before it is given up on: a remote that does not answer
# must not hold the loop for good.
NETWORK_TIMEOUT = 300

# How many times one replay may stop at a conflict and be handed to the session: one for each of
# the clone's commits it replays, at most, and a replay of more than this is not a loop's.
MAX_ROUNDS = 10

# Lines git writes into a conflicted file. `=======` alone is left out: a heading in markdown can
# be underlined with it.
_MARKER = re.compile(rb"(?m)^(<{7}|>{7})( |$)")

Say = Callable[[str, "str | None"], None]


@dataclass(frozen=True)
class Upstream:
    """Where a clone's branch pulls from and pushes to: its remote and the branch there."""

    remote: str
    branch: str

    def __str__(self) -> str:
        return f"{self.remote}/{self.branch}"


@dataclass(frozen=True)
class Conflict:
    """A replay stopped at a conflict, for the session to resolve: the clone, the worktree the
    replay is stopped in, the upstream it replays onto, the commit that conflicted (its short
    hash and subject) and the files git left conflicted."""

    repo: Path
    worktree: Path
    upstream: Upstream
    commit: str
    files: list[str]


# A conflict's resolution: None once the session resolved it, or why it did not.
Resolve = Callable[[Conflict], "str | None"]


@dataclass(frozen=True)
class Stopped:
    """Why a push has to stop the loop (item push-conflict): the clone, the upstream it was
    replayed onto, why it stopped, and - when it stopped at a conflict - the commit that
    conflicted and the files left conflicted, for the loop to tell the owner what to do."""

    repo: Path
    upstream: Upstream
    why: str
    commit: str = ""
    files: tuple[str, ...] = ()


def _env() -> dict[str, str]:
    # Nobody answers a prompt for a password on an unattended run: git fails instead of waiting,
    # and so does Git Credential Manager's window.
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


def _git(repo: Path, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=_env(), timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, b"", b"timed out")


def _out(done: subprocess.CompletedProcess) -> str:
    return done.stdout.decode("utf-8", errors="replace").strip()


def upstream(repo: Path) -> Upstream | None:
    """The upstream of the branch a clone has checked out, or None: a detached HEAD, a branch
    without one, or one that tracks another local branch."""
    branch = _out(_git(repo, "symbolic-ref", "-q", "--short", "HEAD"))
    if not branch:
        return None
    remote = _out(_git(repo, "config", f"branch.{branch}.remote"))
    merge = _out(_git(repo, "config", f"branch.{branch}.merge"))
    if not remote or remote == "." or not merge.startswith("refs/heads/"):
        return None
    return Upstream(remote, merge[len("refs/heads/"):])


def _fetch(repo: Path, up: Upstream) -> bool:
    return _git(repo, "fetch", "-q", up.remote, timeout=NETWORK_TIMEOUT).returncode == 0


def _fetched(repo: Path, up: Upstream, say: Say) -> bool:
    # Tried again on a GitHub remote in the form this machine does not sign in with (item git-protocol).
    return _fetch(repo, up) or (gitauth.switch(repo, up.remote, say) and _fetch(repo, up))


def _push(repo: Path, up: Upstream) -> subprocess.CompletedProcess:
    return _git(repo, "push", "-q", "--porcelain", up.remote, f"HEAD:refs/heads/{up.branch}", timeout=NETWORK_TIMEOUT)


def _refused(done: subprocess.CompletedProcess) -> bool:
    # --porcelain marks a refused ref with `!`, and says why: another loop pushed first.
    return b"[rejected]" in done.stdout or b"non-fast-forward" in done.stdout or b"fetch first" in done.stdout


def _counts(repo: Path) -> tuple[int, int]:
    """How many commits the clone has that its upstream has not, and the other way round."""
    found = _out(_git(repo, "rev-list", "--left-right", "--count", "HEAD...@{u}")).split()
    return (int(found[0]), int(found[1])) if len(found) == 2 else (0, 0)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


# Before a session

def pull(repos: list[Path], say: Say, *, lock: Path | None = None) -> None:
    """Each clone fast-forwarded to its upstream when it is only behind it. Nothing here stops the
    loop: a clone that cannot be pulled is said so, and the session starts from what it has."""
    for repo in repos:
        up = upstream(repo)
        if up is None:
            continue
        if not _fetched(repo, up, say):
            say(f"  {repo.name}: could not fetch from {up.remote}; the session starts from what the clone has.", "DarkYellow")
            continue
        ahead, behind = _counts(repo)
        if not behind:
            continue
        if ahead:
            # Replayed onto the upstream once the session's commit is made, with it.
            say(f"  {repo.name}: has {_plural(ahead, 'commit')} {up} has not, and {up} {_plural(behind, 'commit')} it has not; they are replayed onto it after the session.", "DarkYellow")
            continue
        with commits.commit_lock(lock):
            done = _git(repo, "merge", "-q", "--ff-only", "@{u}")
        if done.returncode == 0:
            say(f"  pulled in {repo.name}: {_plural(behind, 'commit')} from {up}", "DarkGreen")
        else:
            say(f"  {repo.name}: not pulled - changes here are in files {up} has changed since. They are left as they are.", "DarkYellow")


# After a session

def push(repos: list[Path], worktrees: Path, resolve: Resolve, say: Say, *, tries: int = 3, lock: Path | None = None) -> Stopped | None:
    """Each clone's commits pushed to its upstream, replayed onto it first when it has moved on.
    None when the loop can go on; what stopped it when it has to stop: a replay's conflict was
    not resolved, or a replay failed.
    A push that fails otherwise - the remote out of reach, or moving on `tries` times - leaves
    the commits local, said so, and the next session's push carries them."""
    for repo in repos:
        up = upstream(repo)
        if up is None:
            continue
        stopped = _push_one(repo, up, worktrees / repo.name, resolve, say, max(1, tries), lock)
        if stopped is not None:
            return stopped
    return None


def _push_one(repo: Path, up: Upstream, worktree: Path, resolve: Resolve, say: Say, tries: int, lock: Path | None) -> Stopped | None:
    for _ in range(tries):
        if not _fetched(repo, up, say):
            say(f"  {repo.name}: could not fetch from {up.remote}; not pushed - the commits are left local, for a later session's push.", "DarkYellow")
            return None
        ahead, behind = _counts(repo)
        if not ahead:
            return None
        if behind:
            replayed = replay(repo, up, worktree, resolve, say)
            if isinstance(replayed, Stopped):
                return replayed
            with commits.commit_lock(lock):
                moved = _git(repo, "reset", "-q", "--keep", replayed)
            if moved.returncode != 0:
                say(f"  {repo.name}: not pushed - changes here are in files {up} has changed since. The commits are left local.", "DarkYellow")
                return None
            say(f"  replayed onto {up} in {repo.name}: {_out(_git(repo, 'log', '-1', '--format=%h %s'))}", "DarkGreen")
        done = _push(repo, up)
        if done.returncode != 0 and not _refused(done) and gitauth.switch(repo, up.remote, say):
            # Fetched anonymously, but pushed in the form this machine does not sign in with.
            done = _push(repo, up)
        if done.returncode == 0:
            say(f"  pushed in {repo.name}: {_plural(ahead, 'commit')} to {up}", "DarkGreen")
            return None
        if _refused(done):
            say(f"  {repo.name}: {up} moved on before the push; fetched again.", "DarkGray")
            continue
        say(f"  {repo.name}: the push failed (git exit {done.returncode}); the commits are left local, for a later session's push.", "DarkYellow")
        return None
    say(f"  {repo.name}: {up} moved on before each push, {_plural(tries, 'time')}; the commits are left local, for a later session's push.", "DarkYellow")
    return None


def replay(repo: Path, up: Upstream, worktree: Path, resolve: Resolve, say: Say) -> str | Stopped:
    """The clone's commits replayed onto its upstream, in a worktree of their own: the commit the
    replay ends at, or why it could not be finished, said too."""
    onto = _out(_git(repo, "rev-parse", "--verify", "@{u}"))
    head = _out(_git(repo, "rev-parse", "--verify", "HEAD"))
    discard(repo, worktree)
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if _git(repo, "worktree", "add", "-q", "--detach", str(worktree), head).returncode != 0:
        say(f"  {repo.name}: could not make a worktree to replay the commits onto {up} in. The commits are left local.", "Red")
        return Stopped(repo, up, "no worktree could be made to replay the commits in")
    try:
        done = _git(worktree, "rebase", "-q", onto)
        rounds = 0
        while _rebasing(worktree):
            files = _unmerged(worktree)
            if not files:
                # Stopped with nothing conflicted: a resolution that left a commit with no changes,
                # which is dropped, as a rebase drops one already upstream.
                if _git(worktree, "diff", "--cached", "--quiet").returncode == 0:
                    done = _git(worktree, "rebase", "--skip")
                    # A skip that fails with nothing conflicted moved nothing on - a rebase whose
                    # state git could not finish writing, say - and would only fail again.
                    if done.returncode != 0 and _rebasing(worktree) and not _unmerged(worktree):
                        say(f"  {repo.name}: the replay onto {up} could not go on (git exit {done.returncode}). The commits are left local.", "Red")
                        return Stopped(repo, up, f"the replay could not go on (git exit {done.returncode})")
                    continue
                say(f"  {repo.name}: the replay onto {up} stopped (git exit {done.returncode}). The commits are left local.", "Red")
                return Stopped(repo, up, f"the replay stopped (git exit {done.returncode})")
            rounds += 1
            if rounds > MAX_ROUNDS:
                say(f"  {repo.name}: the replay onto {up} stopped at a conflict {MAX_ROUNDS} times. The commits are left local.", "Red")
                return Stopped(repo, up, f"the replay stopped at a conflict {MAX_ROUNDS} times")
            commit = _out(_git(worktree, "log", "-1", "--format=%h %s", "REBASE_HEAD"))
            conflict = Conflict(repo, worktree, up, commit, files)
            why = resolve(conflict)
            if why is not None:
                say(f"  {repo.name}: the conflict replaying {commit} onto {up} was not resolved: {why}. The commits are left local.", "Red")
                return Stopped(repo, up, why, commit, tuple(files))
            left = [name for name in files if _has_markers(worktree / name)]
            if left:
                say(f"  {repo.name}: conflict markers are left in {', '.join(left)}. The commits are left local.", "Red")
                return Stopped(repo, up, f"conflict markers are left in {', '.join(left)}", commit, tuple(files))
            _git(worktree, "add", "-A")
            done = _git(worktree, "-c", "core.editor=true", "rebase", "--continue")
        if done.returncode != 0:
            say(f"  {repo.name}: the replay onto {up} failed (git exit {done.returncode}). The commits are left local.", "Red")
            return Stopped(repo, up, f"the replay failed (git exit {done.returncode})")
        return _out(_git(worktree, "rev-parse", "--verify", "HEAD")) or Stopped(repo, up, "the replay ended at no commit")
    finally:
        discard(repo, worktree)


def _rebasing(worktree: Path) -> bool:
    for state in ("rebase-merge", "rebase-apply"):
        found = _out(_git(worktree, "rev-parse", "--git-path", state))
        if found and (worktree / found).exists():
            return True
    return False


def _unmerged(worktree: Path) -> list[str]:
    return [name for name in _out(_git(worktree, "diff", "--name-only", "--diff-filter=U")).splitlines() if name]


def _has_markers(path: Path) -> bool:
    try:
        return bool(_MARKER.search(path.read_bytes()))
    except OSError:
        # Deleted in the resolution: nothing left in it.
        return False


def discard(repo: Path, worktree: Path) -> None:
    """A replay's worktree removed, and git's record of it - one a killed run left too."""
    if worktree.exists():
        _git(repo, "worktree", "remove", "--force", "--force", str(worktree))
        if worktree.exists():
            shutil.rmtree(worktree, ignore_errors=True)
    _git(repo, "worktree", "prune")


def discard_all(repos: list[Path], worktrees: Path) -> None:
    """What a run that died mid-replay left, cleared as the next one starts."""
    for repo in repos:
        if (worktrees / repo.name).exists():
            discard(repo, worktrees / repo.name)
