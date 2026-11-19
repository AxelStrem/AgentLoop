"""Hearing a tracker change on its server, so that an idle loop looks again at once (item
instant-auto).

An Overseer server streams a document's versions at `GET /v1/watch?document=<doc>`, as
Server-Sent Events: a `changed` event with the version it has when the stream opens, then one each
time the document's file changes - written through the server or, while anyone listens, from
outside it. A `Watcher` keeps that stream open on a thread of its own and counts the versions that
differ from the one before; the first is only what the document was, and no change. The loop reads
the count between its polls (`Run._idle`), and holds no lock and kills nothing to do it.

The stream is a convenience, never the only way work is found: a stream that drops is opened again
after a pause that grows, and a server that has no `/v1/watch`, or refuses the key, ends the watch
for good - the loop's periodic look goes on as before either way. A read that hears nothing, not
even the server's keep-alives, for `READ_TIMEOUT` is taken as a dead stream, and is also when the
thread sees that it was asked to stop: a thread blocked in a read cannot be woken portably, so it
is left to end at the next line or timeout, as a daemon that keeps no process alive.

The script has nothing like it: it ends on an empty queue.
"""

from __future__ import annotations

import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, Iterator

# The server's keep-alives come every 15 seconds; four missed is a dead stream.
READ_TIMEOUT = 60.0
# The pause before the stream is opened again, doubled after each failure up to the last.
RETRY_FIRST = 5.0
RETRY_MOST = 300.0


def events(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """(event, data) for each event of a Server-Sent Events stream, from its lines without their
    line ends. An event without a name is `message`; data lines are joined by line ends; comment
    lines - a keep-alive is one - and fields other than these two are passed over."""
    name, data = "", []
    for line in lines:
        if not line:
            if data:
                yield name or "message", "\n".join(data)
            name, data = "", []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            name = value
        elif field == "data":
            data.append(value)


class Watcher:
    """One document's stream, heard on a daemon thread from `start` until `stop`.

    `changes` counts the versions heard after the first that differ from the one before; `watching`
    says whether the stream is open now; `gave_up` holds why the watch ended for good, if it did.
    `say` is called on the watcher's own thread, so it should only keep what it is given."""

    def __init__(self, server: str, key_file: Path, document: str, say: Callable[[str], None] | None = None):
        self.url = f"{server.rstrip('/')}/v1/watch?{urllib.parse.urlencode({'document': document})}"
        self.key_file = key_file
        self.say = say or (lambda text: None)
        self.changes = 0
        self.version: str | None = None
        self.watching = False
        self.gave_up: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="agentloop-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        pause = RETRY_FIRST
        warned = False
        while not self._stop.is_set():
            try:
                self._listen()
                # A stream the server ended is opened again after the shortest pause, as a fresh
                # one: a server that ends every stream at once is not asked in a tight loop.
                pause = RETRY_FIRST / 2
            except _GiveUp as why:
                self.gave_up = str(why)
                self.say(f" The tracker's changes cannot be heard ({why}); looking at the set times only.")
                return
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as error:
                if not warned and not self._stop.is_set():
                    reason = getattr(error, "reason", error)
                    self.say(f" The tracker's changes could not be heard ({type(reason).__name__}); trying again.")
                    warned = True
            finally:
                self.watching = False
            if self._stop.wait(pause):
                return
            pause = min(pause * 2, RETRY_MOST)

    def _listen(self) -> None:
        try:
            key = self.key_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise _GiveUp(f"the key file could not be read: {type(error).__name__}") from None
        request = urllib.request.Request(self.url, headers={"Authorization": f"Bearer {key}", "Accept": "text/event-stream"})
        try:
            answer = urllib.request.urlopen(request, timeout=READ_TIMEOUT)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise _GiveUp(f"the server refused the agents' key ({error.code})") from None
            # An older server with no /v1/watch, or a document it does not know.
            if error.code in (404, 405):
                raise _GiveUp(f"the server cannot watch it ({error.code})") from None
            raise
        with answer:
            self.watching = True
            for name, data in events(self._lines(answer)):
                if name != "changed":
                    continue
                if self.version is not None and data != self.version:
                    self.changes += 1
                self.version = data

    def _lines(self, answer) -> Iterator[str]:
        while not self._stop.is_set():
            raw = answer.readline()
            if not raw:
                return
            yield raw.decode("utf-8", "replace").rstrip("\r\n")


class _GiveUp(Exception):
    """A watch that will not work however often it is tried."""
