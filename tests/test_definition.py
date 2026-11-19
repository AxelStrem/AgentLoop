"""A project read from its tracker's address (item doc-read): the definition the tracker mounts,
read over a served copy of overseer-server, checked, kept in the loop's folder for when the server
cannot be reached, and given to a session as the one description in its system prompt. A local
bundle goes on working beside it."""

import json
import socket
from pathlib import Path

import pytest

from agentloop import config, definition, loop, session
from test_loop import Project, item, move, project, tracker_text  # noqa: F401 - project is a fixture

DESCRIPTION = ("A made-up project for the tests.\n\n**Checking work.** `pytest` in `{code}`, "
               "and the samples in `{samples}`.")


def definition_text(description: str = DESCRIPTION, repos=(("code", "The code"), ("docs", "")),
                    places=(("samples", "Files to check against"),), systems: str = "windows, macos, linux",
                    settings: str = '{"chainUpTo": 1}', tools=()) -> str:
    """A definition document made from the library's template, as `<name>.project.os` holds it.
    `tools` are (file, sha256, systems) for its Tools list, left out when there are none."""
    def entries(pairs):
        # (name, about), and for a repository optionally its url and branch after them.
        lines = []
        for name, about, *more in pairs:
            lines += ["        - {", f"            - name = {json.dumps(name)}"]
            for field, value in zip(("about", "url", "branch"), (about, *more)):
                if value:
                    lines.append(f"            - {field} = {json.dumps(value)}")
            lines.append("        }")
        return lines
    return "\n".join([
        "<projects/project_definition.os/definition> definition {",
        f"    - description = {json.dumps(description)}",
        "    - Repos {", *entries(repos), "    }",
        "    - Places {", *entries(places), "    }",
        *(["    - Tools {", *[line for file, sha, on in tools for line in (
            "        - {", f"            - file = {json.dumps(file)}", f"            - sha256 = {json.dumps(sha)}",
            f"            - systems = {json.dumps(on)}", "        }")], "    }"] if tools else []),
        f"    - systems = {json.dumps(systems)}",
        f"    - settings = {json.dumps(settings)}",
        "}",
        "",
    ])


def served(overseer, *items: str, with_definition: bool = True, **definition_args) -> str:
    """A tracker `proj.os` that mounts `proj.project.os` beside it, in a folder of its own on the
    server: the tracker's name there."""
    folder = overseer.folder()
    text = tracker_text(*items)
    if with_definition:
        text = text.replace('project (label="Proj") {\n',
                            'project (label="Proj") {\n    - definition (source="proj.project.os/definition", lazy=false)\n', 1)
        overseer.document(definition_text(**definition_args), project="proj.project", folder=folder)
    return overseer.document(text, project="proj", folder=folder)


