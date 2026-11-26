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
