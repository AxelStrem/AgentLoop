"""Homebrew for a project's sessions (item brew-tools): on only where AGENTLOOP_BREW is 1, the
first repository's Brewfile installed before a session when brew says it is not, a failure keeping
the loop from starting, sessions told how to add a tool, and the guard refusing `brew install`."""

import json
import os
import sys
from pathlib import Path

import pytest

from agentloop import brew, config, session, tools
from test_definition import TrackerProject
from test_loop import item, move
from test_tools import _run

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Homebrew is never on for Windows")

# A brew that logs what it is asked, is satisfied once it has installed, and fails on asking.
FAKE = """#!{python}
import os, pathlib, sys
here = pathlib.Path(__file__).parent
with open(here / "calls.txt", "a") as log:
    log.write(" ".join(sys.argv[1:3]) + "\\n")
done = here / "installed"
if sys.argv[1:3] == ["bundle", "check"]:
    sys.exit(0 if done.exists() else 1)
if sys.argv[1:3] == ["bundle", "install"]:
    print("Installing jq")
    if os.environ.get("FAKE_BREW_FAIL"):
        print("Error: No available formula with the name \\"jqq\\".")
        sys.exit(1)
    done.touch()
"""


@pytest.fixture
def fake_brew(tmp_path, monkeypatch):
    folder = tmp_path / "brewbin"
    folder.mkdir()
    script = folder / "brew"
    script.write_text(FAKE.format(python=sys.executable))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(brew.SWITCH, "1")

    def calls() -> list[str]:
        log = folder / "calls.txt"
        return log.read_text().splitlines() if log.exists() else []
    return calls


def test_it_is_on_only_where_the_switch_is_1_and_never_on_windows(monkeypatch):
    assert brew.enabled({brew.SWITCH: "1"})
    assert not brew.enabled({})
    assert not brew.enabled({brew.SWITCH: "yes"})
    monkeypatch.setattr(config, "this_system", lambda: "windows")
    assert not brew.enabled({brew.SWITCH: "1"})


def test_it_installs_what_is_missing_and_says_how_it_went(tmp_path, fake_brew):
    file = tmp_path / brew.BREWFILE
    file.write_text('brew "jq"\n')
    assert not brew.satisfied(file)
    said: list[str] = []
    brew.install(file, said.append)
    assert said == ["Installing jq"] and brew.satisfied(file)
    assert fake_brew() == ["bundle check", "bundle install", "bundle check"]


def test_a_failed_install_is_an_error(tmp_path, fake_brew, monkeypatch):
    monkeypatch.setenv("FAKE_BREW_FAIL", "1")
    file = tmp_path / brew.BREWFILE
    file.write_text('brew "jqq"\n')
    with pytest.raises(brew.BrewError, match="exit code 1"):
        brew.install(file, lambda line: None)


def test_on_without_brew_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(brew.BrewError, match="not on PATH"):
        brew.satisfied(tmp_path / brew.BREWFILE)


def test_sessions_are_told_of_the_brewfile_only_where_it_is_on(tmp_path):
    file = tmp_path / "code" / brew.BREWFILE
    told = session.where_you_are(None, {"code": tmp_path / "code"}, {}, brewfile=file)
    assert "- **Installing a tool:**" in told and " ".join(brew.install_command(file)) in told
    assert "Installing a tool" not in session.where_you_are(None, {"code": tmp_path / "code"}, {})


@pytest.mark.parametrize("command, on, refused", [
    ("brew install jq", "1", True),
    ("cd x && brew upgrade", "1", True),
    ("brew tap some/tap", "1", True),
    ("brew bundle install --no-upgrade --file=/code/Brewfile", "1", False),
    ("brew list", "1", False),
    ("brew install jq", None, False),
])
def test_the_guard_refuses_brew_installs_where_it_is_on(command, on, refused, tmp_path, monkeypatch):
    monkeypatch.delenv("OVERSEER_KEY_FILE", raising=False)
    if on:
        monkeypatch.setenv(brew.SWITCH, on)
    else:
        monkeypatch.delenv(brew.SWITCH, raising=False)
    call = {"tool_name": "Bash", "tool_input": {"command": command}}
    done = _run(tools.path("guard.py"), cwd=tmp_path, stdin=json.dumps(call))
    assert done.returncode == (2 if refused else 0), done.stderr


# The loop

ITEMS = (item("alpha", "ready", complexity="low"), item("beta", "ready", complexity="low"))


@pytest.fixture
def brewing(tmp_path, monkeypatch, fake_claude, overseer):
    def make(*items) -> TrackerProject:
        p = TrackerProject(tmp_path, monkeypatch, fake_claude, overseer, list(items))
        (Path(p.code) / brew.BREWFILE).write_text('brew "jq"\n')
        return p
    return make


def test_the_loop_installs_the_brewfile_once_before_its_sessions(brewing, fake_brew, fake_claude):
    p = brewing(*ITEMS)
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"),
           p.scene(move("beta", "testing"), result="COMMIT: beta: built"))
    assert p.go() == 0
    assert fake_brew().count("bundle install") == 1
    assert len([c for c in fake_claude.calls() if "prompt" in c]) == 2
    text = "\n".join(p.lines)
    assert "Brewfile lists, with Homebrew" in text and "Installing jq" in text
    assert "- **Installing a tool:**" in p.r.system_file.read_text(encoding="utf-8")


def test_a_failed_install_keeps_the_loop_from_starting(brewing, fake_brew, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_BREW_FAIL", "1")
    p = brewing(*ITEMS)
    assert p.go() == 1
    assert [c for c in fake_claude.calls() if "prompt" in c] == []
    assert "Brewfile failed" in "\n".join(p.lines)


def test_off_runs_no_brew_and_says_nothing_of_it(brewing, fake_brew, fake_claude, monkeypatch):
    monkeypatch.delenv(brew.SWITCH)
    p = brewing(*ITEMS)
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"),
           p.scene(move("beta", "testing"), result="COMMIT: beta: built"))
    assert p.go() == 0
    assert fake_brew() == []
    assert "Installing a tool" not in p.r.system_file.read_text(encoding="utf-8")
