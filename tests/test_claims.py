"""An item's owner, and the claim a loop holds on the item it works: tracker.py on the test's server,
and the loop claiming and releasing around each session."""

import json
import os
import re

import pytest

from agentloop import loop
from test_loop import item, move, project, tracker_text  # noqa: F401 - project is a fixture


# An item's worker field naming someone.
NAMED = re.compile(r'^\s*- worker = "[^"]+"$', re.M)


def with_fields(text: str, handle: str, *lines: str) -> str:
    """An item of tracker_text with fields of its own added after its handle."""
    at = f'            - handle = "{handle}"'
    return text.replace(at, "\n".join([at, *(f"            {line}" for line in lines)]))


@pytest.fixture
def clean_env(monkeypatch):
    for name in list(os.environ):
        if (name.startswith(("AGENT_", "OVERSEER_")) or name == "AGENTIC_LOOP"):
            monkeypatch.delenv(name)


# tracker.py

def test_a_claim_is_the_templates_button_refused_while_it_is_held(overseer, clean_env):
    text = tracker_text(item("alpha", "ready", complexity="low"), item("beta"))
    doc = overseer.document(text)
    tracker = overseer.tracker

    assert json.loads(tracker(doc, "claim", "alpha").stdout) == {"handle": "alpha", "claimed": True}
    assert "- working = true" in overseer.text(doc)
    # The second press is the button's own refusal.
    assert json.loads(tracker(doc, "claim", "alpha").stdout) == {
        "handle": "alpha", "claimed": False, "refused": "another agent is working on it"}
    assert json.loads(tracker(doc, "next").stdout)["handle"] == "beta"
    assert json.loads(tracker(doc, "next", "--prefer", "alpha").stdout)["preferred"] is False
    assert "claimed  ready    alpha" in tracker(doc, "queue").stdout
    # The other commands leave it to the session holding it.
    tracker(doc, "note", "alpha", "still at it")

    tracker(doc, "release", "alpha")
    assert "working = true" not in overseer.text(doc)
    assert json.loads(tracker(doc, "next", "--prefer", "alpha").stdout)["preferred"] is True
    assert tracker(doc, "release", "alpha").stdout.strip() == "alpha: nothing to release"
    assert tracker(doc, "release", "gone").stdout.strip() == "gone: nothing to release"
    assert json.loads(tracker(doc, "claim", "alpha").stdout)["claimed"] is True
    tracker(doc, "release", "alpha")


def test_a_claim_names_its_worker_and_a_release_takes_it_out(overseer, clean_env):
    doc = overseer.document(tracker_text(item("alpha", "ready", complexity="low"), item("beta")))
    tracker = overseer.tracker

    claimed = tracker(doc, "claim", "alpha", "--worker", "claude-opus-5-5 (medium)")
    assert json.loads(claimed.stdout) == {"handle": "alpha", "claimed": True}
    text = overseer.text(doc)
    assert "- working = true" in text and '- worker = "claude-opus-5-5 (medium)"' in text
    # A refused claim leaves the holder's name as it is.
    tracker(doc, "claim", "alpha", "--worker", "someone else")
    assert '- worker = "claude-opus-5-5 (medium)"' in overseer.text(doc)
    tracker(doc, "release", "alpha")
    text = overseer.text(doc)
    # The template's own "" back, or no line at all: no name either way.
    assert "working = true" not in text and not NAMED.search(text)

    # Without --worker, the session's AGENT_AUTHOR; without either, no name at all.
    tracker(doc, "claim", "beta", env={"AGENT_AUTHOR": "claude-sonnet-5-5"})
    assert '- worker = "claude-sonnet-5-5"' in overseer.text(doc)
    tracker(doc, "release", "beta")
    tracker(doc, "claim", "beta")
    text = overseer.text(doc)
    assert "- working = true" in text and not NAMED.search(text)
    tracker(doc, "release", "beta")


