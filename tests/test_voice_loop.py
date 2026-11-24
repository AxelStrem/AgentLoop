"""The loop transcribing voice messages before it takes an item (item voice-loop): the engine faked,
the cleanup call played by the fake claude, the tracker on the tests' own server - and the pieces
it is made of: the setting and the route, the one-turn command line, the prompt, the text written
under a recording and `tracker.py people`."""

import json

import pytest

from conftest import AGENT
from test_loop import item, project  # noqa: F401 - the fixture
from test_tracker_assignee import assigned
from test_tracker_bots import _doc
from test_tracker_bots import said as bots_said
from agentloop import config, session, voice

SOUND = "sounds/proj/2026-11-22T08-59-00.webm"
WHISPER = {"engine": "whisper"}


def said(text: str, handle: str, *comments: tuple[str, str, str]) -> str:
    """test_tracker_bots' `said`, each comment an hour after the one before it, so that each has a
    moment of its own."""
    text = bots_said(text, handle, *comments)
    for hour in range(len(comments)):
        text = text.replace('- at = "2026-11-22T09:00:00+00:00"', f'- at = "2026-11-22T{9 + hour:02}:00:00+00:00 "', 1)
    return text.replace("+00:00 \"", "+00:00\"")


def voiced(handle: str = "voiced", stage: str = "ready", **kwargs) -> str:
    return said(item(handle, stage, complexity="low", **kwargs), handle,
                ("ap31", "", "Earlier words."), ("ap31", "", f"Listen: ![]({SOUND})"))


def recorded(p) -> None:
    """The recording, kept beside the tracker where the page keeps one."""
    beside = p.overseer.root / p.tracker.rsplit("/", 1)[0] / SOUND
    beside.parent.mkdir(parents=True, exist_ok=True)
    beside.write_bytes(b"\x1aE\xdf\xa3 not really webm")


@pytest.fixture
def heard(monkeypatch):
    """The engine, faked: what it is given, and what it hears - or the error it raises."""
    calls = []
    answer = {"value": {"language": "ru", "text": "ну сделай это, сделай, и спасибо за прошлое"}}

    def transcribe(file, settings):
        calls.append((file, dict(settings)))
        if isinstance(answer["value"], Exception):
            raise answer["value"]
        return answer["value"]

    monkeypatch.setattr(voice, "transcribe", transcribe)
    return calls, answer


def body_of(p, handle: str, index: int = 1) -> str:
    return p.show(handle)["comments"][index]["body"]


def voice_records(p, handle: str) -> list[dict]:
    return [w for w in p.show(handle).get("work") or [] if w.get("kind") == "voice"]


# The loop

def test_an_unheard_item_is_transcribed_then_taken(project, fake_claude, heard):
    p = project(voiced(), transcribe=WHISPER)
    recorded(p)
    p.play(p.scene(result="Do it, and thanks for the earlier work.", model="claude-sonnet-5-5", cost=0.02),
           p.scene({"fake": "tracker", "args": ["move", "voiced", "testing", "--note", "built"]}, result="COMMIT: voiced: built"))
    assert p.go() == 0
    calls = fake_claude.calls()
    assert len(calls) == 2
    # The cleanup: one turn, no tools, on the voice route, told the rules and what was heard.
    cleanup = calls[0]
    argv = cleanup["argv"]
    assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--max-turns") + 1] == "1"
    assert "--append-system-prompt-file" not in argv and "--dangerously-skip-permissions" not in argv
    assert (cleanup["model"], cleanup["effort"]) == ("sonnet", "medium")
    assert "praise, thanks and comments on work already done" in cleanup["prompt"]
    assert "ну сделай это" in cleanup["prompt"] and "Earlier words." in cleanup["prompt"]
    assert "A project for tests." in cleanup["prompt"] and "- zombo: Zombo" in cleanup["prompt"]
    # The engine had the fetched recording, with the setting's keys.
    files, answer = heard
    assert files[0][1] == WHISPER and files[0][0].name == "2026-11-22T08-59-00.webm"
    # Written under the recording: the English alone, not what was said in Russian (item voice-tweaks).
    assert body_of(p, "voiced") == f"Listen: ![transcribed]({SOUND})\n> Do it, and thanks for the earlier work."
    assert answer["value"]["text"] not in body_of(p, "voiced")
    records = voice_records(p, "voiced")
    assert len(records) == 1 and records[0]["model"] == "claude-sonnet-5-5 (medium)" and records[0]["cost"] in ("0.02", 0.02)
    assert "moved" not in records[0]
    # Then taken, as an item free to take: the build is the second call, on the item's own route.
    assert calls[1]["env"]["OVERSEER_ITEM"].endswith(":voiced") and calls[1]["model"] == "sonnet"
    assert p.show("voiced")["stage"] == "testing"
    assert any("voiced: voice message transcribed (ru)" in line for line in p.lines)
    # The recording fetched is not kept.
    assert not [f for f in (p.r.loop_dir / "voice").rglob("*") if f.is_file()]


