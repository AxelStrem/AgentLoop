"""Mentions in tracker.py (item assign-bot): an item whose title mentions an account, or whose last
comment not written by a loop ends by mentioning one, is free only to a loop whose key signs in as
that account, and is listed as held for it to the rest - on the tests' own server, whose key signs
in as `agent`, and whose directory names the others in conftest's PEOPLE."""

import json

import pytest

from conftest import AGENT
from test_loop import item, tracker_text
from agentloop.tools import tracker


def said(text: str, handle: str, *comments: tuple[str, str, str]) -> str:
    """An item of tracker_text with a conversation: (author, status, body) each, oldest first - a
    status naming a model is a loop's comment, an empty one a person's."""
    entries = []
    for author, status, body in comments:
        entries += ["                    - {",
                    '                        - at = "2026-11-22T09:00:00+00:00"',
                    f"                        - author = {tracker.quoted(author)}",
                    *([f"                        - status = {tracker.quoted(status)}"] if status else []),
                    f"                        - body = {tracker.quoted(body)}",
                    "                    }"]
    talk = ["            div Talk {", "                list Comments {", *entries, "                }", "            }"]
    lines = text.split("\n")
    return "\n".join(lines[:-1] + talk + lines[-1:])


def titled(text: str, handle: str, title: str) -> str:
    return text.replace(f'- title = "The {handle} item"', f"- title = {tracker.quoted(title)}")


def _doc(overseer, *items: str, people: bool = True) -> str:
    """A tracker on the tests' server - one that mounts no directory of people, unless `people`."""
    text = tracker_text(*items)
    return overseer.document(text if people else "\n".join(l for l in text.split("\n") if "mount people" not in l))


def _next(overseer, doc: str, *args: str) -> dict:
    return json.loads(overseer.tracker(doc, "next", *args).stdout)


# The name as the page shows it: Overseer's own cases (people.rs, a_name_is_plain_without_its_markup).
@pytest.mark.parametrize("name, shown", [
    ("**C**laude <icon=bot>", "Claude"),
    ("<color=#e11d48 | C><color=#2563eb | laude>", "Claude"),
    ("*agent* ~~old~~ `code`", "agent old code"),
    ("agent_ap31 and _this_", "agent_ap31 and this"),
    ("a \\* star, and <#ap31>", "a * star, and ap31"),
    ("1 < 2 > 0", "1 < 2 > 0"),
    ("<color=red | <icon=bot> Bot>", "Bot"),
    ("<motion=wave color=red | Bot>", "Bot"),
])
def test_a_name_is_plain_as_overseer_makes_it(name, shown):
    assert tracker.plain(name) == shown


PEOPLE = [("agent", "Agent"), ("zombo", "Zombo"), ("zombobot", "<color=red | Zombo Bot>"),
          ("twin1", "Twin"), ("twin2", "**Twin**")]


@pytest.mark.parametrize("text, meant", [
    ("over to @Zombo Bot", ["zombobot"]),
    ("over to @zombo bot.", ["zombobot"]),
    ("over to @Zombo", ["zombo"]),
    ("over to @zombobot", ["zombobot"]),
    ("over to @Zombo, @agent!", ["agent", "zombo"]),
    ("over to @Zombos", []),
    ("over to @zombo-two", []),
    ("write to me@zombo", []),
    ("ask @Zombo about it, then go on", []),
    ("both of @Twin", ["twin1", "twin2"]),
    ("(@agent)", ["agent"]),
])
def test_only_the_mentions_a_text_ends_with_count(text, meant):
    assert sorted(tracker.trailing(text, PEOPLE)) == meant


def test_a_title_mention_by_display_name_holds_an_item_for_that_account(overseer):
    doc = _doc(overseer, titled(item("theirs", "ready", complexity="low"), "theirs", "Fix it, @Some One, please"),
               titled(item("mine", "ready", complexity="low", points=5), "mine", "@Agent: the page"))
    offered = _next(overseer, doc)
    assert offered["handle"] == "mine"
    assert offered["me"] == AGENT
    assert offered["others"] == [{"handle": "theirs", "bots": ["someone"], "names": ["Some One"]}]
    shown = overseer.tracker(doc, "queue").stdout
    assert "for      ready    theirs           held for Some One" in shown
    assert f"this loop signs in as Agent ({AGENT})" in shown


