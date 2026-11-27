"""tracker.py on a tracker and its template read from fixture files: the items and the template's
stages and complexities, the template version check, and claim and release as the writes they ask
of the server. The server is a stub in this process that serves the fixtures and records what it
is asked, so no overseer-server and no network is needed."""
import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "src" / "agentloop" / "tools"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tracker"
DOC = "agent/proj.os"
TEMPLATE = "library:projects/project_template.os"


def _load_tracker():
    spec = importlib.util.spec_from_file_location("tracker_under_test", TOOLS / "tracker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tracker = _load_tracker()


class StubServer:
    """What Tracker asks of a server, answered from the fixture texts, with each write recorded."""

    url = "http://stub.invalid"

    def __init__(self, template: str | None = None):
        self.texts = {DOC: (FIXTURES / "proj.os").read_text(encoding="utf-8"),
                      TEMPLATE: template or (FIXTURES / "project_template.os").read_text(encoding="utf-8")}
        self.events: list[tuple[str, str, str]] = []
        self.sets: list[tuple[str, str, dict]] = []

    def source(self, name):
        return {"document": name, "version": "v1", "text": self.texts[name], "parts": {}, "bases": [TEMPLATE]}

    def event(self, name, address, event):
        self.events.append((name, address, event))

    def set(self, name, address, value):
        self.sets.append((name, address, value))

    def me(self):
        return "agent"


@pytest.fixture
def server():
    return StubServer()


def test_items_and_the_templates_vocabularies(server):
    t = tracker.Tracker(DOC, server)
    items = {f["handle"]: f for _, _, f in t.items()}
    assert list(items) == ["alpha", "beta", "gamma", "delta"]
    # A field the entry does not write has the template's value.
    assert (items["alpha"]["stage"], items["alpha"]["complexity"]) == ("filed", "unassigned")
    assert items["beta"]["stage"] == "ready"
    assert t.stages() == ["filed", "planning", "ready", "testing", "parts", "later", "finished", "cancelled"]
    assert t.complexities() == ["unassigned", "trivial", "low", "medium", "high"]
    assert tracker.claimed(items["beta"]) and not tracker.claimed(items["alpha"])
    assert tracker.held(items["delta"]) == ["asked"]
    assert tracker.waiting_on(t, items["delta"]) == ["alpha"]


@pytest.mark.parametrize("version", tracker.TEMPLATE_VERSIONS)
def test_a_known_template_version_is_read(version):
    template = (FIXTURES / "project_template.os").read_text(encoding="utf-8")
    template = template.replace("template_version (hidden=true) = 2", f"template_version (hidden=true) = {version}")
    assert tracker.Tracker(DOC, StubServer(template)).stages()[0] == "filed"


@pytest.mark.parametrize("line", ["int template_version (hidden=true) = 0",
                                  f"int template_version (hidden=true) = {tracker.TEMPLATE_VERSIONS[-1] + 1}",
                                  ""])
def test_an_unknown_template_version_is_refused(line):
    template = (FIXTURES / "project_template.os").read_text(encoding="utf-8")
    template = template.replace("int template_version (hidden=true) = 2", line)
    with pytest.raises(SystemExit) as stopped:
        tracker.Tracker(DOC, StubServer(template)).stages()
    # Refused with a sentence, not an exit code alone.
    assert isinstance(stopped.value.code, str)


def _claim(server, handle, capsys):
    tracker.claim(tracker.Tracker(DOC, server), Namespace(handle=handle, worker="opus (medium)", hold_only=False))
    return json.loads(capsys.readouterr().out)


def test_claim_presses_the_button_and_names_the_worker(server, capsys):
    assert _claim(server, "alpha", capsys) == {"handle": "alpha", "claimed": True}
    assert server.events == [(DOC, "project/Items/[alpha]/claim", "click")]
    assert (DOC, "project/Items/[alpha]/worker", {"String": "opus (medium)"}) in server.sets
    # An item for no one becomes the claiming account's.
    assert (DOC, "project/Items/[alpha]/assignee", {"String": "agent"}) in server.sets


def test_claim_refuses_an_item_another_loop_has(server, capsys):
    answer = _claim(server, "beta", capsys)
    assert answer["claimed"] is False and answer["refused"] == tracker.TAKEN
    assert server.events == [] and server.sets == []


def test_claim_refuses_an_item_for_another_account(server, capsys):
    assert _claim(server, "gamma", capsys)["claimed"] is False
    assert server.events == [] and server.sets == []


def test_release_lets_go_of_a_claimed_item(server, capsys):
    tracker.release(tracker.Tracker(DOC, server), Namespace(handle="beta"))
    written = {address.rsplit("/", 1)[1]: value for _, address, value in server.sets}
    assert written["working"] == {"Boolean": False}
    assert written["worker"] == {"String": ""}
    assert capsys.readouterr().out.strip() == "beta: released"


def test_release_of_a_free_item_writes_nothing(server, capsys):
    tracker.release(tracker.Tracker(DOC, server), Namespace(handle="alpha"))
    assert server.sets == []


def _voiced(handle, stage, flags):
    """An item with a voice message marked for transcription, at that stage and flagged so."""
    return f"""        - {{
            - added = "2026-10-01T00:00:00+00:00"
            - handle = "{handle}"
            - title = "The {handle} item"
            - stage = "{stage}"
            - flags = "{flags}"
            div Talk {{
                list Comments {{
                    - {{
                        - at = "2026-10-02T00:00:00+00:00"
                        - author = "owner"
                        - body = "![transcribe](sounds/proj/2026-10-02.webm)"
                    }}
                }}
            }}
        }}
"""


def test_voice_is_heard_on_a_manual_item_but_not_an_asked_or_testing_one(capsys):
    # Item voice-held-stages: what a person said on an item for a person to work is transcribed;
    # one waiting on the person's own answer, or at testing, waits until it is back.
    server = StubServer()
    server.people = lambda t: []  # the fixture's gamma has an assignee, so the queue asks who is who
    items = (_voiced("manual", "planning", "manual") + _voiced("asked", "planning", "asked") +
             _voiced("both", "filed", "manual, asked") + _voiced("tried", "testing", "") +
             _voiced("plain", "ready", ""))
    server.texts[DOC] = server.texts[DOC].replace("    - Items {\n", "    - Items {\n" + items, 1)
    t = tracker.Tracker(DOC, server)
    q = tracker.Queue(t)
    assert sorted(f["handle"] for _, f in q.unheard) == ["manual", "plain"]
    assert {"asked", "both"} <= {f["handle"] for _, f in q.held}
    assert "tried" not in {f["handle"] for _, f in q.free + q.held + q.claimed}
    tracker.next_item(t, Namespace(owner=None, prefer=None))
    assert sorted(json.loads(capsys.readouterr().out)["unheard"]) == ["manual", "plain"]


DISCUSSION = """    - Discussion {
        - {
            - handle = "naming"
            - title = "How things are named"
        }
        - {
            - handle = "alpha"
            - title = "A topic sharing alpha's handle"
        }
    }
"""


def test_show_titles_items_and_discussion_topics_by_their_marks():
    # Item topic-mentions: a topic is named <:handle>, an item <#handle>, and the mark decides
    # which is meant - so a handle both have takes the title of the one its mark names.
    server = StubServer()
    server.texts[DOC] = server.texts[DOC].replace("    - History {\n", DISCUSSION + "    - History {\n", 1)
    t = tracker.Tracker(DOC, server)
    text = "See <:naming>, <#alpha>, <:alpha>, <#naming>, <:nowhere> and `<:naming>`."
    assert tracker.with_titles(t, text) == (
        "See <:naming> (How things are named), <#alpha> (The alpha item), "
        "<:alpha> (A topic sharing alpha's handle), <#naming>, <:nowhere> and `<:naming>`.")


def test_a_topic_mention_in_a_tracker_with_no_discussion_stays_as_it_is(server):
    t = tracker.Tracker(DOC, server)
    assert tracker.with_titles(t, "<:naming> and <#alpha>") == "<:naming> and <#alpha> (The alpha item)"


# Item trackerfeatures: tracker.py shows, names and comments on Discussion topics and Features.
FEATURES = """    - Features {
        - {
            - added = "2026-11-01T00:00:00+00:00"
            - handle = "SEARCH"
            - standing = "wanted"
            - title = "Find anything"
            - requires = "INDEX"
            - synergies = "TAGS"
            - x = 10
            - y = 20
            div Detail {
                - description = "Search over <$INDEX> and <#alpha>."
                - pros = "Fast"
            }
            div Talk {
                list Comments {
                    - {
                        - at = "2026-11-02T00:00:00+00:00"
                        - author = "owner"
                        - body = "Wait for <$INDEX>; see <:naming>."
                    }
                }
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
        - {
            - handle = "LATER"
            - title = "Needs search"
            - requires = "SEARCH"
        }
    }
"""


class WritingServer(StubServer):
    """The stub, with what tracker.py appends recorded too, and a project beside the tracker."""

    def __init__(self, template=None):
        super().__init__(template)
        self.appends: list[tuple[str, str, dict]] = []
        self.texts[DOC] = self.texts[DOC].replace("    - History {\n", DISCUSSION + FEATURES + "    - History {\n", 1)

    def append(self, name, address, fields):
        self.appends.append((name, address, fields))

    def documents(self):
        return [n for n in self.texts if not n.startswith("library:")]


def _shown(server, handle, capsys, as_json=False):
    t = tracker.Tracker(DOC, server)
    tracker.show(t, Namespace(handle=handle, json=as_json, doc=DOC))
    out = capsys.readouterr().out
    return json.loads(out) if as_json else out


def test_show_prints_a_topic_with_its_mark_and_no_work(capsys):
    out = _shown(WritingServer(), "naming", capsys)
    assert "title      How things are named" in out
    assert "kind       topic - a Discussion topic, named <:naming> in text" in out
    assert "stage" not in out and "related" not in out
    assert "conversation, oldest first: nothing said yet" in out


def test_show_prints_a_feature_its_texts_and_the_features_around_it(capsys):
    out = _shown(WritingServer(), "SEARCH", capsys)
    assert "standing   wanted" in out and "named <$SEARCH> in text" in out
    assert not any(line.startswith(("x ", "y ", "shape")) for line in out.splitlines())
    assert "## description\n\nSearch over <$INDEX> (An index of everything) and <#alpha> (The alpha item)." in out
    assert "## pros\n\nFast" in out and "## cons\n\n(nothing written)" in out
    rows = [line.split() for line in out.splitlines() if line.startswith("  ")]
    assert ["needs", "INDEX", "proposed", "An", "index", "of", "everything"] in rows
    assert ["goes", "with", "TAGS", "accepted", "Tags"] in rows
    assert ["needed", "by", "LATER", "proposed", "Needs", "search"] in rows
    # TAGS names SEARCH back as it is named, so it is not listed twice.
    assert sum(r[2:3] == ["TAGS"] or r[1:2] == ["TAGS"] for r in rows) == 1
    assert "Wait for <$INDEX> (An index of everything); see <:naming> (How things are named)." in out


def test_show_json_gives_the_kind(capsys):
    server = WritingServer()
    feature = _shown(server, "SEARCH", capsys, as_json=True)
    assert feature["kind"] == "feature" and feature["description"].startswith("Search over")
    assert {r["relation"] for r in feature["related"]} == {"needs", "goes with", "needed by"}
    assert feature["comments"][0]["author"] == "owner" and "text" in feature
    assert _shown(server, "naming", capsys, as_json=True)["kind"] == "topic"
    assert _shown(server, "alpha", capsys, as_json=True)["kind"] == "item"


def test_a_feature_handle_is_matched_as_written(capsys):
    with pytest.raises(SystemExit, match="in capitals"):
        _shown(WritingServer(), "search", capsys)
    t = tracker.Tracker(DOC, WritingServer())
    assert tracker.with_titles(t, "<$search>, <$NOPE> and `<$SEARCH>`") == "<$search>, <$NOPE> and `<$SEARCH>`"


def test_a_feature_named_as_an_item_was_is_still_titled():
    # Comments written before overseer:feature-mark named features <#HANDLE>.
    t = tracker.Tracker(DOC, WritingServer())
    assert tracker.with_titles(t, "<#TAGS> and <$TAGS>") == "<#TAGS> (Tags) and <$TAGS> (Tags)"


def test_note_comments_on_a_topic_and_a_feature(capsys):
    server = WritingServer()
    t = tracker.Tracker(DOC, server)
    tracker.note(t, Namespace(handle="naming", text="A view on naming."))
    tracker.note(t, Namespace(handle="SEARCH", text="A view on search."))
    tracker.note(t, Namespace(handle="alpha", text="On the item."))
    addresses = [a for _, a, _ in server.appends]
    assert addresses == ["project/Discussion/[naming]/Talk/Comments",
                         "project/Features/[SEARCH]/Talk/Comments",
                         "project/Items/[alpha]/Talk/Comments"]
    assert "comment added" in capsys.readouterr().out


def test_a_topic_or_a_feature_is_never_moved(capsys):
    t = tracker.Tracker(DOC, WritingServer())
    for handle in ("naming", "SEARCH"):
        with pytest.raises(SystemExit, match="no open item"):
            tracker.move(t, Namespace(handle=handle, stage="ready", complexity="low", note=None, points=None))
    assert "naming" not in {f["handle"] for _, _, f in t.items()}


def test_another_projects_topic_is_read_there(capsys):
    server = WritingServer()
    server.texts["agent/other.os"] = server.texts[DOC]
    out = _shown(server, "other:naming", capsys)
    assert "project    other - another project's topic" in out


def test_a_tracker_whose_template_has_neither_list_still_reads(capsys):
    template = (FIXTURES / "project_template.os").read_text(encoding="utf-8")
    template = "\n".join(l for l in template.splitlines() if "list Discussion" not in l and "list Features" not in l)
    t = tracker.Tracker(DOC, StubServer(template))
    assert t.topics() == [] and t.features() == []
    assert tracker.with_titles(t, "<$SEARCH> and <:naming>") == "<$SEARCH> and <:naming>"
    with pytest.raises(SystemExit, match="no item, topic or feature nowhere"):
        tracker.show(t, Namespace(handle="nowhere", json=False, doc=DOC))
