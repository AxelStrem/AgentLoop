import subprocess
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
    # The file changed both before and during the session is left, and said to be.
    assert "mixed.txt" in result.said[0]
    assert [c["repo"] for c in result.commits] == ["repo"]


def test_a_file_changed_before_and_left_as_it_was_says_nothing(tmp_path, lock):
    repo = make_repo(tmp_path / "repo")
    (repo / "README.md").write_text("owner's\n")
    before = commits.snapshot([repo], lock=lock)
    result = commits.commit_session(before, "m", [repo], lock=lock)
    assert result.ok and result.commits == [] and result.said == []


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


