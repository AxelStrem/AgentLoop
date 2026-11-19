"""A changed project definition (item doc-change): seen between sessions by the definition
document's version, shown as a diff, and either used at once (acceptDefinition "auto", the default)
or held until it is accepted ("ask"). A repository or place with no path holds the loop either way;
a system the machine is not on stops it."""

import json

import pytest

from agentloop import cli, config, definition, usage
from test_definition import DESCRIPTION, TrackerProject, definition_text, served
from test_loop import FakeClock, item, make_repo, move


def other_system() -> str:
    return next(s for s in config.SYSTEMS if s != config.this_system())


class Changing(TrackerProject):
    def __init__(self, *args, accept: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        if accept:
            self.set_loop(settings={**self.settings, "acceptDefinition": accept})

    @property
    def document(self):
        return self.overseer.root / definition.document(self.tracker)

    def change(self, **definition_args) -> None:
        """The definition on the server, written again."""
        self.document.write_text(definition_text(**definition_args), encoding="utf-8", newline="\n")

    def set_loop(self, **fields) -> None:
        path = config.paths().settings
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["loops"][0].update(fields)
        config.save_json(path, raw)

    def after_first_session(self, then) -> None:
        """`then` done once the run's first session has ended."""
        r = self.r
        invoke = r.invoke_item
        done = []

        def invoked(*args, **kwargs):
            ok = invoke(*args, **kwargs)
            if not done:
                done.append(True)
                then()
            return ok
        r.invoke_item = invoked

    def while_held(self, *hooks) -> list[str]:
        """Each sleep of the loop while it is held does the next of `hooks`; the states it was in
        at each, returned as they come."""
        left = list(hooks)
        states: list[str] = []
        moved = FakeClock.sleep

        def sleep(seconds: float) -> None:
            if seconds == usage.POLL_SECONDS and left and self.r.status.get("state") == "held":
                states.append(self.r.status.get("state"))
                left.pop(0)()
            moved(self.clock, seconds)
        self.clock.sleep = sleep
        return states

    @property
    def folder(self):
        return self.r.loop_dir


@pytest.fixture
def changing(tmp_path, monkeypatch, fake_claude, overseer):
    def make(*items: str, accept: str | None = None, **definition_args) -> Changing:
        return Changing(tmp_path, monkeypatch, fake_claude, overseer, list(items), accept=accept, **definition_args)
    return make


def two_sessions(p: Changing) -> None:
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"),
           p.scene(move("beta", "testing"), result="COMMIT: beta: built"))


ITEMS = (item("alpha", "ready", complexity="low"), item("beta", "ready", complexity="low"))


# The version, and the diff

def test_the_definition_document_answers_304_while_it_has_not_changed(overseer):
    tracker = served(overseer, item("alpha"))
    key = overseer.key
    changed, tag = definition.source_tag(overseer.url, key, tracker, None)
    assert changed and tag
    assert definition.source_tag(overseer.url, key, tracker, tag) == (False, tag)
    (overseer.root / definition.document(tracker)).write_text(definition_text(description="Another."), encoding="utf-8", newline="\n")
    changed, again = definition.source_tag(overseer.url, key, tracker, tag)
    assert changed and again and again != tag


def test_a_definition_may_not_say_how_its_changes_are_accepted(overseer):
    tracker = served(overseer, item("alpha"), settings='{"acceptDefinition": "auto"}')
    with pytest.raises(config.ConfigError) as caught:
        definition.fetch(overseer.url, overseer.key, tracker)
    assert caught.value.field == "settings.acceptDefinition"


def test_accept_definition_is_auto_or_ask(tmp_path):
    raw = {"format": 1, "bundles": [], "workers": [], "loops": [], "defaults": {"acceptDefinition": "sometimes"}}
    with pytest.raises(config.ConfigError) as caught:
        config.parse_settings(raw, tmp_path / "settings.json")
    assert caught.value.field == "defaults.acceptDefinition"
    assert config.DEFAULTS["acceptDefinition"] == "auto"


