"""A project's tools fetched from beside its tracker (item tools-fetch): plain files kept beside the
definition document on a served overseer-server, listed in its Tools with their sha256 and systems,
fetched into the loop's folder for this machine's system, checked by hash, named by `{bundle}`, used
from the copy when the server is away, and shown and accepted as part of a changed definition."""

import hashlib
import http.server
import os
import threading
import urllib.parse
import urllib.request

import pytest

from agentloop import config, definition, loop
from test_definition import TrackerProject, closed_port, definition_text, served
from test_doc_change import other_system
from test_loop import item, move
from test_loop_from_url import add, machine  # noqa: F401 - machine is a fixture

PROBE = b"print('probe')\n"
LOOK = b"Write-Output 'look'\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(overseer, tracker: str, path: str, data: bytes) -> None:
    """A file kept beside the tracker's definition document, as its owner keeps one."""
    query = urllib.parse.urlencode({"doc": definition.document(tracker), "path": path})
    request = urllib.request.Request(f"{overseer.url}/v1/file?{query}", data=data, method="PUT",
                                     headers={"Authorization": f"Bearer {overseer.key.read_text(encoding='utf-8').strip()}"})
    urllib.request.urlopen(request, timeout=10).read()


def with_tools(overseer, *files: tuple[str, bytes, str], **definition_args) -> str:
    """A tracker whose definition lists `files` - (path, bytes, systems) - each kept on the server."""
    tools = tuple((path, sha(data), on) for path, data, on in files)
    tracker = served(overseer, item("alpha", "ready", complexity="low"), tools=tools, **definition_args)
    for path, data, _ in files:
        put(overseer, tracker, path, data)
    return tracker


def rewrite(overseer, tracker: str, *files: tuple[str, bytes, str]) -> None:
    """The definition written again with these tools, each kept on the server."""
    for path, data, _ in files:
        put(overseer, tracker, path, data)
    (overseer.root / definition.document(tracker)).write_text(
        definition_text(tools=tuple((path, sha(data), on) for path, data, on in files)), encoding="utf-8", newline="\n")


def getter(overseer, tracker: str):
    return definition.file_getter(overseer.url, overseer.key, tracker)


# The manifest

def test_the_tools_are_read_with_the_definition(overseer):
    tracker = with_tools(overseer, ("tests/probe.py", PROBE, ""), ("look.ps1", LOOK, "windows"))
    raw = definition.fetch(overseer.url, overseer.key, tracker)
    assert raw["tools"] == [{"file": "tests/probe.py", "sha256": sha(PROBE), "systems": []},
                            {"file": "look.ps1", "sha256": sha(LOOK), "systems": ["windows"]}]


@pytest.mark.parametrize("tool, field", [
    ({"file": "../x.py", "sha256": "a" * 64, "systems": []}, "tools[0].file"),
    ({"file": "/etc/x", "sha256": "a" * 64, "systems": []}, "tools[0].file"),
    ({"file": "C:/x.py", "sha256": "a" * 64, "systems": []}, "tools[0].file"),
    ({"file": "a\\x.py", "sha256": "a" * 64, "systems": []}, "tools[0].file"),
    ({"file": "a//x.py", "sha256": "a" * 64, "systems": []}, "tools[0].file"),
    ({"file": "", "sha256": "a" * 64, "systems": []}, "tools[0].file"),
    ({"file": "x.py", "sha256": "ab12", "systems": []}, "tools[0].sha256"),
    ({"file": "x.py", "sha256": "a" * 64, "systems": ["plan9"]}, "tools[0].systems"),
])
def test_a_tool_that_fails_its_checks_names_the_field(tool, field):
    raw = {"repos": ["code"], "tools": [tool]}
    with pytest.raises(config.ConfigError) as caught:
        config.check_project(raw, "d")
    assert caught.value.field == field


def test_a_tool_named_twice_is_refused():
    tool = {"file": "x.py", "sha256": "a" * 64, "systems": []}
    with pytest.raises(config.ConfigError) as caught:
        config.check_project({"repos": ["code"], "tools": [tool, {**tool, "file": "X.py"}]}, "d")
    assert caught.value.field == "tools"


# Fetching

