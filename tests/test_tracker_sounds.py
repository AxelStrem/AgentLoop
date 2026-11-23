"""Voice messages in tracker.py (item voice-hold): an item with a sound in its conversation not yet
transcribed is held back - `unheard` - whoever it is for; `sounds` lists those sounds, fetching them
with --fetch; and `heard` writes a transcript under one, refusing a sound that is gone or transcribed
already, and keeping the rest of the comment as it was - on the tests' own server."""

import base64
import json

import pytest

from conftest import AGENT
from test_loop import item
from test_tracker_assignee import assigned
from test_tracker_bots import _doc, _next
from test_tracker_bots import said as bots_said
from agentloop.tools import tracker

AT = "2026-11-22T09:00:00+00:00"
SOUND = "sounds/proj/2026-11-22T08-59-00.webm"


@pytest.mark.parametrize("body, unheard", [
    (f"![]({SOUND})", [SOUND]),
    (f"Listen: ![a note]({SOUND}) please", [SOUND]),
    (f"![transcribed]({SOUND})\n> **Transcript**: do it", []),
    ("![](images/proj/shot.png)", []),
    (f"`![]({SOUND})`", []),
    (f"```\n![]({SOUND})\n```", []),
    ("![](/abs/x.webm)", []),
    ("![](sounds/../x.webm)", []),
    ("![](https://example.com/x.mp3)", []),
    ("![](sounds/proj/a.MP3) and ![](sounds/proj/b.flac)", ["sounds/proj/a.MP3", "sounds/proj/b.flac"]),
])
def test_which_sounds_are_unheard(body, unheard):
    assert tracker.unheard_sounds(body) == unheard


def test_a_transcript_goes_under_the_sound_and_nothing_else_changes():
    body = f"First line.\n![]({SOUND}) and more\nLast line, @Zombo"
    sound = tracker.sounds_in(body)[0]
    written = tracker.with_transcript(body, sound, "**Transcript**: fix it\n\nsoon")
    assert written == (f"First line.\n![transcribed]({SOUND}) and more\n> **Transcript**: fix it\n>\n> soon\n\n"
                       "Last line, @Zombo")


def test_a_transcript_mentions_nobody():
    people = [(AGENT, "Agent"), ("zombo", "Zombo")]
    text = tracker.unmentioning("ask @Zombo about `@agent` it, @agent")
    assert text == "ask `@Zombo` about `@agent` it, `@agent`"
    assert tracker.trailing(text, people) == []


def said(text: str, handle: str, *comments: tuple[str, str, str]) -> str:
    """test_tracker_bots' `said`, each comment an hour after the one before it from AT: `heard`
    finds a comment by its moment."""
    text = bots_said(text, handle, *comments)
    for hour in range(len(comments)):
        text = text.replace(f'- at = "{AT}"', f'- at = "2026-11-22T{9 + hour:02}:00:00+00:00 "', 1)
    return text.replace("+00:00 \"", "+00:00\"")


def _show(overseer, doc, handle):
    return json.loads(overseer.tracker(doc, "show", handle, "--json").stdout)


def test_an_item_with_an_unheard_sound_is_held_for_any_bot(overseer):
    doc = _doc(overseer,
                   said(item("voiced", "ready", complexity="low", points=1), "voiced", ("ap31", "", f"![]({SOUND})")),
                   said(assigned(item("theirs", "ready", complexity="low"), "zombo"), "theirs",
                        ("ap31", "", f"Also: ![]({SOUND})")),
                   item("plain", "ready", complexity="low", points=5))
    offered = _next(overseer, doc)
    assert offered["handle"] == "plain"
    assert offered["unheard"] == ["voiced", "theirs"]
    # Not held for the other bot: held for its sound, which any loop may transcribe.
    assert "others" not in offered
    listed = overseer.tracker(doc, "queue").stdout
    assert "unheard  ready    voiced           waiting for its sounds to be transcribed" in listed
    assert "unheard  ready    theirs" in listed


def test_the_signal_names_what_waits_for_its_sounds(overseer):
    doc = _doc(overseer, said(item("voiced", "ready", complexity="low"), "voiced", ("agent", "opus", f"![]({SOUND})")))
    offered = _next(overseer, doc)
    assert offered["signal"] == tracker.EMPTY
    assert offered["unheard"] == ["voiced"]


def test_a_transcribed_sound_holds_nothing(overseer):
    doc = _doc(overseer, said(item("voiced", "ready", complexity="low"), "voiced",
                                  ("ap31", "", f"![transcribed]({SOUND})\n> **Transcript**: go")))
    offered = _next(overseer, doc)
    assert offered["handle"] == "voiced"
    assert "unheard" not in offered