def closed_port() -> str:
    """An address on loopback that nothing listens on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


# Reading it

def test_the_definition_is_read_through_its_tracker(overseer):
    tracker = served(overseer, item("alpha"))
    raw = definition.fetch(overseer.url, overseer.key, tracker)
    assert raw == {
        "description": DESCRIPTION,
        "repos": [{"name": "code", "about": "The code"}, {"name": "docs"}],
        "places": [{"name": "samples", "about": "Files to check against"}],
        "systems": ["windows", "macos", "linux"],
        "settings": {"chainUpTo": 1},
    }
    found = definition.project(tracker, raw)
    assert (found.name, found.source, found.folder, found.tracker.server) == ("proj", "tracker", None, tracker)
    assert found.repos == ["code", "docs"] and found.places == ["samples"]
    assert found.about == {"code": "The code", "samples": "Files to check against"}
    assert found.description == DESCRIPTION and found.settings == {"chainUpTo": 1}
    assert found.version == definition.version(raw)


def test_empty_systems_are_every_system(overseer):
    tracker = served(overseer, item("alpha"), systems="")
    assert definition.fetch(overseer.url, overseer.key, tracker)["systems"] is None


@pytest.mark.parametrize("args, field", [
    ({"repos": (("Bad Name", ""),)}, "repos[0].name"),
    ({"repos": ()}, "repos"),
    ({"places": (("samples", ""), ("samples", ""))}, "places"),
    ({"places": (("code", ""),)}, "places[0]"),
    ({"systems": "windows, plan9"}, "systems"),
    ({"settings": "{not json"}, "settings"),
    ({"settings": '{"nope": 1}'}, "settings.nope"),
    ({"settings": '{"chainUpTo": "three"}'}, "settings.chainUpTo"),
])
def test_a_definition_that_fails_its_checks_names_the_field(overseer, args, field):
    tracker = served(overseer, item("alpha"), **args)
    with pytest.raises(config.ConfigError) as caught:
        definition.fetch(overseer.url, overseer.key, tracker)
    assert caught.value.field == field
    assert caught.value.where == f"{tracker} definition"


def test_a_tracker_without_a_definition_says_so(overseer):
    tracker = served(overseer, item("alpha"), with_definition=False)
    with pytest.raises(config.ConfigError) as caught:
        definition.fetch(overseer.url, overseer.key, tracker)
    assert caught.value.field == "definition"
    assert "proj.project.os" in caught.value.message


def test_a_server_that_cannot_be_reached_is_unreachable_and_names_no_address(tmp_path):
    key = tmp_path / "key"
    key.write_text("k", encoding="utf-8")
    address = closed_port()
    with pytest.raises(definition.Unreachable) as caught:
        definition.fetch(address, key, "a/proj.os", timeout=5)
    assert "127.0.0.1" not in str(caught.value)


# The settings

def settings_with(loops: list[dict], tmp_path: Path) -> config.Settings:
    return config.parse_settings({"format": 1, "bundles": [], "workers": [{"name": "w", "claude": None, "overseer": None}],
                                  "loops": loops}, tmp_path / "settings.json")


def test_a_loop_names_a_tracker_in_place_of_a_project(tmp_path):
    settings = settings_with([{"name": "one", "tracker": "alice/projects/proj.os", "worker": "w"},
                              {"name": "two", "like": "one", "repos": {"code": "x"}}], tmp_path)
    one, two = settings.loops
    assert (one.project, one.tracker) == ("proj", "alice/projects/proj.os")
    assert (two.project, two.tracker) == ("proj", "alice/projects/proj.os")


@pytest.mark.parametrize("loop, field", [
    ({"name": "one", "tracker": "alice/projects/proj.os", "project": "proj", "worker": "w"}, "loops[0].tracker"),
    ({"name": "one", "tracker": "alice/projects/proj.txt", "worker": "w"}, "loops[0].tracker"),
    ({"name": "one", "tracker": "alice/projects/Big Name.os", "worker": "w"}, "loops[0].tracker"),
    ({"name": "one", "worker": "w"}, "loops[0].project"),
])
def test_a_loop_tracker_that_is_wrong_is_refused(tmp_path, loop, field):
    with pytest.raises(config.ConfigError) as caught:
        settings_with([loop], tmp_path)
    assert caught.value.field == field


def test_a_copy_may_not_name_another_tracker(tmp_path):
    with pytest.raises(config.ConfigError) as caught:
        settings_with([{"name": "one", "tracker": "a/proj.os", "worker": "w"},
                       {"name": "two", "like": "one", "tracker": "a/other.os"}], tmp_path)
    assert caught.value.field == "loops[1].tracker"


def test_a_bundle_gives_what_each_repository_and_place_is(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    config.save_json(folder / "project.json", {
        "format": 1, "name": "proj", "tracker": {"server": "a/proj.os"},
        "repos": [{"name": "code", "about": "The code"}, "docs"], "places": [{"name": "samples"}]})
    found = config.load_bundle(folder)
    assert (found.repos, found.places, found.about) == (["code", "docs"], ["samples"], {"code": "The code"})
    assert found.source == "bundle"


# The system file

def test_the_system_file_holds_the_one_description(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    where = session.where_you_are(None, {"code": code}, {}, {"code": "The code"})
    assert "The project's bundle" not in where
    assert f"  - `code`: `{str(code.resolve()).replace(chr(92), '/')}` - The code\n" in where
    values = session.placeholders(None, {"code": code}, {})
    path = session.write_system_file(tmp_path / "system.md", "guide", "In {code}, not {bundle}.", None, where=where, values=values)
    text = path.read_text(encoding="utf-8")
    assert text.endswith(f"# What the project is\n\nIn {values['code']}, not {{bundle}}.\n")
    assert "as its tracker describes it" not in text


# The loop

class TrackerProject(Project):
    """test_loop's project, its loop reading the project from its tracker's definition instead of
    the bundle."""

    def __init__(self, tmp_path, monkeypatch, fake_claude, overseer, items, **definition_args):
        super().__init__(tmp_path, monkeypatch, fake_claude, overseer, items)
        self.tracker = served(overseer, *items, **definition_args)
        self.samples = tmp_path / "samples"
        self.samples.mkdir()
        self.docs = self.code
        path = config.paths().settings
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["bundles"] = []
        raw["loops"] = [{"name": "proj", "tracker": self.tracker, "worker": "w",
                         "repos": {"code": str(self.code), "docs": str(self.code)},
                         "places": {"samples": str(self.samples)}, "settings": self.settings}]
        config.save_json(path, raw)

    def point_at(self, server: str) -> None:
        """The worker's server moved somewhere that cannot be reached."""
        path = config.paths().settings
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["workers"][0]["overseer"] = {**raw["workers"][0]["overseer"], "server": server}
        config.save_json(path, raw)


