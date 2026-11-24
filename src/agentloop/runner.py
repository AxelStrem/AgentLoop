"""Several loops at once: each started as a process of its own, stopped or killed on its own, and
what each is doing and has printed read back from the files it keeps.

The script's side is `stop-agent-loop.ps1`, `kill-agent-loop.ps1` and the run lock its loops take;
one window each is how the script runs several. Here a loop is `agentloop run <loop> --managed`,
started detached, its output going to `output.log` in its folder (loop.loop_dir). It goes on when
the app that started it closes, and an app started again finds it there: everything this reads -
the run lock, status.json, running.json, the output - is in files, not in this process.

- A loop is alive while its own run lock is held (loop.run_lock), which the OS lets go of when the
  holder dies, so a crashed loop is never taken for a running one. Its pid, which another program
  may have by now, is only trusted for the moment between its start and its taking the lock.
- A loop that is not running is busy, and is not started, while the script works its project, or
  while another loop of the project is running that it cannot run beside (item loop-like): any,
  when the tracker is a file, or one that commits in a repository it does.
- A stop is the stop file, as `stop-agent-loop.ps1` makes it: the loop finishes the item at work,
  commits it and ends, and waits out a usage limit it is in first.
- A kill is the kill file and the end of the session's claude with everything it started, as
  `kill-agent-loop.ps1` ends it: the tree on Windows (`taskkill /T /F`), the process group on macOS
  and Linux, claude being started in a group of its own (session._popen_options). A loop that has
  not ended a while after is ended the same way.
- A loop comes back when `serve` starts (`Runner.come_back`, item autoloops-start): one cut off -
  its process gone mid-run, without being stopped or killed, as a container's are when it is
  stopped or a machine's when it restarts - which said it was working and holds no lock (item
  docker-image); and one with Auto start on whose last run ended on an empty queue, Auto start
  having been off then. A loop a person stopped, killed or turned Auto start off for, one that
  stopped on an error for the owner to look at, and one never started, stay as they are. Each start
  so made is kept in `restarted.json`, with why, for the page, until a person starts the loop.

Differences from the scripts, each deliberate:
- The kill ends the claude running.json names, not every claude.exe whose parent is the loop.
- On Windows the tree is ended as `taskkill /T /F` ends it, but swept again until nothing is left:
  taskkill reads the tree once, and a process being started at that moment lived on.
- Stopping or killing a loop that is not running says so and changes nothing; the scripts make
  the file even then (stop) or say so too (kill).

Only Windows has been tried: this machine is Windows. The process group, `start_new_session` and
`killpg` are untried on macOS and Linux (item other-os).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agentloop import config, definition, loop as loop_mod

# How long a killed loop is given to end of its own - it looks at the kill file every few seconds
# while it waits out a limit - before it is ended too.
KILL_GRACE = 15.0

FINAL = ("stopped", "failed")

# How long a loop started by another app is taken to be starting, before it has said anything, in
# seconds: it says so as soon as it has its lock.
STARTING = 60.0

# How long a start waits for the last run to let go of its output, in seconds: a loop says how it
# ended a moment before its process exits and closes the output it was given.
LET_GO = 2.0

# How long a repository's top is remembered for what is only shown, in seconds.
TOP_KEPT = 60.0


class RunnerError(Exception):
    """Something the runner was asked that cannot be done, said so the owner can act on it."""


@dataclass
class LoopStatus:
    """What the page shows of one loop."""

    name: str
    project: str
    # starting, running, waiting (on a usage limit), idle (on an empty queue), held (on a changed
    # definition to accept, or a path to give - item doc-change), paused (at its worker's own usage
    # limit - item h87x), stopping, stopped or failed.
    state: str
    alive: bool
    pid: int | None = None
    item: str | None = None
    title: str | None = None
    kind: str | None = None
    session: str | None = None
    cost: float = 0.0
    usage: str | None = None
    until: str | None = None
    # Idle, and hearing its tracker's changes as they are made (item instant-auto).
    watching: bool = False
    # Paused: the window past its worker's limit - its key and name, the share used and the limit,
    # and when it resets (item h87x).
    over: dict | None = None
    started: str | None = None
    ended: str | None = None
    exit: int | None = None
    killed: bool = False
    error: str | None = None
    # Set when the project is being worked by something else: another loop on it, or the script.
    busy: str | None = None
    # The Overseer account its key signs in as, once an item mentions one, and the items held for
    # other accounts by their mentions, as {handle, bots, names}: usernames, and the display names
    # the page shows them by (item assign-bot).
    account: str | None = None
    others: list | None = None
    # Voice messages (item voice-loop): whether this loop transcribes them, how many items wait for
    # theirs, and why the last that could not be transcribed was not.
    transcribe: bool | None = None
    unheard: int = 0
    voiceProblem: str | None = None
    # How its last run ended (Run._ended_by): stop, kill, autoOff, empty, error or done.
    endedBy: str | None = None
    # Not running, and `serve` would start it again: why (item autoloops-start).
    comesBack: str | None = None
    # Started again by `serve` at its start, as {at, why}, or not started then, with {error} too.
    restarted: dict | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)

    def line(self) -> str:
        parts = [f"{self.name:<16} {self.state:<9}"]
        if self.item:
            parts.append(f"{self.kind} {self.item}")
        if self.until:
            parts.append(f"until {self.until}")
        if self.alive or self.cost:
            parts.append(f"${self.cost:.2f}")
        if self.state == "failed" and self.error:
            parts.append(self.error)
        if self.busy:
            parts.append(self.busy)
        return "  ".join(parts)


@dataclass
class Killed:
    """What a kill ended, and how to carry the session on by hand."""

    ended_session: bool
    ended_loop: bool
    item: str | None = None
    session: str | None = None
    resume: str | None = None


@dataclass
class Output:
    text: str
    offset: int


@dataclass
class _Files:
    dir: Path
    stop: Path
    kill: Path
    running: Path
    status: Path
    started: Path
    output: Path
    previous: Path
    lock: Path
    restarted: Path


def _files(found: config.Loop) -> _Files:
    folder = loop_mod.loop_dir(found.project, found.name)
    return _Files(folder, folder / "stop", folder / "kill", folder / "running.json", folder / "status.json",
                  folder / "started.json", folder / "output.log", folder / "output.prev.log", loop_mod.lock_path(found.project, found.name),
                  folder / "restarted.json")


def _read_json(path: Path) -> dict | None:
    # Windows refuses the read for the moment a loop replaces the file (loop.write_json).
    for attempt in range(50):
        try:
            value = json.loads(path.read_bytes().decode("utf-8"))
        except PermissionError:
            time.sleep(0.02)
            continue
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None
    return None


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# Why each run's end reads as one a person, or the owner's attention, asked for: such a loop is left
# as it is. A status from before endedBy was written has none, and is left too.
COME_BACK_AFTER = {"empty": "Auto start is on, and its last run ended on an empty queue"}


def comes_back(settings: config.Settings, found: config.Loop, files: _Files, said: dict | None, lock: bool) -> str | None:
    """Why `serve` starting would start this loop again, or None (item autoloops-start):

    - cut off: it said it was working - running, waiting, idle, held or setting up - and holds no
      lock, nor left a stop or kill file: its process was ended under it, by a restart, a
      container stopped or a crash, whether or not Auto start is on (item docker-image);
    - or Auto start is on, and its last run ended as `COME_BACK_AFTER` says: with nothing to do
      while Auto start was off, which it no longer is.

    A loop a person stopped, killed or turned Auto start off for, one that stopped on an error for
    the owner to look at - starting it again unseen would spend sessions on what stopped it - and
    one that never said anything, which may never have begun, are not."""
    if lock or said is None or files.kill.exists() or files.stop.exists():
        return None
    state = said.get("state")
    if not state:
        return None
    if state not in FINAL:
        return "its process ended mid-run, without being stopped"
    if said.get("killed"):
        return None
    why = COME_BACK_AFTER.get(str(said.get("endedBy")))
    if why and config.effective_settings(settings.defaults, found.settings).get("autoStart"):
        return why
    return None


# Repositories, as the page sees them

_tops: dict[Path, tuple[float, Path]] = {}
_tops_lock = threading.Lock()


def remembered_top(folder: Path) -> Path | None:
    """`config.repo_top`, for what the page shows and asks several times over: git takes a good
    part of a second on a busy PC, for every repository of every loop at every look at the list. A
    loop starting still asks git itself. A folder in no repository is not remembered, so that one
    made a repository is seen as soon as the page looks again."""
    now = time.monotonic()
    with _tops_lock:
        kept = _tops.get(folder)
    if kept and now - kept[0] < TOP_KEPT:
        return kept[1]
    top = config.repo_top(folder)
    with _tops_lock:
        if top is None:
            _tops.pop(folder, None)
        else:
            _tops[folder] = (now, top)
    return top


# Processes, as each OS has them

def held(found: config.Loop) -> bool:
    """Whether the loop is running: its own run lock, taken and let go of again at once if nobody
    holds it."""
    return loop_mod.lock_held(loop_mod.lock_path(found.project, found.name))


def busy(found: config.Loop) -> str | None:
    """What keeps a loop that is not running from starting now, or None: the script working its
    project, or another loop of the project it cannot run beside - as loop.Run judges it when it
    starts (`_beside_others`)."""
    others = loop_mod.alive_loops(found.project, but=found.name)
    if not others:
        # The mutex is held, and no loop of this app is alive to hold it.
        return f"{found.project} is being worked by the script" if loop_mod.script_holds(found.project) else None
    try:
        machine = config.load_machine()
    except config.ConfigError:
        return None
    # The project as last read: a check before a start never waits on the server.
    try:
        project = definition.project_for(machine, found, loop_mod.loop_dir(found.project, found.name), read=False).project
    except config.ConfigError:
        return None
    if project is None:
        return None
    mine = set(config.tops(found, project, remembered_top))
    for name in others:
        other = machine.settings.loop(name)
        if other is not None and mine & set(config.tops(other, project, remembered_top)):
            return f"{name} is running in a repository it shares with this one"
    return None


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        # PROCESS_QUERY_LIMITED_INFORMATION
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            # ERROR_ACCESS_DENIED: it is there, and someone else's.
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def end_tree(pid: int) -> None:
    """Ends a process and everything it started: its tree on Windows, its process group - which
    both a loop and a session's claude are started as the leader of - on macOS and Linux."""
    if os.name == "nt":
        _end_tree_windows(pid)
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _end_tree_windows(root: int) -> None:
    # What `taskkill /T /F` does, which the script uses, but swept again until nothing is found:
    # taskkill reads the tree once, so a process its parent was starting at that moment lives on,
    # an orphan holding the loop's output open. A child is a process whose parent pid is one ended
    # here and which began after it, since a pid can be reused. Each handle is held until the end,
    # so no pid ended here is given to another process meanwhile.
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *[ctypes.POINTER(ctypes.c_ulonglong)] * 4)
    kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)

    class Entry(ctypes.Structure):
        # PROCESSENTRY32W
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    kernel.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(Entry))
    kernel.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(Entry))

    def processes() -> list[tuple[int, int]]:
        snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            return []
        found = []
        try:
            entry = Entry()
            entry.dwSize = ctypes.sizeof(Entry)
            more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                found.append((entry.th32ProcessID, entry.th32ParentProcessID))
                more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        return found

    def opened(pid: int) -> tuple[int, int] | None:
        # PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION
        handle = kernel.OpenProcess(0x0001 | 0x1000, False, pid)
        if not handle:
            return None
        created, exited, system, user = (ctypes.c_ulonglong() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user)):
            kernel.CloseHandle(handle)
            return None
        return handle, created.value

    first = opened(root)
    if first is None:
        return
    ended = {root: first}
    try:
        kernel.TerminateProcess(first[0], 1)
        quiet = 0
        # Two sweeps in a row that find nothing, a moment apart, and none started meanwhile.
        for _ in range(100):
            found = False
            for pid, parent in processes():
                if pid in ended or parent not in ended:
                    continue
                got = opened(pid)
                if got is None:
                    continue
                if got[1] < ended[parent][1]:
                    kernel.CloseHandle(got[0])
                    continue
                ended[pid] = got
                kernel.TerminateProcess(got[0], 1)
                found = True
            quiet = 0 if found else quiet + 1
            if quiet >= 2:
                break
            time.sleep(0.05)
    finally:
        for handle, _ in ended.values():
            kernel.CloseHandle(handle)


