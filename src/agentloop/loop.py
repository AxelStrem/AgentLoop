"""The loop: picking an item, routing it, triage and build, going on to another item, repeats, and
the records of each session.

The script's side is the heart of run-agent-loop.ps1: `Get-Prompt`, `Get-ItemContext`,
`Read-Tracker`, `Get-RouteName`, `Get-Signature`, `Format-Figure`, `Test-Tried`, `Add-Attempt`,
`Add-WorkRecord`, `Invoke-Phase`, `Complete-Session`, `Note-Outcome`, `Get-Repeats`, `Test-Trial`,
`Get-Plan`, `Invoke-Item`, `Get-NextInSession`, the preamble that finds the project's tracker and
repositories, and the main loop around them. One `Run` is one loop, as one run of the script is;
several at once are runner.py's. Running a session is session.py's, waiting out a limit
usage.py's, committing commits.py's: this decides what each is asked, and records what came of it.

Differences from the script, each deliberate:
- What it prints goes to an `out` callable, a line at a time, so the runner and the page can take
  the lines; `agentloop run` prints them. Each is given the script's colour for it, through
  `paint`: plain by default, ANSI codes for a terminal and the runner's output log (colours.py).
- attempts.jsonl and the session logs are under the app's data folder, `logs/<project>/`, as
  docs/settings.md settles, not the script's `logs\\<project>`. Whether a model has had its go at
  an item is read first from the item's work records on the server (item shared-attempts), which
  every PC's loops and the script's write, and only then from this PC's attempts.jsonl. The stop and kill
  files, running.json, system.md, the sessions' settings and their scratch folders are the loop's,
  under `logs/<project>/loops/<loop>/`.
- The start line names the stop and kill files, not stop-agent-loop.ps1 and kill-agent-loop.ps1.
  A kill file is seen between sessions and while a limit is waited out; ending a claude in the
  middle of its session is runner.py's, through the `child` running.json names.
- status.json beside them says what the page shows of the loop - its state, the item and session at
  work, the run's cost, the usage line - and how it ended. The script has none: its window shows it.
- A tracker is a document on an Overseer server, never a file in a repository (item simplify):
  nothing of the tracker is committed, so there is no tracker repository to keep clean before the
  run, no other projects' tracker files to leave out of a commit, and no limit of one loop to a
  project for the tracker's sake. The script still works trackers that are files.
- Each loop holds a file lock of its own under the data folder while it runs, and on Windows the
  script's own `Local\\run-agent-loop-<project>` mutex too, so that the app and the script never
  work one project at once while they run side by side (item switchover). Several loops of the app
  may work one project, each in clones of its own, when its tracker is on a server (item
  loop-like): a mutex has one owner, so the first takes it, a later one goes on without it while
  another of the app's loops on the project is alive, and takes it between items once it is free.
  That leaves a gap - the loop that had it ends, and the script starts before another retakes it -
  which is let be: the script only ever adds a loop that ignores claims.
- attempts.jsonl is appended by every loop of the project, each line under a lock beside it.
- Time - the trial's date, the waits, the pause between items - goes through a `Clock` a test
  replaces.
- Each usage report is also kept as the worker's last (`accounts.save_usage`), so the page can show
  it before a loop runs. It is kept as each report comes, during a session too; the script
  reports usage only between iterations.
- `dry` is a start that stops where the first session would begin, writing its prompts; the
  script has none.
- With `autoStart` on, a loop with nothing to work waits rather than ending, and while it waits
  hears its tracker change through the server's watch stream (watch.py, item instant-auto); the
  script ends on an empty queue.
- Each session claims its item before it begins and releases it when it ends, however it ends,
  so that other loops leave it alone (item tracker-claim); the script does neither. It is claimed
  with `tracker.py claim` once the session's snapshot is taken, as the session's own work begins.
  A claimed item another loop has is skipped. claims.json in the loop's folder names
  what the run holds, for the next start - or the runner, when it ends a loop by force - to let go
  of what a run that died left claimed.
- A worker may have a manager, an Overseer username: its loops then take that user's items first,
  before those flagged first, through `tracker.py next --owner` (item manager-first). The script
  has no manager.
- With the run setting `push` on, each session's clones are pulled before it and its commit pushed
  after it (item git-sync, sync.py); the script never pushes. A push whose replay conflicts resumes
  the session with a prompt of its own, `merge`, and the loop stops if it is not resolved, saying
  which repository and files, and what to do (item push-conflict). That
  phase is a line of attempts.jsonl of kind `merge`, and no work record on the item. A commit's
  hash in attempts.jsonl is the one it was made with, before any replay.
- A run has no limit on its items, and the queue none on the items flagged asked (item no-limits):
  it works until nothing on the tracker can be worked, or something else ends it. The script stops
  after `-MaxIterations` items, and on ten items flagged asked.
- A worker may have limits of its own, a share of each usage window (item h87x): past one, the
  loop runs no session - before an item, before a triage's build, before going on to another item
  in a session - until the window resets or going on past it is approved (`_pause`). The script
  has none; its `-StopAtWeeklyUse` (`Test-WeekUsed`) stopped the loop at a share of the week, and
  is retired here: a worker's weekly limit pauses its loops there instead.
- With the run setting `transcribe` naming an engine, the voice messages that hold items back are
  transcribed before an item is taken, and in the idle wait as the tracker changes (item
  voice-loop, `hear`): the engine (voice.py), then a one-turn call with no tools on the `voice`
  route makes the text clear English, which `tracker.py heard` writes under the recording, with a
  work record of kind `voice`. The item is claimed while it is transcribed and let go of after,
  without being made this loop's account's; one claimed already is left alone (item voice-tweaks).
  Nothing is moved or committed. The script has no voice messages.
- A loop waiting for work in its idle wait takes a seat on its tracker's bench, `project/Bench`,
  and gives it up as the wait ends, for the page to show who is waiting (item projbench,
  `tracker.py bench`). The seat is `<account>@<machine>/<loop>`; a run gives up its own once as it
  first reads the tracker, clearing one a killed run left. The script has no bench.
- An item flagged trusted is finished by the loop once it is at testing (item trusted-item,
  `finish_trusted`): after the session's work record, and before each pick for one that came to
  testing otherwise. The script has no trusted flag.
- Only Windows has been tried: this machine is Windows.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from agentloop import accounts, brew, colours, commits, config, definition, session, setup, sync, tools, usage, voice, watch

# The pause between clean iterations, in seconds.
COOLDOWN = 5
# How long a tracker that changed must stay still before an idle loop looks at it (item instant-auto).
SETTLE_SECONDS = 10


class LoopError(Exception):
    """A loop that cannot start: its project, its worker or its repositories are not as they must be."""


# Small things the script does inline

def route_name(route: dict) -> str:
    """`Get-RouteName`."""
    if route.get("effort"):
        return f"{route['model']} {route['effort']}"
    return route["model"]


def signature(model: str, route: dict) -> str:
    """Who a model at an effort is in the tracker: the author of its comments, the model of its
    sessions (`Get-Signature`)."""
    if route.get("effort"):
        return f"{model} ({route['effort']})"
    return model


def same_model(recorded: str, model: str) -> bool:
    """Whether a work record's model is a route's: the record names the model the session
    reported, with its effort - `claude-opus-5-5 (medium)` - where a route may name it by its
    alias, `opus`."""
    name = re.sub(r"\s*\([^()]*\)$", "", recorded.strip())
    if name == model:
        return True
    return "-" not in model and (name == f"claude-{model}" or name.startswith(f"claude-{model}-"))


def figure(value: float) -> str:
    """A figure as PowerShell writes a double, and as tracker.py reads one (`Format-Figure`): no
    trailing zeros, a point for the fraction."""
    text = repr(float(value))
    if text.endswith(".0"):
        text = text[:-2]
    return text


def points_of(item: dict | None) -> int:
    """An item's points as the script reads them: `[int]$item.points`, or 1 when it has none."""
    value = (item or {}).get("points")
    try:
        return int(value) if value not in (None, "", 0, "0") else 1
    except (TypeError, ValueError):
        return 1


def _json_number(value: float) -> float | int:
    # ConvertTo-Json writes a whole double as an integer: 1, not 1.0.
    return int(value) if float(value).is_integer() else value


def _flags(item: dict | None) -> list[str]:
    return [f.strip() for f in str((item or {}).get("flags") or "").split(",") if f.strip()]


def _free(item: dict | None) -> bool:
    """Whether an item as `show --json` gives it is one the queue could offer: open, waiting on
    nothing, and held for no person by a manual or asked flag."""
    return (bool(item) and not item.get("closed") and not item.get("waiting")
            and not any(f in ("manual", "asked") for f in _flags(item)))


def _slashes(path) -> str:
    return str(path).replace("\\", "/")


# Where a loop keeps its files

def loop_dir(project: str, name: str) -> Path:
    """The loop's own folder: its stop and kill files, running.json, status.json and output."""
    return config.paths().data / "logs" / project / "loops" / name


