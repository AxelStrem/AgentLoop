"""A comment in tracker.py: any open item takes one, whatever its stage or flags - it changes
neither - and a closed one takes none - on the tests' own server."""

import json

from test_loop import item, tracker_text


def _shown(overseer, doc: str, handle: str) -> dict:
    return json.loads(overseer.tracker(doc, "show", handle, "--json").stdout)


def test_an_item_waiting_on_a_person_or_its_parts_takes_a_comment(overseer):
    piece = item("piece").replace('            - handle = "piece"',
                                  '            - handle = "piece"\n            - parent = "whole"')
    doc = overseer.document(tracker_text(
        item("tried", stage="testing"), item("shelved", stage="later"), item("whole", stage="parts"), piece,
        item("questioned", stage="planning", flags="asked"), item("theirs", flags="manual")))
    for handle in ("tried", "shelved", "whole", "questioned", "theirs"):
        was = _shown(overseer, doc, handle)
        overseer.tracker(doc, "note", handle, "Something worth knowing.")
        now = _shown(overseer, doc, handle)
        assert now["stage"] == was["stage"] and now["flags"] == was["flags"], handle
        assert "Something worth knowing." in overseer.tracker(doc, "show", handle).stdout, handle


def test_a_closed_item_takes_no_comment(overseer):
    text = tracker_text(item("lone")).replace("    - History {\n    }",
                                              "    - History {\n" + item("done", stage="finished") + "\n    }")
    doc = overseer.document(text)
    before = overseer.text(doc)
    done = overseer.tracker(doc, "note", "done", "Too late.", check=False)
    assert done.returncode and "History" in done.stderr
    assert overseer.text(doc) == before


def test_an_item_waiting_on_a_person_is_still_not_moved(overseer):
    doc = overseer.document(tracker_text(item("tried", stage="testing")))
    before = overseer.text(doc)
    for args in (("move", "tried", "ready", "--complexity", "low"), ("ask", "tried", "why?"), ("after", "tried", "x")):
        done = overseer.tracker(doc, *args, check=False)
        assert done.returncode and "waiting on a person" in done.stderr, args
    assert overseer.text(doc) == before
