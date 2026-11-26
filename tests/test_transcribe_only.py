"""A loop that only transcribes (item transcribe-only): it hears what waits to be transcribed, at
once and as the tracker changes, and never takes an item into work, whatever `next` offers. Driven
on a bare Run whose tracker calls are stubbed, as test_leftfiles drives the hold."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentloop import colours, config, loop, runner, usage


class Clock:
    """Time that moves on by each sleep; `on_sleep` sees the number of sleeps so far."""

    def __init__(self, on_sleep=None):
        self.at = 1_800_000_000.0
        self.slept = 0
        self.on_sleep = on_sleep

    def now(self) -> float:
        return self.at

    def sleep(self, seconds: float) -> None:
        self.slept += 1
        self.at += seconds
        if self.on_sleep:
            self.on_sleep(self.slept)


class Watcher:
    def __init__(self):
        self.watching = True
        self.changes = 0
        self.stopped = False

    def stop(self):
        self.stopped = True


def never(*args, **kwargs):
    raise AssertionError("a loop that only transcribes took an item into work")


def bare_run(tmp_path: Path, answers, clock=None, **settings) -> loop.Run:
    """A Run with only what _transcribe_only reads. `answers` is what each `next` says, the last
    again once they run out; each `transcribe` is recorded and writes a transcript."""
    run = object.__new__(loop.Run)
    folder = tmp_path / "loop"
    folder.mkdir()
    run.project = "proj"
    run.loop_dir = folder
    run.status_file = folder / "status.json"
    run.stop_file = folder / "stop"
    run.kill_file = folder / "kill"
    run.status = {}
    run.settings = {**config.DEFAULTS, "transcribe": {"engine": "whisper"}, "transcribeOnly": True, **settings}
    run.clock = clock or Clock()
    run.lines = []
    run.out = run.lines.append
    run.paint = colours.plain
    run.ended_by = None
    run.lock = None
    run.update_looked = None
    run.unheard_failed = {}
    run.watcher = None
    run.seats = []
    run.looks = 0
    run.transcribed = []
    run.the_watcher = Watcher()
    answers = list(answers)

    def read_next(*args):
        run.looks += 1
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def transcribe(handles):
        run.transcribed.append(list(handles))
        return True

    run.read_next = read_next
    run.transcribe = transcribe
    run._watcher = lambda say: run.the_watcher
    run._clear_seat = lambda: run.seats.append("leave")
    run.bench = lambda action: run.seats.append(action)
    run._move_to_newer = lambda: False
    run._pause = lambda between=True: None
    # What taking an item into work goes through: none of it may be reached.
    run.plan = run.invoke_item = run.claim = run.finish_trusted = run._setup = run._brew = never
    return run


def stop_after(run: loop.Run, sleeps: int):
    def on_sleep(slept):
        if slept >= sleeps:
            run.stop_file.write_text("")
    return on_sleep


def test_it_transcribes_and_never_takes_an_item(tmp_path):
    clock = Clock()
    run = bare_run(tmp_path, [{"signal": "empty", "unheard": ["voiced"]},
                              {"handle": "voiced", "stage": "filed"}], clock)
    clock.on_sleep = stop_after(run, 3)
    assert run._transcribe_only() == 0
    assert run.transcribed == [["voiced"]]
    assert run.ended_by == "stop"
    # No seat taken on the bench, only one a run before left given up.
    assert run.seats == ["leave"]
    assert run.the_watcher.stopped
    assert run.status["state"] == "idle"


def test_it_looks_again_as_the_tracker_changes(tmp_path):
    clock = Clock()
    run = bare_run(tmp_path, [{"signal": "empty"}, {"signal": "empty", "unheard": ["later"]}, {"signal": "empty"}], clock)
    settle = int(loop.SETTLE_SECONDS // usage.POLL_SECONDS) + 1

    def on_sleep(slept):
        if slept == 2:
            run.the_watcher.changes += 1
        if slept >= 2 + settle + 2:
            run.stop_file.write_text("")

    clock.on_sleep = on_sleep
    assert run._transcribe_only() == 0
    assert run.transcribed == [["later"]]


def test_a_kill_ends_it_at_once(tmp_path):
    run = bare_run(tmp_path, [{"signal": "empty"}])
    run.kill_file.write_text("")
    assert run._transcribe_only() == 0
    assert run.ended_by == "kill" and run.looks == 0


def test_a_failed_look_is_tried_again(tmp_path):
    clock = Clock()
    run = bare_run(tmp_path, [None, {"signal": "empty", "unheard": ["back"]}, {"signal": "empty"}], clock,
                   autoStartMinutes=0.01)
    clock.on_sleep = stop_after(run, 4)
    assert run._transcribe_only() == 0
    assert run.transcribed == [["back"]]
    assert sum("could not say" in line for line in run.lines) == 1


def test_it_refuses_to_start_with_transcription_off():
    with pytest.raises(loop.LoopError):
        loop.check_transcribe_only("l", {**config.DEFAULTS, "transcribeOnly": True})
    loop.check_transcribe_only("l", {**config.DEFAULTS, "transcribeOnly": True, "transcribe": {"engine": "whisper"}})
    loop.check_transcribe_only("l", dict(config.DEFAULTS))


def test_only_the_setting_makes_a_loop_transcribe_only(tmp_path):
    assert bare_run(tmp_path, [{"signal": "empty"}]).transcribe_only()
    assert not (tmp_path / "off").mkdir() or bare_run(tmp_path / "off", [{"signal": "empty"}], transcribeOnly=False).transcribe_only()


def test_the_page_is_told_the_loop_only_transcribes(tmp_path, monkeypatch):
    # The page marks such a loop in its row (item transcribe-only-page), from the status runner
    # passes on: a field LoopStatus does not name would be dropped on the way.
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    found = SimpleNamespace(project="p", name="l")
    status = loop.loop_dir("p", "l") / "status.json"
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({"state": "idle", "transcribeOnly": True, "unheard": 2}), encoding="utf-8")
    with loop.file_lock(loop.lock_path("p", "l")):
        said = runner.Runner()._status(found).as_dict()
    assert said["alive"] and said["transcribeOnly"] is True and said["unheard"] == 2
