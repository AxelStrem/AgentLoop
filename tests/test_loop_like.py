"""Several loops on one project (item loop-like): each loop's own lock, the script's mutex shared
between them, a copy of a loop running beside it in clones of its own, and the starts refused."""

import json
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from agentloop import config, loop, runner
from test_loop import FakeClock, item, make_repo, tracker_text


windows = pytest.mark.skipif(sys.platform != "win32", reason="the script's mutex is Windows' own")


def unique() -> str:
    # The script's mutex is named after the project and seen machine-wide: a name of the test's own.
    return f"p-{uuid.uuid4().hex[:8]}"


class Holder:
    """A run lock - or the script's mutex alone - held in a thread of its own until let go of: a
    mutex is owned by the thread that took it, as it is by the loop's process."""

    def __init__(self, take):
        self.took = threading.Event()
        self.done = threading.Event()
        self.error: BaseException | None = None
        self.held = None
        self.thread = threading.Thread(target=self._run, args=(take,), daemon=True)
        self.thread.start()
        assert self.took.wait(10)
        if self.error:
            raise self.error

    def _run(self, take) -> None:
        try:
            with take() as held:
                self.held = held
                self.took.set()
                self.done.wait(30)
        except BaseException as error:
            self.error = error
            self.took.set()

    def release(self) -> None:
        self.done.set()
        self.thread.join(10)


# The locks

