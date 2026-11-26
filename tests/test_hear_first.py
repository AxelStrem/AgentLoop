"""What waits to be transcribed is heard before work taken without a pick, as before any pick (item
voice102): the build a triage goes on to in the same session, and the item a session goes on to.
Driven on a bare Run whose tracker calls are stubbed, as test_transcribe_only drives its wait."""
from pathlib import Path

from agentloop import colours, config, loop

ROUTE = {"model": "opus", "effort": "medium"}


def bare_run(tmp_path: Path, answers, engine="whisper") -> loop.Run:
    """A Run with only what invoke_item and next_in_session read. `answers` is what each `next`
    says, the last again once they run out; each `next`'s arguments and each `transcribe` are
    recorded, and each session too."""
    run = object.__new__(loop.Run)
    folder = tmp_path / "loop"
    folder.mkdir()
    run.loop_dir = folder
    run.stop_file = folder / "stop"
    run.kill_file = folder / "kill"
    run.settings = {**config.DEFAULTS, "transcribe": {"engine": engine}}
    run.lines = []
    run.out = run.lines.append
    run.paint = colours.plain
    run.unheard_failed = {}
    run.taken = None
    run.suggested = None
    run.session_context = 0
    run.seen = {}
    run.looked = []
    run.transcribed = []
    run.sessions = []
    answers = list(answers)

    def read_next(*args):
        run.looked.append(args)
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def transcribe(handles):
        run.transcribed.append(list(handles))
        return True

    def complete_session(prompt_kind, kind, item, route, session_id, resume, from_):
        run.sessions.append((prompt_kind, kind, item["handle"]))
        return True

    run.read_next = read_next
    run.transcribe = transcribe
    run.complete_session = complete_session
    run.last_shown = {"handle": "a", "stage": "ready", "complexity": "medium", "points": "3", "flags": ""}
    run.trial_applies = lambda item: False
    run._pause = lambda between=True: None
    run._killed = lambda: False
    run._over = lambda: None
    run.tried = lambda handle, model: False
    return run


TRIAGED = {"handle": "a", "stage": "filed", "complexity": "unassigned", "points": "3", "flags": ""}


def test_the_build_after_a_triage_waits_for_what_is_unheard(tmp_path):
    run = bare_run(tmp_path, [{"signal": "EMPTY", "unheard": ["b", "c"]}, {"signal": "EMPTY"}])
    assert run.invoke_item(TRIAGED, "triage", ROUTE, "s1", None)
    assert run.transcribed == [["b", "c"]]
    assert run.sessions == [("triage", "triage", "a"), ("continue", "build", "a")]


def test_an_item_given_a_voice_message_in_its_triage_is_not_built_in_it(tmp_path):
    run = bare_run(tmp_path, [{"signal": "EMPTY", "unheard": ["a"]}, {"handle": "a", "stage": "ready"}])
    assert run.invoke_item(TRIAGED, "triage", ROUTE, "s1", None)
    assert run.transcribed == [["a"]]
    assert run.sessions == [("triage", "triage", "a")]
    assert any("not built in this session" in line for line in run.lines)


def test_with_transcription_off_the_build_reads_no_next(tmp_path):
    run = bare_run(tmp_path, [{"signal": "EMPTY", "unheard": ["b"]}], engine="off")
    assert run.invoke_item(TRIAGED, "triage", ROUTE, "s1", None)
    assert run.looked == [] and run.transcribed == []
    assert run.sessions[-1] == ("continue", "build", "a")


def test_going_on_hears_first_and_keeps_its_preference(tmp_path):
    preferred = {"handle": "n", "stage": "filed", "preferred": True}
    run = bare_run(tmp_path, [{**preferred, "unheard": ["b"]}, preferred])
    run.suggested = "n"
    assert run.next_in_session(ROUTE, 0) == preferred
    assert run.transcribed == [["b"]]
    assert run.looked == [("--prefer", "n"), ("--prefer", "n")]


def test_going_on_with_transcription_off_hears_nothing(tmp_path):
    preferred = {"handle": "n", "stage": "filed", "preferred": True, "unheard": ["b"]}
    run = bare_run(tmp_path, [preferred], engine="off")
    run.suggested = "n"
    assert run.next_in_session(ROUTE, 0) == preferred
    assert run.transcribed == []
    assert run.looked == [("--prefer", "n")]
