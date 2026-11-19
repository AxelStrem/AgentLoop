"""Several loops at once, each a process of its own: started, stopped and killed with the fake claude,
on two projects of the test's own."""

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from agentloop import cli, config, runner

from test_loop import git, item, make_repo, tracker_text



class Machine:
    """A PC with two loops, a and b, each on a project of its own with a code repository, its tracker
    on the test's server, and a bundle. The projects' names are the test's own: on Windows
    a loop also takes the script's mutex, which is named after the project and seen machine-wide, so
    two test runs at once - or a script's loop on a project named a - would meet there."""

    def __init__(self, tmp_path: Path, monkeypatch, fake_claude, overseer):
        self.tmp = tmp_path
        self.fake = fake_claude
        home = tmp_path / "home"
        monkeypatch.setenv("AGENTLOOP_HOME", str(home))
        account = tmp_path / "account"
        account.mkdir()
        suffix = uuid.uuid4().hex[:8]
        self.project = {"a": f"a-{suffix}", "b": f"b-{suffix}"}
        self.code: dict[str, Path] = {}
        self.tracker: dict[str, str] = {}
        folder = overseer.folder()
        bundles, loops = [], []
        for name, handles in (("a", ["alpha", "beta"]), ("b", ["gamma"])):
            project = self.project[name]
            self.code[name] = make_repo(tmp_path / name / "code", {"README.md": "code\n"})
            self.tracker[name] = overseer.document(tracker_text(*(item(h, "ready", complexity="low") for h in handles)),
                                                   project=name, folder=folder)
            bundle = home / "config" / "bundles" / project
            bundle.mkdir(parents=True)
            (bundle / "brief.md").write_text("# The brief\n", encoding="utf-8")
            config.save_json(bundle / "project.json", {"format": 1, "name": project, "tracker": {"server": self.tracker[name]},
                                                       "repos": ["code"], "settings": {}})
            bundles.append(str(bundle))
            loops.append({"name": name, "project": project, "worker": "w",
                          "repos": {"code": str(self.code[name])}, "settings": {"trial": None}})
        config.save_json(home / "config" / "settings.json", {
            "format": 1, "bundles": bundles, "workers": [{"name": "w", "claude": str(account), "overseer": overseer.worker}],
            "loops": loops, "defaults": {},
        })
        self.runner = runner.Runner()

    def scene(self, name: str, *steps: dict, result: str = "") -> Path:
        """A build session: a transcript, its steps, a usage report and its final answer."""
        events = [{"type": "system", "subtype": "init", "session_id": "x", "model": "claude-sonnet-5-5"}, {"fake": "transcript"}, *steps]
        events.append({"type": "rate_limit_event", "session_id": "x", "rate_limit_info": {
            "status": "allowed", "resetsAt": "@now+7200", "rateLimitType": "five_hour",
            "unifiedWindows": {"five_hour": {"utilization": 0.35, "resetsAt": "@now+7200"},
                               "seven_day": {"utilization": 0.29, "resetsAt": "@now+172800"}}}})
        events.append({"type": "result", "subtype": "success", "is_error": False, "result": result, "num_turns": 3,
                       "duration_ms": 60000, "total_cost_usd": 0.25, "session_id": "x"})
        path = self.tmp / f"scene-{name}.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        return path

    def start(self, name: str, scene: Path) -> runner.LoopStatus:
        # The loop's claude plays what the environment says as the loop is started.
        self.fake.scenario(str(scene))
        return self.runner.start(name)

    def until(self, name: str, test, timeout: float = 60.0) -> runner.LoopStatus:
        deadline = time.monotonic() + timeout
        while True:
            state = self.runner.status(name)
            if test(state):
                return state
            if time.monotonic() > deadline:
                raise AssertionError(f"{name} never got there: {state}\n{self.runner.output(name).text}")
            time.sleep(0.2)

    def folder(self, name: str) -> Path:
        return config.paths().data / "logs" / self.project[name] / "loops" / name

    def running_json(self, name: str) -> dict:
        try:
            return json.loads((self.folder(name) / "running.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


@pytest.fixture
def machine(tmp_path, monkeypatch, fake_claude, overseer):
    m = Machine(tmp_path, monkeypatch, fake_claude, overseer)
    yield m
    # Nothing left running whatever the test did.
    for name in ("a", "b"):
        state = m.runner.status(name)
        if state.alive and state.pid:
            runner.end_tree(state.pid)


def move(handle: str) -> dict:
    return {"fake": "tracker", "args": ["move", handle, "testing", "--note", "built"]}


def write(path: str) -> dict:
    return {"fake": "write", "path": path, "text": "made by the fake claude\n"}


def test_two_loops_one_stopped_one_killed(machine):
    m = machine
    a = m.start("a", m.scene("a", write("a.txt"), {"fake": "sleep", "seconds": 3}, move("alpha"), result="COMMIT: alpha: built"))
    assert a.alive and a.state in ("starting", "running")
    b = m.start("b", m.scene("b", write("b.txt"), {"fake": "sleep", "seconds": 120}, move("gamma"), result="COMMIT: gamma: built"))
    assert b.alive

    # A second loop on a project already worked is refused.
    with pytest.raises(runner.RunnerError, match=f"already working {m.project['a']}"):
        m.runner.start("a")

    # Both are in their sessions at once.
    a = m.until("a", lambda s: s.item == "alpha")
    assert a.state == "running" and a.kind == "build" and a.session
    m.until("b", lambda s: s.item == "gamma" and m.running_json("b").get("child"))

    # An app started again finds them both, from their files alone.
    fresh = runner.Runner()
    assert [(s.name, s.alive) for s in fresh.statuses()] == [("a", True), ("b", True)]

    # Stop: the item at work is finished and committed, and then the loop ends.
    assert m.runner.stop("a").state == "stopping"
    a = m.until("a", lambda s: not s.alive, timeout=90)
    assert (a.state, a.exit, a.killed, a.item) == ("stopped", 0, False, None)
    assert a.cost == pytest.approx(0.25) and a.usage and "35%" in a.usage
    assert git(m.code["a"], "log", "--format=%s").splitlines()[0] == "alpha: built [sonnet high]"
    assert git(m.code["a"], "status", "--porcelain") == ""
    out = m.runner.output("a")
    assert f"Starting the agent loop on {m.project['a']}." in out.text and "Stopping as asked." in out.text
    assert out.text.endswith("\n") and "\r" not in out.text and m.runner.output("a", out.offset).text == ""

    # Kill: claude is ended mid-session, with what it started, and nothing is committed.
    child = m.running_json("b")["child"]
    killed = m.runner.kill("b")
    assert killed.ended_session and not killed.ended_loop
    assert killed.item == "gamma" and killed.session and f"claude --resume {killed.session}" in killed.resume
    assert not runner.pid_alive(child)
    b = m.until("b", lambda s: not s.alive)
    assert (b.state, b.exit, b.killed, b.error) == ("stopped", 0, True, None)
    assert git(m.code["b"], "log", "--format=%s").splitlines() == ["start"]
    assert git(m.code["b"], "status", "--porcelain").strip() == "?? b.txt"
    assert "Stopped at once, as asked: gamma is left as the session had it" in m.runner.output("b").text

    # Neither can be stopped or killed again.
    with pytest.raises(runner.RunnerError, match="No loop is working"):
        m.runner.stop("a")
    with pytest.raises(runner.RunnerError, match="No loop is working"):
        m.runner.kill("b")


def test_a_loop_that_dies_reads_failed_and_a_new_start_keeps_the_last_output(machine):
    m = machine
    m.start("b", m.scene("b1", {"fake": "sleep", "seconds": 120}))
    first = m.until("b", lambda s: s.item == "gamma" and m.running_json("b").get("child"))
    # Ended from outside, without the kill file. Its claude may be ended a moment before it is, and
    # the loop record that failed session itself; or it never says how it ended.
    runner.end_tree(first.pid)
    b = m.until("b", lambda s: not s.alive)
    assert b.state == "failed" and not b.killed and b.exit in (None, 1)
    said = m.runner.output("b").text

    m.start("b", m.scene("b2", {"fake": "sleep", "seconds": 120}))
    m.until("b", lambda s: s.item == "gamma")
    # As written: a claude ended mid-write may leave a traceback with the OS's line ends, which
    # the runner's output keeps and read_text would not.
    assert (m.folder("b") / "output.prev.log").read_bytes().decode("utf-8") == said
    m.runner.kill("b")
    assert m.until("b", lambda s: not s.alive).killed


def test_a_loop_that_cannot_begin_reads_failed(machine):
    m = machine
    # Its worker's key is not where the settings say: the loop refuses to start.
    path = config.paths().settings
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["workers"][0]["overseer"]["keyFile"] = str(m.tmp / "no-key")
    config.save_json(path, raw)
    m.start("a", m.scene("a"))
    a = m.until("a", lambda s: not s.alive)
    # Refused before it says it is running: the runner tells why from its output.
    assert (a.state, a.error) == ("failed", "ended before it began; see its output")
    assert "names no file holding the agents' key" in m.runner.output("a").text


def test_the_commands(machine, capsys):
    m = machine
    m.fake.scenario(str(m.scene("b", {"fake": "sleep", "seconds": 120})))
    assert cli.main(["start", "b"]) == 0
    assert f"Started b on {m.project['b']}; its output goes to " in capsys.readouterr().out
    m.until("b", lambda s: s.item == "gamma" and m.running_json("b").get("child"))
    assert cli.main(["status"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("a ") and "stopped" in lines[0]
    assert lines[1].startswith("b ") and "running" in lines[1] and "build gamma" in lines[1]
    assert cli.main(["status", "b", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["item"] == "gamma"
    assert cli.main(["kill", "b"]) == 0
    out = capsys.readouterr().out
    assert "The loop b is stopped, its session ended." in out and "claude --resume " in out
    assert cli.main(["stop", "b"]) == 1
    assert f"No loop is working {m.project['b']} as b." in capsys.readouterr().err


def test_output_is_read_to_its_last_whole_line(machine):
    folder = machine.folder("a")
    folder.mkdir(parents=True)
    (folder / "output.log").write_bytes("one\ntwo → three\npart".encode("utf-8"))
    out = machine.runner.output("a")
    assert out.text == "one\ntwo → three\n"
    assert machine.runner.output("a", out.offset).text == ""
    # An offset past the end is a new run's output, read from its top.
    assert machine.runner.output("a", 1000).text == out.text


def holding(output: Path, seconds: float) -> subprocess.Popen:
    """A stand-in for a loop's process a moment from exiting: it has the output open, as the
    runner gives it, for a while."""
    with open(output, "ab") as out:
        return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"], stdout=out,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)


def test_a_loop_reads_stopped_only_once_its_process_has_exited(machine):
    m = machine
    folder = m.folder("a")
    folder.mkdir(parents=True)
    (folder / "output.log").write_bytes(b"Stopping as asked.\n")
    # It has said how it ended, and its process has not exited yet.
    (folder / "status.json").write_text(json.dumps({"state": "stopped", "exit": 0}), encoding="utf-8")
    process = holding(folder / "output.log", 1.0)
    m.runner._started["a"] = process
    try:
        state = m.runner.status("a")
        assert (state.state, state.alive) == ("stopping", True)
        # Started again straight away: the last run is waited for, not refused.
        started = m.start("a", m.scene("a", {"fake": "sleep", "seconds": 120}))
        assert started.alive and process.poll() is not None
        assert (folder / "output.prev.log").read_bytes() == b"Stopping as asked.\n"
        m.until("a", lambda s: s.item == "alpha")
        m.runner.kill("a")
        assert m.until("a", lambda s: not s.alive).state == "stopped"
    finally:
        process.kill()
        process.wait()


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to rename a file another process has open")
def test_a_start_waits_a_moment_for_the_last_output_and_then_refuses(machine):
    m = machine
    folder = m.folder("b")
    folder.mkdir(parents=True)
    output = folder / "output.log"
    output.write_bytes(b"the last run\n")
    # Held by a process this runner did not start - an earlier app's loop - that lets go at once.
    brief = holding(output, 0.8)
    try:
        assert m.start("b", m.scene("b", {"fake": "sleep", "seconds": 120})).alive
        m.until("b", lambda s: s.item == "gamma")
        m.runner.kill("b")
        m.until("b", lambda s: not s.alive)
    finally:
        brief.kill()
        brief.wait()
    # Held for longer than a start waits: refused, the output left as it was.
    long = holding(output, 60)
    try:
        before = output.read_bytes()
        with pytest.raises(runner.RunnerError, match="left a process writing to its output"):
            m.runner.start("b")
        assert output.read_bytes() == before
    finally:
        long.kill()
        long.wait()


def test_a_tree_is_ended_whole(tmp_path):
    # A loop-like process starting a claude-like one in a group of its own, which starts another.
    script = tmp_path / "tree.py"
    script.write_text("\n".join([
        "import subprocess, sys, time",
        "if sys.argv[1] == 'top':",
        "    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == 'win32' else {}",
        "    middle = subprocess.Popen([sys.executable, __file__, 'middle'], stdout=subprocess.PIPE, **options)",
        "    print(middle.pid, middle.stdout.readline().decode().strip(), flush=True)",
        "elif sys.argv[1] == 'middle':",
        "    bottom = subprocess.Popen([sys.executable, __file__, 'bottom'])",
        "    print(bottom.pid, flush=True)",
        "time.sleep(60)",
    ]), encoding="utf-8")
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
    top = subprocess.Popen([sys.executable, str(script), "top"], stdout=subprocess.PIPE, **options)
    pids = [top.pid, *(int(p) for p in top.stdout.readline().split())]
    assert len(pids) == 3 and all(runner.pid_alive(p) for p in pids)
    runner.end_tree(top.pid)
    top.wait(10)
    top.stdout.close()
    deadline = time.monotonic() + 10
    while any(runner.pid_alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not any(runner.pid_alive(p) for p in pids)


def test_the_command_runs_this_same_app():
    argv = runner.command("x")
    assert argv[0] == sys.executable and argv[-3:] == ["run", "x", "--managed"]


def test_a_repository_top_is_remembered_for_a_while(tmp_path, monkeypatch):
    asked: list[Path] = []
    tops = {tmp_path / "repo": tmp_path / "repo", tmp_path / "plain": None}

    def repo_top(folder: Path):
        asked.append(folder)
        return tops[folder]

    monkeypatch.setattr(config, "repo_top", repo_top)
    clock = [1000.0]
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    repo = tmp_path / "repo"
    assert runner.remembered_top(repo) == repo
    assert runner.remembered_top(repo) == repo
    assert asked == [repo]
    clock[0] += runner.TOP_KEPT + 1
    assert runner.remembered_top(repo) == repo
    assert asked == [repo, repo]

    # A folder made a repository is seen at the next look.
    plain = tmp_path / "plain"
    assert runner.remembered_top(plain) is None
    tops[plain] = plain
    assert runner.remembered_top(plain) == plain
