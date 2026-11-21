"""A bundle written out as its definition document and tools (item def-export): the brief split
into the description and each folder's line, the document read back through a served tracker as
the bundle it came from, and the command a person runs. Every project here is made up."""

import hashlib
import json
from pathlib import Path

import pytest

from agentloop import cli, config, definition, export
from test_loop import item, tracker_text
from test_tools_fetch import put

BRIEF = """# Kiln

The project brief for the tracker `kiln.os`: where the project's parts are, its own rules, and
how to check work on it. The workflow is in AUTOTASK.md; where the two differ, this wins.

## What it is

| | what it is |
| --- | --- |
| `{kiln}` | The app: fires "pots" at C:\\temp, `{engine}` beside it. Sessions run here. |
| `{notes}` | The notes repository. **Read only.** |

## Checking

Run `{bundle}/tools/probe.py`.
"""

PROBE = b"print('probe')\n"
LOOK = b"Write-Output 'look'\r\n"


def make_bundle(root: Path, tracker: str = "alice/projects/kiln.os", brief: str = BRIEF,
                settings: dict | None = None) -> Path:
    folder = root / "kiln"
    (folder / "tools" / "sub").mkdir(parents=True)
    (folder / "tools" / "__pycache__").mkdir()
    (folder / "project.json").write_text(json.dumps({
        "format": 1, "name": "kiln", "tracker": {"server": tracker},
        "repos": ["kiln", "notes"], "places": ["engine"], "systems": ["windows", "linux"],
        "settings": settings if settings is not None else {"chainUpTo": 2},
    }), encoding="utf-8")
    (folder / "brief.md").write_text(brief, encoding="utf-8", newline="\n")
    (folder / "tools" / "probe.py").write_bytes(PROBE)
    (folder / "tools" / "sub" / "look.ps1").write_bytes(LOOK)
    (folder / "tools" / "__pycache__" / "probe.cpython-314.pyc").write_bytes(b"\0")
    return folder


# The brief

def test_the_opening_and_the_table_leave_the_brief():
    brief = export.split_brief(BRIEF, ["kiln", "notes", "engine"])
    assert brief.opening_dropped and brief.table_dropped
    assert "The project brief for the tracker" not in brief.text
    assert "| `{kiln}`" not in brief.text
    # The heading stood only over the table, so it goes too; the rest stays as written.
    assert "## What it is" not in brief.text
    assert brief.text.startswith("# Kiln\n\n## Checking\n")
    assert "`{bundle}/tools/probe.py`" in brief.text
    assert brief.about == {"kiln": 'The app: fires "pots" at C:\\temp, `{engine}` beside it. Sessions run here.',
                           "notes": "The notes repository. **Read only.**"}


def test_a_heading_with_more_under_it_stays():
    text = BRIEF.replace("| | what it is |", "One repository, two clones.\n\n| | what it is |")
    brief = export.split_brief(text, ["kiln", "notes"])
    assert "## What it is\n\nOne repository, two clones.\n\n## Checking" in brief.text


def test_a_row_naming_no_folder_of_the_project_keeps_the_table():
    brief = export.split_brief(BRIEF, ["kiln"])
    assert brief.unknown == ["notes"] and not brief.table_dropped
    assert "| `{notes}` |" in brief.text
    assert brief.about["kiln"].startswith("The app")


def test_a_brief_with_neither_is_left_whole():
    text = "# Kiln\n\nWhat it does.\n"
    brief = export.split_brief(text, ["kiln"])
    assert brief.text == text and brief.about == {}
    assert not brief.opening_dropped and not brief.table_dropped


# The files

