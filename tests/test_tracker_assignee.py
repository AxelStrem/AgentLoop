"""The `for` column in tracker.py (item for-col): an item whose `assignee` names an account is free
only to a loop whose key signs in as that account, whatever it mentions; a claim refuses an item for
another account, and gives one for no one to the account that claimed it - on the tests' own
server, whose key signs in as `agent`."""

import json

from conftest import AGENT
from test_loop import item
from test_tracker_bots import _doc, _next, said, titled


def assigned(text: str, account: str) -> str:
    """An item of test_loop's, given to `account` in its `for` column."""
    return text.replace("            - points =", f'            - assignee = "{account}"\n            - points =', 1)


def _show(overseer, doc: str, handle: str) -> dict:
    return json.loads(overseer.tracker(doc, "show", handle, "--json").stdout)


def test_an_item_for_another_account_is_held_for_it(overseer):
    doc = _doc(overseer, assigned(item("theirs", "ready", complexity="low"), "zombo"),
               item("open", "ready", complexity="low", points=5))
    offered = _next(overseer, doc)
    assert offered["handle"] == "open"
    assert offered["me"] == AGENT
    assert offered["others"] == [{"handle": "theirs", "bots": ["zombo"], "names": ["Zombo"]}]
    assert "for      ready    theirs           held for Zombo" in overseer.tracker(doc, "queue").stdout


def test_an_item_for_this_account_is_free_to_it(overseer):
    doc = _doc(overseer, assigned(item("mine", "ready", complexity="low"), AGENT))
    offered = _next(overseer, doc)
    assert offered["handle"] == "mine"
    assert "others" not in offered


def test_the_column_wins_over_mentions(overseer):
    # Given to this account, though a person's last comment hands it to another; and given to
    # another, though its title mentions this one.
    doc = _doc(overseer,
               said(assigned(item("mine", "ready", complexity="low"), AGENT), "mine",
                    ("ap31", "", "Over to you, @Zombo")),
               titled(assigned(item("theirs", "ready", complexity="low"), "zombo"), "theirs", "@Agent: the page"))
    offered = _next(overseer, doc)
    assert offered["handle"] == "mine"
    assert offered["others"] == [{"handle": "theirs", "bots": ["zombo"], "names": ["Zombo"]}]


def test_mentions_still_hold_an_item_for_no_one(overseer):
    doc = _doc(overseer, titled(item("theirs", "ready", complexity="low"), "theirs", "For @Zombo"),
               item("open", "ready", complexity="low", points=5))
    offered = _next(overseer, doc)
    assert offered["handle"] == "open"
    assert [o["handle"] for o in offered["others"]] == ["theirs"]


def test_a_claim_gives_an_item_for_no_one_to_the_loops_account_and_release_keeps_it(overseer):
    doc = _doc(overseer, item("open", "ready", complexity="low"))
    assert json.loads(overseer.tracker(doc, "claim", "open", "--worker", "opus (medium)").stdout)["claimed"]
    assert _show(overseer, doc, "open")["assignee"] == AGENT
    overseer.tracker(doc, "release", "open")
    shown = _show(overseer, doc, "open")
    assert shown["assignee"] == AGENT
    assert shown.get("working") != "true"
    assert "assignee   agent" in overseer.tracker(doc, "show", "open").stdout


def test_a_claim_refuses_an_item_for_another_account(overseer):
    doc = _doc(overseer, assigned(item("theirs", "ready", complexity="low"), "zombo"))
    said_ = json.loads(overseer.tracker(doc, "claim", "theirs").stdout)
    assert said_ == {"handle": "theirs", "claimed": False, "refused": "it is for zombo"}
    shown = _show(overseer, doc, "theirs")
    assert shown["assignee"] == "zombo"
    assert shown.get("working") != "true"


def test_a_claim_of_an_item_for_this_account_keeps_it(overseer):
    doc = _doc(overseer, assigned(item("mine", "ready", complexity="low"), AGENT))
    assert json.loads(overseer.tracker(doc, "claim", "mine").stdout)["claimed"]
    assert _show(overseer, doc, "mine")["assignee"] == AGENT
