"""tracker.py's copy of each tracker it reads: kept in OVERSEER_CACHE with its version, and read
again from the server only once the text changed - against a stub answering as the server does, and
on the tests' own server."""

import importlib.util
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import TRACKER_PY
from test_loop import item, tracker_text


def _tracker_module():
    spec = importlib.util.spec_from_file_location("tracker_under_test", TRACKER_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Stub:
    """A server with one document, answering GET /v1/source as Overseer does: 304 with no body when
    `since` names the tag it has, else the text, its version and its parts. The tag is the version,
    with the part names folded in once there are any (overseer:histparts). It counts what it was
    asked."""

    def __init__(self):
        self.text = "first"
        self.version = "v1"
        self.parts = {}
        self.asked = []  # (since, status) of each read
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                since = query.get("since", [None])[0]
                if since == stub.tag:
                    stub.asked.append((since, 304))
                    self.send_response(304)
                    self.send_header("ETag", f'"{stub.tag}"')
                    self.end_headers()
                    return
                stub.asked.append((since, 200))
                body = json.dumps({"document": query["document"][0], "version": stub.version, "text": stub.text,
                                   "parts": stub.parts}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("ETag", f'"{stub.tag}"')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    @property
    def tag(self):
        names = [n for list_ in self.parts.values() for n in list_]
        return f"{self.version}+{len(names)}" if names else self.version


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.http.shutdown()
    s.http.server_close()


@pytest.fixture
def server(stub, tmp_path):
    key = tmp_path / "key"
    key.write_text("stub-key", encoding="utf-8")
    return _tracker_module().Server(stub.url, str(key))


def test_without_a_cache_folder_every_read_is_whole(stub, server, monkeypatch):
    monkeypatch.delenv("OVERSEER_CACHE", raising=False)
    assert server.source("a/p.os")["text"] == "first"
    assert server.source("a/p.os")["text"] == "first"
    assert stub.asked == [(None, 200), (None, 200)]


def test_an_unchanged_tracker_is_not_sent_again(stub, server, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("OVERSEER_CACHE", str(cache))
    first = server.source("a/p.os")
    again = server.source("a/p.os")
    assert first == again == {"document": "a/p.os", "version": "v1", "tag": "v1", "parts": {}, "text": "first", "bases": None}
    assert stub.asked == [(None, 200), ("v1", 304)]
    copies = list(cache.rglob("*.json"))
    assert len(copies) == 1 and copies[0].name == "p.os.json" and copies[0].parent.name == "a"
    # Nothing of the key is kept with the copy.
    assert "stub-key" not in copies[0].read_text(encoding="utf-8")

    stub.text, stub.version = "second", "v2"
    assert server.source("a/p.os")["text"] == "second"
    assert server.source("a/p.os")["text"] == "second"
    assert stub.asked[2:] == [("v1", 200), ("v2", 304)]
    assert not list(cache.rglob("*.tmp"))


def test_a_document_with_parts_is_asked_for_by_its_tag(stub, server, tmp_path, monkeypatch):
    """The server folds the part names into the ETag, not the version: a copy naming the version
    would be sent whole on every read, and one naming the tag sees a new part straight away."""
    monkeypatch.setenv("OVERSEER_CACHE", str(tmp_path / "cache"))
    stub.parts = {"History": ["a/p.parts/History/2026-W44.os"]}
    assert server.source("a/p.os")["parts"] == stub.parts
    assert server.source("a/p.os")["parts"] == stub.parts
    assert stub.asked == [(None, 200), ("v1+1", 304)]

    stub.parts = {"History": ["a/p.parts/History/2026-W44.os", "a/p.parts/History/2026-W45.os"]}
    assert server.source("a/p.os")["parts"] == stub.parts
    assert stub.asked[2:] == [("v1+1", 200)]


def test_a_copy_kept_before_tags_names_its_version(stub, server, tmp_path, monkeypatch):
    monkeypatch.setenv("OVERSEER_CACHE", str(tmp_path / "cache"))
    server.source("a/p.os")
    copy = next((tmp_path / "cache").rglob("p.os.json"))
    copy.write_text(json.dumps({"document": "a/p.os", "version": "v1", "text": "first", "bases": None}),
                    encoding="utf-8")
    assert server.source("a/p.os") == {"document": "a/p.os", "version": "v1", "tag": "v1", "parts": {}, "text": "first", "bases": None}
    assert stub.asked == [(None, 200), ("v1", 304)]


def test_a_copy_kept_without_its_bases_is_read_again_whole(stub, server, tmp_path, monkeypatch):
    """A copy kept before it held `bases` would hand a 304 back with none, which reads as a server
    too old to name them (bases-cache): it is read whole once, and kept again with them."""
    monkeypatch.setenv("OVERSEER_CACHE", str(tmp_path / "cache"))
    server.source("a/p.os")
    copy = next((tmp_path / "cache").rglob("p.os.json"))
    copy.write_text(json.dumps({"document": "a/p.os", "version": "v1", "tag": "v1", "parts": {}, "text": "first"}),
                    encoding="utf-8")
    server.source("a/p.os")
    assert stub.asked == [(None, 200), (None, 200)]
    assert "bases" in json.loads(copy.read_text(encoding="utf-8"))
    server.source("a/p.os")
    assert stub.asked[2:] == [("v1", 304)]


def test_a_broken_copy_is_read_again_from_the_server(stub, server, tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("OVERSEER_CACHE", str(cache))
    server.source("a/p.os")
    copy = next(cache.rglob("p.os.json"))
    copy.write_text("{not json", encoding="utf-8")
    assert server.source("a/p.os")["text"] == "first"
    assert stub.asked == [(None, 200), (None, 200)]
    assert json.loads(copy.read_text(encoding="utf-8"))["version"] == "v1"


def test_two_servers_keep_copies_apart(stub, server, tmp_path, monkeypatch):
    monkeypatch.setenv("OVERSEER_CACHE", str(tmp_path / "cache"))
    server.source("a/p.os")
    other = Stub()
    try:
        other.text = "elsewhere"
        elsewhere = _tracker_module().Server(other.url, str(tmp_path / "key"))
        assert elsewhere.source("a/p.os")["text"] == "elsewhere"
        assert other.asked == [(None, 200)]
    finally:
        other.http.shutdown()
        other.http.server_close()


def test_the_tracker_is_read_from_its_copy_until_a_write_changes_it(overseer, tmp_path):
    doc = overseer.document(tracker_text(item("alpha")))
    cache = {"OVERSEER_CACHE": str(tmp_path / "cache")}
    first = overseer.tracker(doc, "show", "alpha", env=cache).stdout
    copy = next((tmp_path / "cache").rglob("proj.os.json"))
    held = json.loads(copy.read_text(encoding="utf-8"))
    assert held["document"] == doc and held["text"].replace("\r\n", "\n") == overseer.text(doc).replace("\r\n", "\n")
    assert overseer.tracker(doc, "show", "alpha", env=cache).stdout == first

    # A title only the copy has shows whether the server answered 304 - the copy was read - or
    # sent the text again, as a server built before overseer:source304 does.
    copy.write_text(json.dumps({**held, "text": held["text"].replace("The alpha item", "The kept alpha item")}),
                    encoding="utf-8")
    if "The kept alpha item" not in overseer.tracker(doc, "show", "alpha", env=cache).stdout:
        pytest.skip("the tests' overseer-server sends no 304")

    overseer.tracker(doc, "note", "alpha", "A comment after the copy was kept", env=cache)
    assert "A comment after the copy was kept" in overseer.tracker(doc, "show", "alpha", env=cache).stdout
    assert json.loads(copy.read_text(encoding="utf-8"))["version"] != held["version"]
