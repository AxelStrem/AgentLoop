"""Files left uncommitted because they were changed before a session (item leftfiles): the commit
step names them, a record counts them from session to session, and the loop says them on its
status and its item, and takes no item once one has been left too long. Server-free: the loop is
a bare `Run` given only what these parts read, its tracker calls caught."""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentloop import colours, commits, config, loop

from test_commits import git, make_repo


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    return tmp_path / "state" / "commit.lock"


# The commit step

def test_a_file_changed_before_and_again_is_named_as_left(tmp_path, lock):
    repo = make_repo(tmp_path / "repo", {"mixed.txt": "a\n", "owner.txt": "b\n"})
    (repo / "mixed.txt").write_text("owner's\n")
    (repo / "owner.txt").write_text("owner's\n")
    before = commits.snapshot([repo], lock=lock)
    (repo / "mixed.txt").write_text("owner's and the agent's\n")
    (repo / "new.txt").write_text("the agent's\n")
    result = commits.commit_session(before, "m", [repo], lock=lock)
    assert result.ok
    assert result.left == {"repo": ["mixed.txt"]}
    assert result.untouched == {"repo": ["owner.txt"]}
    assert " M mixed.txt" in git(repo, "status", "--porcelain")


def test_a_file_changed_before_and_not_touched_is_not_left(tmp_path, lock):
    repo = make_repo(tmp_path / "repo")
    (repo / "README.md").write_text("owner's\n")
    before = commits.snapshot([repo], lock=lock)
    result = commits.commit_session(before, "m", [repo], lock=lock)
    assert result.left == {} and result.untouched == {"repo": ["README.md"]} and result.said == []


# The record

def test_the_record_counts_sessions_in_a_row_and_drops_what_is_committed():
    record = commits.track_left({}, {"repo": ["a.py"]}, {"repo": {"a.py"}}, "t1")
    assert record == {"repo": {"a.py": {"since": "t1", "sessions": 1}}}
    # Left again, and then not touched but still changed: counted each time, since kept.
    record = commits.track_left(record, {"repo": ["a.py"]}, {"repo": {"a.py"}}, "t2")
    record = commits.track_left(record, {}, {"repo": {"a.py", "other.py"}}, "t3")
    assert record == {"repo": {"a.py": {"since": "t1", "sessions": 3}}}
    # Committed or stashed: gone from what is changed, gone from the record.
    assert commits.track_left(record, {}, {"repo": set()}, "t4") == {}
    assert commits.still_left(record, {"repo": set()}) == {}
    assert commits.still_left(record, {"repo": {"a.py"}}) == record


def test_overdue_by_sessions_or_by_hours():
    now = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
    fresh = (now - timedelta(hours=1)).isoformat()
    old = (now - timedelta(hours=25)).isoformat()
    record = {"repo": {"a.py": {"since": fresh, "sessions": 2}, "b.py": {"since": fresh, "sessions": 3},
                       "c.py": {"since": old, "sessions": 1}, "d.py": {"since": "not a time", "sessions": 1}}}
    assert commits.overdue(record, 3, 24, now) == [("repo", "b.py"), ("repo", "c.py")]
    assert commits.overdue(record, 2, 24, now) == [("repo", "a.py"), ("repo", "b.py"), ("repo", "c.py")]


# The loop

class Clock:
    def __init__(self, on_sleep=None):
        self.slept = 0
        self.on_sleep = on_sleep

    def now(self) -> float:
        return datetime(2026, 11, 24, 12, tzinfo=timezone.utc).timestamp()

    def sleep(self, seconds: float) -> None:
        self.slept += 1
        if self.on_sleep:
            self.on_sleep()


