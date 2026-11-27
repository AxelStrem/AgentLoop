"""The loop finishes an item at testing that nobody is to check first (item autoaccept,
`Run.finish_tested`): after a session that left one there it asks `tracker.py finish`, unless the
item is flagged for a person, and says what was finished. Which items `finish` takes is
test_tracker's; here only what the loop asks of it, on a bare Run whose tracker calls are stubbed."""
import subprocess

import pytest

from agentloop import colours, loop


def bare_run(said=b"", why=b"", code=0):
    run = object.__new__(loop.Run)
    run.lines = []
    run.out = run.lines.append
    run.paint = colours.plain
    run.asked = []

    def tracker(*args):
        run.asked.append(args)
        return subprocess.CompletedProcess(args, code, said, why)

    run._tracker = tracker
    return run


@pytest.mark.parametrize("now", [{"stage": "testing"}, {"stage": "testing", "flags": "first, auto"}, None])
def test_an_item_left_at_testing_is_finished(now):
    run = bare_run(b"plain: testing -> finished\n")
    run.finish_tested(now)
    assert run.asked == [("finish",)]
    assert any("plain: testing -> finished, as nobody is to check it first" in line for line in run.lines)


@pytest.mark.parametrize("now", [{"stage": "ready"}, {"stage": "testing", "closed": True},
                                 {"stage": "testing", "flags": "review"}, {"stage": "testing", "flags": "auto, manual"},
                                 {"stage": "testing", "flags": "asked"}])
def test_an_item_for_a_person_or_not_at_testing_is_left(now):
    run = bare_run()
    run.finish_tested(now)
    assert run.asked == []


def test_a_failure_is_a_warning():
    run = bare_run(why=b"the server would not finish plain: no\n", code=1)
    run.finish_tested({"stage": "testing"})
    assert any("left there: the server would not finish plain: no" in line for line in run.lines)