def test_an_item_filed_is_its_source_items_owners(overseer, clean_env):
    folder = overseer.folder()
    doc = overseer.document(with_fields(tracker_text(item("alpha"), item("beta")), "alpha", '- owner = "alice"'), folder=folder)
    other = overseer.document(tracker_text(item("far")), project="other", folder=folder)
    tracker = overseer.tracker

    tracker(doc, "file", "gamma", "Filed from alpha", env={"OVERSEER_ITEM": "proj:alpha"})
    assert json.loads(tracker(doc, "show", "gamma", "--json").stdout)["owner"] == "alice"
    tracker(doc, "file", "into", "Filed into the other", "--into", "other", env={"OVERSEER_ITEM": "proj:alpha"})
    assert json.loads(tracker(other, "show", "into", "--json").stdout)["owner"] == "alice"

    # None when the source item has no owner, or there is no source item.
    tracker(doc, "file", "delta", "From beta", env={"OVERSEER_ITEM": "proj:beta"})
    tracker(doc, "file", "eps", "By hand")
    shown = {h: json.loads(tracker(doc, "show", h, "--json").stdout) for h in ("delta", "eps")}
    assert not shown["delta"].get("owner") and not shown["eps"].get("owner")


def manager_items() -> str:
    ready = dict(stage="ready", complexity="low")
    return tracker_text(
        item("plain", points=1, **ready),
        item("firsts", points=2, flags="first", **ready),
        with_fields(item("owned", points=3, **ready), "owned", '- owner = "alice"'),
        with_fields(item("both", points=5, flags="first", **ready), "both", '- owner = "alice"'),
        with_fields(item("theirs", points=1, **ready), "theirs", '- owner = "someone"'))


def test_a_managers_items_come_before_items_flagged_first(overseer, clean_env):
    doc = overseer.document(manager_items())
    tracker = overseer.tracker
    handle = lambda *args: json.loads(tracker(doc, "next", *args).stdout)["handle"]

    # Without an owner the order is what it was: first, then the fewest points.
    assert handle() == "firsts"
    # Owned and first, then owned, then others' first, then the rest.
    assert handle("--owner", "alice") == "both"
    free = tracker(doc, "queue", "--owner", "alice").stdout
    order = [line.split()[1] for line in free.splitlines() if " pts " in line]
    assert order == ["both", "owned", "firsts", "plain", "theirs"]
    assert " @ready" in free and " !ready    firsts" in free and "@ is owned by alice" in free
    # An owner nothing is owned by changes nothing.
    assert handle("--owner", "nobody") == "firsts"
    assert "@ is owned" not in tracker(doc, "queue").stdout


def test_a_preferred_item_gives_way_to_a_managers(overseer, clean_env):
    doc = overseer.document(manager_items())
    nxt = lambda *args: json.loads(overseer.tracker(doc, "next", *args).stdout)

    # Free items ranking above the preferred one win: a manager's item, then one flagged first.
    assert nxt("--prefer", "plain", "--owner", "alice")["handle"] == "both"
    assert nxt("--prefer", "firsts", "--owner", "alice")["handle"] == "both"
    assert nxt("--prefer", "plain")["handle"] == "firsts"
    assert nxt("--prefer", "theirs")["handle"] == "firsts"
    # One of the same rank as the first is taken, and says it was preferred.
    ready = dict(stage="ready", complexity="low")
    doc = overseer.document(tracker_text(
        with_fields(item("owned", points=1, **ready), "owned", '- owner = "alice"'),
        with_fields(item("owned2", points=4, **ready), "owned2", '- owner = "alice"'),
        item("firsts", flags="first", **ready)))
    assert nxt("--owner", "alice")["handle"] == "owned"
    taken = nxt("--prefer", "owned2", "--owner", "alice")
    assert taken["handle"] == "owned2" and taken["preferred"] is True


def test_a_tracker_named_as_a_file_is_refused(tmp_path, overseer, clean_env):
    # Trackers are on a server only: a file's path is a name the server does not know.
    path = tmp_path / "proj.os"
    path.write_text(tracker_text(item("alpha")), encoding="utf-8", newline="\n")
    done = overseer.tracker(str(path), "next", check=False)
    assert done.returncode != 0 and "404" in done.stderr
    alone = overseer.tracker(str(path), "next", env={"OVERSEER_URL": ""}, check=False)
    assert alone.returncode != 0 and "trackers are on an Overseer server, and no OVERSEER_URL names one" in alone.stderr


# The loop

def copy_tracker(p, to: str) -> dict:
    """The tracker as the server keeps it, mid-session."""
    return {"fake": "copy", "from": str(p.overseer.root / p.tracker), "to": to}


