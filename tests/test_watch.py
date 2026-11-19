"""The tracker's watch stream (watch.py, item instant-auto), against the tests' own server."""

from __future__ import annotations

import socket
import time
from pathlib import Path

from agentloop import watch
from test_loop import tracker_text, item


def until(test, seconds: float = 15.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.05)
    return test()


def test_events_are_read_from_the_stream_lines():
    lines = [": keep-alive", "", "event: read", "data: a=1", "", "event: changed", "data: v1", "",
             "data: first", "data:second", "id: 3", "", "event: changed", "data: v2"]
    # The last event has no blank line after it, so it never ended.
    assert list(watch.events(lines)) == [("read", "a=1"), ("changed", "v1"), ("message", "first\nsecond")]


def test_a_change_to_the_tracker_is_heard_and_the_first_version_is_not_one(overseer):
    doc = overseer.document(tracker_text(item("alpha", "testing")))
    said = []
    w = watch.Watcher(overseer.url, overseer.key, doc, said.append)
    w.start()
    try:
        assert until(lambda: w.version is not None)
        assert w.watching and w.changes == 0
        overseer.tracker(doc, "file", "beta", "Beta")
        assert until(lambda: w.changes >= 1)
    finally:
        w.stop()
    assert not said


def test_a_document_the_server_does_not_know_ends_the_watch(overseer):
    said = []
    w = watch.Watcher(overseer.url, overseer.key, f"{overseer.folder()}/nowhere.os", said.append)
    w.start()
    assert until(lambda: not w.alive)
    assert w.gave_up and w.gave_up.startswith("the server cannot watch it (")
    assert len(said) == 1 and "looking at the set times only" in said[0]


def test_a_refused_key_ends_the_watch(overseer, tmp_path: Path):
    doc = overseer.document(tracker_text(item("alpha", "testing")))
    wrong = tmp_path / "wrong-key"
    wrong.write_text("not-the-key", encoding="utf-8")
    w = watch.Watcher(overseer.url, wrong, doc)
    w.start()
    assert until(lambda: not w.alive)
    assert w.gave_up and "refused the agents' key" in w.gave_up


def test_a_server_away_is_tried_again_and_the_watch_stops_when_asked(tmp_path: Path):
    # A port nothing listens on: taken, then let go.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    key = tmp_path / "key"
    key.write_text("k", encoding="utf-8")
    said = []
    w = watch.Watcher(f"http://127.0.0.1:{port}", key, "a/b.os", said.append)
    w.start()
    assert until(lambda: len(said) == 1)
    assert "trying again" in said[0] and not w.watching and w.gave_up is None and w.alive
    w.stop()
    assert until(lambda: not w.alive)
