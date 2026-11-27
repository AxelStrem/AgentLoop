"""The loop lifts the asked flag of a question answered by a click before it takes an item (item
md-controls-agents): `next` names such items as answered, the loop runs `tracker.py answered` for
them and asks `next` again. Driven on a bare Run whose tracker calls are stubbed, as
test_hear_first drives its own; tracker.py's side is in test_tracker."""
from pathlib import Path
from types import SimpleNamespace

from agentloop import colours, loop


def bare_run(tmp_path: Path, answers: dict, status=True) -> loop.Run:
    """A Run with only what read_next reads. `answers` maps a tracker.py command to what it says,
    each a list said in turn, the last again once they run out; every call is recorded."""
    run = object.__new__(loop.Run)
    run.lines = []
    run.out = run.lines.append
    run.paint = colours.plain
    run.worker = SimpleNamespace(manager=None)
    run.status = {"state": "running"} if status else {}
    run.answer_failed = set()
    run.asked = []
    run._status = lambda **changes: None

    def read_tracker(*args):
        run.asked.append(args)
        said = answers[args[0]]
        return said.pop(0) if len(said) > 1 else said[0]

    run.read_tracker = read_tracker
    return run


def test_an_answered_question_is_lifted_and_next_asked_again(tmp_path):
    run = bare_run(tmp_path, {"next": [{"signal": "EMPTY", "answered": ["q1"]}, {"handle": "q1", "stage": "planning"}],
                              "answered": [{"lifted": ["q1"]}]})
    assert run.read_next() == {"handle": "q1", "stage": "planning"}
    assert run.asked == [("next",), ("answered", "q1"), ("next",)]
    assert any("q1: its question was answered by a click" in line for line in run.lines)


def test_one_that_could_not_be_lifted_is_not_tried_again(tmp_path):
    run = bare_run(tmp_path, {"next": [{"signal": "EMPTY", "answered": ["q1"]}], "answered": [None]})
    assert run.read_next() == {"signal": "EMPTY", "answered": ["q1"]}
    assert run.read_next() == {"signal": "EMPTY", "answered": ["q1"]}
    assert run.asked == [("next",), ("answered", "q1"), ("next",)]
    assert "q1" in run.answer_failed


def test_a_dry_run_lifts_nothing(tmp_path):
    run = bare_run(tmp_path, {"next": [{"signal": "EMPTY", "answered": ["q1"]}]}, status=False)
    run.read_next()
    assert run.asked == [("next",)]


def test_nothing_answered_costs_no_call(tmp_path):
    run = bare_run(tmp_path, {"next": [{"handle": "a", "stage": "ready"}]})
    run.read_next()
    assert run.asked == [("next",)]
