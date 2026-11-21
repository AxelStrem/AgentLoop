"""GitHub addresses in the form this machine signs in with (item git-protocol): the two forms of an
address, the choice between them, and a clone or a loop's push switched to the one that signs in.
GitHub itself is never reached: `url.<path>.insteadOf` sends each form to a bare repository, or to
a folder that is not one, and whether a form signs in is the test's to say."""

import subprocess
from pathlib import Path

import pytest

from agentloop import clones, gitauth, sync

HTTPS = "https://github.com/owner/repo.git"
SSH = "git@github.com:owner/repo.git"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.mark.parametrize("given, other", [
    ("https://github.com/owner/repo.git", SSH),
    ("https://github.com/owner/repo", SSH),
    ("https://GitHub.com/owner/repo/", SSH),
    ("git@github.com:owner/repo.git", HTTPS),
    ("git@github.com:owner/repo", HTTPS),
    ("ssh://git@github.com/owner/repo.git", HTTPS),
    ("https://github.com/owner/my.repo-2.git", "git@github.com:owner/my.repo-2.git"),
])
def test_a_github_address_has_both_forms(given, other):
    assert gitauth.other_form(given) == other


@pytest.mark.parametrize("given", [
    "https://user:secret@github.com/owner/repo.git",
    "https://x-access-token@github.com/owner/repo.git",
    "https://gitlab.com/owner/repo.git",
    "git@gitlab.com:owner/repo.git",
    "https://github.com/owner/repo/tree/main",
    "/srv/git/repo.git",
    "C:/repos/repo",
])
def test_any_other_address_is_never_switched(given):
    assert gitauth.other_form(given) is None
    assert gitauth.usable(given, lambda kind: pytest.fail("nothing to ask")) == given


@pytest.mark.parametrize("https, ssh, given, wanted", [
    (False, True, HTTPS, SSH),
    (True, False, SSH, HTTPS),
    (True, True, HTTPS, HTTPS),
    (True, True, SSH, SSH),
    (False, False, HTTPS, HTTPS),
    (False, False, SSH, SSH),
])
def test_the_form_that_signs_in_is_used_and_the_given_one_when_both_or_neither_do(https, ssh, given, wanted):
    assert gitauth.usable(given, lambda kind: https if kind == gitauth.HTTPS else ssh) == wanted


def bare(tmp: Path) -> Path:
    seed = tmp / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    git(seed, "config", "user.name", "Test")
    git(seed, "config", "user.email", "test@example.com")
    (seed / "README.md").write_text("start\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "start")
    found = tmp / "remote.git"
    git(tmp, "clone", "-q", "--bare", str(seed), str(found))
    return found


def only(kind: str):
    return lambda asked: asked == kind


def test_a_clone_is_made_in_the_form_that_signs_in(tmp_path, monkeypatch):
    remote = bare(tmp_path)
    # Only the ssh form reaches the repository; the https one goes nowhere.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "3")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", f"url.{remote.as_posix()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", SSH)
    monkeypatch.setenv("GIT_CONFIG_KEY_2", f"url.{(tmp_path / 'nowhere').as_posix()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_2", HTTPS)
    monkeypatch.setattr(gitauth, "signs_in", only(gitauth.SSH))
    folder = tmp_path / "code" / "repo"
    assert clones.clone("repo", folder, HTTPS)
    assert git(folder, "config", "remote.origin.url") == SSH
    assert (folder / "README.md").is_file()


class Said:
    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, line: str, colour: str | None = None) -> None:
        self.lines.append(line)


def clone_with_commit(tmp: Path, remote: Path, url: str) -> Path:
    path = tmp / "clone"
    git(tmp, "clone", "-q", str(remote), str(path))
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    git(path, "remote", "set-url", "origin", url)
    (path / "notes.md").write_text("work\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "the session's work")
    return path


def test_a_push_that_fails_over_https_is_switched_to_ssh_and_pushed(tmp_path, monkeypatch):
    """A public repository: fetched over https with no credential, refused its push."""
    remote = bare(tmp_path)
    repo = clone_with_commit(tmp_path, remote, HTTPS)
    git(repo, "config", f"url.{remote.as_posix()}.insteadOf", HTTPS)
    git(repo, "config", f"url.{(tmp_path / 'nowhere').as_posix()}.pushInsteadOf", HTTPS)
    git(repo, "config", "--add", f"url.{remote.as_posix()}.insteadOf", SSH)
    monkeypatch.setattr(gitauth, "signs_in", only(gitauth.SSH))
    said = Said()
    assert sync.push([repo], tmp_path / "sync", lambda c: "none", said) is None
    assert git(repo, "config", "remote.origin.url") == SSH
    assert git(remote, "log", "-1", "--format=%s", "main") == "the session's work"
    assert any("switched to " + SSH in line for line in said.lines)


def test_a_fetch_that_fails_over_ssh_is_switched_to_https(tmp_path, monkeypatch):
    remote = bare(tmp_path)
    repo = clone_with_commit(tmp_path, remote, SSH)
    git(repo, "config", f"url.{(tmp_path / 'nowhere').as_posix()}.insteadOf", SSH)
    git(repo, "config", f"url.{remote.as_posix()}.insteadOf", HTTPS)
    monkeypatch.setattr(gitauth, "signs_in", only(gitauth.HTTPS))
    said = Said()
    assert sync.push([repo], tmp_path / "sync", lambda c: "none", said) is None
    assert git(repo, "config", "remote.origin.url") == HTTPS
    assert git(remote, "log", "-1", "--format=%s", "main") == "the session's work"


def test_a_push_that_fails_with_neither_form_signing_in_leaves_the_remote(tmp_path, monkeypatch):
    remote = bare(tmp_path)
    repo = clone_with_commit(tmp_path, remote, HTTPS)
    git(repo, "config", f"url.{remote.as_posix()}.insteadOf", HTTPS)
    git(repo, "config", f"url.{(tmp_path / 'nowhere').as_posix()}.pushInsteadOf", HTTPS)
    monkeypatch.setattr(gitauth, "signs_in", lambda kind: False)
    said = Said()
    assert sync.push([repo], tmp_path / "sync", lambda c: "none", said) is None
    assert git(repo, "config", "remote.origin.url") == HTTPS
    assert any("the push failed" in line for line in said.lines)


def test_a_push_url_of_its_own_is_switched_too(tmp_path, monkeypatch):
    remote = bare(tmp_path)
    repo = clone_with_commit(tmp_path, remote, SSH)
    git(repo, "remote", "set-url", "--push", "origin", HTTPS)
    monkeypatch.setattr(gitauth, "signs_in", only(gitauth.SSH))
    assert gitauth.switch(repo, "origin", Said())
    assert git(repo, "config", "remote.origin.url") == SSH
    assert git(repo, "config", "remote.origin.pushurl") == SSH
