"""The parts stage in tracker.py: an agent moves an item there once nothing is left on it but its
open parts, never one without them, and leaves one there alone - on the tests' own server."""

import json

from test_loop import item, tracker_text


def _part(handle: str, parent: str) -> str:
    return item(handle).replace(f'            - handle = "{handle}"',
                                f'            - handle = "{handle}"\n            - parent = "{parent}"')


def _doc(overseer, stage: str = "filed") -> str:
    return overseer.document(tracker_text(item("whole", stage=stage), _part("piece", "whole"), item("lone")))


def _stage(overseer, doc: str, handle: str) -> str:
    return json.loads(overseer.tracker(doc, "show", handle, "--json").stdout)["stage"]


def test_an_item_with_open_parts_moves_to_parts_without_a_complexity(overseer):
    for stage in ("filed", "planning", "ready"):
        doc = _doc(overseer, stage)
        overseer.tracker(doc, "move", "whole", "parts", "--note", "Split into <#piece>; nothing else is left.")
        assert _stage(overseer, doc, "whole") == "parts"


def test_an_item_without_open_parts_is_refused(overseer):
    doc = _doc(overseer)
    before = overseer.text(doc)
    done = overseer.tracker(doc, "move", "lone", "parts", check=False)
    assert done.returncode and "no open parts" in done.stderr
    assert overseer.text(doc) == before


def test_an_item_at_parts_is_left_alone(overseer):
    doc = _doc(overseer, "parts")
    before = overseer.text(doc)
    # A comment it takes, as any open item does (test_tracker_note); nothing else.
    for args in (("move", "whole", "ready", "--complexity", "low"), ("after", "whole", "lone"),
                 ("ask", "whole", "why?")):
        done = overseer.tracker(doc, *args, check=False)
        assert done.returncode and "waiting on its open parts" in done.stderr, args
    assert overseer.text(doc) == before


def test_the_queue_never_offers_an_item_at_parts(overseer):
    doc = _doc(overseer, "parts")
    offered = [json.loads(overseer.tracker(doc, "next").stdout).get("handle")]
    assert "whole" not in offered


# An item comes after its open parts without naming them: the loop never takes it while one is open.

def test_an_item_with_an_open_part_waits_on_it(overseer):
    doc = overseer.document(tracker_text(item("whole", stage="ready", complexity="low"),
                                         _part("piece", "whole"), item("lone", stage="ready", complexity="low")))
    shown = json.loads(overseer.tracker(doc, "show", "whole", "--json").stdout)
    assert shown["waiting"] == ["piece"]
    assert "waiting    on its parts piece" in overseer.tracker(doc, "show", "whole").stdout
    assert "waiting  ready    whole" in overseer.tracker(doc, "queue").stdout
    offered = json.loads(overseer.tracker(doc, "next", "--prefer", "whole").stdout)
    assert offered["handle"] != "whole"


def test_an_item_whose_parts_are_closed_is_free(overseer):
    doc = overseer.document(tracker_text(item("whole", stage="ready", complexity="low")))
    assert json.loads(overseer.tracker(doc, "show", "whole", "--json").stdout)["waiting"] == []
    assert json.loads(overseer.tracker(doc, "next").stdout)["handle"] == "whole"


def test_after_and_parts_together_are_both_shown(overseer):
    doc = overseer.document(tracker_text(item("whole").replace('- points', '- after = "lone"\n            - points'),
                                         _part("piece", "whole"), item("lone")))
    assert json.loads(overseer.tracker(doc, "show", "whole", "--json").stdout)["waiting"] == ["lone", "piece"]
    assert "waiting    on lone; its parts piece" in overseer.tracker(doc, "show", "whole").stdout


def test_after_within_the_family_is_refused(overseer):
    doc = overseer.document(tracker_text(item("whole"), _part("piece", "whole"), _part("bit", "piece"), item("lone")))
    before = overseer.text(doc)
    for args, said in ((("after", "whole", "piece"), "waits on its open parts already"),
                       (("after", "piece", "whole"), "neither ever free"),
                       (("after", "bit", "whole"), "neither ever free"),
                       (("file", "more", "More", "--parent", "piece", "--after", "whole"), "neither would ever be free")):
        done = overseer.tracker(doc, *args, check=False)
        assert done.returncode and said in done.stderr, args
    assert overseer.text(doc) == before
    overseer.tracker(doc, "after", "piece", "lone")
