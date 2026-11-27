"""An idle loop in Auto start moving to a new build (item autoloops-update): telling a newer build
from the running one, starting loops on the build `current` names, the handover's wait for the old
run, and the loop ending to move and handing its start over. Server-free: the loop is a bare `Run`
given only what these parts read; no process is started but a sleeping Python."""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentloop import __version__, build, colours, config, loop, runner

SRC = Path(__file__).resolve().parents[1] / "src"


def make_version(to: Path, name: str) -> Path:
    """A build's version folder as build.build lays it out; its package returned."""
    package = to / "versions" / name / "agentloop"
    package.mkdir(parents=True)
    (package.parent / "__main__.py").write_text("", encoding="utf-8")
    (package / build.BUILD_FILE).write_text(json.dumps({"version": __version__}), encoding="utf-8")
    return package


def point(to: Path, name: str) -> None:
    (to / "current").write_text(name + "\n", encoding="utf-8")


@pytest.fixture
def built(tmp_path):
    to = tmp_path / "install"
    old = make_version(to, "20261124-100000-aaaaaaa")
    make_version(to, "20261124-110000-bbbbbbb")
    point(to, "20261124-100000-aaaaaaa")
    return to, old


def installed(site: Path, *versions: str, record: bool = True) -> Path:
    package = site / "agentloop"
    package.mkdir(parents=True, exist_ok=True)
    for version in versions:
        info = site / f"agentloop-{version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: agentloop\nVersion: {version}\n\nAbout it.\n", encoding="utf-8")
        if record:
            (info / "RECORD").write_text("", encoding="utf-8")
    return package


# Telling a newer build

def test_a_build_sees_current_moved_on(built):
    to, old = built
    assert build.newer_build(old) is None
    point(to, "20261124-110000-bbbbbbb")
    assert build.newer_build(old) == "20261124-110000-bbbbbbb"
    # A name current gives that is not there to run is no build to move to.
    point(to, "20261124-120000-ccccccc")
    assert build.newer_build(old) is None


def test_the_source_tree_never_has_a_newer_build():
    assert build.newer_build(SRC / "agentloop") is None
    assert build.run_folder(SRC / "agentloop") is None


def test_an_installed_package_sees_the_version_installed_over_it(tmp_path):
    assert build.newer_build(installed(tmp_path / "same", __version__)) is None
    assert build.newer_build(installed(tmp_path / "new", "99.0.0")) == "99.0.0"
    # Still being installed, or two left side by side: nothing for sure.
    assert build.newer_build(installed(tmp_path / "half", "99.0.0", record=False)) is None
    assert build.newer_build(installed(tmp_path / "two", __version__, "99.0.0")) is None


# Starting on the build current names

def test_a_start_runs_the_version_current_names(built, monkeypatch):
    to, old = built
    monkeypatch.setattr(build, "package_dir", lambda: old)
    assert runner.command("l") == [sys.executable, str(to / "versions" / "20261124-100000-aaaaaaa"), "run", "l", "--managed"]
    point(to, "20261124-110000-bbbbbbb")
    assert runner.command("l", setup=True) == [sys.executable, str(to / "versions" / "20261124-110000-bbbbbbb"),
                                               "run", "l", "--managed", "--setup-only"]
    # No current to read: the version this app runs from.
    (to / "current").unlink()
    assert runner.app_command() == [sys.executable, str(old.parent)]


def test_an_installed_package_starts_as_installed(tmp_path, monkeypatch):
    package = installed(tmp_path / "site", "99.0.0")
    monkeypatch.setattr(build, "package_dir", lambda: package)
    assert runner.app_command() == [sys.executable, "-m", "agentloop"]


# The handover's start

def test_start_after_waits_for_the_old_run_to_end_and_let_go_of_its_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    found = SimpleNamespace(project="p", name="l")
    r = runner.Runner()
    monkeypatch.setattr(r, "_loop", lambda name: found)
    started = []
    monkeypatch.setattr(r, "start", lambda name, setup=False, why=None: started.append((name, why, time.monotonic())))
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.6)"])
    lock = loop.lock_path("p", "l")
    held = threading.Event()
    gone = threading.Event()

    def hold():
        with loop.file_lock(lock):
            held.set()
            time.sleep(1.2)
        gone.set()

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(5)
    try:
        r.start_after("l", old.pid, "moved to build b", wait=30)
    finally:
        holder.join()
        old.wait()
    assert started and started[0][:2] == ("l", "moved to build b")
    # Not before the lock was let go of, which was after the process had ended.
    assert gone.is_set()


