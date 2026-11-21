"""Bot tags in tracker.py (item assign-bot): an item tagged `bot-<username>` is free only to a loop
whose key signs in as that account, and is listed as held for those bots to the rest - on the
tests' own server, whose key signs in as `agent`."""

import json

from conftest import AGENT
from test_loop import item, tracker_text


def _doc(overseer, *items: str) -> str:
    return overseer.document(tracker_text(*items))


def _next(overseer, doc: str, *args: str) -> dict:
    return json.loads(overseer.tracker(doc, "next", *args).stdout)


def test_an_item_for_this_bot_is_free_and_one_for_another_is_not(overseer):
    doc = _doc(overseer, item("theirs", "ready", complexity="low", labels="core, bot-someone"),
               item("mine", "ready", complexity="low", points=5, labels=f"bot-{AGENT}"))
    offered = _next(overseer, doc)
    assert offered["handle"] == "mine"
    assert offered["me"] == AGENT
    assert offered["others"] == [{"handle": "theirs", "bots": ["someone"]}]
    shown = overseer.tracker(doc, "queue").stdout
    assert "for      ready    theirs           held for bot someone" in shown
    assert f"this loop signs in as {AGENT}" in shown


def test_prefer_never_takes_an_item_for_another_bot(overseer):
    doc = _doc(overseer, item("theirs", "ready", complexity="low", labels="bot-someone"),
               item("open", "ready", complexity="low"))
    offered = _next(overseer, doc, "--prefer", "theirs")
    assert offered["handle"] == "open"
    assert not offered["preferred"]


def test_an_item_for_several_bots_is_free_to_any_of_them(overseer):
    doc = _doc(overseer, item("shared", "ready", complexity="low", labels=f"bot-someone, bot-{AGENT}"))
    offered = _next(overseer, doc)
    assert offered["handle"] == "shared"
    assert "others" not in offered


def test_the_queue_is_empty_when_only_other_bots_items_are_left(overseer):
    doc = _doc(overseer, item("theirs", "ready", complexity="low", labels="bot-someone, bot-another"))
    offered = _next(overseer, doc)
    assert offered["signal"].startswith("[QUEUE_EMPTY]")
    assert offered["others"] == [{"handle": "theirs", "bots": ["someone", "another"]}]


def test_untagged_items_ask_nothing_of_the_server_and_say_no_account(overseer):
    doc = _doc(overseer, item("open", "ready", complexity="low", labels="core"))
    offered = _next(overseer, doc)
    assert offered["handle"] == "open"
    assert "me" not in offered and "others" not in offered


def test_an_item_waiting_or_held_is_said_so_rather_than_held_for_a_bot(overseer):
    doc = _doc(overseer, item("asked", "planning", labels="bot-someone", flags="asked"), item("free"))
    shown = overseer.tracker(doc, "queue").stdout
    assert "held     planning asked" in shown
    assert "held for bot" not in shown