def test_the_diff_names_what_changed():
    old = {"description": "One.\nTwo.\nThree.", "repos": [{"name": "code", "about": "The code"}, {"name": "docs"}],
           "places": [{"name": "samples"}], "systems": None, "settings": {"chainUpTo": 1, "maxIterations": 5}}
    new = {"description": "One.\nTwo, changed.\nThree.", "repos": [{"name": "code", "about": "All the code"}, {"name": "extra"}],
           "places": [{"name": "samples"}], "systems": ["linux"], "settings": {"chainUpTo": 2, "push": True}}
    d = definition.diff(old, new)
    assert d["description"] == ["@@ -1,3 +1,3 @@", " One.", "-Two.", "+Two, changed.", " Three."]
    assert d["repos"] == {"added": [{"name": "extra", "about": ""}], "removed": [{"name": "docs", "about": ""}],
                          "changed": [{"name": "code", "old": "The code", "new": "All the code"}]}
    assert d["places"] == {"added": [], "removed": [], "changed": []}
    assert d["systems"] == {"old": None, "new": ["linux"]}
    assert d["settings"] == {"added": {"push": True}, "removed": {"maxIterations": 5},
                             "changed": {"chainUpTo": {"old": 1, "new": 2}}}
    lines = definition.diff_lines(d)
    assert "+ repository extra" in lines and "- repository docs" in lines
    assert "~ systems: every system -> linux" in lines and "~ setting chainUpTo: 1 -> 2" in lines
    assert definition.diff_lines(definition.diff(old, old)) == []


def test_settling_keeps_the_accepted_copy_until_a_change_is_accepted(tmp_path):
    raw = {"description": "A.", "repos": [{"name": "code"}], "places": [], "systems": None, "settings": {}}
    changed = {**raw, "description": "B."}
    assert definition.settle(tmp_path, "t/p.os", raw, "ask") == "first"
    assert definition.settle(tmp_path, "t/p.os", raw, "ask") == "same"
    assert definition.settle(tmp_path, "t/p.os", changed, "ask") == "pending"
    assert definition.kept(tmp_path, "t/p.os")["definition"]["description"] == "A."
    assert definition.change(tmp_path, "t/p.os")["pending"] is True
    # Put back before it was accepted, it is no change.
    assert definition.settle(tmp_path, "t/p.os", raw, "ask") == "same"
    assert definition.change(tmp_path, "t/p.os") is None
    definition.settle(tmp_path, "t/p.os", changed, "ask")
    assert definition.accept(tmp_path, "t/p.os") == "accepted"
    assert definition.kept(tmp_path, "t/p.os")["definition"]["description"] == "B."
    assert definition.accept(tmp_path, "t/p.os") is None
    # Under auto the read is kept at once, and the copy it replaced shown until dismissed - the
    # oldest one, through a second change.
    assert definition.settle(tmp_path, "t/p.os", {**raw, "description": "C."}, "auto") == "changed"
    assert definition.settle(tmp_path, "t/p.os", {**raw, "description": "D."}, "auto") == "changed"
    shown = definition.change(tmp_path, "t/p.os")
    assert shown["pending"] is False and shown["diff"]["description"][1:] == ["-B.", "+D."]
    assert definition.accept(tmp_path, "t/p.os") == "dismissed"
    assert definition.change(tmp_path, "t/p.os") is None


# The loop

def test_auto_goes_on_with_a_change_and_shows_it(changing, fake_claude):
    p = changing(*ITEMS)
    two_sessions(p)
    p.run()
    p.after_first_session(lambda: p.change(description="Changed while it ran, in {code}."))
    assert p.r.run() == 0
    assert p.show("beta")["stage"] == "testing"
    assert "definition has changed, and this run goes on with the new one" in p.text
    assert p.r.bundle.description == "Changed while it ran, in {code}."
    assert "Changed while it ran, in " in p.r.system_file.read_text(encoding="utf-8")
    shown = definition.change(p.folder, p.tracker)
    assert shown["pending"] is False and f"-{DESCRIPTION.splitlines()[0]}" in shown["diff"]["description"]
    assert definition.accept(p.folder, p.tracker) == "dismissed"


def test_an_unchanged_definition_is_not_read_again(changing, fake_claude, monkeypatch):
    p = changing(*ITEMS)
    two_sessions(p)
    p.run()
    fetched = []
    real = definition.fetch
    monkeypatch.setattr(definition, "fetch", lambda *a, **k: fetched.append(1) or real(*a, **k))
    assert p.r.run() == 0
    assert fetched == [] and "definition has changed" not in p.text


def test_ask_holds_the_loop_until_the_change_is_accepted(changing, fake_claude):
    p = changing(*ITEMS, accept="ask")
    two_sessions(p)
    p.run()
    p.after_first_session(lambda: p.change(description="Changed: read me first."))
    states = p.while_held(lambda: None, lambda: definition.accept(p.folder, p.tracker))
    assert p.r.run() == 0
    assert states == ["held", "held"]
    assert "No session runs until the change is accepted" in p.text and "The change was accepted" in p.text
    assert p.show("beta")["stage"] == "testing"
    assert "Changed: read me first." in p.r.system_file.read_text(encoding="utf-8")
    assert definition.change(p.folder, p.tracker) is None


