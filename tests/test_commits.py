import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentloop import commits


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def make_repo(path: Path, files: dict[str, str] | None = None) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "core.autocrlf", "false")
    for name, text in (files or {"README.md": "readme\n"}).items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text, encoding="utf-8", newline="\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "start")
    return path.resolve()


def committed(repo: Path) -> list[str]:
    return git(repo, "show", "--name-only", "--no-renames", "--format=", "HEAD").split()


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    return tmp_path / "state" / "commit.lock"


def test_only_the_sessions_own_files_are_committed(tmp_path, lock):
    repo = make_repo(tmp_path / "repo", {"owner.txt": "owner\n", "mixed.txt": "mixed\n", "gone.txt": "gone\n", "old.txt": "old\n"})
    (repo / "owner.txt").write_text("owner's\n")
    (repo / "mixed.txt").write_text("owner's\n")
    (repo / "staged.txt").write_text("staged by the owner\n")
    git(repo, "add", "staged.txt")
    before = commits.snapshot([repo], lock=lock)
    assert set(before[repo]) == {"owner.txt", "mixed.txt", "staged.txt"}

    (repo / "mixed.txt").write_text("owner's and the agent's\n")
    (repo / "new.txt").write_text("the agent's\n")
    (repo / "gone.txt").unlink()
    (repo / "old.txt").rename(repo / "renamed.txt")
    result = commits.commit_session(before, "item: what changed", [repo], lock=lock)

    assert result.ok
    assert sorted(committed(repo)) == ["gone.txt", "new.txt", "old.txt", "renamed.txt"]
    assert git(repo, "log", "-1", "--format=%s").strip() == "item: what changed"
    # The owner's work is where it was: changed, the staged file still staged.
    status = git(repo, "status", "--porcelain")
    assert " M owner.txt" in status and " M mixed.txt" in status and "A  staged.txt" in status
    assert result.said[0] == "left uncommitted in repo, already changed before this session: mixed.txt"
    assert result.said[1].startswith("committed in repo: ") and result.said[1].endswith("item: what changed (4 files)")
    record = result.commits[0]
    assert record["repo"] == "repo" and record["tracker"] is False
    assert record["hash"] == git(repo, "log", "-1", "--format=%h").strip()
    # git's shortstat sees the rename as one file, and changing nothing.
    assert (record["files"], record["insertions"], record["deletions"]) == (3, 1, 1)


def test_a_file_changed_before_and_left_as_it_was_says_nothing(tmp_path, lock):
    repo = make_repo(tmp_path / "repo")
    (repo / "README.md").write_text("owner's\n")
    before = commits.snapshot([repo], lock=lock)
    result = commits.commit_session(before, "m", [repo], lock=lock)
    assert result.ok and result.commits == [] and result.said == []


def branch_to_merge(repo: Path, files: dict[str, str]) -> None:
    git(repo, "checkout", "-q", "-b", "feature")
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feature")
    git(repo, "checkout", "-q", "main")


def test_a_merge_the_session_began_is_committed_whole(tmp_path, lock):
    repo = make_repo(tmp_path / "repo", {"owner.txt": "owner\n", "README.md": "readme\n"})
    branch_to_merge(repo, {"feature.txt": "feature\n"})
    (repo / "owner.txt").write_text("owner's\n")
    before = commits.snapshot([repo], lock=lock)

    git(repo, "merge", "-q", "--no-commit", "--no-ff", "feature")
    (repo / "README.md").write_text("readme, and the merge's note\n")
    result = commits.commit_session(before, "item: merge feature", [repo], lock=lock)

    assert result.ok, result.said
    assert git(repo, "log", "-1", "--format=%P").split() == [git(repo, "rev-parse", "main~1").strip(), git(repo, "rev-parse", "feature").strip()]
    assert sorted(git(repo, "diff", "--name-only", "HEAD~1", "HEAD").split()) == ["README.md", "feature.txt"]
    assert git(repo, "status", "--porcelain") == " M owner.txt\n"