def test_the_last_persons_comment_ending_with_a_mention_holds_it(overseer):
    doc = _doc(overseer,
               said(item("asked", "ready", complexity="low"), "asked",
                    ("ap31", "", "Rework it this way. @zombobot")),
               said(item("mid", "ready", complexity="low"), "mid",
                    ("ap31", "", "Ask @Zombo Bot, then do it.")))
    offered = _next(overseer, doc)
    assert offered["handle"] == "mid"
    assert offered["others"] == [{"handle": "asked", "bots": ["zombobot"], "names": ["Zombo Bot"]}]


def test_a_bots_reply_keeps_the_hold_and_a_persons_later_comment_lets_go(overseer):
    held = said(item("held", "ready", complexity="low"), "held",
                ("ap31", "", "Over to you, @Zombo"), ("zombo", "opus (medium)", "On it - @ap31 might want to look."))
    let_go = said(item("free", "ready", complexity="low"), "free",
                  ("ap31", "", "Over to you, @Zombo"), ("ap31", "", "Anyone can take it now."))
    offered = _next(overseer, _doc(overseer, held, let_go))
    assert offered["handle"] == "free"
    assert [o["handle"] for o in offered["others"]] == ["held"]


def test_prefer_never_takes_an_item_for_another_account(overseer):
    doc = _doc(overseer, titled(item("theirs", "ready", complexity="low"), "theirs", "@someone's"),
               item("open", "ready", complexity="low"))
    offered = _next(overseer, doc, "--prefer", "theirs")
    assert offered["handle"] == "open"
    assert not offered["preferred"]


def test_an_item_for_several_accounts_is_free_to_any_of_them(overseer):
    doc = _doc(overseer, titled(item("shared", "ready", complexity="low"), "shared", "@someone or @agent"))
    offered = _next(overseer, doc)
    assert offered["handle"] == "shared"
    assert "others" not in offered


def test_the_queue_is_empty_when_only_others_items_are_left(overseer):
    doc = _doc(overseer, titled(item("theirs", "ready", complexity="low"), "theirs", "For @someone and @ap31"))
    offered = _next(overseer, doc)
    assert offered["signal"].startswith("[QUEUE_EMPTY]")
    assert offered["others"] == [{"handle": "theirs", "bots": ["someone", "ap31"], "names": ["Some One", "Dmitrii"]}]


def test_items_mentioning_no_one_say_no_account(overseer):
    doc = _doc(overseer, titled(item("open", "ready", complexity="low"), "open", "Mail me@example.com"),
               said(item("talked", "ready", complexity="low"), "talked", ("ap31", "", "Nothing for @nobody.")))
    offered = _next(overseer, doc)
    assert offered["handle"] == "open"
    assert "me" not in offered and "others" not in offered


def test_old_bot_tags_mean_nothing_now(overseer):
    doc = _doc(overseer, item("tagged", "ready", complexity="low", labels="core, bot-someone"))
    assert _next(overseer, doc)["handle"] == "tagged"


def test_an_item_waiting_or_held_is_said_so_rather_than_held_for_an_account(overseer):
    doc = _doc(overseer, titled(item("asked", "planning", flags="asked"), "asked", "For @someone"), item("free"))
    shown = overseer.tracker(doc, "queue").stdout
    assert "held     planning asked" in shown
    assert "held for" not in shown


def test_without_the_directory_only_this_loops_own_account_is_known(overseer):
    doc = _doc(overseer, titled(item("theirs", "ready", complexity="low"), "theirs", "For @someone"),
               titled(item("mine", "ready", complexity="low"), "mine", "For @Agent"), people=False)
    offered = _next(overseer, doc)
    assert offered["handle"] == "theirs"
    assert offered["me"] == AGENT
    assert "others" not in offered