@pytest.fixture
def tracker_project(tmp_path, monkeypatch, fake_claude, overseer):
    def make(*items: str, **definition_args) -> TrackerProject:
        return TrackerProject(tmp_path, monkeypatch, fake_claude, overseer, list(items), **definition_args)
    return make


def test_a_loop_on_a_tracker_runs_on_its_definition(tracker_project, fake_claude):
    p = tracker_project(item("alpha", "ready", complexity="low"))
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    assert p.go() == 0
    assert p.show("alpha")["stage"] == "testing"
    assert p.r.bundle.source == "tracker" and p.r.settings["chainUpTo"] == 1
    # The definition beside the tracker is no project beside it, for tracker.py or the prompt.
    assert p.r.beside() == []
    system = p.r.system_file.read_text(encoding="utf-8")
    slashed = lambda path: str(path.resolve()).replace("\\", "/")
    filled = DESCRIPTION.replace("{code}", slashed(p.code)).replace("{samples}", slashed(p.samples))
    assert system.count("# What the project is") == 1
    assert system.endswith(f"# What the project is\n\n{filled}\n")
    # The tracker's own description is not asked for: the definition is the one text.
    assert "A project for tests." not in system
    assert f"  - `code`: `{slashed(p.code)}` - The code\n" in system
    assert f"  - `samples`: `{slashed(p.samples)}` - Files to check against" in system
    assert "The project's bundle" not in system
    kept = json.loads((p.r.loop_dir / definition.COPY).read_text(encoding="utf-8"))
    assert kept["tracker"] == p.tracker and kept["definition"]["description"] == DESCRIPTION
    assert kept["version"] == p.r.bundle.version


def test_a_run_starts_from_the_copy_when_the_server_cannot_be_reached(tracker_project, fake_claude):
    p = tracker_project(item("alpha", "ready", complexity="low"))
    first = p.run()
    assert first.definition_note is None
    p.point_at(closed_port())
    r = p.run()
    assert r.bundle.source == "tracker" and r.bundle.description == DESCRIPTION
    assert "could not be read" in r.definition_note and "starts from the copy read" in r.definition_note
    assert "127.0.0.1" not in r.definition_note
    r._prepare()
    assert any("starts from the copy" in line for line in p.lines)
    assert "# What the project is" in r.system_file.read_text(encoding="utf-8")


def test_a_run_with_no_copy_and_no_server_does_not_start(tracker_project, fake_claude):
    p = tracker_project(item("alpha", "ready", complexity="low"))
    p.point_at(closed_port())
    with pytest.raises(loop.LoopError) as caught:
        p.run()
    assert "keeps no copy" in str(caught.value) and "does not start" in str(caught.value)


def test_a_definition_that_fails_its_checks_stops_the_loop_even_with_a_copy(tracker_project, fake_claude, overseer):
    p = tracker_project(item("alpha", "ready", complexity="low"))
    p.run()
    (overseer.root / p.tracker.replace("proj.os", "proj.project.os")).write_text(
        definition_text(settings='{"nope": 1}'), encoding="utf-8", newline="\n")
    with pytest.raises(loop.LoopError) as caught:
        p.run()
    assert "settings.nope" in str(caught.value)


def test_a_loop_on_a_local_bundle_still_runs(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"))
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    assert p.go() == 0
    assert p.r.bundle.source == "bundle"
    assert not (p.r.loop_dir / definition.COPY).exists()
    system = p.r.system_file.read_text(encoding="utf-8")
    assert "# What the project is, as its tracker describes it\n\nA project for tests.\n\n---\n\n# The brief\n" in system