def test_start_after_gives_up_on_a_run_that_does_not_let_go(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    r = runner.Runner()
    monkeypatch.setattr(r, "_loop", lambda name: SimpleNamespace(project="p", name="l"))
    monkeypatch.setattr(r, "start", lambda *a, **k: pytest.fail("started while the old run held its lock"))
    with loop.file_lock(loop.lock_path("p", "l")):
        with pytest.raises(runner.RunnerError, match="has not ended"):
            r.start_after("l", 999999, wait=0.3)


@pytest.mark.skipif(os.name != "nt", reason="only Windows waits for the process: its open output cannot be moved")
def test_on_windows_start_after_waits_for_the_process_too(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    r = runner.Runner()
    monkeypatch.setattr(r, "_loop", lambda name: SimpleNamespace(project="p", name="l"))
    monkeypatch.setattr(r, "start", lambda *a, **k: pytest.fail("started while the old run was alive"))
    with pytest.raises(runner.RunnerError, match="has not ended"):
        r.start_after("l", os.getpid(), wait=0.3)


def test_a_loop_whose_handover_failed_comes_back_when_serve_starts(tmp_path):
    files = SimpleNamespace(kill=tmp_path / "kill", stop=tmp_path / "stop")
    found = SimpleNamespace(settings={"autoStart": True})
    settings = SimpleNamespace(defaults={})
    said = {"state": "stopped", "endedBy": "update", "killed": False}
    assert "newer build" in runner.comes_back(settings, found, files, said, lock=False)
    found.settings["autoStart"] = False
    assert runner.comes_back(settings, found, files, said, lock=False) is None


# The loop

class Clock:
    def __init__(self):
        self.at = datetime(2026, 11, 24, 12, tzinfo=timezone.utc).timestamp()

    def now(self) -> float:
        return self.at

    def sleep(self, seconds: float) -> None:
        self.at += seconds


class Watcher:
    watching = False
    changes = 0

    def stop(self) -> None:
        pass


def bare_run(tmp_path: Path, managed: bool = True) -> loop.Run:
    """A Run with only what `_idle` and the handover read; its tracker is never asked."""
    run = object.__new__(loop.Run)
    folder = tmp_path / "loop"
    folder.mkdir()
    run.loop = SimpleNamespace(name="l")
    run.loop_dir = folder
    run.status_file = folder / "status.json"
    run.stop_file = folder / "stop"
    run.kill_file = folder / "kill"
    run.status = {}
    run.settings = {**config.DEFAULTS, "autoStart": True, "autoStartMinutes": 30}
    run.clock = Clock()
    run.lines = []
    run.out = run.lines.append
    run.paint = colours.plain
    run.ended_by = None
    run.killed = False
    run.limit_stopped = False
    run.managed = managed
    run.moving_to = None
    run.told_newer = None
    run.update_looked = None
    run.lock = None
    run.watcher = None
    run._auto_start = lambda: True
    run.bench = lambda action: True
    run._watcher = lambda say: Watcher()
    run.finish_tested = lambda now=None: None
    run.hear = lambda answer: answer
    run.read_next = lambda *args: {"signal": "Nothing to work."}
    return run


def test_an_idle_loop_ends_to_move_to_a_newer_build_and_hands_its_start_over(tmp_path, built, monkeypatch):
    to, old = built
    monkeypatch.setattr(build, "package_dir", lambda: old)
    run = bare_run(tmp_path)
    looks = []

    def read_next(*args):
        looks.append(run.clock.now())
        if len(looks) == 2:
            # A build is made while the loop waits.
            point(to, "20261124-110000-bbbbbbb")
        return {"signal": "Nothing to work."}

    run.read_next = read_next
    # The look interval is long; a newer build is still met within a minute of it.
    run.settings["autoStartMinutes"] = 0.5
    assert run._idle() is None
    assert run.ended_by == "update" and run.moving_to == "20261124-110000-bbbbbbb"
    assert run.clock.now() - looks[-1] <= loop.UPDATE_SECONDS + loop.usage.POLL_SECONDS
    assert run._ended_by(0, None) == "update"
    assert any("A newer build, 20261124-110000-bbbbbbb, is in place" in line for line in run.lines)

    spawned = []

    class Popen:
        def __init__(self, argv, **options):
            spawned.append((argv, options))

    monkeypatch.setattr(subprocess, "Popen", Popen)
    run._hand_over()
    argv, options = spawned[0]
    assert argv == [sys.executable, str(to / "versions" / "20261124-110000-bbbbbbb"), "start", "l",
                    "--after", str(os.getpid()), "--why", "moved to build 20261124-110000-bbbbbbb"]
    # Detached, as the runner starts a loop, and writing nowhere near the loop's own output.
    assert options["cwd"] == run.loop_dir and options["stdout"].name.endswith("handover.log")
    assert any(key in options for key in ("creationflags", "start_new_session"))
    assert any("Handed the start over" in line for line in run.lines)


def test_a_handover_that_cannot_start_says_so(tmp_path, built, monkeypatch):
    to, old = built
    monkeypatch.setattr(build, "package_dir", lambda: old)
    run = bare_run(tmp_path)
    run.moving_to = "b"

    def refused(argv, **options):
        raise OSError("no")

    monkeypatch.setattr(subprocess, "Popen", refused)
    run._hand_over()
    assert any("could not be handed over" in line for line in run.lines)


def test_a_loop_on_the_newest_build_waits_on(tmp_path, built, monkeypatch):
    to, old = built
    monkeypatch.setattr(build, "package_dir", lambda: old)
    run = bare_run(tmp_path)
    looks = []

    def read_next(*args):
        looks.append(1)
        if len(looks) == 3:
            run.stop_file.write_text("")
        return {"signal": "Nothing to work."}

    run.read_next = read_next
    run.settings["autoStartMinutes"] = 1
    assert run._idle() is None
    assert run.ended_by == "stop" and run.moving_to is None


def test_a_run_in_a_terminal_is_told_once_and_goes_on(tmp_path, built, monkeypatch):
    to, old = built
    monkeypatch.setattr(build, "package_dir", lambda: old)
    point(to, "20261124-110000-bbbbbbb")
    run = bare_run(tmp_path, managed=False)
    assert run._move_to_newer() is False and run._move_to_newer() is False
    assert run.ended_by is None and run.moving_to is None
    assert sum("A newer build" in line for line in run.lines) == 1