def test_the_tools_for_this_system_are_fetched_with_the_copy(overseer, tmp_path):
    tracker = with_tools(overseer, ("tests/probe.py", PROBE, ""), ("look.ps1", LOOK, other_system()))
    raw = definition.fetch(overseer.url, overseer.key, tracker)
    assert definition.settle(tmp_path, tracker, raw, "auto", getter(overseer, tracker)) == "first"
    tools = tmp_path / definition.FILES / "tools"
    assert (tools / "tests" / "probe.py").read_bytes() == PROBE
    # A tool for another system is not fetched.
    assert not (tools / "look.ps1").exists()
    found = definition.project(tracker, raw, loop_dir=tmp_path)
    assert found.files == tmp_path / definition.FILES and found.tools == tools
    # One changed on this machine is fetched again; the rest are left.
    (tools / "tests" / "probe.py").write_bytes(b"tampered")
    assert definition.settle(tmp_path, tracker, raw, "auto", getter(overseer, tracker)) == "same"
    assert (tools / "tests" / "probe.py").read_bytes() == PROBE


def test_a_definition_with_no_tools_for_this_system_has_no_folder(overseer, tmp_path):
    tracker = with_tools(overseer, ("look.ps1", LOOK, other_system()))
    raw = definition.fetch(overseer.url, overseer.key, tracker)
    definition.settle(tmp_path, tracker, raw, "auto", getter(overseer, tracker))
    assert not (tmp_path / definition.FILES).exists()
    assert definition.project(tracker, raw, loop_dir=tmp_path).files is None


def test_a_file_that_is_not_the_one_listed_is_not_used(overseer, tmp_path):
    tracker = with_tools(overseer, ("tests/probe.py", PROBE, ""))
    put(overseer, tracker, "tests/probe.py", b"something else")
    raw = definition.fetch(overseer.url, overseer.key, tracker)
    with pytest.raises(config.ConfigError) as caught:
        definition.settle(tmp_path, tracker, raw, "auto", getter(overseer, tracker))
    assert caught.value.field == "tools" and "tests/probe.py" in caught.value.message
    assert overseer.url not in str(caught.value) and "127.0.0.1" not in str(caught.value)
    # Nothing was written: no copy, no tools.
    assert definition.kept(tmp_path, tracker) is None
    assert not (tmp_path / definition.FILES).exists() and not (tmp_path / (definition.FILES + ".new")).exists()


def test_a_file_the_server_does_not_keep_names_it(overseer, tmp_path):
    tracker = served(overseer, item("alpha"), tools=(("gone.py", sha(PROBE), ""),))
    raw = definition.fetch(overseer.url, overseer.key, tracker)
    with pytest.raises(config.ConfigError) as caught:
        definition.settle(tmp_path, tracker, raw, "auto", getter(overseer, tracker))
    assert "gone.py" in caught.value.message
    # Where the server looks for it, relative to its root, so the owner can compare (tools-404).
    folder, _, leaf = tracker.rpartition("/")
    assert f"{folder}/tools/{leaf[:-len('.os')]}.project/gone.py" in caught.value.message
    assert "older" not in caught.value.message


def test_a_server_without_the_file_route_is_called_older(tmp_path):
    """A bare 404 - no JSON refusal of Overseer's - is a server with no /v1/file (tools-404)."""
    class Bare(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Bare)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        key = tmp_path / "key"
        key.write_text("k", encoding="utf-8")
        get = definition.file_getter(f"http://127.0.0.1:{server.server_address[1]}", key, "a/p.os", timeout=5)
        with pytest.raises(config.ConfigError) as caught:
            get("x.py")
    finally:
        server.shutdown()
        server.server_close()
    assert "no /v1/file" in caught.value.message and "older" in caught.value.message
    assert "x.py" in caught.value.message and "127.0.0.1" not in caught.value.message


def test_the_kept_path_is_beside_the_definition():
    assert definition.kept_path("a/b/p.project.os", "sub/x.ps1") == "a/b/tools/p.project/sub/x.ps1"
    assert definition.kept_path("p.project.os", "x.py") == "tools/p.project/x.py"


def test_a_server_away_while_fetching_is_unreachable(tmp_path):
    key = tmp_path / "key"
    key.write_text("k", encoding="utf-8")
    raw = {"repos": [{"name": "code"}], "tools": [{"file": "x.py", "sha256": sha(PROBE), "systems": []}]}
    with pytest.raises(definition.Unreachable) as caught:
        definition.place_tools(raw, tmp_path / "project", definition.file_getter(closed_port(), key, "a/p.os", timeout=5))
    assert "127.0.0.1" not in str(caught.value)