def bare_run(tmp_path: Path, repo: Path, clock=None, **settings) -> loop.Run:
    """A Run with only what keep_left and _left_hold read; its tracker calls are kept, not made."""
    run = object.__new__(loop.Run)
    folder = tmp_path / "loop"
    folder.mkdir()
    run.repos = [repo]
    run.loop_dir = folder
    run.left_file = folder / "left.json"
    run.status_file = folder / "status.json"
    run.stop_file = folder / "stop"
    run.kill_file = folder / "kill"
    run.status = {}
    run.settings = {**config.DEFAULTS, **settings}
    run.clock = clock or Clock()
    run.lines = []
    run.out = run.lines.append
    run.paint = colours.plain
    run.told_untouched = set()
    run.scratch = tmp_path / "scratch"
    run.ended_by = None
    run.notes = []

    def tracker(*args):
        note = Path(args[2][1:]).read_text(encoding="utf-8") if args[0] == "note" else None
        run.notes.append((args[:2], note))
        return subprocess.CompletedProcess(args, 0, b"", b"")

    run._tracker = tracker
    return run


def session(run: loop.Run, repo: Path, change: dict[str, str], handle: str = "item") -> commits.Committed:
    """One session's commit: the snapshot, the session's changes, the commit and keep_left."""
    before = commits.snapshot([repo])
    for name, text in change.items():
        (repo / name).write_text(text)
    done = commits.commit_session(before, "m", [repo])
    run.keep_left({"handle": handle}, done)
    return done


def status(run: loop.Run) -> dict:
    return json.loads(run.status_file.read_text(encoding="utf-8"))


def test_a_file_left_is_said_on_the_page_and_the_item_and_the_loop_holds_after_n_sessions(tmp_path):
    repo = make_repo(tmp_path / "repo", {"code.py": "x = 1\n"})
    (repo / "code.py").write_text("x = 2\n")   # the owner's, before any session
    run = bare_run(tmp_path, repo, leftSessions=2)

    session(run, repo, {"code.py": "x = 3\n"}, "first")
    assert status(run)["leftFiles"] == [{"repo": "repo", "path": "code.py", "sessions": 1, "since": run._now()}]
    assert run.notes[0][0] == ("note", "first")
    assert "`repo/code.py` - left 1 session in a row" in run.notes[0][1] and "Commit them, or stash them" in run.notes[0][1]
    assert run._left_hold() is None   # once: not yet

    session(run, repo, {"code.py": "x = 4\n"}, "second")
    assert status(run)["leftFiles"][0]["sessions"] == 2

    # The owner commits it while the loop waits: it goes on by itself.
    def owner_commits():
        assert status(run)["state"] == "held" and status(run)["heldFor"] == "left"
        git(repo, "commit", "-q", "-am", "the owner's")

    run.clock = Clock(owner_commits)
    assert run._left_hold() is None
    assert run.clock.slept == 1
    assert status(run)["state"] == "running" and status(run)["leftFiles"] is None
    assert not run.left_file.exists()
    assert any("committed or stashed" in line for line in run.lines)


def test_a_stop_ends_the_hold(tmp_path):
    repo = make_repo(tmp_path / "repo", {"code.py": "x = 1\n"})
    (repo / "code.py").write_text("x = 2\n")
    run = bare_run(tmp_path, repo, leftSessions=1)
    session(run, repo, {"code.py": "x = 3\n"})
    run.clock = Clock(lambda: run.stop_file.write_text(""))
    assert run._left_hold() == 0 and run.ended_by == "stop"


def test_a_file_changed_before_and_not_touched_is_said_once_and_holds_nothing(tmp_path):
    repo = make_repo(tmp_path / "repo", {"code.py": "x = 1\n"})
    (repo / "code.py").write_text("x = 2\n")
    run = bare_run(tmp_path, repo, leftSessions=1)
    for _ in range(3):
        session(run, repo, {"new.py": f"y = {_}\n"})
    said = [line for line in run.lines if "not by it" in line]
    assert len(said) == 1 and "code.py" in said[0]
    assert run.notes == [] and not run.left_file.exists()
    assert status(run)["leftFiles"] is None
    assert run._left_hold() is None
