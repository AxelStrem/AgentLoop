"""A loop added from a tracker's address (item loop-from-url): the address read, the definition
shown, every path checked before the loop is saved, and the loop then carrying its own server and
key file when they are not its worker's - from the page's routes and from `agentloop add`."""

import io
import json
import subprocess
from pathlib import Path

import pytest

from agentloop import cli, config, definition, loop
from test_definition import DESCRIPTION, TrackerProject, closed_port, served
from test_loop import item, move
from test_server import serve  # noqa: F401 - a fixture


# The address

@pytest.mark.parametrize("text, expected", [
    ("https://o.example.com/doc/alice/projects/x.os", ("https://o.example.com", "alice/projects/x.os")),
    ("http://127.0.0.1:8080/pre/doc/alice/x.os", ("http://127.0.0.1:8080/pre", "alice/x.os")),
    ("https://o.example.com/?doc=alice%2Fprojects%2Fx.os", ("https://o.example.com", "alice/projects/x.os")),
    ("https://o.example.com/index.html?doc=alice/x.os", ("https://o.example.com", "alice/x.os")),
    ("  alice/projects/x.os ", (None, "alice/projects/x.os")),
    ("x.os", (None, "x.os")),
])
def test_an_address_gives_its_server_and_tracker(text, expected):
    assert config.parse_address(text) == expected


@pytest.mark.parametrize("text", ["", "ftp://h/doc/a/x.os", "https://h/a/x.os", "https://h/doc/a/x.txt",
                                  "alice/../x.os", "alice//x.os", "https://h/doc/a/Bad Name.os"])
def test_an_address_that_is_no_tracker_is_refused(text):
    with pytest.raises(config.ConfigError) as caught:
        config.parse_address(text)
    assert caught.value.field == "address"


def test_the_summary_is_the_first_paragraph_and_the_headings():
    text = "# AgentLoop\n\nThe loop,\nrebuilt.\n\n## Rules\n\nPython.\n\n## Checking\n"
    assert definition.summary(text) == {"lead": "The loop, rebuilt.", "headings": ["AgentLoop", "Rules", "Checking"]}
    assert definition.summary("x" * 700)["lead"].endswith("…")


# The machine

def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


@pytest.fixture
def machine(tmp_path, monkeypatch, overseer):
    """A machine whose worker reaches the served Overseer, no loops on it yet, and the folders a
    loop on `proj` names: two git repositories and a place."""
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    config.save_json(config.paths().settings, {
        "format": 1,
        "workers": [{"name": "w", "claude": None, "overseer": overseer.worker},
                    {"name": "bare", "claude": None, "overseer": None}],
        "loops": [], "defaults": {},
    })

    class Found:
        code = git_repo(tmp_path / "code")
        docs = git_repo(tmp_path / "docs")
        samples = tmp_path / "samples"
        plain = tmp_path / "plain"
    Found.samples.mkdir()
    Found.plain.mkdir()
    return Found


def address(overseer, tracker: str) -> str:
    return f"{overseer.url}/doc/{tracker}"


def add(machine, tracker: str, **more):
    args = {"name": "proj", "address": tracker, "key_file": None, "worker": "w",
            "repos": {"code": str(machine.code), "docs": str(machine.docs)}, "places": {"samples": str(machine.samples)}}
    args.update(more)
    return definition.add(config.load_settings(), **args)


def test_the_preview_shows_what_the_project_names(machine, overseer):
    tracker = served(overseer, item("alpha"))
    shown = definition.preview(config.load_settings(), address(overseer, tracker), None, "w")
    assert shown["tracker"] == tracker and shown["project"] == "proj" and shown["server"] == overseer.url
    assert shown["keyFile"] == str(overseer.key) and shown["ownServer"] is False
    assert shown["summary"] == definition.summary(DESCRIPTION)
    assert shown["repos"] == [{"name": "code", "about": "The code"}, {"name": "docs"}]
    assert shown["places"] == [{"name": "samples", "about": "Files to check against"}]
    assert shown["systems"] == ["windows", "macos", "linux"] and shown["systemProblem"] is None
    assert shown["settings"] == {"chainUpTo": 1}


