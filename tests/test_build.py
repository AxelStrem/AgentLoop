import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentloop import build, config

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
        cwd=repo, check=True, capture_output=True,
    )


@pytest.fixture
def source(tmp_path):
    """A git repository of its own holding a copy of src/, with a __pycache__ in it."""
    repo = tmp_path / "source"
    shutil.copytree(SRC, repo / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"))
    (repo / "src" / "agentloop" / "__pycache__").mkdir()
    (repo / "src" / "agentloop" / "__pycache__" / "x.pyc").write_bytes(b"x")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("x\n")
    (repo / ".gitignore").write_text("__pycache__/\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first")
    return repo


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def as_source(source, monkeypatch):
    """The command builds the package it runs from: here, the temporary repository's."""
    package = (source / "src" / "agentloop").resolve()
    monkeypatch.setattr(build, "package_dir", lambda: package)
    return package


def _head(repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def _run(*cmd):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=60)


def test_build_copies_only_what_running_needs(source, tmp_path):
    built = build.build(tmp_path / "out", source / "src" / "agentloop")
    version = tmp_path / "out" / "versions" / built.id
    assert (version / "agentloop" / "cli.py").is_file() and (version / "__main__.py").is_file()
    assert not (version / "tests").exists() and not (version / "agentloop" / "__pycache__").exists()
    assert not list(version.rglob("*.pyc"))
    assert (tmp_path / "out" / "current").read_text() == built.id + "\n"
    assert (tmp_path / "out" / "agentloop.cmd").is_file() and (tmp_path / "out" / "agentloop").is_file()
    assert built.id.endswith(_head(source)[:7])
    record = json.loads((version / "agentloop" / "_build.json").read_text())
    assert (record["commit"], record["clean"], record["python"]) == (_head(source), True, sys.executable)
    assert not (source / "src" / "agentloop" / "_build.json").exists()


def test_the_copy_prints_its_version(source, tmp_path):
    built = build.build(tmp_path / "out", source / "src" / "agentloop")
    expected = f"({_head(source)[:7]}, clean, built "
    by_folder = _run(sys.executable, tmp_path / "out" / "versions" / built.id, "version")
    assert by_folder.returncode == 0, by_folder.stderr
    assert expected in by_folder.stdout
    launcher = _run(built.launcher, "version")
    assert launcher.returncode == 0, launcher.stderr
    assert launcher.stdout == by_folder.stdout


def test_launcher_passes_arguments_through(source, tmp_path):
    built = build.build(tmp_path / "out dir", source / "src" / "agentloop")
    done = _run(built.launcher, "import-psd1", "a file with spaces.psd1", "--out", str(tmp_path / "export"))
    assert done.returncode == 2
    assert "a file with spaces.psd1" in done.stderr


def test_a_second_build_leaves_the_first_alone(source, tmp_path):
    package = source / "src" / "agentloop"
    first = build.build(tmp_path / "out", package)
    first_dir = tmp_path / "out" / "versions" / first.id
    first_files = sorted(p.relative_to(tmp_path) for p in first_dir.rglob("*"))
    cli = package / "cli.py"
    cli.write_text(cli.read_text(encoding="utf-8") + "\n# an edit\n", encoding="utf-8")
    second = build.build(tmp_path / "out", package)
    assert second.id != first.id and second.id.endswith("-dirty")
    assert second.previous == first.id
    assert (tmp_path / "out" / "current").read_text() == second.id + "\n"
    assert first_files == sorted(p.relative_to(tmp_path) for p in first_dir.rglob("*"))
    assert "# an edit" not in (first_dir / "agentloop" / "cli.py").read_text(encoding="utf-8")
    assert "# an edit" in (tmp_path / "out" / "versions" / second.id / "agentloop" / "cli.py").read_text(encoding="utf-8")
    assert "dirty" in _run(second.launcher, "version").stdout
    third = build.build(tmp_path / "out", package)
    assert third.kept == [first.id]


def test_refuses_a_foreign_folder(source, tmp_path):
    foreign = tmp_path / "out"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("mine\n")
    with pytest.raises(build.BuildError, match="not empty and is not an agentloop build"):
        build.build(foreign, source / "src" / "agentloop")
    assert [p.name for p in foreign.iterdir()] == ["notes.txt"]


def test_refuses_a_target_inside_the_repository(source):
    with pytest.raises(build.BuildError, match="goes outside it"):
        build.build(source / "dist", source / "src" / "agentloop")


# The command works on the machine's settings, here redirected to a temporary home.


def test_command_writes_install_and_then_uses_it(as_source, tmp_path, home, capsys):
    assert build.run(str(tmp_path / "out")) == 0
    assert "built    " in capsys.readouterr().out
    settings = json.loads((home / "config" / "settings.json").read_text(encoding="utf-8"))
    assert settings["install"] == config.home_relative(tmp_path / "out")
    assert config.expand(settings["install"]) == tmp_path / "out"
    assert build.run(None) == 0
    assert len(list((tmp_path / "out" / "versions").iterdir())) == 2