def test_each_loop_holds_a_lock_of_its_own(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    p = unique()
    a, b = loop.lock_path(p, "a"), loop.lock_path(p, "b")
    assert a == config.paths().data / "loops" / p / "a.lock"
    with loop.run_lock(p, a) as held:
        assert held.others == []
        assert loop.lock_held(a) and not loop.lock_held(b)
        assert loop.alive_loops(p) == ["a"] and loop.alive_loops(p, but="a") == []
        with pytest.raises(loop.LoopError, match=f"The loop a is already working {p}."):
            with loop.run_lock(p, a):
                pass
        with loop.run_lock(p, b) as second:
            assert second.others == ["a"] and loop.alive_loops(p) == ["a", "b"]
    assert loop.alive_loops(p) == []


@windows
def test_a_second_loop_goes_on_without_the_mutex_and_takes_it_once_free(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    p = unique()
    first = Holder(lambda: loop.run_lock(p, loop.lock_path(p, "a")))
    with loop.run_lock(p, loop.lock_path(p, "b")) as second:
        # The first keeps the mutex; the second starts all the same, since a loop of the app has it.
        assert first.held.mutex.held and not second.mutex.held
        assert loop.script_holds(p)
        assert not second.retake()
        first.release()
        # Between its items, once the first has ended.
        assert second.retake() and second.mutex.held
    assert not loop.script_holds(p)


@windows
def test_a_start_is_refused_while_the_script_has_the_mutex(tmp_path, monkeypatch, fake_claude):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    p = unique()

    class Script:
        def __enter__(self):
            self.mutex = loop.ScriptMutex(p)
            assert self.mutex.take()

        def __exit__(self, *raised):
            self.mutex.close()

    script = Holder(Script)
    try:
        with pytest.raises(loop.LoopError, match=f"A loop is already working {p}: the script"):
            with loop.run_lock(p, loop.lock_path(p, "a")):
                pass
        assert not loop.lock_held(loop.lock_path(p, "a"))
        found = config.Loop("a", p, "w", {}, {})
        assert runner.busy(found) == f"{p} is being worked by the script"
    finally:
        script.release()
    assert runner.busy(found) is None


def test_a_file_lock_is_had_by_one_holder_at_a_time(tmp_path):
    path = tmp_path / "attempts.lock"
    with loop.file_lock(path):
        assert loop.lock_held(path)
        with pytest.raises(loop.Held):
            with loop.file_lock(path, wait=0.2):
                pass
    assert not loop.lock_held(path)


# Two loops of one project, on a tracker served on loopback

class Served:
    """A project whose tracker is on a copy of overseer-server: a loop `one`, its copy `two` in a
    clone of its own, and `three`, a copy placed where `one` is."""

    def __init__(self, tmp_path: Path, monkeypatch, overseer):
        monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
        self.tmp = tmp_path
        self.project = unique()
        self.tracker = overseer.document(tracker_text(*(item(h, "ready", complexity="low") for h in ("alpha", "beta"))))
        self.code = {"one": make_repo(tmp_path / "code", {"README.md": "code\n"}),
                     "two": make_repo(tmp_path / "code-2", {"README.md": "code\n"})}
        bundle = config.paths().bundles / self.project
        bundle.mkdir(parents=True)
        (bundle / "brief.md").write_text("# The brief\n", encoding="utf-8")
        config.save_json(bundle / "project.json", {"format": 1, "name": self.project, "tracker": {"server": self.tracker},
                                                   "repos": ["code"], "settings": {}})
        account = tmp_path / "account"
        account.mkdir()
        config.save_json(config.paths().settings, {
            "format": 1, "bundles": [str(bundle)],
            "workers": [{"name": "w", "claude": str(account), "overseer": overseer.worker}],
            "loops": [{"name": "one", "project": self.project, "worker": "w", "repos": {"code": str(self.code["one"])},
                       "settings": {"trial": None}},
                      {"name": "two", "like": "one", "repos": {"code": str(self.code["two"])}},
                      {"name": "three", "like": "one"}],
            "defaults": {},
        })
        self.lines: dict[str, list[str]] = {}

    def run(self, name: str) -> loop.Run:
        machine = config.load_machine()
        self.lines[name] = []
        return loop.Run(machine, machine.settings.loop(name), out=self.lines[name].append, clock=FakeClock())

    def scene(self, *steps: dict, result: str = "COMMIT: @item: built") -> Path:
        events = [{"type": "system", "subtype": "init", "session_id": "x", "model": "claude-sonnet-5-5"}, {"fake": "transcript"}, *steps,
                  {"type": "result", "subtype": "success", "is_error": False, "result": result, "num_turns": 3,
                   "duration_ms": 60000, "total_cost_usd": 0.25, "session_id": "x"}]
        path = self.tmp / f"scene-{uuid.uuid4().hex[:6]}.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        return path


@pytest.fixture
def served(tmp_path, monkeypatch, fake_claude, overseer):
    return Served(tmp_path, monkeypatch, overseer)


def test_two_loops_of_one_project_run_at_once_and_take_different_items(served, fake_claude):
    s = served
    fake_claude.scenario(str(s.scene({"fake": "sleep", "seconds": 3},
                                     {"fake": "tracker", "args": ["move", "@item", "testing"]})))
    one, two = s.run("one"), s.run("two")
    codes: dict[str, int] = {}
    threads = {r.loop.name: threading.Thread(target=lambda r=r: codes.__setitem__(r.loop.name, r.run()), daemon=True) for r in (one, two)}
    threads["one"].start()
    deadline = time.monotonic() + 60
    while one.status.get("item") != "alpha":
        assert time.monotonic() < deadline, s.lines["one"]
        time.sleep(0.05)
    threads["two"].start()
    while two.status.get("item") != "beta":
        assert time.monotonic() < deadline, s.lines["two"]
        time.sleep(0.05)
    # Both at work at once, the first holding the script's mutex and the second without it.
    assert one.status.get("item") == "alpha"
    if sys.platform == "win32":
        assert one.lock.mutex.held and not two.lock.mutex.held
    # Each loop's status reads its own lock; neither is busy with the other.
    machine = config.load_machine()
    assert runner.held(machine.settings.loop("one")) and runner.held(machine.settings.loop("two"))
    assert not runner.held(machine.settings.loop("three"))
    for t in threads.values():
        t.join(90)
    assert codes == {"one": 0, "two": 0}, "\n".join(s.lines["one"] + ["====="] + s.lines["two"])

    calls = fake_claude.calls()
    worked = sorted((c["env"]["OVERSEER_ITEM"].rpartition(":")[2], Path(c["cwd"]).resolve()) for c in calls)
    assert worked == [("alpha", s.code["one"]), ("beta", s.code["two"])]
    attempts = config.paths().data / "logs" / s.project / "attempts.jsonl"
    lines = [json.loads(line) for line in attempts.read_text(encoding="utf-8").splitlines()]
    assert sorted((a["handle"], a["to"]) for a in lines) == [("alpha", "testing"), ("beta", "testing")]


def test_a_loop_sharing_a_repository_with_a_running_one_is_refused(served, fake_claude):
    s = served
    machine = config.load_machine()
    three = s.run("three")
    with loop.run_lock(s.project, loop.lock_path(s.project, "one")):
        assert runner.busy(machine.settings.loop("three")) == "one is running in a repository it shares with this one"
        assert runner.busy(machine.settings.loop("two")) is None
        with pytest.raises(runner.RunnerError, match="three cannot start: one is running in a repository it shares with this one."):
            runner.Runner().start("three")
        with pytest.raises(loop.LoopError, match=f"The loop one is working {s.project} in .*code, as this one would"):
            three.run()
    assert fake_claude.calls() == []
    assert runner.busy(machine.settings.loop("three")) is None
