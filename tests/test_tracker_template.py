"""The project template tracker.py reads from the server (item own-server): which version it is,
and what is said when the server's is not one this tracker.py knows. A small fake server answers
/v1/source, so each test can give the template - or its absence - that it is about; the tests on a
served copy of overseer-server read the real one."""
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import TRACKER_PY
from test_loop import item, tracker_text

TEMPLATE = "library:projects/project_template.os"


def entry(tag: str) -> list[str]:
    return ["        - {", f'            - tag = "{tag}"', "        }"]


def template(version_line: str | None) -> str:
    """A project template as the server hands it out, with the stages the move below looks for."""
    return "\n".join([
        "// The project template",
        "tab project {",
        *([f"    {version_line}"] if version_line else []),
        '    list Stages (label="Stages") {',
        *entry("filed"), *entry("planning"),
        "    }",
        '    list Complexities (label="Complexities") {',
        *entry("unassigned"),
        "    }",
        "}",
    ])


@pytest.fixture
def fake_server(tmp_path):
    """A server answering /v1/source from `documents`, a name to its answer; any other request is
    refused, so a test that reaches past the template's check shows it."""
    documents: dict[str, dict] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the name http.server calls
            url = urllib.parse.urlparse(self.path)
            name = urllib.parse.parse_qs(url.query).get("document", [""])[0]
            if url.path != "/v1/source" or name not in documents:
                self._answer(404, {"error": "no such document"})
                return
            self._answer(200, {"document": name, "version": "v1", "parts": {}, **documents[name]})

        def _answer(self, code, body):
            raw = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    key = tmp_path / "key"
    key.write_text("fake-key", encoding="utf-8")

    def run(*args):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_", "OVERSEER_")) and k != "AGENTIC_LOOP"}
        env.update(OVERSEER_URL=f"http://127.0.0.1:{server.server_address[1]}", OVERSEER_KEY_FILE=str(key))
        return subprocess.run([sys.executable, str(TRACKER_PY), "--doc", "a/proj.os", *args],
                              capture_output=True, text=True, encoding="utf-8", env=env)

    try:
        yield documents, run
    finally:
        server.shutdown()
        server.server_close()


def tracker_answer(bases=(TEMPLATE,)) -> dict:
    answer = {"text": tracker_text(item("alpha"))}
    if bases is not None:
        answer["bases"] = list(bases)
    return answer


def said(done) -> str:
    """What a refused command said: one line, and never a traceback."""
    assert done.returncode != 0
    assert "Traceback" not in done.stderr
    lines = done.stderr.strip().splitlines()
    assert len(lines) == 1, done.stderr
    return lines[0]


def test_a_template_of_a_known_version_is_read(fake_server):
    documents, run = fake_server
    documents["a/proj.os"] = tracker_answer()
    documents[TEMPLATE] = {"text": template("int template_version (hidden=true) = 1")}
    done = run("move", "alpha", "nowhere", "--note", "x")
    # The stages came from the template: the move is refused by its own rule, naming them.
    assert "no stage nowhere; the stages are filed, planning" in said(done)


def test_a_newer_template_asks_for_a_newer_agentloop(fake_server):
    documents, run = fake_server
    documents["a/proj.os"] = tracker_answer()
    documents[TEMPLATE] = {"text": template("int template_version (hidden=true) = 7")}
    line = said(run("move", "alpha", "planning", "--note", "x"))
    assert line == ("the server's project template (project_template.os) is version 7; "
                    "this AgentLoop knows 1: update AgentLoop")


def test_an_older_template_asks_for_a_newer_overseer(fake_server):
    documents, run = fake_server
    documents["a/proj.os"] = tracker_answer()
    documents[TEMPLATE] = {"text": template("int template_version (hidden=true) = 0")}
    line = said(run("move", "alpha", "planning", "--note", "x"))
    assert line == ("the server's project template (project_template.os) is version 0; "
                    "this AgentLoop knows 1: update the server's Overseer")


def test_a_template_from_before_versions_asks_for_a_newer_overseer(fake_server):
    documents, run = fake_server
    documents["a/proj.os"] = tracker_answer()
    documents[TEMPLATE] = {"text": template(None)}
    line = said(run("move", "alpha", "planning", "--note", "x"))
    assert "is from before template versions; this AgentLoop knows 1: update the server's Overseer" in line


def test_a_server_that_names_no_bases_is_too_old(fake_server):
    documents, run = fake_server
    documents["a/proj.os"] = tracker_answer(bases=None)
    line = said(run("move", "alpha", "planning", "--note", "x"))
    assert "proj.os inherits projects/project_template.os" in line
    assert "too old to hand out the templates a document inherits: update its Overseer" in line


def test_a_template_the_server_does_not_have_is_named(fake_server):
    documents, run = fake_server
    documents["a/proj.os"] = tracker_answer(bases=[])
    line = said(run("move", "alpha", "planning", "--note", "x"))
    assert line == "proj.os inherits projects/project_template.os, which the server does not have among its library templates"


def test_the_template_is_kept_in_the_cache_under_a_name_windows_takes(overseer, tmp_path):
    """With OVERSEER_CACHE, the template's copy is kept beside the trackers' - its `library:` name
    holds a `:`, which Windows refuses in a file name - and a second read is a 304."""
    doc = overseer.document(tracker_text(item("alpha")))
    cache = tmp_path / "cache"
    overseer.tracker(doc, "move", "alpha", "planning", "--note", "Looked.", env={"OVERSEER_CACHE": str(cache)})
    kept = [p for p in cache.rglob("*.json") if p.name == "project_template.os.json"]
    assert len(kept) == 1 and kept[0].parent.name == "library~projects"
    assert "template_version" in json.loads(kept[0].read_text(encoding="utf-8"))["text"]
    # Read again from the copy: the server answers 304 and the move still knows the stages.
    overseer.tracker(doc, "move", "alpha", "filed", "--note", "Back.", env={"OVERSEER_CACHE": str(cache)})


def test_a_tracker_read_from_its_copy_still_names_its_template(overseer, tmp_path):
    """A tracker unchanged since its copy was kept is a 304, and the copy hands back the bases the
    server named, so a move after a read still finds its template (bases-cache)."""
    doc = overseer.document(tracker_text(item("alpha")))
    env = {"OVERSEER_CACHE": str(tmp_path / "cache")}
    overseer.tracker(doc, "show", "alpha", env=env)
    kept = [p for p in (tmp_path / "cache").rglob("*.json") if p.name == "proj.os.json"]
    assert len(kept) == 1 and json.loads(kept[0].read_text(encoding="utf-8"))["bases"]
    overseer.tracker(doc, "move", "alpha", "planning", "--note", "Looked.", env=env)
    assert 'stage = "planning"' in overseer.text(doc)