def test_an_item_for_another_bot_is_transcribed_but_not_taken(project, fake_claude, heard, monkeypatch):
    p = project(assigned(voiced(), "zombo"), transcribe=WHISPER)
    recorded(p)
    p.play(p.scene(result="Do it."))
    # While it is transcribed the item is claimed - held against other loops - and still zombo's
    # (item voice-tweaks).
    during = []
    engine = voice.transcribe

    def watched(file, settings):
        during.append(p.show("voiced"))
        return engine(file, settings)

    monkeypatch.setattr(voice, "transcribe", watched)
    assert p.go() == 0
    assert len(fake_claude.calls()) == 1
    assert "![transcribed]" in body_of(p, "voiced")
    assert [(s.get("working"), s.get("assignee")) for s in during] == [("true", "zombo")]
    assert "transcribing" in str(during[0].get("worker"))
    shown = p.show("voiced")
    assert shown["stage"] == "ready" and shown.get("working") != "true" and shown["assignee"] == "zombo"
    assert len(voice_records(p, "voiced")) == 1
    assert not p.r.claimed


def test_an_item_another_loop_has_claimed_is_left_to_it(project, fake_claude, heard):
    """An item already marked as being worked is neither transcribed nor counted a failure: it is
    transcribed once it is free (item voice-tweaks)."""
    from test_claims import with_fields
    p = project(with_fields(voiced(), "voiced", "- working = true"), transcribe=WHISPER)
    recorded(p)
    assert p.go() == 0
    assert fake_claude.calls() == [] and heard[0] == []
    assert f"![]({SOUND})" in body_of(p, "voiced")
    assert p.show("voiced").get("working") == "true"
    assert not p.r.status.get("voiceProblem")
    assert not [line for line in p.lines if "could not be transcribed" in line]


def test_a_failed_transcription_is_said_once_and_the_loop_goes_on(project, fake_claude, heard):
    _, answer = heard
    answer["value"] = voice.VoiceError("local Whisper is not set up")
    p = project(voiced(), item("plain", "ready", complexity="low"), transcribe=WHISPER)
    recorded(p)
    p.play(p.scene({"fake": "tracker", "args": ["move", "plain", "testing", "--note", "built"]}, result="COMMIT: plain: built"))
    assert p.go() == 0
    assert len(fake_claude.calls()) == 1
    assert p.show("plain")["stage"] == "testing"
    said_lines = [line for line in p.lines if "could not be transcribed" in line]
    assert said_lines == ["  voiced: its voice message could not be transcribed - local Whisper is not set up. It is left held until the next run."]
    assert f"![]({SOUND})" in body_of(p, "voiced")
    assert p.r.status["voiceProblem"] == "voiced: local Whisper is not set up"
    assert p.r.status["unheard"] == 1