def test_the_definition_and_the_tools_are_written(tmp_path):
    out = tmp_path / "out"
    report = export.export(make_bundle(tmp_path), out, "Kiln fires pots.")
    assert report.definition == out / "kiln.project.os"
    assert report.tools == ["probe.py", "sub/look.ps1"]
    assert (out / "tools" / "kiln.project" / "probe.py").read_bytes() == PROBE
    assert (out / "tools" / "kiln.project" / "sub" / "look.ps1").read_bytes() == LOOK
    assert not list(out.rglob("*.pyc"))
    text = report.definition.read_text(encoding="utf-8")
    assert text.startswith("<projects/project_definition.os/definition> definition {\n")
    # A tool with CRLF is listed by its text with LF, as a copy through git on another clone has
    # it (tools-eol).
    assert hashlib.sha256(LOOK.replace(b"\r\n", b"\n")).hexdigest() in text
    assert hashlib.sha256(LOOK).hexdigest() not in text
    assert report.override == '- definition (source="kiln.project.os/definition", lazy=false)'
    assert report.document == "alice/projects/kiln.project.os"
    assert report.no_about == ["engine"]
    steps = "\n".join(report.steps("https://example.test/"))
    assert report.override in steps and "no line says what these are: engine" in steps
    assert "https://example.test/v1/file?doc=alice%2Fprojects%2Fkiln.project.os&path=sub%2Flook.ps1" in steps
    # Where a copy lands, and why the curl lines are safer (tools-404).
    assert "alice/projects/tools/kiln.project/" in steps and "line endings" in steps


def test_an_unempty_folder_is_refused(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "kiln.project.os").write_text("mine", encoding="utf-8")
    with pytest.raises(config.ConfigError) as caught:
        export.export(make_bundle(tmp_path), out, "")
    assert "not an empty folder" in caught.value.message
    assert (out / "kiln.project.os").read_text(encoding="utf-8") == "mine"


def test_a_bad_bundle_is_refused(tmp_path):
    folder = make_bundle(tmp_path)
    (folder / "project.json").write_text(json.dumps({"format": 1, "name": "other"}), encoding="utf-8")
    with pytest.raises(config.ConfigError):
        export.export(folder, tmp_path / "out", "")
    assert not (tmp_path / "out").exists()


# Read back from a server

def served_bundle(overseer, tmp_path, **bundle_args) -> tuple[str, Path]:
    """A made-up tracker `kiln.os` on the served copy, whose mount names `kiln.project.os` beside
    it as the export's step 2 says, and the bundle that names it."""
    folder = overseer.folder()
    text = tracker_text(item("alpha")).replace(
        'project (label="Proj") {\n', 'project (label="Proj") {\n    - definition (source="kiln.project.os/definition", lazy=false)\n', 1)
    tracker = overseer.document(text, project="kiln", folder=folder)
    return tracker, make_bundle(tmp_path, tracker=tracker, **bundle_args)


def test_the_definition_reads_back_as_the_bundle(overseer, tmp_path):
    description = 'Kiln fires "pots".\nIt holds $(no) expression, a `tick`, a \\ and a\ttab.'
    tracker, bundle = served_bundle(overseer, tmp_path)
    out = tmp_path / "out"
    report = export.export(bundle, out, description)
    # Laid out as the server's own folder: the document beside the tracker, and its tools.
    (overseer.root / report.document).write_bytes(report.definition.read_bytes())
    for path in report.tools:
        put(overseer, tracker, path, (report.tools_dir / path).read_bytes())

    raw = definition.fetch(overseer.url, overseer.key, tracker)
    want = config.load_bundle(bundle)
    got = definition.project(tracker, raw)
    assert got.repos == want.repos and got.places == want.places
    assert got.systems == want.systems and got.settings == want.settings
    assert got.about == export.split_brief(BRIEF, ["kiln", "notes", "engine"]).about
    assert got.description.startswith(description + "\n\n# Kiln\n")
    assert got.description == description + "\n\n" + report.brief.text
    assert [t["file"] for t in raw["tools"]] == report.tools
    assert all(t["systems"] == [] for t in raw["tools"])

    fetched = tmp_path / "fetched"
    definition.place_tools(raw, fetched, definition.file_getter(overseer.url, overseer.key, tracker))
    assert (fetched / "tools" / "probe.py").read_bytes() == PROBE
    assert (fetched / "tools" / "sub" / "look.ps1").read_bytes() == LOOK


