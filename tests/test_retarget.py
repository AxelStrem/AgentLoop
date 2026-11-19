"""A loop on a bundle moved to its tracker, keeping this machine's paths (item retarget): the
definition read and checked against the loop's paths before anything is saved, the loop then
written with `tracker` in place of `project`, its copy kept, and the bundle dropped once no loop
uses it - from definition.retarget and from `agentloop retarget`. Each test makes up a bundle and
serves its definition on a copy of Overseer."""

import json
import subprocess
from pathlib import Path

import pytest

from agentloop import cli, config, definition, loop
from test_definition import DESCRIPTION, closed_port, served
from test_loop import item


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


class Machine:
    """A machine with a bundle `proj` and a loop `proj` on it, whose worker reaches the served
    Overseer; the folders its project names, a place `inside` within the bundle's folder."""

    def __init__(self, tmp_path: Path, overseer, tracker: str, systems=("windows", "macos", "linux"), loops=None):
        self.tmp = tmp_path
        self.bundle = tmp_path / "bundles" / "proj"
        self.bundle.mkdir(parents=True)
        (self.bundle / "brief.md").write_text("A made-up brief.\n", encoding="utf-8")
        config.save_json(self.bundle / "project.json", {
            "format": 1, "name": "proj", "tracker": {"server": tracker},
            "repos": ["code", "docs"], "places": ["samples"], "systems": list(systems), "settings": {},
        })
        self.code = git_repo(tmp_path / "code")
        self.docs = git_repo(tmp_path / "docs")
        self.samples = self.bundle / "samples"
        self.samples.mkdir()
        self.paths = {"repos": {"code": str(self.code), "docs": str(self.docs)}, "places": {"samples": str(self.samples)}}
        config.save_json(config.paths().settings, {
            "format": 1, "bundles": [str(self.bundle)],
            "workers": [{"name": "w", "claude": None, "overseer": overseer.worker}],
            "loops": loops if loops is not None else [{"name": "proj", "project": "proj", "worker": "w", **self.paths}],
            "defaults": {},
        })

    def text(self) -> str:
        return config.paths().settings.read_text(encoding="utf-8")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))


def retarget(name="proj", address=None, **more):
    return definition.retarget(config.load_machine(), name, address, **more)


def test_a_loop_is_moved_to_its_tracker_with_its_paths_kept(home, tmp_path, overseer):
    tracker = served(overseer, item("alpha"))
    m = Machine(tmp_path, overseer, tracker)
    done = retarget()
    assert done["tracker"] == tracker and done["moved"] == ["proj"] and done["bundleDropped"] is True
    # The place inside the bundle's folder is named, since the bundle is going away.
    assert done["repoint"] == ["places.samples"]
    raw = json.loads(m.text())
    assert raw["bundles"] == []
    assert raw["loops"] == [{"name": "proj", "tracker": tracker, "worker": "w", **m.paths}]
    found = config.load_machine().settings.loop("proj")
    assert found.tracker == tracker and found.project == "proj" and found.overseer is None
    kept = definition.kept(loop.loop_dir("proj", "proj"), tracker)
    assert kept["definition"]["description"] == DESCRIPTION


def test_the_address_given_is_used_in_place_of_the_bundles(home, tmp_path, overseer):
    tracker = served(overseer, item("alpha"))
    Machine(tmp_path, overseer, "nobody/elsewhere/proj.os")
    assert retarget(address=f"{overseer.url}/doc/{tracker}")["tracker"] == tracker
    assert config.load_settings().loop("proj").tracker == tracker


def test_a_missing_path_is_refused_with_nothing_saved(home, tmp_path, overseer):
    tracker = served(overseer, item("alpha"))
    m = Machine(tmp_path, overseer, tracker, loops=[{"name": "proj", "project": "proj", "worker": "w",
                                                     "repos": {"code": str(tmp_path / "nope")}}])
    before = m.text()
    with pytest.raises(definition.Refusals) as caught:
        retarget()
    assert sorted(p.field for p in caught.value.problems) == ["places.samples", "repos.code", "repos.docs"]
    assert m.text() == before
    assert not (loop.loop_dir("proj", "proj") / definition.COPY).exists()


def test_a_system_the_project_does_not_run_on_is_refused(home, tmp_path, overseer):
    other = next(s for s in ("windows", "macos", "linux") if s != config.this_system())
    tracker = served(overseer, item("alpha"), systems=other)
    m = Machine(tmp_path, overseer, tracker)
    before = m.text()
    with pytest.raises(definition.Refusals) as caught:
        retarget()
    assert [p.field for p in caught.value.problems] == ["systems"]
    assert m.text() == before


def test_a_running_loop_is_refused(home, tmp_path, overseer):
    tracker = served(overseer, item("alpha"))
    m = Machine(tmp_path, overseer, tracker)
    before = m.text()
    with loop.file_lock(loop.lock_path("proj", "proj")):
        with pytest.raises(definition.Refusals) as caught:
            retarget()
    assert [p.field for p in caught.value.problems] == ["running"]
    assert "stop it first" in caught.value.problems[0].message
    assert m.text() == before