def test_a_change_waiting_from_before_holds_the_first_session(changing, fake_claude):
    p = changing(*ITEMS, accept="ask")
    p.run()
    p.change(description="Changed while it was stopped.")
    r = p.run()
    assert r.bundle.description == DESCRIPTION
    p.while_held(lambda: r.kill_file.write_text(""))
    assert r.run() == 0
    assert "Stopped at once, as asked, while held." in p.text
    assert p.show("alpha")["stage"] == "ready"
    assert definition.change(p.folder, p.tracker)["pending"] is True


def test_a_new_repository_with_no_path_holds_the_loop_under_auto_too(changing, fake_claude, tmp_path):
    p = changing(*ITEMS)
    two_sessions(p)
    p.run()
    p.after_first_session(lambda: p.change(repos=(("code", "The code"), ("docs", ""), ("extra", "More code"))))
    extra = make_repo(tmp_path / "extra", {"README.md": "extra\n"})
    states = p.while_held(lambda: None, lambda: p.set_loop(repos={"code": str(p.code), "docs": str(p.code), "extra": str(extra)}))
    assert p.r.run() == 0
    assert states[0] == "held"
    assert "repos.extra" in p.text and "No session runs until it is given" in p.text
    assert p.show("beta")["stage"] == "testing"
    assert p.r.repos == [p.code.resolve(), extra.resolve()]


def test_a_system_the_machine_is_not_on_stops_the_loop(changing, fake_claude):
    p = changing(*ITEMS)
    two_sessions(p)
    p.run()
    p.after_first_session(lambda: p.change(systems=other_system()))
    assert p.r.run() == 1
    assert "systems" in p.text and "Stopping." in p.text
    assert p.show("beta")["stage"] == "ready"


def test_a_change_at_start_under_auto_is_said_first(changing, fake_claude):
    p = changing(*ITEMS)
    p.run()
    p.change(description="Changed while it was stopped.")
    r = p.run()
    assert r.bundle.description == "Changed while it was stopped."
    assert "acceptDefinition is auto" in r.definition_note


# The command, and the page

def test_show_definition_and_accept(changing, fake_claude, capsys):
    p = changing(*ITEMS, accept="ask")
    assert cli.main(["show-definition", "proj"]) == 0
    assert "has not read" in capsys.readouterr().out
    p.run()
    assert cli.main(["show-definition", "proj"]) == 0
    out = capsys.readouterr().out
    assert "acceptDefinition ask" in out and "repository code - The code" in out and "No change since" in out
    p.change(description="Changed.", settings='{"chainUpTo": 2}')
    p.run()
    assert cli.main(["show-definition", "proj"]) == 0
    out = capsys.readouterr().out
    assert "waits to be accepted" in out and "  +Changed." in out and "~ setting chainUpTo: 1 -> 2" in out
    assert cli.main(["accept", "proj"]) == 0
    assert "is accepted" in capsys.readouterr().out
    assert definition.kept(p.folder, p.tracker)["definition"]["settings"] == {"chainUpTo": 2}
    assert cli.main(["accept", "proj"]) == 0
    assert "Nothing to accept" in capsys.readouterr().out


def test_the_page_shows_a_change_and_accepts_it(changing, fake_claude):
    from agentloop import server

    p = changing(*ITEMS, accept="ask")
    p.run()
    p.change(repos=(("code", "The code"), ("docs", ""), ("extra", "")))
    p.run()
    import threading

    from test_server import Client

    srv = server.Server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = Client(srv)
        shown = c.json("GET", "/api/loops/proj")
        assert shown["definitionChange"]["pending"] is True and shown["acceptDefinition"] == "ask"
        assert shown["definitionChange"]["diff"]["repos"]["added"] == [{"name": "extra", "about": ""}]
        assert "extra" in shown["definition"]["repos"] and "repos.extra" in [m["field"] for m in shown["missing"]]
        accepted = c.json("POST", "/api/loops/proj/accept-definition", {})
        assert accepted["definitionChange"] is None
        assert definition.kept(p.folder, p.tracker)["definition"]["repos"][-1] == {"name": "extra"}
    finally:
        srv.close()
