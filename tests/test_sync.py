"""Pulling before a session and pushing after it (item git-sync): sync.py on clones of a bare
repository in the test's own folder - two clones standing for two machines - and the loop around
it, with the fake claude resolving a conflict, or not."""

import subprocess
from pathlib import Path

import pytest

from agentloop import config, loop, sync


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def configure(repo: Path) -> None:
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "core.autocrlf", "false")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def commit(repo: Path, name: str, text: str, message: str) -> None:
    write(repo / name, text)
    git(repo, "add", "--", name)
    git(repo, "commit", "-q", "-m", message, "--", name)


def publish(repo: Path) -> None:
    """A clone's commits sent to its remote, as another machine's loop would."""
    git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")


class Remote:
    """A bare repository and clones of it."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.bare = tmp / "remote.git"
        seed = tmp / "seed"
        seed.mkdir()
        git(seed, "init", "-q", "-b", "main")
        configure(seed)
        for name in ("README.md", "notes.md", "other.md"):
            write(seed / name, f"{name}\n")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "start")
        git(tmp, "clone", "-q", "--bare", str(seed), str(self.bare))

    def clone(self, name: str) -> Path:
        path = self.tmp / name
        # Checked out without line endings changed: the files are compared as written.
        git(self.tmp, "clone", "-q", "-c", "core.autocrlf=false", str(self.bare), str(path))
        configure(path)
        return path.resolve()

    def log(self) -> list[str]:
        return git(self.bare, "log", "--format=%s", "main").splitlines()

    def show(self, name: str) -> str:
        return git(self.bare, "show", f"main:{name}")


class Said:
    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, line: str, colour: str | None = None) -> None:
        self.lines.append(line)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def remote(tmp_path: Path) -> Remote:
    return Remote(tmp_path)


def never(conflict: sync.Conflict) -> str | None:
    raise AssertionError("no conflict was expected")


# Pulling

def test_a_clone_only_behind_is_fast_forwarded(remote):
    a, b = remote.clone("a"), remote.clone("b")
    commit(a, "notes.md", "from a\n", "a's work")
    publish(a)
    write(b / "other.md", "b's own, uncommitted\n")
    said = Said()
    sync.pull([b], said)
    assert git(b, "log", "-1", "--format=%s").strip() == "a's work"
    assert (b / "notes.md").read_text() == "from a\n"
    # The owner's work in progress is carried along.
    assert (b / "other.md").read_text() == "b's own, uncommitted\n"
    assert said.lines == ["  pulled in b: 1 commit from origin/main"]


def test_a_clone_with_changes_where_the_pull_would_change_is_left(remote):
    a, b = remote.clone("a"), remote.clone("b")
    commit(a, "notes.md", "from a\n", "a's work")
    publish(a)
    write(b / "notes.md", "b's own, uncommitted\n")
    said = Said()
    sync.pull([b], said)
    assert git(b, "log", "-1", "--format=%s").strip() == "start"
    assert (b / "notes.md").read_text() == "b's own, uncommitted\n"
    assert "b: not pulled - changes here are in files origin/main has changed since." in said.text


def test_a_clone_ahead_and_behind_is_left_for_the_push(remote):
    a, b = remote.clone("a"), remote.clone("b")
    commit(a, "notes.md", "from a\n", "a's work")
    publish(a)
    commit(b, "other.md", "from b\n", "b's work")
    said = Said()
    sync.pull([b], said)
    assert git(b, "log", "-1", "--format=%s").strip() == "b's work"
    assert "they are replayed onto it after the session" in said.text


def test_a_clone_without_an_upstream_is_neither_pulled_nor_pushed(remote):
    b = remote.clone("b")
    git(b, "branch", "-q", "--unset-upstream")
    commit(b, "notes.md", "from b\n", "b's work")
    said = Said()
    sync.pull([b], said)
    assert sync.push([b], remote.tmp / "wt", never, said) is None
    assert said.lines == [] and remote.log() == ["start"]


# Pushing

def test_a_commit_is_pushed(remote):
    b = remote.clone("b")
    commit(b, "notes.md", "from b\n", "b's work")
    said = Said()
    assert sync.push([b], remote.tmp / "wt", never, said) is None
    assert remote.log() == ["b's work", "start"]
    assert said.lines == ["  pushed in b: 1 commit to origin/main"]


def test_nothing_to_push_says_nothing(remote):
    b = remote.clone("b")
    said = Said()
    assert sync.push([b], remote.tmp / "wt", never, said) is None
    assert said.lines == []


def test_a_commit_is_replayed_onto_what_was_pushed_since_and_the_owners_work_is_kept(remote):
    a, b = remote.clone("a"), remote.clone("b")
    commit(a, "notes.md", "from a\n", "a's work")
    publish(a)
    write(b / "untracked.txt", "b's new file\n")
    write(b / "README.md", "b's change, uncommitted\n")
    commit(b, "other.md", "from b\n", "b's work")
    said = Said()
    assert sync.push([b], remote.tmp / "wt", never, said) is None
    # Linear: b's commit on top of a's.
    assert remote.log() == ["b's work", "a's work", "start"]
    assert git(b, "rev-parse", "HEAD") == git(remote.bare, "rev-parse", "main")
    assert (b / "notes.md").read_text() == "from a\n"
    assert (b / "untracked.txt").read_text() == "b's new file\n"
    assert (b / "README.md").read_text() == "b's change, uncommitted\n"
    assert git(b, "status", "--short").splitlines() == [" M README.md", "?? untracked.txt"]
    assert "  replayed onto origin/main in b: " in said.text
    assert not (remote.tmp / "wt" / "b").exists()
    assert len(git(b, "worktree", "list").splitlines()) == 1


def test_changes_where_the_replay_would_change_leave_the_commit_local(remote):
    a, b = remote.clone("a"), remote.clone("b")
    commit(a, "notes.md", "from a\n", "a's work")
    publish(a)
    write(b / "notes.md", "b's own, uncommitted\n")
    commit(b, "other.md", "from b\n", "b's work")
    head = git(b, "rev-parse", "HEAD")
    said = Said()
    assert sync.push([b], remote.tmp / "wt", never, said) is None
    assert remote.log() == ["a's work", "start"]
    assert git(b, "rev-parse", "HEAD") == head
    assert (b / "notes.md").read_text() == "b's own, uncommitted\n"
    assert "b: not pushed - changes here are in files origin/main has changed since. The commits are left local." in said.lines[-1]


def test_a_conflict_resolved_is_pushed(remote):
    a, b = remote.clone("a"), remote.clone("b")
    commit(a, "notes.md", "from a\n", "a's work")
    publish(a)
    commit(b, "notes.md", "from b\n", "b's work")
    seen = []

    def resolve(conflict: sync.Conflict) -> str | None:
        seen.append(conflict)
        text = (conflict.worktree / "notes.md").read_text()
        assert "<<<<<<<" in text and conflict.worktree != b
        write(conflict.worktree / "notes.md", "from a\nfrom b\n")
        return None

    said = Said()
    assert sync.push([b], remote.tmp / "wt", resolve, said) is None
    assert [c.files for c in seen] == [["notes.md"]]
    assert seen[0].commit.endswith(" b's work") and str(seen[0].upstream) == "origin/main"
    assert remote.log() == ["b's work", "a's work", "start"]
    assert remote.show("notes.md") == "from a\nfrom b\n"
    assert (b / "notes.md").read_text() == "from a\nfrom b\n"
    assert git(b, "status", "--short") == ""
    assert not (remote.tmp / "wt" / "b").exists()


@pytest.mark.parametrize("resolution, why", [
    (None, "conflict markers are left in notes.md"),
    ("not plain from the two sides", "was not resolved: not plain from the two sides"),
])
def test_a_conflict_not_resolved_stops_with_the_commit_local(remote, resolution, why):
    a, b = remote.clone("a"), remote.clone("b")
    commit(a, "notes.md", "from a\n", "a's work")
    publish(a)
    commit(b, "notes.md", "from b\n", "b's work")
    head = git(b, "rev-parse", "HEAD")
    said = Said()
    stopped = sync.push([b], remote.tmp / "wt", lambda conflict: resolution, said)
    assert stopped is not None and stopped.repo == b and stopped.files == ("notes.md",)
    assert stopped.commit.endswith(" b's work") and str(stopped.upstream) == "origin/main"
    assert remote.log() == ["a's work", "start"]
    assert git(b, "rev-parse", "HEAD") == head and git(b, "status", "--short") == ""
    assert why in said.lines[-1] and said.lines[-1].endswith("The commits are left local.")
    assert not (remote.tmp / "wt" / "b").exists()
    assert len(git(b, "worktree", "list").splitlines()) == 1


def test_a_resolution_that_changes_nothing_drops_the_commit(remote):
    a, b = remote.clone("a"), remote.clone("b")
    commit(a, "notes.md", "from a\n", "a's work")
    publish(a)
    commit(b, "notes.md", "from b\n", "b's work")

    def theirs(conflict: sync.Conflict) -> str | None:
        write(conflict.worktree / "notes.md", "from a\n")
        return None

    assert sync.push([b], remote.tmp / "wt", theirs, Said()) is None
    assert remote.log() == ["a's work", "start"]
    assert git(b, "rev-parse", "HEAD") == git(remote.bare, "rev-parse", "main")


def test_a_rebase_that_cannot_go_on_stops_rather_than_skipping_for_ever(remote, monkeypatch):
    # Seen under a parallel test run: a rebase that stopped with nothing conflicted and its todo list
    # never written, so that every `rebase --skip` failed and the replay skipped for ever.
    a, b = remote.clone("a"), remote.clone("b")
    commit(a, "notes.md", "from a\n", "a's work")
    publish(a)
    commit(b, "notes.md", "from b\n", "b's work")
    head = git(b, "rev-parse", "HEAD")
    run = sync._git

    def half_written(repo, *args, **kwargs):
        done = run(repo, *args, **kwargs)
        if args[:2] == ("rebase", "-q"):
            git(repo, "reset", "-q", "--hard")
            state = Path(repo) / git(repo, "rev-parse", "--git-path", "rebase-merge").strip()
            (state / "git-rebase-todo").unlink()
        return done

    monkeypatch.setattr(sync, "_git", half_written)
    said = Said()
    stopped = sync.push([b], remote.tmp / "wt", never, said)
    assert stopped is not None and stopped.repo == b and "could not go on" in stopped.why
    assert remote.log() == ["a's work", "start"]
    assert git(b, "rev-parse", "HEAD") == head
    assert said.lines[-1].endswith("The commits are left local.")
    assert not (remote.tmp / "wt" / "b").exists()


def test_a_push_refused_is_tried_again(remote, monkeypatch):
    a, b = remote.clone("a"), remote.clone("b")
    commit(b, "other.md", "from b\n", "b's work")
    fetch = sync._fetch
    raced = []

    def racing(repo, up):
        done = fetch(repo, up)
        # Another loop pushes between this fetch and the push, once.
        if not raced:
            raced.append(True)
            commit(a, "notes.md", "from a\n", "a's work")
            publish(a)
        return done

    monkeypatch.setattr(sync, "_fetch", racing)
    said = Said()
    assert sync.push([b], remote.tmp / "wt", never, said) is None
    assert remote.log() == ["b's work", "a's work", "start"]
    assert "  b: origin/main moved on before the push; fetched again." in said.lines


def test_a_push_refused_each_time_is_left_local(remote, monkeypatch):
    a, b = remote.clone("a"), remote.clone("b")
    commit(b, "other.md", "from b\n", "b's work")
    fetch = sync._fetch
    count = []

    def racing(repo, up):
        done = fetch(repo, up)
        count.append(True)
        commit(a, "notes.md", f"from a, {len(count)}\n", f"a's work {len(count)}")
        publish(a)
        return done

    monkeypatch.setattr(sync, "_fetch", racing)
    said = Said()
    assert sync.push([b], remote.tmp / "wt", never, said, tries=2) is None
    assert "b's work" not in remote.log()
    assert said.lines[-1] == "  b: origin/main moved on before each push, 2 times; the commits are left local, for a later session's push."


def test_a_remote_out_of_reach_leaves_the_commit_local(remote):
    b = remote.clone("b")
    commit(b, "notes.md", "from b\n", "b's work")
    git(b, "remote", "set-url", "origin", str(remote.tmp / "gone.git"))
    said = Said()
    sync.pull([b], said)
    assert sync.push([b], remote.tmp / "wt", never, said) is None
    assert said.lines == ["  b: could not fetch from origin; the session starts from what the clone has.",
                          "  b: could not fetch from origin; not pushed - the commits are left local, for a later session's push."]
    # Neither names where the remote is.
    assert "gone.git" not in said.text


def test_a_worktree_left_by_a_run_that_died_is_cleared(remote):
    b = remote.clone("b")
    left = remote.tmp / "wt" / "b"
    git(b, "worktree", "add", "-q", "--detach", str(left), "HEAD")
    sync.discard_all([b], remote.tmp / "wt")
    assert not left.exists()
    assert len(git(b, "worktree", "list").splitlines()) == 1


# The settings

@pytest.mark.parametrize("settings, field", [({"push": "yes"}, "push"), ({"pushTries": 0}, "pushTries")])
def test_the_push_settings_are_checked(settings, field):
    with pytest.raises(config.ConfigError) as raised:
        config.check_run_settings("settings.json", "defaults", settings)
    assert raised.value.field == f"defaults.{field}"


def test_pushing_is_off_by_default():
    assert config.DEFAULTS["push"] is False and config.DEFAULTS["pushTries"] == 3


# The loop around it, with the fake claude

from test_loop import item, move, project  # noqa: E402,F401 - project is a fixture



def wired(p) -> tuple[Path, Path]:
    """The project's code given a remote, and another clone of it - another machine's."""
    bare = p.tmp / "remote.git"
    git(p.tmp, "clone", "-q", "--bare", str(p.code), str(bare))
    git(p.code, "remote", "add", "origin", str(bare))
    git(p.code, "fetch", "-q", "origin")
    git(p.code, "branch", "-q", "-u", "origin/main")
    other = p.tmp / "other"
    git(p.tmp, "clone", "-q", "-c", "core.autocrlf=false", str(bare), str(other))
    configure(other)
    return bare, other


def worktree(p) -> Path:
    return loop.loop_dir("proj", "proj") / "sync" / "code"


def faked(path: str, text: str) -> dict:
    return {"fake": "write", "path": path, "text": text}


def test_pushing_off_leaves_the_remote_alone(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"))
    bare, other = wired(p)
    commit(other, "notes.md", "from the other\n", "the other's work")
    publish(other)
    p.play(p.scene(faked("built.txt", "built\n"), move("alpha", "testing"), result="COMMIT: alpha: built"))
    assert p.go() == 0
    assert git(bare, "log", "--format=%s", "main").splitlines() == ["the other's work", "start"]
    assert not (p.code / "notes.md").exists()


def test_a_session_starts_from_what_was_pushed_and_its_commit_is_pushed(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"), push=True)
    bare, other = wired(p)
    commit(other, "notes.md", "from the other\n", "the other's work")
    publish(other)
    p.play(p.scene(faked("built.txt", "built\n"), move("alpha", "testing"), result="COMMIT: alpha: built"))
    assert p.go() == 0
    assert git(bare, "log", "--format=%s", "main").splitlines() == ["alpha: built [sonnet high]", "the other's work", "start"]
    assert "  pulled in code: 1 commit from origin/main" in p.lines
    assert "  pushed in code: 1 commit to origin/main" in p.lines
    # The pulled file is not the session's.
    assert git(p.code, "show", "--name-only", "--format=", "HEAD").split() == ["built.txt"]


def test_a_conflict_is_resolved_by_the_session_that_made_it(project, fake_claude, monkeypatch):
    p = project(item("alpha", "ready", complexity="low"), push=True)
    bare, other = wired(p)
    # The other machine pushes while the session runs: here, before it, with no pull to see it.
    monkeypatch.setattr(sync, "pull", lambda repos, say, lock=None: None)
    commit(other, "README.md", "the other's\n", "the other's work")
    publish(other)
    p.play(p.scene(faked("README.md", "mine\n"), move("alpha", "testing"), result="COMMIT: alpha: built"),
           p.scene(faked(str(worktree(p) / "README.md"), "the other's\nmine\n"), result="Both kept.\n\nMERGED: kept both lines"))
    assert p.go() == 0
    calls = fake_claude.calls()
    assert len(calls) == 2
    assert calls[1]["resume"] and calls[1]["session_id"] == calls[0]["session_id"]
    assert calls[1]["prompt"].startswith("Your work on alpha has been committed, but origin/main has moved on since")
    assert f"{str(worktree(p)).replace(chr(92), '/')}" in calls[1]["prompt"]
    assert "the files left conflicted there are: README.md." in calls[1]["prompt"]
    assert git(bare, "log", "--format=%s", "main").splitlines() == ["alpha: built [sonnet high]", "the other's work", "start"]
    assert git(bare, "show", "main:README.md") == "the other's\nmine\n"
    assert (p.code / "README.md").read_text() == "the other's\nmine\n"
    assert "  merged: kept both lines" in p.lines
    assert [(a["kind"], a["to"]) for a in p.attempts()] == [("build", "testing"), ("merge", "merged")]
    assert p.attempts()[1]["commits"] == []
    assert not worktree(p).exists()


def test_a_conflict_left_unmerged_stops_the_loop(project, fake_claude, monkeypatch):
    p = project(item("alpha", "ready", complexity="low"), item("beta", "ready", complexity="low"), push=True)
    bare, other = wired(p)
    monkeypatch.setattr(sync, "pull", lambda repos, say, lock=None: None)
    commit(other, "README.md", "the other's\n", "the other's work")
    publish(other)
    p.play(p.scene(faked("README.md", "mine\n"), move("@item", "testing"), result="COMMIT: alpha: built"),
           p.scene(result="UNMERGED: the two rewrite the same line differently"))
    assert p.go() == 1
    assert len(fake_claude.calls()) == 2
    assert git(bare, "log", "--format=%s", "main").splitlines() == ["the other's work", "start"]
    assert git(p.code, "log", "-1", "--format=%s").strip() == "alpha: built [sonnet high]"
    assert git(p.code, "status", "--short") == ""
    assert any(line.endswith("was not resolved: the two rewrite the same line differently. The commits are left local.") for line in p.lines)
    stop = [line for line in p.lines if line.startswith("Stopping: ")]
    assert stop == [f"Stopping: {p.code.name} could not be pushed - replaying {git(p.code, 'log', '-1', '--format=%h').strip()} alpha: built [sonnet high] onto origin/main "
                    "conflicts in README.md, and it was not resolved: the two rewrite the same line differently. The commit is left local, and alpha where the session left it."]
    hand = p.lines[p.lines.index(stop[0]) + 1]
    assert hand.startswith(f"To finish it by hand, in {str(p.code).replace(chr(92), '/')}: stash any changes of your own, git pull --rebase, resolve README.md,")
    assert hand.endswith("Or carry the session on with:")
    assert p.attempts()[-1]["to"] == "unmerged: the two rewrite the same line differently"
    assert not worktree(p).exists()


def test_a_merge_that_touches_the_clone_is_refused(project, fake_claude, monkeypatch):
    p = project(item("alpha", "ready", complexity="low"), push=True)
    bare, other = wired(p)
    monkeypatch.setattr(sync, "pull", lambda repos, say, lock=None: None)
    commit(other, "README.md", "the other's\n", "the other's work")
    publish(other)
    p.play(p.scene(faked("README.md", "mine\n"), move("alpha", "testing"), result="COMMIT: alpha: built"),
           p.scene(faked(str(worktree(p) / "README.md"), "both\n"), faked("stray.txt", "x\n"), result="MERGED: both"))
    assert p.go() == 1
    assert git(bare, "log", "--format=%s", "main").splitlines() == ["the other's work", "start"]
    assert p.attempts()[-1]["to"] == "unmerged: the session changed files in the clone itself"


def test_a_stop_without_a_conflict_names_the_repository_and_no_files(tmp_path):
    stopped = sync.Stopped(tmp_path / "code", sync.Upstream("origin", "main"), "the replay stopped (git exit 1)")
    first, second = loop.Run.stopped_lines(stopped, "alpha")
    assert first == ("Stopping: code could not be pushed - replaying its commits onto origin/main could not be finished: "
                     "the replay stopped (git exit 1). The commit is left local, and alpha where the session left it.")
    assert f"in {str(tmp_path / 'code').replace(chr(92), '/')}:" in second and "resolve what it stops at" in second


def test_the_merge_prompt_says_a_failing_check_is_left_unmerged(project, fake_claude, monkeypatch):
    p = project(item("alpha", "ready", complexity="low"), push=True)
    bare, other = wired(p)
    monkeypatch.setattr(sync, "pull", lambda repos, say, lock=None: None)
    commit(other, "README.md", "the other's\n", "the other's work")
    publish(other)
    p.play(p.scene(faked("README.md", "mine\n"), move("alpha", "testing"), result="COMMIT: alpha: built"),
           p.scene(result="UNMERGED: the tests fail after keeping both"))
    assert p.go() == 1
    assert "If a check fails after your resolution, leave it as it is and answer UNMERGED, saying which check failed" in fake_claude.calls()[1]["prompt"]
    assert p.attempts()[-1]["to"] == "unmerged: the tests fail after keeping both"