def test_a_system_the_project_does_not_run_on_is_said(machine, overseer):
    other = next(s for s in ("windows", "macos", "linux") if s != config.this_system())
    tracker = served(overseer, item("alpha"), systems=other)
    shown = definition.preview(config.load_settings(), tracker, None, "w")
    assert config.this_system() in shown["systemProblem"]
    with pytest.raises(definition.Refusals) as caught:
        add(machine, tracker)
    assert [p.field for p in caught.value.problems] == ["systems"]
    assert config.load_settings().loops == []


def test_a_loop_is_added_with_its_definition_kept(machine, overseer):
    tracker = served(overseer, item("alpha"))
    new = add(machine, address(overseer, tracker), run_settings=None)
    assert new == {"name": "proj", "tracker": tracker, "worker": "w",
                   "repos": {"code": str(machine.code), "docs": str(machine.docs)}, "places": {"samples": str(machine.samples)}}
    found = config.load_settings().loop("proj")
    assert found.tracker == tracker and found.overseer is None
    kept = definition.kept(loop.loop_dir("proj", "proj"), tracker)
    assert kept["definition"]["description"] == DESCRIPTION


def test_every_wrong_path_and_the_name_are_refused_at_once(machine, overseer):
    tracker = served(overseer, item("alpha"))
    add(machine, tracker)
    with pytest.raises(definition.Refusals) as caught:
        add(machine, tracker, repos={"code": str(machine.plain), "docs": str(machine.plain / "nope"), "other": str(machine.code)},
            places={})
    fields = sorted(p.field for p in caught.value.problems)
    assert fields == ["name", "places.samples", "repos.code", "repos.docs", "repos.other"]
    by = {p.field: p.message for p in caught.value.problems}
    assert "git repository" in by["repos.code"] and by["repos.docs"] == "no such folder"
    assert [l.name for l in config.load_settings().loops] == ["proj"]


def test_a_wrong_address_worker_and_key_are_refused_at_once(machine, overseer, tmp_path):
    with pytest.raises(definition.Refusals) as caught:
        definition.preview(config.load_settings(), "https://h/nothing", str(tmp_path / "no.key"), "nobody")
    assert sorted(p.field for p in caught.value.problems) == ["address", "keyFile", "worker"]


def test_a_bare_name_needs_a_worker_with_a_server(machine, overseer):
    tracker = served(overseer, item("alpha"))
    with pytest.raises(definition.Refusals) as caught:
        definition.preview(config.load_settings(), tracker, str(overseer.key), "bare")
    assert [p.field for p in caught.value.problems] == ["address"]


def test_a_tracker_on_another_server_than_the_workers_is_carried_by_the_loop(machine, overseer):
    tracker = served(overseer, item("alpha"))
    new = add(machine, address(overseer, tracker), worker="bare", key_file=str(overseer.key))
    assert new["overseer"] == {"server": overseer.url, "keyFile": str(overseer.key)}
    found = config.load_settings().loop("proj")
    assert config.overseer_for(config.load_settings(), found) == found.overseer == new["overseer"]


def test_an_unreachable_server_is_said_without_its_address(machine, overseer):
    with pytest.raises(definition.Unreachable) as caught:
        definition.preview(config.load_settings(), f"{closed_port()}/doc/a/proj.os", str(overseer.key), "w", timeout=5)
    assert "127.0.0.1" not in str(caught.value)


def test_a_copy_inherits_the_loops_own_server(tmp_path):
    raw = {"format": 1, "workers": [{"name": "w", "claude": None, "overseer": None}],
           "loops": [{"name": "one", "tracker": "a/proj.os", "worker": "w", "overseer": {"server": "http://s", "keyFile": "k"}},
                     {"name": "two", "like": "one"}]}
    settings = config.parse_settings(raw, tmp_path / "settings.json")
    assert settings.loop("two").overseer == {"server": "http://s", "keyFile": "k"}
    raw["loops"][0]["overseer"] = {"server": "http://s"}
    with pytest.raises(config.ConfigError) as caught:
        config.parse_settings(raw, tmp_path / "settings.json")
    assert caught.value.field == "loops[0].overseer"


