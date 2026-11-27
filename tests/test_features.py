"""features.py on a tracker and its template served by a stub in this process, as tests/test_tracker.py
serves them: its commands read a tracker whose template is any version tracker.py knows, the
current one included (item features-template-v3), a newer one is refused with the line saying to
update AgentLoop, and the edits are the writes the server is asked for - an emptied field written
as the template's own value."""
import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "src" / "agentloop" / "tools"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tracker"
DOC = "agent/proj.os"
TEMPLATE = "library:projects/project_template.os"


def _load_features():
    spec = importlib.util.spec_from_file_location("features_under_test", TOOLS / "features.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


features = _load_features()
# The tracker module features.py imported itself, beside it in the tools folder.
tracker = features.tracker

# The fixture template has no Standings, which only features.py reads: the project template's four.
STANDINGS = """    list Standings (entry=<Label>, key="tag") {
        - {
            - tag = "proposed"
        }
        - {
            - tag = "wanted"
        }
        - {
            - tag = "accepted"
        }
        - {
            - tag = "discarded"
        }
    }
"""

FEATURES = """    - Features {
        - {
            - added = "2026-11-01T00:00:00+00:00"
            - handle = "SEARCH"
            - standing = "wanted"
            - title = "Find anything"
            - requires = "INDEX"
            - synergies = "TAGS"
            div Detail {
                - description = "Search over <$INDEX>."
                - pros = "Fast"
            }
        }
        - {
            - handle = "INDEX"
            - title = "An index of everything"
        }
        - {
            - handle = "TAGS"
            - standing = "accepted"
            - title = "Tags"
            - synergies = "SEARCH"
        }
    }
"""


def _template(version_line):
    text = (FIXTURES / "project_template.os").read_text(encoding="utf-8")
    text = text.replace("    list Features (", STANDINGS + "    list Features (", 1)
    return text.replace("    int template_version (hidden=true) = 2", version_line, 1)


class StubServer:
    """What Tracker asks of a server, answered from the fixture texts, with each write recorded."""

    url = "http://stub.invalid"

    def __init__(self, version):
        doc = (FIXTURES / "proj.os").read_text(encoding="utf-8")
        self.texts = {DOC: doc.replace("    - History {\n", FEATURES + "    - History {\n", 1),
                      TEMPLATE: _template(f"    int template_version (hidden=true) = {version}")}
        self.sets: list[tuple[str, str, dict]] = []
        self.appends: list[tuple[str, str, dict]] = []

    def source(self, name):
        return {"document": name, "version": "v1", "text": self.texts[name], "parts": {}, "bases": [TEMPLATE]}

    def set(self, name, address, value):
        self.sets.append((name, address, value))

    def append(self, name, address, fields):
        self.appends.append((name, address, fields))

    def documents(self):
        return [n for n in self.texts if not n.startswith("library:")]

    def me(self):
        return "agent"


def _run(monkeypatch, capsys, server, *argv):
    """features.py's main with these arguments, on the stub: what it prints."""
    monkeypatch.setattr(tracker, "open_tracker", lambda doc: tracker.Tracker(DOC, server))
    monkeypatch.setattr(sys, "argv", ["features.py", *argv, "--doc", DOC])
    features.main()
    return capsys.readouterr().out


@pytest.mark.parametrize("version", tracker.TEMPLATE_VERSIONS)
def test_list_and_show_read_every_known_template_version(version, monkeypatch, capsys):
    server = StubServer(version)
    listed = json.loads(_run(monkeypatch, capsys, server, "list", "--json"))
    assert listed == {"proposed": [{"handle": "INDEX", "title": "An index of everything"}],
                      "wanted": [{"handle": "SEARCH", "title": "Find anything"}],
                      "accepted": [{"handle": "TAGS", "title": "Tags"}],
                      "discarded": []}
    shown = json.loads(_run(monkeypatch, capsys, server, "show", "SEARCH", "--json"))
    assert (shown["standing"], shown["requires"], shown["pros"]) == ("wanted", "INDEX", "Fast")
    assert shown["named_by"]["synergies"] == ["TAGS"]
    assert server.sets == [] and server.appends == []


def test_the_current_template_version_is_known():
    # The project template Overseer serves is version 3 since its review flag (overseer reviewflag).
    assert 3 in tracker.TEMPLATE_VERSIONS


@pytest.mark.parametrize("line", [f"    int template_version (hidden=true) = {tracker.TEMPLATE_VERSIONS[-1] + 1}",
                                  "    int template_version (hidden=true) = 0"])
def test_an_unknown_template_version_stops_a_command_that_reads_the_template(line, monkeypatch, capsys):
    # The template is read where the tracker leaves something to it - the standings, for list and
    # add - as tracker.py reads it, and checked then.
    server = StubServer(2)
    server.texts[TEMPLATE] = _template(line)
    for argv in (["list"], ["add", "SHARE", "Share a view"]):
        with pytest.raises(SystemExit) as stopped:
            _run(monkeypatch, capsys, server, *argv)
        assert isinstance(stopped.value.code, str) and "template" in stopped.value.code
    if "= 0" not in line:
        assert "update AgentLoop" in stopped.value.code
    assert server.sets == [] and server.appends == []


@pytest.fixture
def current(monkeypatch, capsys):
    """A run of features.py on a tracker whose template is the newest version tracker.py knows."""
    server = StubServer(tracker.TEMPLATE_VERSIONS[-1])
    return server, lambda *argv: _run(monkeypatch, capsys, server, *argv)


def test_set_writes_an_own_field_and_a_detail_text(current):
    server, run = current
    run("set", "INDEX", "title", "The index")
    run("set", "SEARCH", "cons", "Slow to build")
    run("set", "SEARCH", "pros", "")
    assert server.sets == [(DOC, "project/Features/[INDEX]/title", {"String": "The index"}),
                           (DOC, "project/Features/[SEARCH]/Detail/cons", {"String": "Slow to build"}),
                           (DOC, "project/Features/[SEARCH]/Detail/pros", {"String": ""})]


def test_set_standing_back_to_its_default_gives_the_template_its_value(current):
    server, run = current
    assert "accepted -> wanted" in run("set", "TAGS", "standing", "wanted")
    run("set", "SEARCH", "standing", "proposed")
    assert [(a, v) for _, a, v in server.sets] == [("project/Features/[TAGS]/standing", {"String": "wanted"}),
                                                   ("project/Features/[SEARCH]/standing", {"String": "proposed"})]


def test_set_refuses_a_field_or_standing_it_does_not_know(current):
    server, run = current
    for argv in (["set", "SEARCH", "x", "1"], ["set", "SEARCH", "standing", "maybe"]):
        with pytest.raises(SystemExit):
            run(*argv)
    assert server.sets == []


def test_link_of_a_two_way_relation_writes_both_features(current):
    server, run = current
    run("link", "INDEX", "incompatible", "TAGS")
    assert [(a, v) for _, a, v in server.sets] == [("project/Features/[INDEX]/incompatible", {"String": "TAGS"}),
                                                   ("project/Features/[TAGS]/incompatible", {"String": "INDEX"})]


def test_unlink_of_a_one_way_relation_writes_only_the_one(current):
    server, run = current
    run("unlink", "SEARCH", "requires", "INDEX")
    assert [(a, v) for _, a, v in server.sets] == [("project/Features/[SEARCH]/requires", {"String": ""})]


def test_add_appends_a_feature_in_the_templates_field_order(current):
    server, run = current
    assert run("add", "SHARE", "Share a view", "--standing", "wanted").startswith("added SHARE at wanted")
    [(doc, address, fields)] = server.appends
    assert (doc, address) == (DOC, "project/Features")
    assert list(fields) == ["added", "handle", "standing", "title"]
    assert fields["handle"] == {"String": "SHARE"} and fields["title"] == {"String": "Share a view"}
