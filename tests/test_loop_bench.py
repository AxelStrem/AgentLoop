"""The bench in the loop (item projbench): a loop waiting for work sits on its tracker's bench for
as long as it waits, and a run clears a seat a killed run of the loop left - with the fake
`claude`, on the tests' own server."""

import re
import socket

from conftest import AGENT
from test_loop import idling, item, move, project  # noqa: F401 - project is a fixture


def _seats(p) -> list[str]:
    return re.findall(r'- seat = "([^"]*)"', p.overseer.text(p.tracker))


def _seat(r) -> str:
    return f"{AGENT}@{socket.gethostname()}/{r.loop.name}"


def test_a_waiting_loop_sits_on_the_bench_and_leaves_when_work_comes(project, fake_claude):
    p = project(item("alpha", "testing"), autoStart=True, autoStartMinutes=1)
    r = p.run()
    waiting, working = [], []

    def after_work() -> None:
        # The first poll of the wait after beta's session: on the bench again.
        if fake_claude.calls() and r.status.get("state") == "idle" and not waiting[1:]:
            waiting.append(_seats(p))
    idling(p, lambda: waiting.append(_seats(p)),
           lambda: p.overseer.tracker(p.tracker, "file", "beta", "Beta"),
           *[after_work] * 40,
           lambda: r.stop_file.write_text(""))
    # What the bench holds while beta's session runs: read by the session itself.
    real = r.invoke_item

    def invoke_item(*args, **kwargs):
        working.append(_seats(p))
        return real(*args, **kwargs)
    r.invoke_item = invoke_item
    p.play(p.scene(move("beta", "testing"), result="COMMIT: beta: looked"))
    assert r.run() == 0
    assert len(fake_claude.calls()) == 1
    assert waiting == [[_seat(r)], [_seat(r)]]
    # Off the bench while working, and once the stop ended the wait.
    assert working == [[]]
    assert _seats(p) == []
    assert "bench could not be written" not in p.text


def test_a_seat_left_by_a_killed_run_is_cleared(project, fake_claude):
    p = project(item("alpha", "testing"))
    r = p.run()
    p.overseer.tracker(p.tracker, "bench", "sit", "--loop", r.seat())
    assert _seats(p) == [_seat(r)]
    assert r.run() == 0
    assert _seats(p) == []