def test_an_unreachable_server_is_refused(home, tmp_path, overseer):
    m = Machine(tmp_path, overseer, "a/proj.os")
    before = m.text()
    with pytest.raises(definition.Unreachable) as caught:
        retarget(address=f"{closed_port()}/doc/a/proj.os", timeout=5)
    assert "127.0.0.1" not in str(caught.value)
    assert m.text() == before


def test_a_tracker_of_another_project_is_refused(home, tmp_path, overseer):
    tracker = served(overseer, item("alpha"))
    m = Machine(tmp_path, overseer, tracker)
    raw = json.loads(m.text())
    other = tmp_path / "bundles" / "other"
    other.mkdir()
    (other / "brief.md").write_text("x\n", encoding="utf-8")
    config.save_json(other / "project.json", {**json.loads((m.bundle / "project.json").read_text(encoding="utf-8")),
                                              "name": "other"})
    raw["bundles"].append(str(other))
    raw["loops"] = [{"name": "o", "project": "other", "worker": "w", **m.paths}]
    config.save_json(config.paths().settings, raw)
    before = m.text()
    with pytest.raises(definition.Refusals) as caught:
        retarget("o")
    assert [p.field for p in caught.value.problems] == ["tracker"]
    assert m.text() == before


def test_a_path_into_another_bundle_is_named_to_repoint(home, tmp_path, overseer):
    tracker = served(overseer, item("alpha"))
    m = Machine(tmp_path, overseer, tracker)
    tools = tmp_path / "bundles" / "engine" / "tools"
    tools.mkdir(parents=True)
    (tools.parent / "brief.md").write_text("x\n", encoding="utf-8")
    config.save_json(tools.parent / "project.json", {"format": 1, "name": "engine", "tracker": {"server": "a/engine.os"},
                                                     "repos": ["code"], "settings": {}})
    raw = json.loads(m.text())
    raw["bundles"].append(str(tools.parent))
    raw["loops"][0]["places"]["samples"] = str(tools)
    config.save_json(config.paths().settings, raw)
    done = retarget()
    assert done["repoint"] == ["places.samples"]
    assert json.loads(m.text())["bundles"] == [str(tools.parent)]


def test_a_loop_already_on_a_tracker_and_a_copy_are_refused(home, tmp_path, overseer):
    tracker = served(overseer, item("alpha"))
    m = Machine(tmp_path, overseer, tracker)
    raw = json.loads(m.text())
    raw["loops"].append({"name": "twin", "like": "proj"})
    config.save_json(config.paths().settings, raw)
    with pytest.raises(definition.Refusals) as caught:
        retarget("twin")
    assert caught.value.field == "like"
    with pytest.raises(definition.Refusals) as caught:
        retarget("nobody")
    assert caught.value.field == "name"


def test_the_bundle_stays_while_another_loop_uses_it(home, tmp_path, overseer):
    tracker = served(overseer, item("alpha"))
    m = Machine(tmp_path, overseer, tracker)
    raw = json.loads(m.text())
    raw["loops"].append({"name": "second", "project": "proj", "worker": "w", **m.paths})
    config.save_json(config.paths().settings, raw)
    assert retarget()["bundleDropped"] is False
    assert json.loads(m.text())["bundles"] == [str(m.bundle)]
    assert config.load_machine().settings.loop("second").tracker is None
    assert retarget("second")["bundleDropped"] is True
    assert json.loads(m.text())["bundles"] == []


def test_a_copy_moves_with_the_loop_it_is_like(home, tmp_path, overseer):
    tracker = served(overseer, item("alpha"))
    m = Machine(tmp_path, overseer, tracker)
    raw = json.loads(m.text())
    raw["loops"].append({"name": "twin", "like": "proj", "project": "proj"})
    config.save_json(config.paths().settings, raw)
    done = retarget()
    assert done["moved"] == ["proj", "twin"] and done["bundleDropped"] is True
    twin = config.load_machine().settings.loop("twin")
    assert twin.tracker == tracker and "project" not in json.loads(m.text())["loops"][1]
    assert definition.kept(loop.loop_dir("proj", "twin"), tracker) is not None


# The command

def test_the_command_retargets_and_names_what_to_repoint(home, tmp_path, overseer, capsys):
    tracker = served(overseer, item("alpha"))
    Machine(tmp_path, overseer, tracker)
    assert cli.main(["retarget", "proj"]) == 0
    out = capsys.readouterr().out
    assert f"on {tracker}" in out and "dropped from bundles" in out and "places.samples" in out


def test_the_command_says_each_refusal(home, tmp_path, overseer, capsys):
    tracker = served(overseer, item("alpha"))
    Machine(tmp_path, overseer, tracker, loops=[{"name": "proj", "project": "proj", "worker": "w", "repos": {}}])
    assert cli.main(["retarget", "proj"]) == 2
    err = capsys.readouterr().err
    assert "repos.code" in err and "repos.docs" in err and "places.samples" in err