def test_sounds_lists_the_unheard_and_fetches_them(overseer, tmp_path):
    doc = _doc(overseer, said(item("voiced", "testing"), "voiced",
                                  ("ap31", "", "ok"), ("ap31", "", f"Hear this ![]({SOUND})")))
    listed = json.loads(overseer.tracker(doc, "sounds").stdout)
    assert len(listed) == 1
    assert listed[0]["handle"] == "voiced" and listed[0]["path"] == SOUND and listed[0]["author"] == "ap31"
    assert tracker.instant(listed[0]["at"]) == tracker.instant("2026-11-22T10:00:00+00:00")
    assert listed[0]["title"] == "The voiced item"
    # The file beside the document, where the page keeps a recording.
    beside = overseer.root / doc.rsplit("/", 1)[0] / SOUND
    beside.parent.mkdir(parents=True, exist_ok=True)
    beside.write_bytes(b"\x1aE\xdf\xa3 not really webm")
    fetched = json.loads(overseer.tracker(doc, "sounds", "--fetch", env={"AGENT_SCRATCH": str(tmp_path)}).stdout)
    here = fetched[0]["file"]
    assert here and here.endswith("tracker-sounds/proj/2026-11-22T08-59-00.webm")
    with open(here, "rb") as f:
        assert f.read() == beside.read_bytes()


def test_sounds_fetches_none_the_server_lacks(overseer, tmp_path):
    doc = _doc(overseer, said(item("voiced"), "voiced", ("ap31", "", f"![]({SOUND})")))
    fetched = json.loads(overseer.tracker(doc, "sounds", "--fetch", env={"AGENT_SCRATCH": str(tmp_path)}).stdout)
    assert fetched[0]["file"] is None


def test_heard_writes_the_transcript_and_keeps_the_mention(overseer, tmp_path):
    body = f"Two things.\n![]({SOUND})\nOver to you, @Zombo"
    doc = _doc(overseer, said(item("voiced", "testing", flags="asked"), "voiced",
                                  ("ap31", "", "first"), ("ap31", "", body), ("ap31", "", "last")))
    words = tmp_path / "words.md"
    words.write_text("**Transcript** (ru): сделай\n\nDo it, and ask @agent.", encoding="utf-8")
    done = overseer.tracker(doc, "heard", "voiced", "2026-11-22T10:00:00+00:00", SOUND, f"@{words}")
    assert "transcript written" in done.stdout
    comments = _show(overseer, doc, "voiced")["comments"]
    assert [c["body"] for c in comments][0::2] == ["first", "last"]
    assert comments[1]["body"] == (f"Two things.\n![transcribed]({SOUND})\n> **Transcript** (ru): сделай\n>\n"
                                   "> Do it, and ask `@agent.`\n\nOver to you, @Zombo")
    # Still for Zombo, by the mention the comment ends with - and no longer unheard.
    offered = _next(overseer, doc)
    assert "unheard" not in offered
    assert json.loads(overseer.tracker(doc, "sounds").stdout) == []


def test_heard_picks_the_comment_by_its_moment(overseer, tmp_path):
    doc = _doc(overseer, said(item("voiced"), "voiced", ("ap31", "", f"![]({SOUND})")))
    overseer.tracker(doc, "heard", "voiced", "2026-11-22T09:00:00Z", SOUND, "go")
    assert _show(overseer, doc, "voiced")["comments"][0]["body"] == f"![transcribed]({SOUND})\n> go"


def test_heard_refuses_what_is_not_there_to_transcribe(overseer):
    doc = _doc(overseer, said(item("voiced"), "voiced", ("ap31", "", f"![]({SOUND})"),
                                  ("ap31", "", f"![transcribed]({SOUND.replace('59', '58')})\n> done")))
    gone = overseer.tracker(doc, "heard", "voiced", "2026-11-22T11:00:00+00:00", SOUND, "x", check=False)
    assert gone.returncode and "no comment at" in gone.stderr
    other = overseer.tracker(doc, "heard", "voiced", AT, "sounds/proj/other.webm", "x", check=False)
    assert other.returncode and "no longer holds" in other.stderr
    overseer.tracker(doc, "heard", "voiced", AT, SOUND, "first")
    again = overseer.tracker(doc, "heard", "voiced", AT, SOUND, "second", check=False)
    assert again.returncode and "transcribed already" in again.stderr
    bodies = [c["body"] for c in _show(overseer, doc, "voiced")["comments"]]
    assert bodies[0] == f"![transcribed]({SOUND})\n> first"
