"""A project's repositories cloned when a loop is added from its tracker (item clone): the address
a definition gives a repository read, the folder asked for cloned into when it is missing or empty,
used as it is when it is a clone of that address already, and refused by name otherwise - from the
helper, the page's route and `agentloop add`. The addresses are bare repositories on this disk, so
nothing goes over the network."""

import io
import subprocess
from pathlib import Path

import pytest

from agentloop import cli, clones, config, definition
from test_definition import served
from test_loop import item
from test_loop_from_url import address, git_repo, machine  # noqa: F401 - machine is a fixture
from test_server import serve  # noqa: F401 - a fixture


def _git(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


@pytest.fixture
def origin(tmp_path) -> Path:
    """A bare repository with a commit on `main`, and a branch `other` with a file of its own."""
    work = tmp_path / "origin-work"
    work.mkdir()
    _git("init", "-q", "-b", "main", cwd=work)
    (work / "README.md").write_text("main\n", encoding="utf-8")
    _git("add", "README.md", cwd=work)
    _git("commit", "-q", "-m", "first", cwd=work)
    _git("checkout", "-q", "-b", "other", cwd=work)
    (work / "OTHER.md").write_text("other\n", encoding="utf-8")
    _git("add", "OTHER.md", cwd=work)
    _git("commit", "-q", "-m", "other", cwd=work)
    _git("checkout", "-q", "main", cwd=work)
    bare = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(work), str(bare))
    return bare


def repos(url: str, branch: str = ""):
    """The definition's repositories: `code` without an address, `docs` with `url`."""
    return (("code", "The code"), ("docs", "", url, branch))


# The helper

@pytest.mark.parametrize("a, b", [
    ("https://github.com/a/b", "https://GitHub.com/a/b.git/"),
    ("https://user:secret@github.com/a/b.git", "git@github.com:a/b"),
    ("ssh://git@github.com/a/b", "https://github.com/a/b"),
])
def test_addresses_of_one_repository_are_the_same(a, b):
    assert clones.same_address(a, b)


def test_addresses_of_others_are_not():
    assert not clones.same_address("https://github.com/a/b", "https://github.com/a/c")
    assert not clones.same_address("https://github.com/a/b", "https://gitlab.com/a/b")


def test_credentials_are_never_said():
    assert clones.without_credentials("https://user:secret@host/a.git") == "https://host/a.git"


def test_a_missing_or_empty_folder_is_cloned_into(origin, tmp_path):
    assert clones.state("docs", tmp_path / "nope", str(origin)) == clones.EMPTY
    (tmp_path / "empty").mkdir()
    assert clones.state("docs", tmp_path / "empty", str(origin)) == clones.EMPTY
    assert clones.clone("docs", tmp_path / "deep" / "docs", str(origin)) is True
    assert (tmp_path / "deep" / "docs" / "README.md").is_file()
    assert clones.clone("docs", tmp_path / "empty", origin.as_uri()) is True


def test_a_branch_given_is_the_one_checked_out(origin, tmp_path):
    clones.clone("docs", tmp_path / "docs", str(origin), "other")
    assert (tmp_path / "docs" / "OTHER.md").is_file()
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=tmp_path / "docs") == "other"


def test_a_clone_of_it_already_is_used_as_it_is(origin, tmp_path):
    folder = tmp_path / "docs"
    clones.clone("docs", folder, str(origin))
    (folder / "mine.txt").write_text("the person's own\n", encoding="utf-8")
    assert clones.state("docs", folder, str(origin) + "/") == clones.CLONE
    assert clones.clone("docs", folder, origin.as_uri()) is False
    assert (folder / "mine.txt").read_text(encoding="utf-8") == "the person's own\n"


def test_a_folder_holding_anything_else_is_refused_by_name(origin, tmp_path):
    files = tmp_path / "files"
    files.mkdir()
    (files / "a.txt").write_text("x", encoding="utf-8")
    other = git_repo(tmp_path / "other")
    (other / "a.txt").write_text("x", encoding="utf-8")
    inside = tmp_path / "inner"
    clones.clone("docs", inside, str(origin))
    (inside / "sub").mkdir()
    (inside / "sub" / "b.txt").write_text("x", encoding="utf-8")
    a_file = tmp_path / "file.txt"
    a_file.write_text("x", encoding="utf-8")
    for folder in (files, other, inside / "sub", a_file):
        with pytest.raises(config.ConfigError) as caught:
            clones.clone("docs", folder, str(origin))
        assert caught.value.field == "repos.docs" and str(folder) in caught.value.message
    assert (files / "a.txt").is_file() and not (other / "README.md").exists()