# A changed tool

def test_a_changed_tool_waits_with_its_definition_and_is_shown(overseer, tmp_path):
    tracker = with_tools(overseer, ("tests/probe.py", PROBE, ""))
    get = getter(overseer, tracker)
    definition.settle(tmp_path, tracker, definition.fetch(overseer.url, overseer.key, tracker), "ask", get)
    changed = b"print('probe')\nprint('more')\n"
    rewrite(overseer, tracker, ("tests/probe.py", changed, ""), ("look.ps1", LOOK, ""))
    raw = definition.fetch(overseer.url, overseer.key, tracker)
    assert definition.settle(tmp_path, tracker, raw, "ask", get) == "pending"
    # The accepted tools are left as they are until the change is accepted.
    assert (tmp_path / definition.FILES / "tools" / "tests" / "probe.py").read_bytes() == PROBE
    assert (tmp_path / definition.FILES_PENDING / "tools" / "tests" / "probe.py").read_bytes() == changed
    shown = definition.change(tmp_path, tracker)
    tools = shown["diff"]["tools"]
    assert tools["added"] == [{"file": "look.ps1", "systems": []}] and tools["removed"] == []
    assert tools["changed"][0]["file"] == "tests/probe.py" and "+print('more')" in tools["changed"][0]["lines"]
    lines = definition.diff_lines(shown["diff"])
    assert "+ tool look.ps1" in lines and "~ tool tests/probe.py changed:" in lines and "  +print('more')" in lines
    assert definition.accept(tmp_path, tracker) == "accepted"
    assert (tmp_path / definition.FILES / "tools" / "tests" / "probe.py").read_bytes() == changed
    assert (tmp_path / definition.FILES / "tools" / "look.ps1").read_bytes() == LOOK
    assert not (tmp_path / definition.FILES_PENDING).exists()


def test_a_changed_tool_used_at_once_keeps_the_old_one_until_dismissed(overseer, tmp_path):
    tracker = with_tools(overseer, ("tests/probe.py", PROBE, ""))
    get = getter(overseer, tracker)
    definition.settle(tmp_path, tracker, definition.fetch(overseer.url, overseer.key, tracker), "auto", get)
    changed = b"print('changed')\n"
    rewrite(overseer, tracker, ("tests/probe.py", changed, ""))
    assert definition.settle(tmp_path, tracker, definition.fetch(overseer.url, overseer.key, tracker), "auto", get) == "changed"
    assert (tmp_path / definition.FILES / "tools" / "tests" / "probe.py").read_bytes() == changed
    assert (tmp_path / definition.FILES_PREVIOUS / "tools" / "tests" / "probe.py").read_bytes() == PROBE
    lines = definition.change(tmp_path, tracker)["diff"]["tools"]["changed"][0]["lines"]
    assert "-print('probe')" in lines and "+print('changed')" in lines
    assert definition.accept(tmp_path, tracker) == "dismissed"
    assert not (tmp_path / definition.FILES_PREVIOUS).exists()


# Line endings (item tools-eol)

PROBE_CRLF = b"print('probe')\r\nprint('again')\r\n"
PROBE_LF = PROBE_CRLF.replace(b"\r\n", b"\n")
# Binary: a NUL early on, so its bytes are hashed as they are.
BLOB = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + bytes(range(256))


def listed_as(overseer, path: str, listed: str, served_bytes: bytes) -> str:
    """A tracker whose definition lists `path` by the hash `listed`, the server keeping `served_bytes`."""
    tracker = served(overseer, item("alpha", "ready", complexity="low"), tools=((path, listed, ""),))
    put(overseer, tracker, path, served_bytes)
    return tracker


def test_a_text_tools_hash_sets_its_line_endings_aside():
    assert definition.tool_sha256(PROBE_CRLF) == definition.tool_sha256(PROBE_LF) == sha(PROBE_LF)
    assert definition.tool_sha256(b"a\rb\r") == sha(b"a\nb\n")
    # A binary file's bytes are its own, CRs and all.
    assert definition.tool_sha256(BLOB) == sha(BLOB)
    assert definition.tool_matches(PROBE_LF, sha(PROBE_CRLF)) and definition.tool_matches(PROBE_CRLF, sha(PROBE_LF))
    assert not definition.tool_matches(BLOB.replace(b"\r\n", b"\n"), sha(BLOB))