def write_json(path: Path, value: dict) -> None:
    """Written whole or not at all, for a reader in another process. Windows refuses to replace a
    file another process has open for that moment, so it is tried again."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(json.dumps(value, separators=(",", ":")).encode("utf-8"))
    for attempt in range(50):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


def remove(path: Path) -> None:
    """Gone, or an error. Windows refuses to delete a file for the moment another process - the
    runner, reading running.json for a kill - has it open, so it is tried again."""
    for attempt in range(50):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


# The items a loop has claimed

def claims_path(folder: Path) -> Path:
    """The items the loop in `folder` holds, and how to reach their tracker: what a run that died
    holding them, or was ended by force, leaves for the next start - or the runner - to let go of."""
    return folder / "claims.json"


def _tracker_command() -> list[str]:
    """tracker.py run as an import rather than as a script: Python keeps an imported module's
    bytecode beside it, and compiles a script anew every time - ~20 ms of each of the ten or so
    calls an item takes (trackerstart). Its folder takes the place `-c` gives the current one on
    the module path, so the tool sees the path a script would. Where the folder cannot be written,
    it is compiled each time, as before."""
    script = tools.path("tracker.py")
    return [sys.executable, "-c",
            f"import sys; sys.path[0] = {str(script.parent)!r}; sys.argv[0] = {str(script)!r}; "
            "import tracker; tracker.main()"]


def _moment(written) -> datetime | None:
    """A comment's `at` as a moment, however the server wrote it, for finding a comment by it."""
    try:
        return datetime.fromisoformat(str(written).strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _run_tracker(tracker: str, env: dict[str, str | None], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([*_tracker_command(), "--doc", tracker, *args],
                          capture_output=True, env=session.build_env(env))


def release_left(folder: Path) -> list[str]:
    """Lets go of every item claims.json in `folder` still names, and says which were let go of. One
    that cannot be released now stays named, to be tried again."""
    path = claims_path(folder)
    try:
        held = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return []
    left, released = [], []
    for handle in held.get("handles") or []:
        if _run_tracker(held["tracker"], held.get("env") or {}, "release", handle).returncode == 0:
            released.append(handle)
        else:
            left.append(handle)
    if left:
        write_json(path, dict(held, handles=left))
    else:
        remove(path)
    return released


# The locks: each loop's own, and the script's

class Held(LoopError):
    """A lock another process - or another loop in this one - holds."""


def _try_lock(handle) -> bool:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


@contextlib.contextmanager
def file_lock(path: Path, wait: float = 0.0) -> Iterator[None]:
    """The OS's lock on a file, tried again for `wait` seconds; Held if it cannot be had. Each open
    of the file is a holder of its own, so two in one process exclude each other too. A lock whose
    holder died is released by the OS, so nothing is left to clear up by hand."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait
    handle = open(path, "a+b")
    try:
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise Held(f"{path.name} is held.")
            time.sleep(0.05)
        try:
            yield
        finally:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


def lock_held(path: Path) -> bool:
    """Whether a process holds the lock: taken and let go of again at once if nobody does."""
    try:
        with file_lock(path):
            return False
    except Held:
        return True


def lock_path(project: str, name: str) -> Path:
    """A loop's run lock, held while it runs. Each loop of a project has its own in the project's
    folder, where the others find it (item loop-like)."""
    return config.paths().data / "loops" / project / f"{name}.lock"


def alive_loops(project: str, but: str | None = None) -> list[str]:
    """The loops of this app working the project now, by their run locks: all but `but`."""
    return _alive_in(config.paths().data / "loops" / project, but)


def _alive_in(folder: Path, but: str | None) -> list[str]:
    if not folder.is_dir():
        return []
    return [lock.stem for lock in sorted(folder.glob("*.lock")) if lock.stem != but and lock_held(lock)]


class ScriptMutex:
    """run-agent-loop.ps1's own `Local\\run-agent-loop-<project>`, which keeps the script and the
    app from working one project at once (item switchover). Owned by the thread that took it, which
    is the one the whole loop runs in. macOS and Linux have no script, so there it is always had."""

    def __init__(self, project: str):
        self.held = sys.platform != "win32"
        self._mutex = None
        if sys.platform != "win32":
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
        self._kernel = kernel
        self._mutex = kernel.CreateMutexW(None, False, config.script_mutex(project.lower()))
        if not self._mutex:
            # As before: a mutex that cannot be made keeps nobody out.
            self.held = True

    def take(self, wait: float = 0.0) -> bool:
        """Takes it if it is free, waiting up to `wait` seconds; whether this now holds it."""
        if self.held:
            return True
        # WAIT_OBJECT_0, or WAIT_ABANDONED: a script that died holding it.
        self.held = self._kernel.WaitForSingleObject(self._mutex, int(wait * 1000)) in (0, 0x80)
        return self.held

    def close(self) -> None:
        if self._mutex:
            if self.held:
                self._kernel.ReleaseMutex(self._mutex)
            self._kernel.CloseHandle(self._mutex)
            self._mutex = None
        self.held = sys.platform != "win32"


def script_holds(project: str) -> bool:
    """Whether something holds the script's mutex for the project: the script, or an app loop.
    Taken and let go of again at once if nobody does."""
    mutex = ScriptMutex(project)
    try:
        return not mutex.take()
    finally:
        mutex.close()


# How long a start tries again for a lock that is held: the runner, and a loop starting, look at
# each loop's lock and the script's mutex by taking them for a moment.
LOCK_RETRY = 1.0


@dataclass
class RunLock:
    """What a running loop holds: its own lock, and the script's mutex - or not, while another app
    loop on the project has it."""

    project: str
    name: str
    mutex: ScriptMutex
    # The project's other app loops alive as this one began.
    others: list[str]

    def retake(self) -> bool:
        """Takes the script's mutex if this loop does not have it and it is free: between items, so
        that it is held again once the loop that had it ends."""
        return self.mutex.take()


@contextlib.contextmanager
def run_lock(project: str, path: Path, wait: float = 0.0) -> Iterator[RunLock]:
    """The loop's own lock, and the script's mutex. Raises LoopError when the loop is running
    already, or when the script works the project: the mutex held while no other loop of this app
    on the project is alive. A loop that cannot have the mutex because another of this app's loops
    has it goes on without, and takes it between items once it is free."""
    name = path.stem
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(file_lock(path, max(wait, LOCK_RETRY)))
        except Held:
            raise LoopError(f"The loop {name} is already working {project}.") from None
        mutex = stack.enter_context(contextlib.closing(ScriptMutex(project)))
        mutex.take(LOCK_RETRY)
        # Looked at after the mutex, so that a loop starting beside this one is seen either way.
        others = _alive_in(path.parent, name)
        if not mutex.held and not others:
            raise LoopError(f"A loop is already working {project}: the script, run-agent-loop.ps1.")
        yield RunLock(project, name, mutex, others)


# What a phase used

@dataclass
class Phase:
    """What a phase used, over every session it took - one stopped by a usage limit used some too
    (`$script:Phase`)."""

    cost: float = 0.0
    turns: int = 0
    ms: int = 0
    input: int = 0
    read: int = 0
    write: int = 0
    output: int = 0
    model: str | None = None


@dataclass
class Plan:
    kind: str
    route: dict


# The loop

class Run:
    """One loop on one project, as one run of run-agent-loop.ps1 is."""

    def __init__(
        self,
        machine: config.Machine,
        loop: config.Loop,
        *,
        out: Callable[[str], None] = print,
        paint: Callable[[str, str | None], str] = colours.plain,
        clock: usage.Clock | None = None,
        overrides: dict | None = None,
        worker: str | None = None,
        on_start: Callable[[subprocess.Popen], None] | None = None,
        lock_wait: float = 0.0,
        setup_only: bool = False,
        again: bool = False,
    ):
        self.out = out
        # A run that only sets the project up, and runs no session: `agentloop setup`, and the
        # page's Set up again on a loop that is stopped (item project-setup). `again` runs the
        # script whether or not it is due.
        self.setup_only = setup_only
        self.again = again
        self.paint = paint
        self.clock = clock or usage.Clock()
        self.on_start = on_start
        self.lock_wait = lock_wait
        self.machine = machine
        self.loop = loop
        worker_name = worker or loop.worker
        found = machine.settings.worker(worker_name)
        if found is None:
            raise LoopError(f'No worker named "{worker_name}" in the settings.')
        self.worker = found
        self.project = loop.project
        # A token or key the worker names that is not set is refused at the start, not by the
        # first session after a triage's worth of waiting (item claude-container).
        try:
            session.sign_in_variables(found.sign_in)
        except session.SessionError as error:
            raise LoopError(str(error)) from None

        # The tracker is a document on an Overseer server, named as it is there, reached at the
        # server the loop names, else its worker (item loop-from-url), with the key of the account
        # the agents act as.
        overseer = config.overseer_for(machine.settings, loop, worker_name)
        key = config.expand(overseer["keyFile"]) if overseer else None
        if not overseer or not key.is_file():
            if loop.overseer:
                raise LoopError(f"{self.project}'s tracker is on a server, but the key file loop {loop.name} names is not there.")
            raise LoopError(f"{self.project}'s tracker is on a server, but worker {self.worker.name} names no file holding the agents' key.")
        self.overseer: dict = overseer
        self.tracker_env: dict[str, str | None] = {"OVERSEER_URL": str(overseer["server"]).rstrip("/"),
                                                   "OVERSEER_KEY_FILE": str(key.resolve())}

        # The project: its definition read from the tracker, or the copy kept when the server
        # cannot be reached (item doc-read).
        self.loop_dir = loop_dir(self.project, loop.name)
        try:
            loaded = definition.project_for(machine, loop, self.loop_dir, worker=worker_name)
        except config.ConfigError as error:
            raise LoopError(str(error)) from None
        project = loaded.project
        assert project is not None
        # What the run says first: that it starts from the copy, or on a changed definition, when
        # it does.
        self.definition_note = loaded.note
        # The definition document's version, for the check between sessions (item doc-change),
        # and whether the server was away at the last one, so that it is said once.
        self.definition_tag = loaded.tag
        self.definition_away = False
        self.project_def = project
        # What the project's setup script left for its sessions, once it has run or been read
        # (item project-setup); None while there is none.
        self.project_env: setup.SessionEnv | None = None
        self.overrides = {name: value for name, value in (overrides or {}).items() if value is not None}
        self.settings = config.loop_settings(machine, loop, project)
        self.settings.update(self.overrides)
        try:
            config.check_system(project)
            placed = config.place_repos(loop, project)
        except config.ConfigError as error:
            raise LoopError(str(error)) from None
        self.placed = placed
        self.repos: list[Path] = placed.tops
        self.workdir = self.repos[0]
        self.tracker = _slashes(placed.tracker_name)

        data = config.paths().data
        self.log_dir = data / "logs" / self.project
        self.attempts = self.log_dir / "attempts.jsonl"
        self.stop_file = self.loop_dir / "stop"
        self.kill_file = self.loop_dir / "kill"
        self.state_file = self.loop_dir / "running.json"
        self.claims_file = claims_path(self.loop_dir)
        self.status_file = self.loop_dir / "status.json"
        self.system_file = self.loop_dir / "system.md"
        self.session_settings = self.loop_dir / "session-settings.json"
        # Where a push replays the clones' commits onto their upstream, a worktree for each.
        self.worktrees = self.loop_dir / "sync"
        self.lock_file = lock_path(self.project, loop.name)
        self.lock: RunLock | None = None
        self.claude = session.find_claude(machine.settings.claude)

        # The run's state, as the script keeps it in its script scope.
        self.started_at = ""
        self.iteration = 0
        self.run_cost = 0.0
        self.last_rate: dict | None = None
        self.killed = False
        # A stop or a kill asked while paused at a worker's limit, before a triage's build.
        self.limit_stopped = False
        self.seen: dict[str, dict] = {}
        self.session_spent: dict[str, session.Spent] = {}
        self.session_context = 0
        self.suggested: str | None = None
        self.went_on_from: str | None = None
        self.commits: list[dict] = []
        self.phase = Phase()
        self.variables: dict[str, str | None] = {}
        self.scratch: Path | None = None
        self._beside: list[str] | None = None
        # What the project says about its tags, read once a run: it is the same answer for every
        # item, and a tracker.py call is most of an item's overhead (trackerstart).
        self._tags: dict | None = None
        # The item as complete_session last read it, for invoke_item to go on from without
        # reading it again; None once a merge session may have changed it.
        self.last_shown: dict | None = None
        self.status: dict = {}
        # The items this run holds now, and the one a session could not have: another loop's.
        self.claimed: set[str] = set()
        self.taken: str | None = None
        # Why the last claim was refused, in tracker.py's words, when it was not another loop's.
        self.refused = ""
        # The conflict a merge phase is asked to resolve.
        self.conflict: sync.Conflict | None = None
        # The tracker's watch stream while the loop is idle, for a test to see.
        self.watcher: watch.Watcher | None = None
        # The items whose voice messages could not be transcribed this run, and why: skipped for
        # the rest of it, said once (item voice-loop).
        self.unheard_failed: dict[str, str] = {}
        # Whether this run has given up the seat on the bench a killed run of the loop may have
        # left, and whether a sit or a leave has failed - said once a run (item projbench).
        self.bench_cleared = False
        self.bench_failed = False

    def _status(self, **changes) -> None:
        """status.json, for the page: written on each change, whole."""
        self.status.update(changes)
        self.status["updated"] = self._now()
        write_json(self.status_file, self.status)

    def _now(self) -> str:
        return datetime.fromtimestamp(self.clock.now()).astimezone().isoformat(timespec="seconds")

    def _took_rate(self, rate: dict) -> None:
        self.last_rate = rate
        self._status(usage=usage.usage_line(rate, usage.account_name(self.variables.get("CLAUDE_CONFIG_DIR"))))
        self._keep_rate(rate)

    def _keep_rate(self, rate: dict) -> None:
        try:
            accounts.save_usage(self.worker.name, rate, self.clock.now())
        except OSError:
            # The page's record of it only; the loop goes on without it.
            pass

    # Settings, by the script's names

    @property
    def routes(self) -> dict:
        return self.settings["routes"]

    def _s(self, key: str):
        return self.settings[key]

    # tracker.py

    def _tracker(self, *args: str) -> subprocess.CompletedProcess:
        return _run_tracker(self.tracker, self.tracker_env, *args)

    def read_tracker(self, *args: str) -> dict | None:
        """Asks tracker.py, which answers in one line of JSON; None if it refused (`Read-Tracker`)."""
        done = self._tracker(*args)
        text = done.stdout.decode("utf-8", errors="replace").strip()
        if done.returncode != 0 or not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def read_next(self, *args: str) -> dict | None:
        """`tracker.py next`: the item to take, or the signal. A worker with a manager asks for that
        user's items first; every `next` goes through here so that the three cannot differ."""
        owner = ("--owner", self.worker.manager) if self.worker.manager else ()
        answer = self.read_tracker("next", *args, *owner)
        # The account the loop signs in as, and the items held for other accounts by a mention
        # (item assign-bot), for the page to warn about a bot no loop here runs. Each answer
        # replaces the last, so a mention taken back clears the warning. A dry run, whose status a run never
        # began, writes none.
        if answer is not None and self.status:
            self._status(account=answer.get("me"), others=answer.get("others") or [],
                         unheard=len(answer.get("unheard") or []))
        return answer

    def _tracker_text(self, *args: str) -> str:
        # Its lines joined with a line feed, as PowerShell's -join gives them, whatever line ends
        # python wrote on this OS.
        return "\n".join(self._tracker(*args).stdout.decode("utf-8", errors="replace").splitlines())

    # Claiming the item at work

    def _write_claims(self) -> None:
        if self.claimed:
            write_json(self.claims_file, {"tracker": self.tracker, "env": self.tracker_env, "handles": sorted(self.claimed)})
        else:
            remove(self.claims_file)

    def claim(self, handle: str, worker: str | None = None, hold_only: bool = False) -> bool | None:
        """Marks the item as this loop's for its session, with `worker` - who works it - for the
        page to show: True when it is, False when another loop has it already or it is for another
        account (item for-col) - `refused` saying which - None when tracker.py could not say.
        `hold_only` only holds it against other loops, whoever it is for, and does not make it
        this account's: what transcribing it takes (item voice-tweaks)."""
        self.refused = ""
        said = self.read_tracker("claim", handle, *(("--worker", worker) if worker else ()),
                                 *(("--hold-only",) if hold_only else ()))
        if not said:
            return None
        if not said.get("claimed"):
            # tracker.py's own refusal only when it is not the claim button's, which the line
            # has always put as another loop at work.
            refusal = str(said.get("refused") or "")
            self.refused = refusal if refusal.startswith("it is for ") else ""
            return False
        self.claimed.add(handle)
        self._write_claims()
        return True

    def release(self, handle: str) -> None:
        """Lets go of an item claimed. One that cannot be let go of now is left in claims.json, for
        the next start to try again."""
        if handle not in self.claimed:
            return
        if self._tracker("release", handle).returncode == 0:
            self.claimed.discard(handle)
            self._write_claims()
        else:
            self.say(f"  {handle} could not be released; the next start of this loop tries again.", "DarkYellow")

    # The prompts

    def item_context(self, item: dict) -> str:
        """The item as tracker.py show prints it, and what the project says about the tags it
        carries (`Get-ItemContext`)."""
        h = item["handle"]
        # One call for both: `show --json` carries the text `show` prints. Its lines are joined
        # with a line feed, as _tracker_text joins them.
        details = self.read_tracker("show", h, "--json")
        shown = "\n".join(str((details or {}).get("text") or "").splitlines()).strip()
        text = f"\n\nThe item, as tracker.py show {h} prints it now:\n\n{shown}\n"
        tags = self.tags()
        if tags:
            carried = [t.strip() for t in str((details or {}).get("labels") or "").split(",")] if details and details.get("labels") else []
            said, others = [], []
            for name, value in tags.items():
                if not value.get("about"):
                    continue
                if name in carried:
                    said.append(f"## {name} - {value.get('name')}\n\n{value.get('about')}")
                else:
                    others.append(f"{name} ({value.get('name')})")
            if said:
                text += "\nWhat the project says about the tags this item carries:\n\n" + "\n\n".join(said) + "\n"
            if others:
                text += f"\nOther tags with something to say: {', '.join(others)} - tracker.py tag <tag> prints what one says, should the work turn out to reach that part.\n"
        return text + "\n"

    def tags(self) -> dict | None:
        """`tracker.py tag --json`, read the first time it is asked for in a run; a read that
        failed is tried again next time."""
        if self._tags is None:
            self._tags = self.read_tracker("tag", "--json")
        return self._tags

    def beside(self) -> list[str]:
        if self._beside is None:
            self._beside = [line for line in self._tracker_text("projects").splitlines() if line.strip()]
        return self._beside

    def prompt(self, kind: str, item: dict, from_: str | None) -> str:
        """What each kind of session is told (`Get-Prompt`). `from_` is the item a session has just
        finished, when it goes on to this one at its own asking."""
        h = item["handle"]
        if kind == "merge":
            return self.merge_prompt(h)
        if from_:
            picked = (f"Your work on {from_} has been committed. As you asked, go on in this same session with {h}, "
                      f"at {item.get('stage')}, in the tracker {self.tracker}: work that item and no other now.")
        else:
            picked = (
                "AUTOTASK.md and the project's brief are in your system prompt: follow them, and do not read either file again. "
                f"The loop has picked the item for you: {h}, at {item.get('stage')}, in the tracker {self.tracker}. "
                f"The repositories you may change, which the loop commits, are {', '.join(_slashes(r) for r in self.repos)} - run git in those folders and no others. "
                f"For files of your own, use the scratch folder {self.variables.get('AGENT_SCRATCH')}, also in AGENT_SCRATCH. "
                "Work that item and no other."
            )
        beside = self.beside()
        if beside and not from_:
            picked += (f" The other projects' trackers beside this one: {', '.join(beside)}. An item of one is named <project>:<handle>: "
                       "tracker.py show reads it, after waits on it, and file --into <project> files an item there - as AUTOTASK.md says under Other projects.")
        # A build going on from its own triage has the item before it already.
        if kind != "continue":
            picked += self.item_context(item)
        if kind != "continue" and not from_:
            picked += commits.repo_status(self.repos)
        close = (
            "This is an unattended run: nobody will answer a question during it, so anything that needs the owner goes into the tracker as AUTOTASK.md says. "
            f"Do not commit: the loop commits your changes when you finish. End your reply with a line 'COMMIT: {h}: <what changed>', under 72 characters, to be the commit message - "
            "or, if nothing on this project can be worked at all, as AUTOTASK.md says under Stopping the loop, with a line 'STOP_LOOP: <what is wrong>' instead. "
            "If another item could be worked straight away with what you have read here - it is beside this one and reaches the same code - you may add a line 'NEXT: <its handle>' after the COMMIT line, as AUTOTASK.md says under Going on to a related item."
        )
        ask = "if something needs a decision, ask with tracker.py ask, which moves it to planning and flags it asked"
        if kind == "triage":
            return (picked + f"Triage it: investigate, then move it to ready with its complexity, and its points as your plan puts them - or, {ask} - or, if it has to wait for another item first, add that to its after list and leave it where it is. "
                    "Do not start building it: the loop decides who builds it, and may ask you to go on in this same conversation. " + close)
        if kind == "build":
            return picked + f"Build it as its plan says, check it, and move it to testing - or, {ask}. " + close
        if kind == "continue":
            return f"Your triage of {h} has been committed. Now build it as your plan says, check it, and move it to testing - or, {ask}. " + close
        if kind == "discuss":
            return (picked + f"It is flagged discuss: only your thoughts are wanted. Investigate, then give your analysis and a recommendation with tracker.py ask, which moves it to planning and flags it asked. "
                    "Do not move it to ready, and build nothing. " + close)
        raise ValueError(f"no prompt for {kind!r}")

    def merge_prompt(self, h: str) -> str:
        """What a session is told when its commit conflicts with what was pushed since (item
        git-sync). The script has no such prompt: it never pushes."""
        c = self.conflict
        files = ", ".join(c.files)
        return (
            f"Your work on {h} has been committed, but {c.upstream} has moved on since, and replaying the commit onto it to push it conflicts. "
            f"The replay is stopped in a worktree of its own, {_slashes(c.worktree)}, of the repository {_slashes(c.repo)}, at the commit {c.commit}; "
            f"the files left conflicted there are: {files}. "
            "Resolve them in that worktree: edit each so that it keeps what both sides meant, with no conflict markers left, and check the result as the brief says if it can be checked there. "
            "If a check fails after your resolution, leave it as it is and answer UNMERGED, saying which check failed: the loop does not push what fails. "
            f"Change nothing in {_slashes(c.repo)} itself, and run no git command that changes anything - no add, commit, rebase, cherry-pick, checkout or reset: "
            "the loop stages your resolution, goes on with the replay and pushes it once you are done. "
            "If the resolution is not plain from the two sides, leave the files as they are, and say why in a comment on the item - "
            "any open item takes one, whatever its stage - and in the line below: the loop stops, and shows it to the owner. "
            "End your reply with a line 'MERGED: <how you resolved it>' once it is resolved, or 'UNMERGED: <why>' if you left it."
        )

    # Records

    def tried(self, handle: str, model: str) -> bool:
        """Whether a model has had a go at building an item, in this run or an earlier one
        (`Test-Tried`). The item's work records on the server are read first, so that a build on
        another PC counts too - the script reads only attempts.jsonl, which is one PC's - and
        attempts.jsonl after them, for the sessions that left no record."""
        shown = self.read_tracker("show", handle, "--json")
        work = shown.get("work") if isinstance(shown, dict) else None
        for record in work if isinstance(work, list) else []:
            if isinstance(record, dict) and record.get("kind") == "build" and same_model(str(record.get("model") or ""), model):
                return True
        if not self.attempts.exists():
            return False
        for line in self.attempts.read_text(encoding="utf-8").splitlines():
            try:
                a = json.loads(line)
            except ValueError:
                continue
            if isinstance(a, dict) and a.get("handle") == handle and a.get("kind") == "build" and a.get("model") == model:
                return True
        return False

    def add_attempt(self, item: dict, kind: str, route: dict, session_id: str, outcome: str, now: dict | None) -> dict:
        """A line of attempts.jsonl for one session: the item, who worked it and where it went, what
        the session used, and what it committed (`Add-Attempt`). `item` is the item as it was
        picked, `now` as the session left it."""
        points = points_of(now) if now and now.get("points") else points_of(item)
        was = points_of(item)
        p = self.phase
        line = {
            "at": datetime.fromtimestamp(self.clock.now()).strftime("%Y-%m-%dT%H:%M:%S"),
            "handle": item["handle"], "kind": kind, "model": route["model"], "effort": route.get("effort"),
            "from": item.get("stage"), "to": outcome, "points": points,
            "complexity": now.get("complexity") if now else item.get("complexity"),
            "session": session_id,
            "cost": _json_number(round(p.cost, 4)), "turns": p.turns, "minutes": _json_number(round(p.ms / 60000, 1)),
            "tokens": {"input": p.input, "cache_read": p.read, "cache_write": p.write, "output": p.output},
            "commits": list(self.commits),
        }
        if points != was:
            line["points_was"] = was
        # A session that went on to this item from another, at its own asking - to weigh, later,
        # what going on cost against starting afresh.
        if self.went_on_from:
            line["went_on_from"] = self.went_on_from
        self.attempts.parent.mkdir(parents=True, exist_ok=True)
        # Every loop of the project appends here. A write in append mode is not one append on
        # Windows - the C runtime seeks to the end, then writes - so two loops could each write
        # where the file ended: the lock beside it makes them take turns.
        with file_lock(self.attempts.with_name("attempts.lock"), wait=60.0):
            with self.attempts.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(line, separators=(",", ":"), ensure_ascii=False) + "\n")
        return line

    def add_work_record(self, before: commits.Snapshot, item: dict, kind: str, route: dict, now: dict | None) -> None:
        """The session's record on its item, written into the tracker before the commit so that it
        is part of it (`Add-WorkRecord`)."""
        if not now:
            return
        p = self.phase
        model = signature(p.model or route["model"], route)
        lines = commits.session_lines(before, self.repos)
        record = ["work", item["handle"], "--kind", kind, "--model", model, "--moved", f"{item.get('stage')} > {now.get('stage')}",
                  "--points", str(points_of(now)), "--cost", figure(round(p.cost, 2)),
                  "--read", str(round((p.read + p.input) / 1000)), "--written", str(round(p.write / 1000)),
                  "--output", str(round(p.output / 1000)), "--turns", str(p.turns),
                  "--minutes", figure(round(p.ms / 60000, 1)), "--lines", str(lines)]
        done = self._tracker(*record)
        if done.returncode != 0:
            said = (done.stdout + done.stderr).decode("utf-8", errors="replace").strip()
            self.say(f"  The session's record could not be written to the tracker: {said}", "DarkYellow")

    def finish_trusted(self, now: dict | None = None) -> None:
        """The items flagged trusted that are at testing, finished (item trusted-item): the owner
        trusts them to be done right, so the testing comment is their record and no person reads it
        first. Given the item a session has just left - `now` - only when it is such an item; with
        none, every such item, for one a person moved to testing by hand, or a parent the template
        moved on from parts. Always after the session's work record, which History keeps with the
        item but `tracker.py work` cannot reach once it is there. A failure leaves the item at
        testing, said as a warning. The script has no trusted flag."""
        if now is not None and (now.get("closed") or now.get("stage") != "testing" or "trusted" not in _flags(now)):
            return
        done = self._tracker("finish")
        said = done.stdout.decode("utf-8", errors="replace").strip().splitlines()
        for line in said:
            self.say(f"  {line}, as it is trusted", "Green")
        if done.returncode != 0:
            why = (done.stderr.decode("utf-8", errors="replace").strip().splitlines() or ["no reason given"])[-1]
            self.say(f"  A trusted item could not be finished, and is left at testing: {why}", "DarkYellow")

    def note_outcome(self, item: dict, route: dict, now: dict | None) -> None:
        """What a session that finished did with its item (`Note-Outcome`): counted when it left it
        at the stage it took it at and still free to take; forgotten otherwise."""
        h = item["handle"]
        if _free(now) and now.get("stage") == item.get("stage"):
            key = f"{item.get('stage')} {route['model']}"
            times = self.seen[h]["times"] + 1 if h in self.seen and self.seen[h]["key"] == key else 1
            self.seen[h] = {"key": key, "times": times}
        else:
            self.seen.pop(h, None)

    def repeats(self, handle: str, stage: str, model: str) -> int:
        """How many sessions in a row this run left an item at this stage, worked on this model
        (`Get-Repeats`)."""
        s = self.seen.get(handle)
        return s["times"] if s and s["key"] == f"{stage} {model}" else 0

    # Routing

    def trial_applies(self, item: dict | None) -> bool:
        """`Test-Trial`: until its date, items of its complexity and of its points or fewer."""
        trial = self._s("trial")
        if not trial or not item or item.get("complexity") != trial["complexity"]:
            return False
        if datetime.fromtimestamp(self.clock.now()) >= datetime.strptime(trial["until"], "%Y-%m-%d"):
            return False
        return points_of(item) <= trial["upTo"]

    def plan(self, item: dict) -> Plan:
        """What kind of session an item gets, and on which route (`Get-Plan`)."""
        if item.get("discuss"):
            # Thoughts only, at whatever stage: nothing is built, so no complexity to route by.
            plan = Plan("discuss", self.routes["triage"])
        elif item.get("stage") == "ready":
            route = self.routes.get(item.get("complexity")) or self.routes["unassigned"]
            if self.trial_applies(item):
                route = self._s("trial")["route"]
            escalation = self._s("escalation")
            if route["model"] != escalation["model"] and self.tried(item["handle"], route["model"]):
                self.say(f"  {route['model']} has had its go at {item['handle']}; this build is on {escalation['model']}.", "DarkYellow")
                route = escalation
            plan = Plan("build", route)
        else:
            plan = Plan("triage", self.routes["triage"])
        if self._s("model"):
            plan.route = {"model": self._s("model"), "effort": ""}
        return plan

    def say(self, line: str, colour: str | None = None) -> None:
        """A line out, in the colour the script writes it in (colours.py)."""
        self.out(self.paint(line, colour))

    # Running a session's work

    def _killed(self) -> bool:
        return self.kill_file.exists()

    def run_phase(self, kind: str, item: dict, route: dict, session_id: str, resume: bool, from_: str | None) -> dict | None:
        """One prompt's sessions to their end, waiting out any usage limit that stops one and
        resuming it (`Invoke-Phase`). The result event, or None."""
        asked = self.prompt(kind, item, from_)
        prompt = asked
        log = self.log_dir / f"loop-{self.started_at}-{self.iteration}-{kind}.jsonl"
        self.phase = Phase()
        p = self.phase
        while True:
            spec = session.SessionSpec(
                claude=self.claude, session_id=session_id, resume=resume, prompt=prompt,
                route=session.Route(route["model"], route.get("effort") or None), workdir=self.workdir,
                system_file=self.system_file, settings=self.session_settings, log=log,
                variables=dict(self.variables), state_file=self.state_file, item=item["handle"],
            )
            r = session.run_session(spec, on_event=self._show, on_start=self.on_start)
            final = r.final
            if r.rate:
                self._took_rate(r.rate)
            # This run's own share: what the session has used now, less what it had used before -
            # in the triage it carries on from, or a run of it a usage limit stopped. A session
            # begun afresh under the same id starts from nothing again.
            was = self.session_spent.get(session_id)
            if not was or r.spent.cost < was.cost:
                was = session.Spent()
            share = r.spent.cost - was.cost
            p.cost += share
            p.input += r.spent.input - was.input
            p.read += r.spent.read - was.read
            p.write += r.spent.write - was.write
            p.output += r.spent.output - was.output
            p.turns += r.turns
            p.ms += r.ms
            if r.model:
                p.model = r.model
            if r.context:
                self.session_context = r.context
            if r.spent.cost > 0:
                self.session_spent[session_id] = r.spent
            self.run_cost += share
            self._status(cost=round(self.run_cost, 4))
            if final:
                self.out("")
                self.say(f"  [{final.get('subtype')}: {r.turns} turns, {figure(round(r.ms / 60000, 1))} min, ${figure(round(share, 2))}]", "DarkGray")
            if not usage.limited(r, per_use=self.worker.sign_in_kind == "apiKey"):
                return final

            told = []

            def on_wait(waiting: usage.Waiting) -> None:
                # Said once, as the wait begins.
                if not told:
                    told.append(True)
                    self.say(waiting.line(), "DarkYellow")
                    self._status(state="waiting", until=waiting.until.astimezone().isoformat(timespec="seconds"), limit=waiting.limit)

            waited = usage.wait_out(
                r.rate, ask_every=float(self._s("askEveryMinutes")),
                probe=lambda: usage.probe(self.claude, self.variables, self.session_settings, self.log_dir),
                killed=self._killed, clock=self.clock, on_wait=on_wait,
            )
            self._status(state="running", until=None, limit=None)
            if waited.rate:
                self._took_rate(waited.rate)
            if waited.outcome == "killed":
                self.killed = True
                return None
            if waited.outcome == "lifted":
                self.say(usage.LIFTED_LINE, "DarkYellow")
            # A session stopped before claude saved it has nothing to carry on from, and starts again.
            if session.find_transcript(session_id, self.worker.claude):
                resume = True
                prompt = usage.resume_prompt(asked)

    def _show(self, event: session.Event) -> None:
        # Kept for the page as it comes, not only once the session ends: a long session would
        # leave the page the usage of the one before it. The script reports usage only between
        # iterations (`Write-Usage`), and so does the output here.
        if event.kind == "rate" and event.data:
            self._keep_rate(event.data)
        colour = session.event_colour(event)
        for line in session.format_event(event):
            self.say(line, colour)

    def resume_command(self, session_id: str) -> str:
        return session.resume_command(session_id, self.workdir, self.worker.claude)

    def complete_session(self, prompt_kind: str, kind: str, item: dict, route: dict, session_id: str,
                         resume: bool, from_: str | None) -> bool:
        """Runs one session's work on an item, records it, and commits what it changed
        (`Complete-Session`). False when the loop has to stop: the session failed, a commit did, or
        the agent found nothing on the project can be worked, or a pushed commit's conflict was
        not resolved."""
        # Pulled before the snapshot, so that what the pull brings is not taken for the session's.
        if self._s("push"):
            sync.pull(self.repos, self.say)
        before = commits.snapshot(self.repos)
        self.commits = []
        self.went_on_from = from_
        self.suggested = None
        self.taken = None
        # What signs the comments the session adds to the tracker: the name it was started with,
        # since which model that stands for is known only once the session says. The claim shows
        # it on the item too, as the agent at work.
        author = signature(route["model"], route)
        # Claimed once the snapshot is taken, and let go of before the commit.
        held = self.claim(item["handle"], author)
        if held is None:
            self.say(f"tracker.py could not claim {item['handle']}. Stopping.", "Red")
            return False
        if not held:
            self.taken = item["handle"]
            self.say(f"  {item['handle']}: {self.refused or 'another loop is working on it'} - left to it.", "DarkYellow")
            return True
        self._status(item=item["handle"], title=item.get("title"), kind=kind, session=session_id)
        # The session's variables: that author, and where an item it files into another project
        # came from.
        self.variables = session.session_variables(
            author=author, scratch=self.scratch, tracker=self.tracker,
            item=f"{Path(self.tracker).stem}:{item['handle']}", claude=self.worker.claude,
            overseer=self.overseer, sign_in=self.worker.sign_in, project_env=self.project_env,
        )
        try:
            final = self.run_phase(prompt_kind, item, route, session_id, resume, from_)
        finally:
            self.release(item["handle"])
        now = self.read_tracker("show", item["handle"], "--json")
        self.last_shown = now
        # Killed: during a wait, or with its claude ended mid-session - whatever it last said.
        killed = self.killed or self._killed()
        failed = not final or bool(final.get("is_error"))
        lines = session.parse_final(str(final.get("result") or "") if final else "")
        # The agent's word that the project cannot be worked counts only as a line of its final
        # answer, as COMMIT does.
        halt = lines.stop if not failed else None
        if killed:
            outcome = "killed"
        elif halt:
            outcome = f"stopped by the agent: {halt}"
        elif failed:
            outcome = "failed"
        else:
            outcome = now.get("stage") if now else "gone"
        # Recorded here when nothing is to be committed, and after the commit otherwise, with it.
        if killed or halt or failed:
            self.add_attempt(item, kind, route, session_id, outcome, now)

        if killed:
            self.killed = True
            # Nothing is committed and nothing cleared away: the session's changes stay in the
            # tree, its transcript and scratch folder are kept, and it can be carried on by hand.
            remove(self.kill_file)
            self.say(f"Stopped at once, as asked: {item['handle']} is left as the session had it, its changes uncommitted. To carry the session on by hand:", "Yellow")
            self.say(f"  {self.resume_command(session_id)}", "Yellow")
            return False
        if halt:
            self.say(f"The agent stopped the loop: {halt}", "Yellow")
            self.say(f"Its changes, if any, are uncommitted; the session can be carried on with: {self.resume_command(session_id)}", "Yellow")
            return False
        if not final:
            self.say(f"The session ended without a result. Stopping, with its changes uncommitted; see the log in {self.log_dir}, or carry it on with: {self.resume_command(session_id)}", "Red")
            return False
        if failed:
            # Left uncommitted: half a session's work is for the owner to look at, not to record.
            self.say(f"The session failed: {final.get('result')}. Stopping, with its changes uncommitted; carry it on with: {self.resume_command(session_id)}", "Red")
            return False
        self.say(f"  {item['handle']}: {item.get('stage')} -> {outcome}", "Cyan")

        # The agent's own one-line account of the session, or a plain one if it gave none, and what
        # ran it. Double quotes are swapped out, as the script swaps them, so the messages read the
        # same whichever wrote them.
        message = lines.commit or f"{item['handle']}: autotask {kind}"
        message = message.replace('"', "'") + f" [{route_name(route)}]"
        # The item it would go on to, given what it has read: taken up by the loop if it can be.
        self.suggested = lines.next
        self.add_work_record(before, item, kind, route, now)
        self.finish_trusted(now)
        done = commits.commit_session(before, message, self.repos)
        for said in done.said:
            self.say("  " + said, "DarkGreen" if said.startswith("committed") else "DarkYellow")
        self.commits = done.commits
        self.add_attempt(item, kind, route, session_id, outcome, now)
        self.note_outcome(item, route, now)
        if not done.ok:
            self.say("A commit failed. Stopping, with the session's changes as they are.", "Red")
            return False
        if self._s("push"):
            return self.push(item, route, session_id)
        return True

    def push(self, item: dict, route: dict, session_id: str) -> bool:
        """The session's commit pushed, with the session resumed to resolve a conflict in the
        replay (item git-sync). False when the loop has to stop."""
        resolve = lambda conflict: self.resolve_conflict(conflict, item, route, session_id)  # noqa: E731
        stopped = sync.push(self.repos, self.worktrees, resolve, self.say, tries=self._s("pushTries"))
        if stopped is None:
            return True
        if self.killed:
            remove(self.kill_file)
            self.say(f"Stopped at once, as asked, while {item['handle']}'s commit was replayed: it is left local. To carry the session on by hand:", "Yellow")
        else:
            for line in self.stopped_lines(stopped, item["handle"]):
                self.say(line, "Red")
        self.say(f"  {self.resume_command(session_id)}", "Yellow")
        return False

    @staticmethod
    def stopped_lines(stopped: sync.Stopped, handle: str) -> list[str]:
        """What the owner is told when a push stops the loop (item push-conflict): which
        repository, which files, why, and what to do. The script has no such message: it never
        pushes."""
        repo = _slashes(stopped.repo)
        if stopped.files:
            files = ", ".join(stopped.files)
            what = f"replaying {stopped.commit} onto {stopped.upstream} conflicts in {files}, and it was not resolved: {stopped.why}."
            fix = f"resolve {files}"
        else:
            what = f"replaying its commits onto {stopped.upstream} could not be finished: {stopped.why}."
            fix = "resolve what it stops at"
        return [
            f"Stopping: {stopped.repo.name} could not be pushed - {what} The commit is left local, and {handle} where the session left it.",
            f"To finish it by hand, in {repo}: stash any changes of your own, git pull --rebase, {fix}, check the result as the brief says, "
            "git add the files and git rebase --continue, then git push. Or carry the session on with:",
        ]

    def resolve_conflict(self, conflict: sync.Conflict, item: dict, route: dict, session_id: str) -> str | None:
        """The session that made a commit resumed to resolve the conflict replaying it: None once
        it did, or why not."""
        # The session may change the item: what complete_session read of it is stale.
        self.last_shown = None
        self.say(f"  {conflict.repo.name}: replaying {conflict.commit} onto {conflict.upstream} conflicts in {', '.join(conflict.files)}; the session resolves it.", "Yellow")
        self.conflict = conflict
        before = {repo: commits.changed(repo) for repo in self.repos}
        made = self.commits
        try:
            final = self.run_phase("merge", item, route, session_id, True, None)
        finally:
            self.conflict = None
        killed = self.killed or self._killed()
        failed = not final or bool(final.get("is_error"))
        lines = session.parse_final(str(final.get("result") or "") if final else "")
        if killed:
            self.killed = True
            why = "the loop was killed"
        elif failed:
            why = "the session failed"
        elif lines.unmerged:
            why = lines.unmerged
        elif not lines.merged:
            why = "the session said neither MERGED nor UNMERGED"
        elif {repo: commits.changed(repo) for repo in self.repos} != before:
            why = "the session changed files in the clone itself"
        else:
            why = None
        now = self.read_tracker("show", item["handle"], "--json")
        # The merge's own line: what it cost, and what came of it. Its commits are the session's,
        # recorded with it already.
        self.commits = []
        self.add_attempt(item, "merge", route, session_id, f"unmerged: {why}" if why else "merged", now)
        self.commits = made
        if why is None:
            self.say(f"  merged: {lines.merged}", "Cyan")
        return why

    def invoke_item(self, item: dict, kind: str, route: dict, session_id: str, from_: str | None) -> bool:
        """One item's work in a session, and the build a triage that leaves the item ready may go
        straight on to (`Invoke-Item`). True when the loop can go on."""
        if not self.complete_session(kind, kind, item, route, session_id, bool(from_), from_):
            return False
        if self.taken:
            return True
        # A triage that leaves its item ready at a complexity worth the larger model, or small, goes
        # on to build it, resumed at the same model and effort: the conversation so far is still in
        # the cache, and what it read for the plan is what the build needs.
        # The item as complete_session has just read it: nothing since has changed it, unless a merge
        # session ran, which forgets it.
        now = self.last_shown if self.last_shown and self.last_shown.get("handle") == item["handle"] else None
        now = now or self.read_tracker("show", item["handle"], "--json")
        small = bool(now) and points_of(now) <= self._s("buildInTriageUpTo")
        # Only an item the queue would offer now: one the triage left waiting on another item, or
        # held for a person, is not built in the same session either (`Invoke-Item` builds it
        # regardless; the queue's rule is the one kept here).
        if (kind == "triage" and _free(now) and now.get("stage") == "ready"
                and (now.get("complexity") in self._s("buildAfterTriage") or small) and not self.trial_applies(now)):
            # Past the worker's own limit, the build waits until it resets or going on is approved.
            if self._pause(between=False) is not None:
                self.limit_stopped = True
                self.say(f"The triage of {item['handle']} is committed. To build it by hand in the same session:", "Yellow")
                self.say(f"  {self.resume_command(session_id)}", "Yellow")
                return False
            # A kill asked for during the triage stops the loop before the build: the triage is
            # committed, and the build can be begun by hand in the same session.
            if self._killed():
                self.killed = True
                remove(self.kill_file)
                self.say(f"Stopped at once, as asked, after the triage of {item['handle']}. To build it by hand in the same session:", "Yellow")
                self.say(f"  {self.resume_command(session_id)}", "Yellow")
                return False
            self.out("")
            self.say("  build, in the same session", "Cyan")
            asked = self.suggested
            if not self.complete_session("continue", "build", now, route, session_id, True, None):
                return False
            # The build's own word on what comes next, or else the triage's.
            if not self.suggested:
                self.suggested = asked
        return True

    def next_in_session(self, route: dict, chained: int) -> dict | None:
        """Whether the session may go on to the item it asked for, and the item if so
        (`Get-NextInSession`)."""
        asked = self.suggested
        if not asked:
            return None
        why = None
        nxt = None
        if chained + 1 >= self._s("chainUpTo"):
            why = f"this session has worked {self._s('chainUpTo')} items"
        elif self.session_context > self._s("chainContextUpTo"):
            why = f"its context is {round(self.session_context / 1000)}k tokens"
        elif self.stop_file.exists() or self.kill_file.exists():
            why = "a stop was asked for"
        elif self._over() is not None:
            why = f"{self.worker.name} is past one of its usage limits"
        if not why:
            nxt = self.read_next("--prefer", asked)
            if not nxt or nxt.get("signal") or not nxt.get("preferred"):
                why = "it is not free to take now"
            elif nxt.get("stage") == "ready" and not nxt.get("discuss") and self.tried(nxt["handle"], route["model"]):
                why = f"{route['model']} has had its go at building it"
            elif self.repeats(nxt["handle"], nxt.get("stage"), route["model"]) >= self._s("repeatUpTo"):
                why = f"sessions of this run have left it at {nxt.get('stage')} {self._s('repeatUpTo')} times"
        if why:
            self.say(f"  The session asked to go on to {asked}; not in this session: {why}.", "DarkGray")
            return None
        return nxt

    def usage_line(self) -> None:
        account = usage.account_name(self.variables.get("CLAUDE_CONFIG_DIR"))
        line = usage.usage_line(self.last_rate, account)
        if not line and self.worker.sign_in_kind == "apiKey":
            line = usage.per_use_line(account)
        if line:
            self.say("  " + line, "DarkCyan")

    # Voice messages (item voice-loop). The script has none.

    def transcribing(self) -> bool:
        return str((self._s("transcribe") or {}).get("engine") or "off") != "off"

    def hear(self, answer: dict | None) -> dict | None:
        """next's answer, once the voice messages of the items it names as unheard are transcribed
        - asked again after them, so that the items they held are free to take. With transcription
        off they stay held, for a loop that transcribes. An item whose transcription failed is not
        tried again this run. A round that wrote nothing ends it: what is left cannot be heard now."""
        for _ in range(3):
            if not answer or not self.transcribing():
                return answer
            waiting = [h for h in answer.get("unheard") or [] if h not in self.unheard_failed]
            if not waiting or not self.transcribe(waiting):
                return answer
            answer = self.read_next()
        return answer

    def _voice_failed(self, handle: str, why: str) -> None:
        self.unheard_failed[handle] = why
        self.say(f"  {handle}: its voice message could not be transcribed - {why}. It is left held until the next run.", "DarkYellow")
        self._status(voiceProblem=f"{handle}: {why}")

    def transcribe(self, handles: list[str]) -> bool:
        """The sounds on those items transcribed and cleaned up, each written under its recording,
        with a work record of kind voice on its item. True when one was written.

        Each item is claimed while its sounds are transcribed - with --hold-only, which leaves who
        it is for as it was - and let go of after, so that no other loop works or transcribes it
        meanwhile (item voice-tweaks). One another loop has claimed is left to it, and is not a
        failure: it is transcribed once it is free, by whichever loop sees it first. Nothing is
        moved or committed."""
        folder = self.loop_dir / "voice"
        folder.mkdir(parents=True, exist_ok=True)
        done = _run_tracker(self.tracker, {**self.tracker_env, "AGENT_SCRATCH": _slashes(folder)}, "sounds", "--fetch")
        try:
            sounds = json.loads(done.stdout.decode("utf-8", errors="replace")) if done.returncode == 0 else None
        except ValueError:
            sounds = None
        if not isinstance(sounds, list):
            self.say("  tracker.py could not list the voice messages to transcribe; they wait for the next look.", "DarkYellow")
            return False
        wrote = False
        people: list[dict] | None = None
        setting = dict(self._s("transcribe"))
        # The item claimed for its sounds, and those that could not be: `sounds` lists an item's
        # sounds one after another, so each is claimed once and let go of before the next.
        holding: str | None = None
        passed: set[str] = set()
        try:
            for sound in sounds:
                h = str(sound.get("handle") or "")
                if h not in handles or h in self.unheard_failed or h in passed:
                    continue
                if h != holding:
                    if holding:
                        self.release(holding)
                        holding = None
                    got = self.claim(h, f"transcribing ({setting.get('engine')})", hold_only=True)
                    if not got:
                        passed.add(h)
                        why = "another loop is working on it" if got is False else "tracker.py could not claim it"
                        self.say(f"  {h}: its voice messages are not transcribed now - {why}.", "DarkGray")
                        continue
                    holding = h
                if not sound.get("file"):
                    self._voice_failed(h, f"the server has no recording {sound.get('path')}")
                    continue
                self.say(f"  {h}: transcribing the voice message of {sound.get('author') or 'someone'} with {setting.get('engine')}", "Cyan")
                try:
                    heard = voice.transcribe(Path(str(sound["file"])), setting)
                except voice.VoiceError as error:
                    self._voice_failed(h, str(error))
                    continue
                except OSError as error:
                    self._voice_failed(h, f"the engine could not be run ({type(error).__name__})")
                    continue
                if not str(heard.get("text") or "").strip():
                    self._voice_failed(h, "the engine heard no words in it")
                    continue
                if people is None:
                    listed = self.read_tracker("people")
                    people = listed if isinstance(listed, list) else []
                cleaned = self._clean_up(sound, heard, people)
                if cleaned is None:
                    # A usage limit: the next session waits it out, and the sound is heard after it.
                    return wrote
                if isinstance(cleaned, str):
                    self._voice_failed(h, cleaned)
                    continue
                text, route, r = cleaned
                words = folder / f"heard-{uuid.uuid4().hex}.md"
                words.write_bytes(voice.transcript_text(text).encode("utf-8"))
                try:
                    written = self._tracker("heard", h, str(sound.get("at") or ""), str(sound.get("path") or ""), f"@{_slashes(words)}")
                finally:
                    remove(words)
                if written.returncode != 0:
                    said = written.stderr.decode("utf-8", errors="replace").strip().splitlines()
                    self._voice_failed(h, f"tracker.py would not write it: {said[-1] if said else 'no reason given'}")
                    continue
                wrote = True
                self._voice_record(h, heard, text, route, r)
        finally:
            if holding:
                self.release(holding)
            # The recordings fetched are of no more use: one not heard is fetched again next time.
            for left in (folder / "tracker-sounds").rglob("*"):
                if left.is_file():
                    remove(left)
        return wrote

    def _voice_record(self, handle: str, heard: dict, text: str, route: dict, r: session.SessionResult) -> None:
        """A transcription said in the output, counted in the run's cost, and recorded on its item
        as a work record of kind voice, so that what it cost shows in Stats."""
        cost = r.spent.cost
        self.run_cost += cost
        self._status(cost=round(self.run_cost, 4))
        language = heard.get("language") or "language not said"
        self.say(f"  {handle}: voice message transcribed ({language}), {len(text)} characters, cleaned up on "
                 f"{route_name(route)} for ${figure(round(cost, 2))}.", "Green")
        record = self._tracker("work", handle, "--kind", "voice", "--model", signature(r.model or route["model"], route),
                               "--cost", figure(round(cost, 2)), "--read", str(round((r.spent.read + r.spent.input) / 1000)),
                               "--written", str(round(r.spent.write / 1000)), "--output", str(round(r.spent.output / 1000)),
                               "--turns", str(r.turns), "--minutes", figure(round(r.ms / 60000, 1)))
        if record.returncode != 0:
            said = (record.stdout + record.stderr).decode("utf-8", errors="replace").strip()
            self.say(f"  The transcription's record could not be written to the tracker: {said}", "DarkYellow")

    def _clean_up(self, sound: dict, heard: dict, people: list[dict]) -> tuple[str, dict, session.SessionResult] | str | None:
        """The one-turn call on the voice route that makes a transcript clear English: its text,
        the route and what it used; why not, when it failed; None when a usage limit stopped it."""
        h = str(sound.get("handle"))
        shown = self.read_tracker("show", h, "--json")
        if not shown:
            return f"tracker.py could not show {h}"
        at = _moment(sound.get("at"))
        comments = shown.get("comments") or []
        mine = next((i for i, c in enumerate(comments) if at is not None and _moment(c.get("at")) == at), None)
        if mine is None:
            return "the comment that holds it is gone"
        route = self.routes["voice"]
        prompt = voice.cleanup_prompt(
            description=self.project_def.description, handle=h, title=str(shown.get("title") or ""),
            before=comments[:mine], comment=str(comments[mine].get("body") or ""), people=people, heard=heard,
            author=str(sound.get("author") or comments[mine].get("author") or "someone"))
        folder = self.loop_dir / "voice"
        session_id = str(uuid.uuid4())
        variables = session.session_variables(
            author=signature(route["model"], route), scratch=folder, tracker=self.tracker,
            item=f"{Path(self.tracker).stem}:{h}", claude=self.worker.claude, overseer=self.overseer,
            sign_in=self.worker.sign_in, project_env=self.project_env)
        spec = session.SessionSpec(
            claude=self.claude, session_id=session_id, resume=False, prompt=prompt,
            route=session.Route(route["model"], route.get("effort") or None), workdir=folder,
            system_file=None, settings=None, log=self.log_dir / f"loop-{self.started_at}-voice.jsonl",
            variables=variables, one_turn=True)
        try:
            r = session.run_session(spec, on_start=self.on_start)
        except OSError as error:
            return f"claude could not be run for the cleanup ({type(error).__name__})"
        finally:
            # Nothing to carry on from: the call is over once it has answered.
            with contextlib.suppress(OSError):
                session.remove_transcript(session_id, self.worker.claude)
        if r.rate:
            self._took_rate(r.rate)
        if usage.limited(r, per_use=self.worker.sign_in_kind == "apiKey"):
            self.say(f"  {h}: its voice message waits - {self.worker.name} is at a usage limit.", "DarkYellow")
            return None
        final = r.final
        text = str((final or {}).get("result") or "").strip()
        if not final or final.get("is_error") or not text:
            return f"the cleanup call failed ({(final or {}).get('subtype') or 'no result'})"
        return text, route, r

    # The whole run

    def _prepare(self) -> None:
        if self.definition_note:
            self.say(f" {self.definition_note}", "DarkYellow")
        self.loop_dir.mkdir(parents=True, exist_ok=True)
        # A replay's worktree a run that died, or was killed, left.
        sync.discard_all(self.repos, self.worktrees)
        self._write_system()

    def _write_system(self) -> None:
        # AUTOTASK.md and what the project is, copied once, so the whole run works to one version
        # of them, with this machine's folders in them - and again only when the run takes a
        # changed definition (item doc-change). What the project is is the definition's one
        # description; its tracker's is not asked for (item doc-read).
        folders, places = self.placed.folders, self.placed.places
        described = self.project_def.description.replace("\r\n", "\n")
        session.write_system_file(
            self.system_file, session.autotask(), described,
            # The project's tools, fetched from its tracker when it lists any (item tools-fetch).
            where=session.where_you_are(self.project_def.files, folders, places, self.project_def.about,
                                        project_env=setup.env_dir(self.project) if self.project_env is not None else None,
                                        brewfile=brew.brewfile(self.workdir) if brew.enabled() else None),
            values=session.placeholders(self.project_def.files, folders, places),
        )
        session.write_session_settings(self.session_settings)

    def run(self) -> int:
        """The main loop. 0 when it ended as a loop ends - the queue empty, a stop asked for, its
        items worked - and 1 when it stopped on something the owner has to look at."""
        with run_lock(self.project, self.lock_file, self.lock_wait) as lock:
            self._beside_others(lock.others)
            self.lock = lock
            self.status = {}
            self._status(loop=self.loop.name, project=self.project, worker=self.worker.name, state="running", pid=os.getpid(),
                         started=self._now(), item=None, title=None, kind=None, session=None, cost=0.0,
                         usage=None, until=None, limit=None, ended=None, exit=None, killed=False, error=None,
                         transcribe=self.transcribing(), unheard=0, voiceProblem=None)
            code = 1
            error = None
            try:
                # What a run that died, or was ended by force, still held: let go of first.
                for handle in release_left(self.loop_dir):
                    self.say(f" Released {handle}, which the last run of this loop left claimed.", "DarkYellow")
                self._prepare()
                # A stop or a kill asked of a loop that has since ended is not one asked of this one.
                for left in (self.stop_file, self.kill_file, self.state_file):
                    remove(left)
                code = self._loop()
                return code
            except BaseException as raised:
                # Its type only: the message is in the output, and may name paths.
                error = type(raised).__name__
                raise
            finally:
                # Each session lets go of its item as it ends; this is for an error between the two.
                for handle in sorted(self.claimed):
                    self.release(handle)
                remove(self.state_file)
                self._status(state="stopped" if code == 0 and not error else "failed", ended=self._now(),
                             exit=None if error else code, error=error, killed=self.killed, until=None, limit=None,
                             item=None, title=None, kind=None, session=None)

    def _beside_others(self, others: list[str]) -> None:
        """Refuses a start beside another loop of the project that commits in a repository this one
        does."""
        for name in others:
            other = self.machine.settings.loop(name)
            if other is None:
                continue
            shared = [top for top in config.tops(other, self.project_def) if top in self.repos]
            if shared:
                raise LoopError(f"The loop {name} is working {self.project} in {_slashes(shared[0])}, as this one would: "
                                "each loop of a project needs clones of its own.")

    def _loop(self) -> int:
        self.started_at = datetime.fromtimestamp(self.clock.now()).strftime("%Y%m%d-%H%M%S")
        if self.setup_only:
            return self._setup_alone()
        self.say(f" Starting the agent loop on {self.project}. To stop it once the item at work is done, make the file {self.stop_file} - or at once, {self.kill_file}", "Cyan")
        status = 0
        while True:
            # The script's mutex, had again once the loop of the project that had it has ended.
            if self.lock:
                self.lock.retake()
            if self.kill_file.exists():
                remove(self.kill_file)
                self.say("Stopped at once, as asked, between items.", "Yellow")
                break
            if self.stop_file.exists():
                remove(self.stop_file)
                self.say("Stopping as asked.", "Green")
                break
            # A changed definition, before the next session: read again after a session, and
            # waited on from the first while one is still to be accepted (item doc-change).
            ended = self._definition(read=self.iteration > 0)
            if ended is None:
                # The project's tools, set up before its first session and whenever the accepted
                # definition's setup script changes (item project-setup).
                ended = self._setup()
            if ended is None:
                ended = self._brew()
            if ended is None:
                ended = self._pause()
            if ended is not None:
                status = ended
                break
            self.iteration += 1
            self.say("----------------------------------------", "Yellow")
            self.say(f" Iteration {self.iteration}...", "Yellow")
            self.say("----------------------------------------", "Yellow")

            # Trusted items at testing are finished first, so that a parent they bring to testing
            # is too; then voice messages are transcribed, so that the items they held can be taken.
            self.finish_trusted()
            item = self.hear(self.read_next())
            self._clear_seat()
            if not item:
                self.say("tracker.py could not say what comes next. Stopping.", "Red")
                status = 1
                break
            if item.get("signal"):
                self.say(item["signal"], "Green")
                if not self._auto_start():
                    break
                item = self._idle()
                if not item:
                    break
                # A wait can be long, and the definition may have changed in it.
                ended = self._definition(read=True)
                if ended is not None:
                    status = ended
                    break
                self._begin_again()

            plan = self.plan(item)
            kind, route = plan.kind, plan.route
            # Picked again at the stage sessions on the same model left it at, an item would get
            # the same session again - once more is allowed, as a session may say what the next
            # should finish.
            times = self.repeats(item["handle"], item.get("stage"), route["model"])
            if times >= self._s("repeatUpTo"):
                self.say(f"{item['handle']} is still at {item.get('stage')} after {times} sessions on {route['model']} left it there. Stopping, for the owner to look.", "Red")
                status = 1
                break

            self.say(f" {item['handle']} - {item.get('title')}", "Cyan")
            self.say(f"  {kind}, at {item.get('stage')}, complexity {item.get('complexity')}: {route_name(route)}", "Cyan")

            session_id = str(uuid.uuid4())
            self.scratch = self.loop_dir / "scratch" / session_id
            self.scratch.mkdir(parents=True, exist_ok=True)
            self.session_context = 0
            from_ = None
            chained = 0
            stopping = False
            while True:
                if not self.invoke_item(item, kind, route, session_id, from_):
                    stopping = True
                    break
                # Going on, in the same session, to the item it asked for - on the route it has,
                # since another model could not carry its cache.
                nxt = self.next_in_session(route, chained)
                if not nxt:
                    break
                self.iteration += 1
                chained += 1
                from_ = item["handle"]
                item = nxt
                kind = "discuss" if item.get("discuss") else "build" if item.get("stage") == "ready" else "triage"
                self.out("")
                self.say(f" {item['handle']} - {item.get('title')}", "Cyan")
                self.say(f"  {kind}, at {item.get('stage')}, in the same session, as it asked (item {self.iteration})", "Cyan")
            if stopping:
                status = 0 if self._stopped_cleanly() else 1
                break
            session.clear_session(session_id, self.scratch, self.worker.claude)
            self.usage_line()
            # A brief pause between clean iterations.
            self.clock.sleep(COOLDOWN)

        self.say(f"Loop finished; its sessions came to ${figure(round(self.run_cost, 2))}.", "Green")
        self.usage_line()
        return status

    def _auto_start(self) -> bool:
        """Whether the loop waits for work when the queue is empty, rather than ending. Read from
        the settings each time, since the page's toggle reaches a loop already running; settings
        that cannot be read just now leave it as it was."""
        try:
            machine = config.load_machine()
            found = machine.settings.loop(self.loop.name)
            if found is not None:
                current = config.loop_settings(machine, found)
                self.settings["autoStart"] = current["autoStart"]
                self.settings["autoStartMinutes"] = current["autoStartMinutes"]
        except (config.ConfigError, OSError):
            pass
        return bool(self._s("autoStart"))

    def _idle(self) -> dict | None:
        """Waits, spending no session, until `tracker.py next` offers an item, and returns it; None
        when a stop, a kill or the toggle turned off ends the wait. New items, and the owner's
        answers to items flagged asked, are what free it. A failed
        look is tried again at the next: a server away for a moment is no reason to end a loop that
        has nothing to do. The script has nothing like this; it ends on the signal.

        It looks every `autoStartMinutes`, and also once the tracker has changed and then been
        still for `SETTLE_SECONDS`, heard through the server's watch stream (item instant-auto):
        a burst of edits is one look, and an item is not triaged while the owner is still writing
        it. The set times stay, for a stream that cannot be heard, and for an item freed by one in
        another project's tracker, which this one's stream does not tell of."""

        def wait() -> float:
            seconds = float(self._s("autoStartMinutes")) * 60
            at = self.clock.now() + seconds
            self._status(state="idle", until=datetime.fromtimestamp(at).astimezone().isoformat(timespec="seconds"), limit=None)
            return at

        self.say(f" Nothing to work: looking again every {figure(self._s('autoStartMinutes'))} minutes, and as the tracker changes.", "Cyan")
        self._status(item=None, title=None, kind=None, session=None, watching=False)
        look_at = wait()
        failing = False
        heard: list[str] = []
        # On the bench before the stream is opened: the sit is then the version it opens on, not a
        # change that would make this loop look again. Another idle loop's sit or leave is a change
        # it hears - one look, which writes nothing, so loops cannot set each other off.
        self.bench("sit")
        watcher = self._watcher(heard.append)
        # The count of changes looked at, and the one heard last with when it was first seen.
        looked, counted, since = 0, 0, self.clock.now()
        try:
            while True:
                if self.kill_file.exists():
                    remove(self.kill_file)
                    self.say("Stopped at once, as asked, while idle.", "Yellow")
                    return None
                if self.stop_file.exists():
                    remove(self.stop_file)
                    self.say("Stopping as asked.", "Green")
                    return None
                if not self._auto_start():
                    self.say("Auto-start was turned off. Stopping.", "Green")
                    return None
                while heard:
                    self.say(heard.pop(0), "DarkYellow")
                if bool(self.status.get("watching")) != watcher.watching:
                    self._status(watching=watcher.watching)
                if watcher.changes != counted:
                    counted, since = watcher.changes, self.clock.now()
                if counted != looked and self.clock.now() - since >= SETTLE_SECONDS:
                    look_at = self.clock.now()
                if self.clock.now() >= look_at:
                    looked = counted
                    if self.lock:
                        self.lock.retake()
                    # What changed may be a trusted item a person moved to testing.
                    self.finish_trusted()
                    item = self.hear(self.read_next())
                    if item and not item.get("signal"):
                        self._status(state="running", until=None, watching=False)
                        return item
                    if not item and not failing:
                        self.say("tracker.py could not say what comes next. Trying again at the next look.", "DarkYellow")
                    failing = not item
                    look_at = wait()
                self.clock.sleep(usage.POLL_SECONDS)
        finally:
            watcher.stop()
            self.watcher = None
            self._status(watching=False)
            # Off the bench however the wait ends: work found, a stop, a kill, the toggle, an error.
            self.bench("leave")

    def seat(self) -> str:
        """The loop's seat on the bench, as `tracker.py bench --loop` takes it: the machine and the
        loop. tracker.py puts the account the key signs in as before it, so that each loop anywhere
        has one seat."""
        return f"{socket.gethostname()}/{self.loop.name}"

    def bench(self, action: str) -> bool:
        """`tracker.py bench sit` or `leave`, for the seat. Only for the page, so a failure is
        said once a run and never stops the loop."""
        done = self._tracker("bench", action, "--loop", self.seat())
        if done.returncode == 0:
            return True
        if not self.bench_failed:
            self.bench_failed = True
            self.say(" The loop's seat on the tracker's bench could not be written; it waits all the same.", "DarkYellow")
        return False

    def _clear_seat(self) -> None:
        """Once a run, as it first reads the tracker: the seat a run of this loop that was killed
        in its wait left behind, given up, since no `finally` of that run did."""
        if not self.bench_cleared:
            self.bench_cleared = True
            self.bench("leave")

    def _watcher(self, say: Callable[[str], None]) -> watch.Watcher:
        """The stream of the tracker the loop works, opened for one wait, on the server and with
        the key tracker.py is given."""
        self.watcher = watch.Watcher(str(self.overseer["server"]), config.expand(self.overseer["keyFile"]), self.tracker, say)
        self.watcher.start()
        return self.watcher

    def _begin_again(self) -> None:
        """After a wait, as a run begun now would: its items counted from the first, nothing held
        against an item from before the wait, which the owner may have sent back since. The logs
        are named by the run's start and the item's number, so that has to move on too."""
        self.started_at = datetime.fromtimestamp(self.clock.now()).strftime("%Y%m%d-%H%M%S")
        self.iteration = 1
        self.seen = {}
        self.say("----------------------------------------", "Yellow")
        self.say(f" Work has come up. Iteration 1...", "Yellow")
        self.say("----------------------------------------", "Yellow")

    # A changed definition (item doc-change). The script has nothing like it: its projects are
    # files on the PC it runs on, read once.

    def _accept_mode(self) -> str:
        """acceptDefinition, read from the settings each time as `_auto_start` reads its toggle, so
        that a change on the page reaches a loop already running."""
        try:
            machine = config.load_machine()
            found = machine.settings.loop(self.loop.name)
            if found is not None:
                return config.accept_mode(machine, found)
        except (config.ConfigError, OSError):
            pass
        return self.settings.get("acceptDefinition", "auto")

    # The project's setup script (item project-setup). The script has nothing like it: its machine
    # had the tools already.

    def _setup_mode(self) -> str:
        """setup, read from the settings each time, as `_accept_mode` reads acceptDefinition."""
        try:
            machine = config.load_machine()
            found = machine.settings.loop(self.loop.name)
            if found is not None:
                return config.setup_mode(machine, found)
        except (config.ConfigError, OSError):
            pass
        return self.settings.get("setup", "auto")

    def _setup(self) -> int | None:
        """Before a session: the setup script the accepted definition names for this system, run
        when it has not run for this script, or last failed, or was asked to run again; and what it
        left for the sessions, read. None to go on; 1 when it failed, with the end of its output
        said, for the person to put right before the loop starts again."""
        tool = config.setup_tool(self.project_def.raw)
        if tool is None or self.project_def.files is None or self._setup_mode() == "off":
            if self.project_env is not None:
                self.project_env = None
                self._write_system()
            return None
        script = self.project_def.files / "tools" / tool["file"]
        try:
            if setup.due(self.project, tool):
                self.say(f" Setting up {self.project}'s tools: {tool['file']}, in {_slashes(setup.env_dir(self.project))}.", "Cyan")
                self._status(state="setting-up")
                found = setup.run(self.project, script, tool, lambda line: self.say(f"  {line}", "DarkGray"),
                                  on_start=self._setup_started)
                self.say(f" {self.project} is set up.", "Green")
                self._status(state="running")
            elif self.project_env is None:
                found = setup.session_env(self.project)
            else:
                return None
        except (setup.SetupError, OSError) as error:
            # An OSError is the project's folder or its record not written: its type only, for
            # its message names paths.
            if isinstance(error, OSError):
                error = f"its folder could not be written ({type(error).__name__})"
            self.say(f" {self.project}'s setup failed: {error}. No session runs until it succeeds: put it right, then "
                     f"start the loop again, or use Set up again on the page (agentloop setup {self.loop.name} --again).", "Red")
            return 1
        finally:
            remove(self.state_file)
        self.project_env = found
        self._write_system()
        return None

    def _setup_alone(self) -> int:
        """A run that sets the project up and ends: 0 when it is set up, or has nothing to set up."""
        tool = config.setup_tool(self.project_def.raw)
        if tool is None:
            self.say(f" {self.project}'s definition names no setup script for this system: nothing to set up.", "Green")
            return 0
        if self._setup_mode() == "off":
            self.say(f" The loop {self.loop.name} has setup off: its machine has the tools already. Nothing was run.", "Yellow")
            return 0
        if self.again:
            setup.forget(self.project)
        elif not setup.due(self.project, tool):
            self.say(f" {self.project} is set up already, by this {tool['file']}.", "Green")
            return 0
        return self._setup() or 0

    def _brew(self) -> int | None:
        """Before a session, where Homebrew is on (item brew-tools): what the first repository's
        Brewfile lists and is not installed yet, installed. None to go on; 1 when brew failed."""
        file = brew.brewfile(self.workdir)
        if not brew.enabled() or not file.is_file():
            return None
        try:
            if brew.satisfied(file):
                return None
            self.say(f" Installing what {_slashes(file)} lists, with Homebrew.", "Cyan")
            self._status(state="setting-up")
            brew.install(file, lambda line: self.say(f"  {line}", "DarkGray"), on_start=self._setup_started)
            self.say(f" {self.project}'s Brewfile is installed.", "Green")
            self._status(state="running")
        except (brew.BrewError, OSError) as error:
            if isinstance(error, OSError):
                error = type(error).__name__
            self.say(f" Installing {self.project}'s Brewfile failed: {error}. No session runs until it succeeds: put "
                     "the Brewfile right, then start the loop again.", "Red")
            return 1
        finally:
            remove(self.state_file)
        return None

    def _setup_started(self, process: subprocess.Popen) -> None:
        """running.json for the setup script, as for a session's claude, so that a kill ends it at once."""
        write_json(self.state_file, {"pid": os.getpid(), "child": process.pid, "session": None, "item": None, "resume": None})

    def _stop_asked(self, where: str) -> bool:
        """A stop or a kill asked while the loop waits on something other than a session."""
        if self.kill_file.exists():
            remove(self.kill_file)
            self.say(f"Stopped at once, as asked, while {where}.", "Yellow")
            return True
        if self.stop_file.exists():
            remove(self.stop_file)
            self.say("Stopping as asked.", "Green")
            return True
        return False

    def _definition(self, read: bool) -> int | None:
        """Between sessions, a loop on a tracker: with `read`, whether its definition has changed -
        a 304 from the server while the document has not - and, when it has, the change settled
        (`definition.settle`). Under "auto" the run goes on with the new definition; under "ask",
        or with a change already waiting from before, it runs no session until the change is
        accepted. None to go on; else the run's exit, as `_loop` ends with it. A server that
        cannot be reached is no change: the run goes on with what it has, and says so once."""
        tracker = self.loop.tracker
        if read:
            server, key = str(self.overseer["server"]), config.expand(self.overseer["keyFile"])
            raw = None
            try:
                changed, tag = definition.source_tag(server, key, tracker, self.definition_tag)
                if changed or tag is None:
                    raw = definition.fetch(server, key, tracker)
                    # Its tools fetched with it, before it is kept (item tools-fetch).
                    settled = definition.settle(self.loop_dir, tracker, raw, self._accept_mode(),
                                                definition.file_getter(server, key, tracker))
            except definition.Unreachable as why:
                raw = None
                if not self.definition_away:
                    self.say(f" {self.project}'s definition could not be read ({why}); going on with the one this run has.", "DarkYellow")
                self.definition_away = True
            except config.ConfigError as error:
                self.say(f"{error}. Stopping, for the definition to be put right on the server.", "Red")
                return 1
            else:
                self.definition_away = False
                self.definition_tag = tag
            if raw is not None:
                if settled in ("changed", "first") and definition.version(raw) != self.project_def.version:
                    self.say(f" {self.project}'s definition has changed, and this run goes on with the new one (acceptDefinition is auto). "
                             f"agentloop show-definition {self.loop.name} shows what changed.", "DarkYellow")
                    return self._take(raw)
        if definition.pending(self.loop_dir, tracker) is not None:
            return self._hold()
        return None

    def _hold(self) -> int | None:
        """Runs no session while a changed definition waits to be accepted - on the page, or with
        `agentloop accept` - then goes on with it. A stop or a kill still ends the wait."""
        self.say(f" {self.project}'s definition has changed since this loop accepted it. No session runs until the change is accepted, "
                 f"on the page or with agentloop accept {self.loop.name}; agentloop show-definition {self.loop.name} shows what changed.", "Yellow")
        self._status(state="held", item=None, title=None, kind=None, session=None, until=None, limit=None)
        while True:
            if self._stop_asked("held"):
                return 0
            if definition.pending(self.loop_dir, self.loop.tracker) is None:
                held = definition.kept(self.loop_dir, self.loop.tracker)
                if held is None:
                    self.say("The definition this loop accepted is gone from its folder. Stopping.", "Red")
                    return 1
                self.say(" The change was accepted; going on with it.", "Green")
                return self._take(held["definition"])
            self.clock.sleep(usage.POLL_SECONDS)

    def _over(self) -> usage.Over | None:
        """The window the worker is past its own limit in, unapproved (item h87x). Its limits are
        read from the settings each time, so that the page's Edit reaches a loop already running;
        settings that cannot be read just now leave them as they were."""
        worker = self.worker
        try:
            found = next((w for w in config.load_settings().workers if w.name == worker.name), None)
            if found is not None:
                worker = found
        except (config.ConfigError, OSError):
            pass
        return accounts.over_limit(worker, self.clock.now())

    def _pause(self, between: bool = True) -> int | None:
        """Runs no session while the worker is past one of its own limits (item h87x): until the
        window resets, or going on past it is approved - on the page, or with `agentloop
        approve-limit` - as `_hold` waits for a definition's Accept. A session already running is
        never ended for it, so one may go a little past. None to go on; 0 when a stop or a kill
        ended the wait. `between` items, no item is at work; before a build, its item still is. The
        script has nothing like it."""
        over = self._over()
        if over is None:
            return None
        told = None
        while over is not None:
            if (over.window, over.resets) != told:
                told = (over.window, over.resets)
                self.say(f" {over.line(self.worker.name)}", "Yellow")
                resets = usage.local(over.resets).isoformat(timespec="seconds") if over.resets else None
                if between:
                    self._status(item=None, title=None, kind=None, session=None)
                self._status(state="paused", until=resets, limit=over.window, over={
                    "window": over.window, "name": usage.window_name(over.window),
                    "used": over.used, "limit": over.limit, "resets": resets})
            if self._stop_asked("paused at a usage limit"):
                self._status(over=None)
                return 0
            self.clock.sleep(usage.POLL_SECONDS)
            last = over
            over = self._over()
        approved = accounts.approvals(self.worker.name).get(last.window, 0) > self.clock.now()
        why = "going on past it was approved" if approved else "it has reset"
        self.say(f" Going on: {self.worker.name}'s {usage.window_name(last.window)} limit holds no more, as {why}.", "Green")
        self._status(state="running", until=None, limit=None, over=None)
        return None

    def _take(self, raw: dict) -> int | None:
        """The run on a new definition: its settings, its repositories and places placed again, and
        its system prompt written again. A system the machine is not on stops the run, as
        `check_system` refuses a start; a repository or place the loop has no path for holds it,
        whatever acceptDefinition says, until the path is given in the loop's settings."""
        project = definition.project(self.loop.tracker, raw, loop_dir=self.loop_dir)
        try:
            config.check_system(project)
        except config.ConfigError as error:
            self.say(f"{error}. Stopping.", "Red")
            return 1
        told = False
        while True:
            try:
                machine = config.load_machine()
                found = machine.settings.loop(self.loop.name)
                if found is None:
                    self.say(f"The loop {self.loop.name} is gone from the settings. Stopping.", "Red")
                    return 1
                placed = config.place_repos(found, project)
                break
            except config.ConfigError as error:
                if not told:
                    self.say(f" {error}. No session runs until it is given in the loop's settings (Edit, on the page).", "Yellow")
                    self._status(state="held", item=None, title=None, kind=None, session=None, until=None, limit=None)
                    told = True
            if self._stop_asked("held"):
                return 0
            self.clock.sleep(usage.POLL_SECONDS)
        self.loop = found
        self.project_def = project
        self.settings = config.loop_settings(machine, found, project)
        self.settings.update(self.overrides)
        self.placed = placed
        self.repos = placed.tops
        self.workdir = self.repos[0]
        self._write_system()
        self._status(state="running")
        return None

    def dry(self, handle: str | None = None) -> int:
        """A start that stops where the first session would begin: the settings read, the
        repositories placed, the tracker read, the item picked - the queue's next, or `handle` -
        and routed, and the system prompt and the prompt written to `dry/` in the loop's folder.
        No claude is run, nothing is committed or recorded, and the loop's lock is not taken:
        a loop of the project may be running, and its own system.md is left alone. The script
        has nothing like it; it is for checking a loop's settings, and a project's description, without
        spending a session. 0 when both were written, 1 when there was no item to write them for."""
        dry = self.loop_dir / "dry"
        self.system_file = dry / "system.md"
        self.session_settings = dry / "session-settings.json"
        dry.mkdir(parents=True, exist_ok=True)
        self._prepare()
        if handle:
            shown = self.read_tracker("show", handle, "--json")
            if not shown:
                self.say(f"tracker.py has no item {handle}.", "Red")
                return 1
            flags = [f.strip() for f in str(shown.get("flags") or "").split(",")]
            item = {"handle": shown["handle"], "stage": shown.get("stage") or "filed",
                    "complexity": shown.get("complexity") or "unassigned", "flags": shown.get("flags") or "",
                    "discuss": "discuss" in flags, "points": shown.get("points") or "1", "title": shown.get("title") or ""}
        else:
            item = self.read_next()
            if not item:
                self.say("tracker.py could not say what comes next.", "Red")
                return 1
            if item.get("signal"):
                self.say(item["signal"], "Green")
                return 1
        plan = self.plan(item)
        session_id = str(uuid.uuid4())
        # Named in the prompt as a session's would be, but not made: no session will use it.
        self.scratch = self.loop_dir / "scratch" / session_id
        self.variables = session.session_variables(
            author=signature(plan.route["model"], plan.route), scratch=self.scratch, tracker=self.tracker,
            item=f"{Path(self.tracker).stem}:{item['handle']}", claude=self.worker.claude,
            overseer=self.overseer, sign_in=self.worker.sign_in, project_env=self.project_env,
        )
        prompt_file = dry / "prompt.md"
        prompt_file.write_bytes(self.prompt(plan.kind, item, None).encode("utf-8"))
        self.say(f" {item['handle']} - {item.get('title')}", "Cyan")
        self.say(f"  {plan.kind}, at {item.get('stage')}, complexity {item.get('complexity')}: {route_name(plan.route)}", "Cyan")
        self.say(f"  system prompt  {self.system_file}")
        self.say(f"  prompt         {prompt_file}")
        return 0

    def _stopped_cleanly(self) -> bool:
        # A kill, or a stop while paused at a worker's limit, is a stop asked for; anything else is
        # for the owner to look at.
        return self.killed or self.limit_stopped


def find_loop(machine: config.Machine, name: str) -> config.Loop:
    loop = machine.settings.loop(name)
    if loop is None:
        known = ", ".join(l.name for l in machine.settings.loops) or "none"
        raise LoopError(f"No loop named {name}. The loops are: {known}")
    return loop