def test_a_session_holds_its_item_and_lets_go_of_it(project, fake_claude):
    p = project(item("alpha", labels="core"))
    mid = p.tmp / "mid"
    p.play(p.scene(copy_tracker(p, str(mid / "triage.os")), move("alpha", "ready", "medium", 3), result="COMMIT: alpha: triaged"),
           p.scene(copy_tracker(p, str(mid / "build.os")), move("alpha", "testing"), result="COMMIT: alpha: built"))
    assert p.go() == 0
    for phase in ("triage", "build"):
        text = (mid / f"{phase}.os").read_text(encoding="utf-8")
        assert "            - working = true" in text
        # The agent at work by the name that signs its comments.
        assert NAMED.search(text)
    final = p.overseer.text(p.tracker)
    assert "working = true" not in final and not NAMED.search(final)
    assert p.show("alpha")["stage"] == "testing"
    assert not loop.claims_path(p.r.loop_dir).exists()


@pytest.mark.parametrize("how", ["killed", "stopped by the agent", "failed"])
def test_a_session_lets_go_of_its_item_however_it_ends(project, fake_claude, how):
    p = project(item("alpha", "ready", complexity="low"))
    r = p.run()
    steps = [move("alpha", "testing")]
    if how == "killed":
        steps.append({"fake": "write", "path": str(r.kill_file), "text": ""})
    p.play(p.scene(*steps, result="STOP_LOOP: cannot be worked" if how != "killed" else "COMMIT: alpha: built",
                   error=how == "failed"))
    r.run()
    assert p.attempts()[0]["to"].startswith(how)
    assert "working = true" not in p.overseer.text(p.tracker)
    assert not loop.claims_path(r.loop_dir).exists()


def test_an_item_another_loop_has_is_left_to_it(project, fake_claude, monkeypatch):
    p = project(item("alpha", "ready", complexity="low"), item("beta", "ready", complexity="low", points=5))
    r = p.run()
    real = r.claim
    refused = []

    def claim(handle, worker=None):
        # Another loop takes alpha between this one's next and its claim, the first time.
        if handle == "alpha" and not refused:
            refused.append(handle)
            return False
        return real(handle, worker)

    monkeypatch.setattr(r, "claim", claim)
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"),
           p.scene(move("beta", "testing"), result="COMMIT: beta: built"))
    assert r.run() == 0
    assert "  alpha: another loop is working on it - left to it." in p.lines
    assert len(fake_claude.calls()) == 2
    assert [a["handle"] for a in p.attempts()] == ["alpha", "beta"]


def test_an_item_claimed_already_is_not_picked(project, fake_claude):
    p = project(with_fields(item("alpha", "ready", complexity="low"), "alpha", "- working = true"),
                item("beta", "ready", complexity="low", points=5))
    p.play(p.scene(move("beta", "testing"), result="COMMIT: beta: built"))
    assert p.go() == 0
    assert [a["handle"] for a in p.attempts()] == ["beta"]
    assert "            - working = true" in p.overseer.text(p.tracker)


def test_a_worker_with_a_manager_takes_the_managers_item_before_one_flagged_first(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low", flags="first"),
                with_fields(item("beta", "ready", complexity="low", points=5), "beta", '- owner = "alice"'))
    settings = p.tmp / "home" / "config" / "settings.json"
    raw = json.loads(settings.read_text(encoding="utf-8"))
    raw["workers"][0]["manager"] = "alice"
    settings.write_text(json.dumps(raw), encoding="utf-8")
    p.play(p.scene(move("beta", "testing"), result="COMMIT: beta: built"),
           p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    r = p.run()
    assert r.worker.manager == "alice"
    assert r.read_next()["handle"] == "beta"
    assert r.run() == 0
    assert [a["handle"] for a in p.attempts()] == ["beta", "alpha"]


def test_what_a_dead_run_left_claimed_is_released_at_start(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"))
    r = p.run()
    # A run that died mid-session: its claim on the server, and claims.json naming it.
    p.overseer.tracker(p.tracker, "claim", "alpha")
    loop.write_json(loop.claims_path(r.loop_dir), {"tracker": r.tracker, "env": r.tracker_env, "handles": ["alpha"]})
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    assert r.run() == 0
    assert " Released alpha, which the last run of this loop left claimed." in p.lines
    assert p.attempts()[0]["to"] == "testing"
    assert not loop.claims_path(r.loop_dir).exists()


def test_a_claim_that_cannot_be_let_go_of_is_kept_for_the_next_start(tmp_path, clean_env):
    folder = tmp_path / "loop"
    loop.write_json(loop.claims_path(folder), {"tracker": str(tmp_path / "missing.os"), "env": {}, "handles": ["alpha"]})
    assert loop.release_left(folder) == []
    assert json.loads(loop.claims_path(folder).read_text(encoding="utf-8"))["handles"] == ["alpha"]