# The loop runs through its own server

def test_a_loop_with_its_own_server_runs_through_it(tmp_path, monkeypatch, fake_claude, overseer):
    p = TrackerProject(tmp_path, monkeypatch, fake_claude, overseer, [item("alpha", "ready", complexity="low")])
    # The worker's server is gone; the loop names the served one itself.
    p.point_at(closed_port())
    path = config.paths().settings
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["loops"][0]["overseer"] = overseer.worker
    config.save_json(path, raw)
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    assert p.go() == 0
    assert p.show("alpha")["stage"] == "testing"
    assert p.r.tracker_env["OVERSEER_URL"] == overseer.url.rstrip("/")


# The page's routes

def test_the_routes_read_and_add(machine, overseer, serve):
    tracker = served(overseer, item("alpha"))
    c = serve()
    shown = c.json("POST", "/api/definition", {"address": address(overseer, tracker), "worker": "w"})
    assert shown["project"] == "proj" and shown["repos"][0] == {"name": "code", "about": "The code"}
    form = {"address": address(overseer, tracker), "worker": "w", "name": "proj",
            "repos": {"code": str(machine.code), "docs": str(machine.plain)}, "places": {}}
    refused = c.json("POST", "/api/loops/from-tracker", form, expect=400)
    assert sorted(p["field"] for p in refused["problems"]) == ["places.samples", "repos.docs"]
    form["repos"]["docs"] = str(machine.docs)
    form["places"]["samples"] = str(machine.samples)
    added = c.json("POST", "/api/loops/from-tracker", form)
    assert added["name"] == "proj" and added["server"] is None
    assert added["definition"] == {"repos": ["code", "docs"], "places": ["samples"]}
    assert added["missing"] == []


def test_a_loop_on_its_own_server_shows_its_host(machine, overseer, serve):
    tracker = served(overseer, item("alpha"))
    add(machine, address(overseer, tracker), worker="bare", key_file=str(overseer.key))
    loops = serve().json("GET", "/api/loops")["loops"]
    assert loops[0]["server"] == overseer.url.split("://", 1)[1]


def test_the_route_says_an_unreachable_server_as_502(machine, overseer, serve):
    status, _, raw = serve().request("POST", "/api/definition",
                                     {"address": f"{closed_port()}/doc/a/proj.os", "worker": "w"})
    assert status == 502 and "127.0.0.1" not in raw.decode()


# The command

def test_the_command_adds_from_flags(machine, overseer, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    tracker = served(overseer, item("alpha"))
    code = cli.main(["add", address(overseer, tracker), "--worker", "w", "--repo", f"code={machine.code}",
                     "--repo", f"docs={machine.docs}", "--place", f"samples={machine.samples}"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "repository code - Sessions run here. The code" in out and "place      samples - Files to check against" in out
    assert config.load_settings().loop("proj").tracker == tracker


def test_the_command_without_a_terminal_names_the_flags_still_needed(machine, overseer, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    tracker = served(overseer, item("alpha"))
    assert cli.main(["add", address(overseer, tracker), "--worker", "w", "--repo", f"code={machine.code}"]) == 2
    err = capsys.readouterr().err
    assert "--repo docs=<path>" in err and "--place samples=<path>" in err and "--repo code" not in err
    assert config.load_settings().loops == []


def test_the_command_asks_again_for_a_wrong_path(machine, overseer, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers = io.StringIO(f"{machine.plain}\n{machine.samples}\n{machine.docs}\n")
    monkeypatch.setattr("sys.stdin", answers)
    tracker = served(overseer, item("alpha"))
    assert cli.main(["add", address(overseer, tracker), "--worker", "w", "--repo", f"code={machine.code}"]) == 0
    captured = capsys.readouterr()
    assert "repos.docs" in captured.err
    assert config.load_settings().loop("proj").repos["docs"] == str(machine.docs)