def test_a_clone_that_fails_says_which_repository_and_stops(tmp_path):
    with pytest.raises(config.ConfigError) as caught:
        clones.clone("docs", tmp_path / "docs", "https://user:secret@127.0.0.1:9/nothing.git", timeout=60)
    assert caught.value.field == "repos.docs" and caught.value.message.startswith("docs could not be cloned from")
    assert "secret" not in caught.value.message
    assert not (tmp_path / "docs").exists() or not any((tmp_path / "docs").iterdir())


def test_the_folder_offered_is_beside_the_others_or_under_home(tmp_path):
    assert clones.default_folder(tmp_path, "proj", "docs") == tmp_path / "proj" / "docs"
    assert clones.default_folder(tmp_path, "proj", "docs", ["", str(tmp_path / "src" / "code")]) == tmp_path / "src" / "docs"


# The definition

def test_the_definition_gives_a_repositorys_address_and_branch(machine, overseer, origin):
    tracker = served(overseer, item("alpha"), repos=repos(str(origin), "other"))
    shown = definition.preview(config.load_settings(), address(overseer, tracker), None, "w")
    assert shown["repos"] == [{"name": "code", "about": "The code"}, {"name": "docs", "url": str(origin), "branch": "other"}]
    assert shown["home"] == Path.home().as_posix()


# The page's route

def test_the_route_clones_and_the_loop_is_added_on_it(machine, overseer, origin, serve, tmp_path):
    tracker = served(overseer, item("alpha"), repos=repos(str(origin)))
    c = serve()
    form = {"address": address(overseer, tracker), "worker": "w"}
    folder = (tmp_path / "clones" / "docs").as_posix()
    assert c.json("POST", "/api/clone", {**form, "repo": "docs", "folder": folder}) == {"folder": folder, "cloned": True}
    assert c.json("POST", "/api/clone", {**form, "repo": "docs", "folder": folder}) == {"folder": folder, "cloned": False}
    refused = c.json("POST", "/api/clone", {**form, "repo": "docs", "folder": str(machine.samples / "..")}, expect=400)
    assert refused["field"] == "repos.docs"
    refused = c.json("POST", "/api/clone", {**form, "repo": "code", "folder": folder}, expect=400)
    assert refused["field"] == "repos.code" and "no address" in refused["error"]
    added = c.json("POST", "/api/loops/from-tracker", {**form, "name": "proj", "places": {"samples": str(machine.samples)},
                                                       "repos": {"code": str(machine.code), "docs": folder}})
    assert added["name"] == "proj" and added["missing"] == []


# The command

def test_the_command_clones_with_the_flag(machine, overseer, origin, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    tracker = served(overseer, item("alpha"), repos=repos(str(origin)))
    folder = tmp_path / "clones" / "docs"
    base = ["add", address(overseer, tracker), "--worker", "w", "--repo", f"code={machine.code}",
            "--place", f"samples={machine.samples}"]
    assert cli.main(base) == 2
    assert "--clone" in capsys.readouterr().err
    assert cli.main([*base, "--repo", f"docs={folder}", "--clone"]) == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert f"cloned from {origin}" in out and "docs: cloned into" in out
    assert config.load_settings().loop("proj").repos["docs"] == str(folder)


def test_the_command_stops_when_a_clone_fails(machine, overseer, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    tracker = served(overseer, item("alpha"), repos=repos((tmp_path / "missing.git").as_posix()))
    assert cli.main(["add", address(overseer, tracker), "--worker", "w", "--repo", f"code={machine.code}",
                     "--repo", f"docs={tmp_path / 'new' / 'docs'}", "--place", f"samples={machine.samples}", "--clone"]) == 2
    assert "docs could not be cloned" in capsys.readouterr().err
    assert config.load_settings().loops == []


def test_the_command_offers_the_folder_and_clones_on_a_terminal(machine, overseer, origin, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    # The folder offered is beside code's; a folder holding something else is refused and asked again.
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{machine.samples}\n\ny\n"))
    tracker = served(overseer, item("alpha"), repos=repos(str(origin)))
    (machine.samples / "x.txt").write_text("x", encoding="utf-8")
    # The fixture's own docs repository is where the folder offered is: moved out of the way.
    machine.docs.rename(machine.docs.parent / "docs-before")
    assert cli.main(["add", address(overseer, tracker), "--worker", "w", "--repo", f"code={machine.code}",
                     "--place", f"samples={machine.samples}"]) == 0
    captured = capsys.readouterr()
    assert "repos.docs" in captured.err and "docs: cloned into" in captured.out
    expected = (machine.code.parent / "docs").as_posix()
    assert config.load_settings().loop("proj").repos["docs"] == expected
    assert (machine.code.parent / "docs" / "README.md").is_file()