def test_a_failed_cleanup_is_a_failure_of_that_item_only(project, fake_claude, heard):
    p = project(voiced(), transcribe=WHISPER)
    recorded(p)
    p.play(p.scene(result="", error=True))
    assert p.go() == 0
    assert len(fake_claude.calls()) == 1
    assert any("the cleanup call failed" in line for line in p.lines)
    assert f"![]({SOUND})" in body_of(p, "voiced")
    assert not voice_records(p, "voiced")


def test_with_transcription_off_unheard_items_stay_held(project, fake_claude, heard):
    p = project(voiced())
    recorded(p)
    assert p.go() == 0
    assert fake_claude.calls() == [] and heard[0] == []
    assert f"![]({SOUND})" in body_of(p, "voiced")
    assert p.r.status["transcribe"] is False and p.r.status["unheard"] == 1
    # Not an error: the item is only not free to take yet (item voice-tweaks).
    assert not p.r.status.get("voiceProblem")
    assert not [line for line in p.lines if "could not be transcribed" in line]


def test_an_english_message_is_written_alone(project, fake_claude, heard):
    _, answer = heard
    answer["value"] = {"language": "", "text": "so um fix the thing, fix it"}
    p = project(assigned(voiced(), "zombo"), transcribe=WHISPER)
    recorded(p)
    p.play(p.scene(result="Fix the thing."))
    assert p.go() == 0
    assert body_of(p, "voiced") == f"Listen: ![transcribed]({SOUND})\n> Fix the thing."


# The pieces

def test_the_setting_and_the_route():
    settings = config.effective_settings({"routes": {"voice": {"model": "opus"} | {"effort": "high"}}})
    assert settings["routes"]["voice"] == {"model": "opus", "effort": "high"}
    assert settings["routes"]["triage"] == config.DEFAULTS["routes"]["triage"]
    assert config.effective_settings()["transcribe"] == {"engine": "off"}
    assert config.effective_settings()["routes"]["voice"] == {"model": "sonnet", "effort": "medium"}
    config.check_run_settings("f", "settings", {"transcribe": {"engine": "openai", "keyFile": "~/k"},
                                                "routes": {"voice": {"model": "haiku", "effort": "low"}}})
    for wrong in ({"engine": "loud"}, "whisper", {"model": "large-v3"}):
        with pytest.raises(config.ConfigError, match="transcribe"):
            config.check_run_settings("f", "settings", {"transcribe": wrong})


def test_a_one_turn_command_line():
    argv = session.command_line("claude", "sid", session.Route("sonnet", "medium"), settings=None, system_file=None,
                                resume=False, one_turn=True)
    assert argv == ["claude", "-p", "--output-format", "stream-json", "--verbose", "--model", "sonnet",
                    "--tools", "", "--max-turns", "1", "--session-id", "sid", "--effort", "medium"]


def test_the_prompt_holds_what_the_speaker_had_before_them():
    prompt = voice.cleanup_prompt(
        description="The game.", handle="jump", title="Jumping",
        before=[{"at": "2026-11-22T09:00:00+00:00", "author": "ap31", "body": "first"}],
        comment="Hear ![](sounds/x.webm)", people=[{"username": "zombo", "name": "Zombo"}],
        heard={"language": "", "text": "um make it jump"}, author="ap31")
    assert prompt.startswith(voice.CLEANUP_RULES)
    for part in ("The game.", "## The item: jump - Jumping", "--- 2026-11-22T09:00:00+00:00 ap31\nfirst",
                 "by ap31\n\nHear ![](sounds/x.webm)", "- zombo: Zombo", "(language: not said)\n\num make it jump"):
        assert part in prompt
    assert "Never write an @ before a name" in prompt and "Add nothing the speaker did not say" in prompt


def test_tracker_people(overseer):
    doc = _doc(overseer, item("alpha"))
    people = json.loads(overseer.tracker(doc, "people").stdout)
    names = {p["username"]: p["name"] for p in people}
    assert names["zombo"] == "Zombo" and AGENT in names
    # The display name without its markup.
    assert names["someone"] == "Some One"