def test_no_settings_and_no_tools_read_back(overseer, tmp_path):
    tracker, bundle = served_bundle(overseer, tmp_path, settings={})
    for path in (bundle / "tools").rglob("*"):
        if path.is_file():
            path.unlink()
    report = export.export(bundle, tmp_path / "out", "")
    assert report.tools == []
    (overseer.root / report.document).write_bytes(report.definition.read_bytes())
    raw = definition.fetch(overseer.url, overseer.key, tracker)
    assert raw["settings"] == {} and "tools" not in raw


def test_the_trackers_description_is_read(overseer, tmp_path):
    tracker, _ = served_bundle(overseer, tmp_path)
    assert export.tracker_description(overseer.url, overseer.key, tracker) == "A project for tests."


# The command

def test_the_command_writes_and_says_what_is_left(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "load_settings", lambda path=None: (_ for _ in ()).throw(config.ConfigError("settings.json", "", "none")))
    described = tmp_path / "described.md"
    described.write_text("Kiln fires pots.\n", encoding="utf-8")
    code = cli.main(["export-definition", str(make_bundle(tmp_path)), "--out", str(tmp_path / "out"),
                     "--tracker-description", str(described)])
    printed = capsys.readouterr().out
    assert code == 0
    assert (tmp_path / "out" / "kiln.project.os").is_file()
    assert '- definition (source="kiln.project.os/definition", lazy=false)' in printed
    assert "<server>/v1/file?doc=alice%2Fprojects%2Fkiln.project.os&path=probe.py" in printed


def test_the_command_without_a_description_or_a_server_is_refused(tmp_path, monkeypatch, capsys):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(config.empty_settings()), encoding="utf-8")
    real = config.load_settings
    monkeypatch.setattr(config, "load_settings", lambda path=None: real(settings_file))
    code = cli.main(["export-definition", str(make_bundle(tmp_path)), "--out", str(tmp_path / "out")])
    assert code == 2
    assert "--tracker-description" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


DOCS_EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"


def test_the_docs_example_definition_reads(overseer, tmp_path):
    """docs/examples/quill.project.os is a definition the server hands out, and its tool is the
    file beside it, so the example in the docs stays one that works."""
    folder = overseer.folder()
    text = tracker_text(item("alpha")).replace(
        'project (label="Proj") {\n', 'project (label="Proj") {\n    - definition (source="quill.project.os/definition", lazy=false)\n', 1)
    tracker = overseer.document(text, project="quill", folder=folder)
    (overseer.root / folder / "quill.project.os").write_bytes((DOCS_EXAMPLES / "quill.project.os").read_bytes())
    tool = (DOCS_EXAMPLES / "tools" / "quill.project" / "look.py").read_bytes()
    put(overseer, tracker, "look.py", tool)

    got = definition.project(tracker, definition.fetch(overseer.url, overseer.key, tracker))
    assert got.name == "quill" and got.repo_names() == ["quill"] and got.places == ["press"]
    assert got.systems == ["windows"] and got.settings == {"chainUpTo": 2}
    raw = definition.fetch(overseer.url, overseer.key, tracker)
    definition.place_tools(raw, tmp_path, definition.file_getter(overseer.url, overseer.key, tracker))
    assert (tmp_path / "tools" / "look.py").read_bytes() == tool


def test_the_docs_example_settings_load():
    settings = config.load_settings(DOCS_EXAMPLES / "settings.json")
    assert [l.tracker for l in settings.loops] == ["alice/projects/quill.os", "alice/projects/quill.os", "bob/orchard.os"]
    assert settings.loop("quill-2").places == {"press": "C:/Program Files/Press"}
    assert not settings.bundles
