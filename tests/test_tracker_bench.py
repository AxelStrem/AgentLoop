"""The bench in tracker.py (item projbench): a loop waiting for work takes a seat on its tracker's
`project/Bench` and gives it up again - on the tests' own server, whose project template has the
bench (overseer:benchdiv)."""

import re

from conftest import AGENT
from test_loop import item, tracker_text

LOOP = "machine/main"
SEAT = "agent@machine/main"


def _seats(text: str) -> list[str]:
    return re.findall(r'- seat = "([^"]*)"', text)


def test_a_seat_is_taken_and_given_up(overseer):
    doc = overseer.document(tracker_text(item("lone")))
    said = overseer.tracker(doc, "bench", "sit", "--loop", LOOP)
    assert "on the bench" in said.stdout
    text = overseer.text(doc)
    assert _seats(text) == [SEAT]
    assert f'- account = "{AGENT}"' in text
    # The `/` a seat holds is escaped in the address it is removed by, so the leave finds it.
    said = overseer.tracker(doc, "bench", "leave", "--loop", LOOP)
    assert "left the bench" in said.stdout
    assert _seats(overseer.text(doc)) == []


def test_a_seat_taken_again_is_still_one(overseer):
    doc = overseer.document(tracker_text(item("lone")))
    overseer.tracker(doc, "bench", "sit", "--loop", LOOP)
    overseer.tracker(doc, "bench", "sit", "--loop", LOOP)
    overseer.tracker(doc, "bench", "sit", "--loop", "machine/other")
    assert sorted(_seats(overseer.text(doc))) == ["agent@machine/main", "agent@machine/other"]
    overseer.tracker(doc, "bench", "leave", "--loop", LOOP)
    assert _seats(overseer.text(doc)) == ["agent@machine/other"]


def test_leaving_a_seat_not_taken_is_quiet(overseer):
    doc = overseer.document(tracker_text(item("lone")))
    before = overseer.text(doc)
    said = overseer.tracker(doc, "bench", "leave", "--loop", LOOP)
    assert "not on the bench" in said.stdout
    assert overseer.text(doc) == before


def test_a_tracker_with_no_bench_is_no_failure(overseer):
    # A document holding its whole project, with no template and so no bench - as a tracker is on
    # a server whose template is older than the bench.
    doc = overseer.document('project (label="Bare") {\n    string name = "Bare"\n}\n', project="bare")
    before = overseer.text(doc)
    for action in ("sit", "leave"):
        said = overseer.tracker(doc, "bench", action, "--loop", LOOP)
        assert said.returncode == 0 and "no bench" in said.stderr, action
    assert overseer.text(doc) == before