def _detached() -> list[dict]:
    """The ways to start a loop that outlives the app, best first. On Windows a console of its own
    with no window - DETACHED_PROCESS would give each git and claude it starts a window of its own -
    out of the app's job if the job allows it; on macOS and Linux a session of its own, which a
    closing terminal's hangup does not reach."""
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        return [{"creationflags": flags | subprocess.CREATE_BREAKAWAY_FROM_JOB}, {"creationflags": flags}]
    return [{"start_new_session": True}]


def command(name: str, setup: bool = False) -> list[str]:
    """The command that runs a loop with this same app: a build's folder (build.py), which Python
    runs through its __main__.py, or else the installed package. With `setup`, a run that only
    sets its project up again (item project-setup)."""
    package = Path(__file__).resolve().parent
    more = ["--setup-only"] if setup else []
    if (package.parent / "__main__.py").is_file():
        return [sys.executable, str(package.parent), "run", name, "--managed", *more]
    return [sys.executable, "-m", "agentloop", "run", name, "--managed", *more]


def output_path(name: str) -> Path:
    found = config.load_settings().loop(name)
    if found is None:
        raise RunnerError(f"No loop named {name}.")
    return _files(found).output


# The runner

@dataclass
class Runner:
    """The loops of this machine's settings. Holds nothing a restarted app would need: only the
    processes it started itself, so that it can reap them."""

    _started: dict[str, subprocess.Popen] = field(default_factory=dict)

    def _loop(self, name: str) -> config.Loop:
        settings = config.load_settings()
        found = settings.loop(name)
        if found is None:
            known = ", ".join(l.name for l in settings.loops) or "none"
            raise RunnerError(f"No loop named {name}. The loops are: {known}")
        return found

    def start(self, name: str, setup: bool = False, why: str | None = None) -> LoopStatus:
        """Starts the loop as a process of its own. Refused while its project is being worked.
        With `setup`, a run that runs its project's setup script again and ends, no session run
        (item project-setup). With `why`, a start `serve` made of its own (`come_back`), kept in
        restarted.json for the page; a start without one is a person's, and clears it."""
        found = self._loop(name)
        files = _files(found)
        # A loop of ours that has said how it ended is waited for, for the moment it takes to exit.
        process = self._started.get(name)
        if process is not None and process.poll() is None and (_read_json(files.status) or {}).get("state") in FINAL:
            try:
                process.wait(LET_GO)
            except subprocess.TimeoutExpired:
                pass
        # A loop just started has not taken its lock yet, but has said it is starting.
        state = self._status(found)
        if state.alive:
            raise RunnerError(f"The loop {name} is already working {found.project}.")
        if state.busy:
            raise RunnerError(f"{name} cannot start: {state.busy}.")
        files.dir.mkdir(parents=True, exist_ok=True)
        # The last run's output is kept beside this one's; status.json is the new run's to write.
        if files.output.exists():
            # Windows refuses to rename a file another process has open: the last run's, started
            # by another app and a moment from exiting, is given a little while to let go of it.
            deadline = time.monotonic() + LET_GO
            while True:
                try:
                    os.replace(files.output, files.previous)
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        # Held open by a process the last run started, which is still going.
                        raise RunnerError(f"The last run of {name} left a process writing to its output; end it first.") from None
                    time.sleep(0.1)
        loop_mod.remove(files.status)
        loop_mod.remove(files.started)
        with open(files.output, "wb") as out, open(os.devnull, "rb") as nothing:
            argv = command(name, setup)
            process = None
            for options in _detached():
                try:
                    process = subprocess.Popen(argv, stdin=nothing, stdout=out, stderr=subprocess.STDOUT,
                                               cwd=files.dir, close_fds=True, **options)
                    break
                except OSError:
                    # A job that does not allow breaking away refuses the start: the next way.
                    continue
            if process is None:
                raise RunnerError(f"The loop {name} could not be started.")
        self._started[name] = process
        loop_mod.write_json(files.started, {"pid": process.pid, "at": time.time()})
        if why:
            loop_mod.write_json(files.restarted, {"at": _now(), "why": why})
        else:
            loop_mod.remove(files.restarted)
        return self.status(name)

    def not_restarted(self, name: str, why: str, error: str) -> None:
        """Keeps, for the page, that `serve` would have started the loop again and could not."""
        loop_mod.write_json(_files(self._loop(name)).restarted, {"at": _now(), "why": why, "error": error})

    def stop(self, name: str) -> LoopStatus:
        """Asks the loop to stop once the item at work is done (`stop-agent-loop.ps1`)."""
        found = self._loop(name)
        state = self.status(name)
        if not state.alive:
            raise RunnerError(f"No loop is working {found.project} as {name}.")
        files = _files(found)
        files.stop.touch()
        return self.status(name)

    def kill(self, name: str, grace: float = KILL_GRACE) -> Killed:
        """Ends the loop now, its session's claude and all it started with it, its changes left
        uncommitted (`kill-agent-loop.ps1`)."""
        found = self._loop(name)
        state = self.status(name)
        if not state.alive:
            raise RunnerError(f"No loop is working {found.project} as {name}.")
        files = _files(found)
        # Read by the loop between sessions, between a triage and its build, while it waits out a
        # limit, and once the session ended below has returned to it.
        files.kill.touch()
        running: dict = {}
        ended_session = False

        def end_session() -> None:
            # A session begun a moment ago may not have named its claude yet, so this is looked
            # at again until the loop has ended.
            nonlocal running, ended_session
            running = _read_json(files.running) or running
            child = running.get("child")
            if not ended_session and isinstance(child, int) and pid_alive(child):
                end_tree(child)
                ended_session = True

        end_session()
        ended_loop = False
        deadline = time.monotonic() + grace
        while held(found) and time.monotonic() < deadline:
            self._reap(name)
            time.sleep(0.2)
            end_session()
        if held(found) and state.pid and pid_alive(state.pid):
            end_tree(state.pid)
            ended_loop = True
            # Ended without its say, so its kill file is left for nobody: cleared, as the loop
            # would have cleared it.
            files.kill.unlink(missing_ok=True)
            # Nor did it say how it ended: said here, so that it reads as killed, and so that
            # `serve` starting does not take it for one cut off and start it again.
            said = _read_json(files.status) or {}
            loop_mod.write_json(files.status, {**said, "state": "failed", "killed": True,
                                               "error": "ended by force after the kill", "item": None})
            # Nor did it let go of the item it was working: let go of here, as the loop would have.
            loop_mod.release_left(files.dir)
        self._reap(name)
        return Killed(ended_session, ended_loop, running.get("item"), running.get("session"), running.get("resume"))

    def _reap(self, name: str) -> None:
        # The page's server asks from several threads: only the process looked at is let go of.
        process = self._started.get(name)
        if process is not None and process.poll() is not None and self._started.get(name) is process:
            self._started.pop(name, None)

    def come_back(self) -> list[tuple[str, str]]:
        """The loops `serve` starts again as it starts, each with why (item autoloops-start): those
        `comes_back` names, of this machine's settings, in their order."""
        settings = config.load_settings()
        found = []
        for each in settings.loops:
            files = _files(each)
            why = comes_back(settings, each, files, _read_json(files.status), held(each))
            if why:
                found.append((each.name, why))
        return found

    def status(self, name: str) -> LoopStatus:
        return self._status(self._loop(name))

    def statuses(self) -> list[LoopStatus]:
        settings = config.load_settings()
        return [self._status(found, settings) for found in settings.loops]

    def _status(self, found: config.Loop, settings: config.Settings | None = None) -> LoopStatus:
        self._reap(found.name)
        files = _files(found)
        # The lock first: a loop says how it ended before it lets go of it, so one seen without
        # its lock and not saying so has died - read the other way round, it may have ended
        # cleanly in between.
        lock = held(found)
        said = _read_json(files.status)

        def made(state: str, alive: bool, **more) -> LoopStatus:
            fields = {k: v for k, v in (said or {}).items() if k in LoopStatus.__dataclass_fields__}
            fields.update(name=found.name, project=found.project, state=state, alive=alive, **more)
            fields["restarted"] = _read_json(files.restarted)
            if not alive:
                try:
                    fields["comesBack"] = comes_back(settings or config.load_settings(), found, files, said, lock)
                except config.ConfigError:
                    fields["comesBack"] = None
            fields["cost"] = float(fields.get("cost") or 0.0)
            return LoopStatus(**fields)

        if said is None:
            # Started, and not yet so far as to say anything - or it ended before it could. Its pid
            # is trusted only for as long as a loop takes to say it has begun.
            started = _read_json(files.started) or {}
            pid = started.get("pid")
            if not isinstance(pid, int):
                return made("stopped", False, busy=busy(found))
            process = self._started.get(found.name)
            running = process.poll() is None if process else pid_alive(pid) and time.time() - float(started.get("at") or 0) < STARTING
            if running:
                return made("starting", True, pid=pid)
            return made("failed", False, pid=pid, error="ended before it began; see its output", busy=busy(found))
        state = said.get("state")
        if state in FINAL:
            # Said before its process exits: stopped only once the process is gone, so that a start
            # straight after does not meet it still holding the output. Only a process this runner
            # started is known for sure; one an earlier app started, start() waits out.
            process = self._started.get(found.name)
            if process is not None and process.poll() is None:
                return made("stopping", True)
            return made(state, False, busy=busy(found))
        if not lock:
            # It said it was running, and let go of the lock without saying how it ended: it died.
            return made("failed", False, error=said.get("error") or "ended without saying how", busy=busy(found))
        if files.kill.exists() or files.stop.exists():
            return made("stopping", True)
        return made(state or "running", True)

    def output(self, name: str, offset: int = 0) -> Output:
        """What the loop has printed from `offset` on, up to its last whole line, and the offset to
        ask from next. An offset past the end - a new run's output - starts again from the top."""
        files = _files(self._loop(name))
        try:
            with open(files.output, "rb") as f:
                size = f.seek(0, os.SEEK_END)
                if offset > size:
                    offset = 0
                f.seek(offset)
                data = f.read()
        except FileNotFoundError:
            return Output("", 0)
        whole = data.rfind(b"\n") + 1
        return Output(data[:whole].decode("utf-8", errors="replace"), offset + whole)