@pytest.mark.parametrize("listed, kept", [(PROBE_CRLF, PROBE_LF), (PROBE_LF, PROBE_CRLF)],
                         ids=["listed-crlf-kept-lf", "listed-lf-kept-crlf"])
def test_a_tool_whose_line_endings_changed_on_the_way_is_used(overseer, tmp_path, listed, kept):
    # Listed by the raw hash of one copy, as definitions exported before tools-eol are, and kept
    # on the server as git on another clone left it.
    tracker = listed_as(overseer, "tests/probe.py", sha(listed), kept)
    raw = definition.fetch(overseer.url, overseer.key, tracker)
    assert definition.settle(tmp_path, tracker, raw, "auto", getter(overseer, tracker)) == "first"
    # Written as the server gave it, and found right on the next start.
    assert (tmp_path / definition.FILES / "tools" / "tests" / "probe.py").read_bytes() == kept
    assert definition.tool_problems(raw, tmp_path / definition.FILES) == []


def test_a_binary_tool_with_one_byte_changed_is_refused(overseer, tmp_path):
    changed = BLOB[:-1] + b"\0"
    tracker = listed_as(overseer, "logo.png", sha(BLOB), changed)
    with pytest.raises(config.ConfigError) as caught:
        definition.settle(tmp_path, tracker, definition.fetch(overseer.url, overseer.key, tracker), "auto", getter(overseer, tracker))
    assert "logo.png" in caught.value.message
    # Nor is a binary file's CR read away: its CRLF turned to LF is another file.
    tracker = listed_as(overseer, "logo.png", sha(BLOB), BLOB.replace(b"\r\n", b"\n"))
    with pytest.raises(config.ConfigError):
        definition.settle(tmp_path / "b", tracker, definition.fetch(overseer.url, overseer.key, tracker), "auto", getter(overseer, tracker))


def test_a_text_tool_with_a_real_change_is_refused(overseer, tmp_path):
    tracker = listed_as(overseer, "tests/probe.py", sha(PROBE_CRLF), PROBE_LF.replace(b"again", b"other"))
    with pytest.raises(config.ConfigError) as caught:
        definition.settle(tmp_path, tracker, definition.fetch(overseer.url, overseer.key, tracker), "auto", getter(overseer, tracker))
    assert caught.value.field == "tools" and "tests/probe.py" in caught.value.message


def test_a_tool_listed_again_by_its_normalised_hash_is_no_change_to_review(overseer, tmp_path):
    # A definition exported before tools-eol, then again after: the same file, listed by another hash.
    tracker = with_tools(overseer, ("tests/probe.py", PROBE_CRLF, ""))
    get = getter(overseer, tracker)
    definition.settle(tmp_path, tracker, definition.fetch(overseer.url, overseer.key, tracker), "ask", get)
    (overseer.root / definition.document(tracker)).write_text(
        definition_text(tools=(("tests/probe.py", definition.tool_sha256(PROBE_CRLF), ""),)), encoding="utf-8", newline="\n")
    put(overseer, tracker, "tests/probe.py", PROBE_LF)
    # It waits, as any change to the definition does, but has no tool in it to review.
    assert definition.settle(tmp_path, tracker, definition.fetch(overseer.url, overseer.key, tracker), "ask", get) == "pending"
    assert definition.change(tmp_path, tracker)["diff"]["tools"]["changed"] == []
    # A real change, its line endings changed too, is reported as changed, with its text diffed.
    changed = PROBE_LF.replace(b"again", b"other")
    rewrite(overseer, tracker, ("tests/probe.py", changed, ""))
    assert definition.settle(tmp_path, tracker, definition.fetch(overseer.url, overseer.key, tracker), "ask", get) == "pending"
    tools = definition.change(tmp_path, tracker)["diff"]["tools"]["changed"]
    assert tools[0]["file"] == "tests/probe.py" and tools[0]["contents"]
    assert "-print('again')" in tools[0]["lines"] and "+print('other')" in tools[0]["lines"]


