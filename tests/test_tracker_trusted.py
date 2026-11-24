"""The trusted flag in tracker.py: the loop finishes an item flagged trusted once it is at testing,
with `finish`, and nothing else - on the tests' own server (item trusted-item)."""

import json

from test_loop import item, tracker_text


def _part(handle: str, parent: str, **fields) -> str:
    return item(handle, **fields).replace(f'            - handle = "{handle}"',
                                          f'            - handle = "{handle}"\n            - parent = "{parent}"')


def _show(overseer, doc: str, handle: str) -> dict:
    return json.loads(overseer.tracker(doc, "show", handle, "--json").stdout)


def test_a_trusted_item_at_testing_is_finished_with_its_work_record(overseer):
    doc = overseer.document(tracker_text(item("alpha", "testing", flags="trusted"), item("beta")))
    overseer.tracker(doc, "work", "alpha", "--kind", "build", "--model", "claude-opus-5-5 (medium)", "--cost", "0.42")
    done = overseer.tracker(doc, "finish", "alpha")
    assert "alpha: testing -> finished" in done.stdout
    shown = _show(overseer, doc, "alpha")
    # The template's own status, finished, is not written out.
    assert shown["closed"] and shown.get("status", "finished") == "finished" and shown["finished_at"]
    assert [(w["kind"], w["model"]) for w in shown["work"]] == [("build", "claude-opus-5-5 (medium)")]
    assert not _show(overseer, doc, "beta").get("closed")


def test_finish_refuses_what_is_not_trusted_at_testing(overseer):
    doc = overseer.document(tracker_text(
        item("plain", "testing"), item("early", "ready", complexity="low", flags="trusted"),
        item("whole", "testing", flags="trusted"), _part("piece", "whole")))
    before = overseer.text(doc)
    for handle, why in (("plain", "not flagged trusted"), ("early", "not testing"), ("whole", "open parts")):
        done = overseer.tracker(doc, "finish", handle, check=False)
        assert done.returncode and why in done.stderr, handle
    assert overseer.text(doc) == before


def test_move_still_refuses_finished_to_a_trusted_item(overseer):
    doc = overseer.document(tracker_text(item("alpha", "ready", complexity="low", flags="trusted")))
    done = overseer.tracker(doc, "move", "alpha", "finished", check=False)
    assert done.returncode and "for a person to pick" in done.stderr


def test_finish_with_no_handle_finishes_every_trusted_item_at_testing(overseer):
    # The part goes first; its parent, at parts, comes to testing with it and is finished too.
    doc = overseer.document(tracker_text(
        item("one", "testing", flags="trusted"), item("plain", "testing"),
        item("whole", "parts", flags="trusted"), _part("piece", "whole", stage="testing", flags="trusted"),
        item("two", "testing", flags="trusted")))
    done = overseer.tracker(doc, "finish")
    finished = [line.split(":")[0] for line in done.stdout.splitlines()]
    assert sorted(finished) == ["one", "piece", "two", "whole"]
    for handle in finished:
        assert _show(overseer, doc, handle)["closed"], handle
    assert _show(overseer, doc, "plain")["stage"] == "testing"
    # Nothing left: a second call says nothing and changes nothing.
    before = overseer.text(doc)
    assert overseer.tracker(doc, "finish").stdout.strip() == ""
    assert overseer.text(doc) == before