def test_a_merge_begun_before_the_session_is_left_for_the_owner(tmp_path, lock):
    repo = make_repo(tmp_path / "repo")
    branch_to_merge(repo, {"feature.txt": "feature\n"})
    git(repo, "merge", "-q", "--no-commit", "--no-ff", "feature")
    before = commits.snapshot([repo], lock=lock)

    (repo / "new.txt").write_text("the agent's\n")
    result = commits.commit_session(before, "m", [repo], lock=lock)

    assert not result.ok
    assert result.said == ["not committed in repo: a merge in progress holds files changed before this session: feature.txt"]
    assert git(repo, "log", "-1", "--format=%s").strip() == "start"


def test_a_merge_still_in_conflict_is_not_committed(tmp_path, lock):
    repo = make_repo(tmp_path / "repo", {"a.txt": "a\n"})
    branch_to_merge(repo, {"a.txt": "feature's a\n"})
    (repo / "a.txt").write_text("main's a\n")
    git(repo, "commit", "-q", "-am", "main")
    before = commits.snapshot([repo], lock=lock)

    subprocess.run(["git", "-C", str(repo), "merge", "-q", "feature"], capture_output=True)
    result = commits.commit_session(before, "m", [repo], lock=lock)

    assert not result.ok
    assert result.said == ["not committed in repo: unmerged files, still in conflict: a.txt"]
    assert "UU a.txt" in git(repo, "status", "--porcelain")


def test_each_repository_is_committed_on_its_own(tmp_path, lock):
    code = make_repo(tmp_path / "code")
    docs = make_repo(tmp_path / "docs", {"notes.md": "notes\n"})
    before = commits.snapshot([code, docs], lock=lock)
    (code / "a.py").write_text("x = 1\n")
    (docs / "notes.md").write_text("notes\nmore\n")
    result = commits.commit_session(before, "m", [code, docs], lock=lock)
    # No repository holds a tracker: each record says so, as the script's says of a code repository.
    assert [(c["repo"], c["tracker"]) for c in result.commits] == [("code", False), ("docs", False)]
    assert committed(code) == ["a.py"] and committed(docs) == ["notes.md"]


def test_the_sessions_lines_are_counted(tmp_path, lock):
    repo = make_repo(tmp_path / "repo", {"code.py": "a\nb\nc\n", "notes.os": "a\n", "owner.py": "a\n"})
    (repo / "owner.py").write_text("owner's\n")
    before = commits.snapshot([repo], lock=lock)
    (repo / "owner.py").write_text("owner's and more\n")
    (repo / "code.py").write_text("a\nB\nc\nd\n", newline="\n")  # 2 added, 1 taken out
    (repo / "made.py").write_text("1\n2\n3\n", newline="\n")      # 3 added, untracked
    (repo / "picture.png").write_bytes(b"\x89PNG\0\0\1\2")  # binary: nothing
    (repo / "notes.os").write_text("a\nnotes\nnotes\n", newline="\n")  # 2 added: no file is the tracker's now
    assert commits.session_lines(before, [repo], lock=lock) == 8


def test_the_prompts_status(tmp_path):
    clean = make_repo(tmp_path / "clean")
    busy = make_repo(tmp_path / "busy")
    (busy / "README.md").write_text("changed\n")
    text = commits.repo_status([clean, busy])
    assert text.startswith("Uncommitted changes as the session begins, as git status --short prints them")
    name = lambda repo: str(repo).replace("\\", "/")
    assert f"{name(clean)}: clean\n\n{name(busy)}:\n M README.md\n\n" in text
    for n in range(45):
        (busy / f"f{n:02}.txt").write_text("x")
    lines = commits.repo_status([busy]).split("\n")
    # README.md and 45 more: 40 of them shown.
    assert len(lines) == 3 + 40 + 3 and lines[43:] == ["... and 6 more", "", ""]


HOLDER = """
import sys, time
from pathlib import Path
from agentloop import commits
with commits.commit_lock(Path(sys.argv[1])):
    print("held", flush=True)
    time.sleep(float(sys.argv[2]))
"""


def test_the_lock_keeps_a_second_process_waiting(lock):
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(lock), "1.5"], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        with commits.commit_lock(lock):
            waited = time.monotonic() - started
        assert waited > 0.8
    finally:
        holder.wait(timeout=30)
    # And once it is let go, it is taken at once.
    started = time.monotonic()
    with commits.commit_lock(lock):
        pass
    assert time.monotonic() - started < 0.5