@pytest.mark.skipif(os.name != "nt", reason="batch files are given CRLF on Windows alone")
def test_a_batch_file_fetched_with_lf_is_written_with_crlf_on_windows(overseer, tmp_path):
    script = b"@echo off\ngoto end\n:end\necho done\n"
    tracker = with_tools(overseer, ("run.cmd", script, ""))
    raw = definition.fetch(overseer.url, overseer.key, tracker)
    definition.settle(tmp_path, tracker, raw, "auto", getter(overseer, tracker))
    assert (tmp_path / definition.FILES / "tools" / "run.cmd").read_bytes() == script.replace(b"\n", b"\r\n")
    assert definition.tool_problems(raw, tmp_path / definition.FILES) == []


def test_a_definition_without_tools_keeps_its_version():
    # A loop's copy read before tools were is no change once they are: none listed, none in it.
    node = {"children": [{"name": "Repos", "children": [{"children": [{"name": "name", "parameters": {"value": {"String": "c"}}}]}]},
                         {"name": "Tools", "children": []}]}
    assert "tools" not in definition.from_node(node, "d")


# The loop

def test_a_session_is_told_where_the_tools_are(tmp_path, monkeypatch, fake_claude, overseer):
    items = [item("alpha", "ready", complexity="low")]
    tools = (("tests/probe.py", sha(PROBE), ""),)
    p = TrackerProject(tmp_path, monkeypatch, fake_claude, overseer, items, tools=tools,
                       description="Run `{bundle}/tools/tests/probe.py`.")
    put(overseer, p.tracker, "tests/probe.py", PROBE)
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    assert p.go() == 0
    folder = p.r.loop_dir / definition.FILES
    assert (folder / "tools" / "tests" / "probe.py").read_bytes() == PROBE
    slashed = str(folder.resolve()).replace("\\", "/")
    system = p.r.system_file.read_text(encoding="utf-8")
    assert f"Run `{slashed}/tools/tests/probe.py`." in system
    assert f"- **The project's tools** (`bundle`): `{slashed}`" in system


def test_a_run_offline_uses_the_tools_fetched_and_refuses_a_missing_one(tmp_path, monkeypatch, fake_claude, overseer):
    items = [item("alpha", "ready", complexity="low")]
    p = TrackerProject(tmp_path, monkeypatch, fake_claude, overseer, items, tools=(("probe.py", sha(PROBE), ""),))
    put(overseer, p.tracker, "probe.py", PROBE)
    p.run()
    p.point_at(closed_port())
    r = p.run()
    assert "starts from the copy" in r.definition_note and r.project_def.tools == r.loop_dir / definition.FILES / "tools"
    (r.loop_dir / definition.FILES / "tools" / "probe.py").unlink()
    with pytest.raises(loop.LoopError) as caught:
        p.run()
    assert "probe.py is missing" in str(caught.value) and "does not start" in str(caught.value)


def test_a_tampered_tool_refuses_the_start(tmp_path, monkeypatch, fake_claude, overseer):
    items = [item("alpha", "ready", complexity="low")]
    p = TrackerProject(tmp_path, monkeypatch, fake_claude, overseer, items, tools=(("probe.py", sha(PROBE), ""),))
    put(overseer, p.tracker, "probe.py", b"not the probe")
    with pytest.raises(loop.LoopError) as caught:
        p.run()
    assert "probe.py" in str(caught.value) and "sha256" in str(caught.value)


# Adding a loop

def test_the_preview_lists_the_tools(machine, overseer):
    tracker = with_tools(overseer, ("probe.py", PROBE, ""), ("look.ps1", LOOK, other_system()))
    shown = definition.preview(config.load_settings(), tracker, None, "w")
    assert shown["tools"] == [{"file": "probe.py", "systems": [], "here": True},
                              {"file": "look.ps1", "systems": [other_system()], "here": False}]


def test_a_loop_added_has_its_tools_fetched(machine, overseer):
    tracker = with_tools(overseer, ("probe.py", PROBE, ""))
    add(machine, tracker)
    assert (loop.loop_dir("proj", "proj") / definition.FILES / "tools" / "probe.py").read_bytes() == PROBE


def test_a_tampered_tool_refuses_the_add(machine, overseer):
    tracker = with_tools(overseer, ("probe.py", PROBE, ""))
    put(overseer, tracker, "probe.py", b"not the probe")
    with pytest.raises(definition.Refusals) as caught:
        add(machine, tracker)
    assert [p.field for p in caught.value.problems] == ["tools"] and "probe.py" in caught.value.message
    assert config.load_settings().loops == []
    assert definition.kept(loop.loop_dir("proj", "proj"), tracker) is None

