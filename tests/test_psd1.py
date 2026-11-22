import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agentloop import config, export, psd1
from agentloop.config import ConfigError

# Made-up projects written as AgenticLoop/projects/*.psd1 are, each with a shape the importer
# must read: a tracker on the server or a file, each form of Account, a folder with a space in
# its name, a docs repository at a nested folder, and repositories on two drives.
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "psd1"
NAMES = ["ferry", "heron", "kiln", "loom", "maple", "orchard", "quill"]

SERVER = "https://tracker.example.com"
KEY = "C:\\Users\\someone\\.overseer\\agent_someone.key"
DOCS = "D:\\Work\\Notes\\shared"


def _tracker(name):
    return f"{DOCS}\\documents\\someone\\projects\\{name}.os"


# What Import-PowerShellDataFile gives for each, as run-agent-loop.ps1 reads it.
PARSED = {
    "kiln": {"Tracker": "someone/projects/kiln.os", "Server": SERVER, "KeyFile": KEY,
             "Repos": ["D:\\Work\\Kiln"], "Account": 1},
    "loom": {"Tracker": _tracker("loom"), "Repos": ["D:\\Work\\studio\\Loom Game"], "Account": 2},
    "quill": {"Tracker": "someone/projects/quill.os", "Server": SERVER, "KeyFile": KEY,
              "Repos": ["D:\\Work\\Quill"], "Account": 1},
    "ferry": {"Tracker": _tracker("ferry"),
              "Repos": ["D:\\Work\\ferry\\engine", "D:\\Work\\ferry\\engine-dev"], "Account": 2},
    "orchard": {"Tracker": _tracker("orchard"),
                "Repos": ["D:\\Work\\Orchard", "D:\\Work\\Orchard-Server", DOCS], "Account": 1},
    "heron": {"Tracker": _tracker("heron"), "Repos": ["D:\\Work\\Heron"], "Account": 2},
    "maple": {"Tracker": _tracker("maple"), "Repos": ["D:\\Work\\Maple", "F:\\Unity\\MapleOverlay"], "Account": 2},
}

# Every folder they name is the top of its repository, the docs repository included: it is
# cloned at Notes\shared.
TOPS = [r.replace("\\", "/") for p in PARSED.values() for r in p["Repos"]]


def _top_of(folder):
    text = str(folder).replace("\\", "/")
    for top in TOPS:
        if text == top or text.startswith(top + "/"):
            return Path(top)
    return None


# The projects whose .psd1 names its tracker as a file: the app refuses them as they are (item
# simplify), and takes them once the tracker is named on the server, as `on_server` writes it.
FILE_TRACKERS = ["ferry", "heron", "loom", "maple", "orchard"]


def on_server(name: str, text: str) -> str:
    """A .psd1's text with its tracker file swapped for the same tracker on the server."""
    file = f'Tracker = "{_tracker(name)}"'
    assert file in text
    return text.replace(file, f'Tracker = "someone/projects/{name}.os"\n    Server = "{SERVER}"\n    KeyFile = "{KEY}"')


def _psd1(name: str, folder: Path) -> Path:
    """The fixture, its tracker on the server: as it is, or swapped in a copy in `folder`."""
    path = FIXTURES / f"{name}.psd1"
    if name not in FILE_TRACKERS:
        return path
    copy = folder / f"{name}.psd1"
    copy.write_text(on_server(name, path.read_text(encoding="utf-8")), encoding="utf-8")
    return copy


# What each converts to, its tracker on the server: the project's repos, the Claude account its
# sessions ran on, and where each repository is.
CONVERTED = {
    "kiln": (["kiln"], "~/.claude-account1", {"kiln": "D:/Work/Kiln"}),
    "loom": (["loom-game"], "~/.claude-account2", {"loom-game": "D:/Work/studio/Loom Game"}),
    "quill": (["quill"], "~/.claude-account1", {"quill": "D:/Work/Quill"}),
    "ferry": (["engine", "engine-dev"], "~/.claude-account2",
              {"engine": "D:/Work/ferry/engine", "engine-dev": "D:/Work/ferry/engine-dev"}),
    "orchard": (["orchard", "orchard-server", "shared"], "~/.claude-account1",
                {"orchard": "D:/Work/Orchard", "orchard-server": "D:/Work/Orchard-Server",
                 "shared": "D:/Work/Notes/shared"}),
    "heron": (["heron"], "~/.claude-account2", {"heron": "D:/Work/Heron"}),
    "maple": (["maple", "mapleoverlay"], "~/.claude-account2",
              {"maple": "D:/Work/Maple", "mapleoverlay": "F:/Unity/MapleOverlay"}),
}


@pytest.mark.parametrize("name", NAMES)
def test_parses_each_psd1(name):
    assert psd1.parse_file(FIXTURES / f"{name}.psd1") == PARSED[name]


def _powershell():
    return shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(not _powershell(), reason="no PowerShell here to compare with")
@pytest.mark.parametrize("name", NAMES)
def test_parses_as_powershell_does(name):
    script = "& { param($p) Import-PowerShellDataFile -LiteralPath $p | ConvertTo-Json -Depth 5 -Compress }"
    done = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-Command", script, str(FIXTURES / f"{name}.psd1")],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert psd1.parse_file(FIXTURES / f"{name}.psd1") == json.loads(done.stdout)


@pytest.mark.parametrize("name", NAMES)
def test_converts_each_psd1(name, tmp_path):
    path = _psd1(name, tmp_path)
    found = psd1.convert(path, psd1.parse_file(path), _top_of)
    repos, claude, placed = CONVERTED[name]
    assert (found.source.name, found.source.tracker) == (name, f"someone/projects/{name}.os")
    assert (found.source.repos, found.source.places, found.source.settings) == (repos, [], {})
    assert found.claude == claude
    # The key file is under `~` on the PC whose home holds it, and written out in full anywhere else.
    assert (found.server, found.key_file) == (SERVER, config.home_relative(KEY))
    assert found.placed == placed


@pytest.mark.parametrize("name", FILE_TRACKERS)
def test_a_tracker_file_is_refused(name):
    path = FIXTURES / f"{name}.psd1"
    with pytest.raises(ConfigError, match="Tracker: is a file, and the app works only trackers on an Overseer server"):
        psd1.convert(path, psd1.parse_file(path), _top_of)


@pytest.mark.parametrize("text, expected", [
    ("@{ a = 'it''s'; b = \"q`\"\"\"x\" }", {"a": "it's", "b": 'q""x'}),
    ("@{ a = 1, 2 ,3 }", {"a": [1, 2, 3]}),
    ("@{\r\n  a = @(\r\n 'x'\r\n 'y', 'z'\r\n )\r\n}", {"a": ["x", "y", "z"]}),
    ("<# block #>@{ 'quoted key' = $true; n = -2.5; z = $NULL } # end", {"quoted key": True, "n": -2.5, "z": None}),
    ("@{ r = @{ low = @{ model = 'opus'; effort = 'medium' } } }", {"r": {"low": {"model": "opus", "effort": "medium"}}}),
    ("@{ a = @() }", {"a": []}),
])
def test_parses_the_subset(text, expected):
    assert psd1.parse(text) == expected


@pytest.mark.parametrize("text, line, says", [
    ("@{\n a = \"$HOME\\x\"\n}", 2, "variables"),
    ("@{\n a = [datetime]'2026-01-01'\n}", 2, "only hashtables"),
    ("@{\n a = 1\n A = 2\n}", 3, "twice"),
    ("@{ a = 'x' ", 1, "a ; or a new line"),
    ("'not a table'", 1, "one hashtable"),
    ("@{ a = 'x' }\n@{}", 2, "after the hashtable"),
])
def test_refuses_what_it_does_not_read(text, line, says):
    with pytest.raises(ConfigError) as error:
        psd1.parse(text, "x.psd1")
    assert str(error.value).startswith(f"x.psd1: line {line}: ")
    assert says in str(error.value)


def _project(folder, name, body, brief=True, tools=False):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.psd1"
    path.write_text(body, encoding="utf-8")
    if brief:
        (folder / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    if tools:
        (folder / name).mkdir()
        (folder / name / "look.ps1").write_text("# a tool\n", encoding="utf-8")
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    return config.paths()


def test_import_writes_the_export_and_nothing_else(tmp_path, home):
    path = _project(tmp_path / "projects", "heron", on_server("heron", (FIXTURES / "heron.psd1").read_text(encoding="utf-8")), tools=True)
    (tmp_path / "projects" / "heron" / "__pycache__").mkdir()
    (tmp_path / "projects" / "heron" / "__pycache__" / "x.pyc").write_bytes(b"")
    out = tmp_path / "out"
    done = psd1.import_psd1(path, out, "The tracker's own.", top_of=_top_of)
    report = done.report
    assert report.definition == out / "heron.project.os" and report.tracker == "someone/projects/heron.os"
    text = report.definition.read_text(encoding="utf-8")
    assert '- description = "The tracker\'s own.\\n\\n# heron\\n"' in text
    assert '- name = "heron"' in text
    assert report.tools == ["look.ps1"] and (out / "tools" / "heron.project" / "look.ps1").is_file()
    assert not (out / "tools" / "heron.project" / "__pycache__").exists()
    # The loop is added from its tracker once the export is on the server: the line says how, with
    # the .psd1's key file and repositories.
    assert done.add_line() == (f"agentloop add {SERVER}/doc/someone/projects/heron.os --key-file {config.home_relative(KEY)} "
                               "--repo heron=D:/Work/Heron")
    # This machine's settings are not touched, and no bundle is made.
    assert not home.settings.exists() and not (home.config / "bundles").exists()
    # Nothing is written over.
    with pytest.raises(ConfigError, match="is not an empty folder"):
        psd1.import_psd1(path, out, "Again.", top_of=_top_of)


def test_import_reads_the_trackers_description_with_the_psd1s_key(tmp_path, home, monkeypatch):
    path = _project(tmp_path, "heron", on_server("heron", (FIXTURES / "heron.psd1").read_text(encoding="utf-8")))
    asked = []
    monkeypatch.setattr(export, "tracker_description", lambda server, key, tracker: asked.append((server, key, tracker)) or "Read.")
    done = psd1.import_psd1(path, tmp_path / "out", top_of=_top_of)
    assert asked == [(SERVER, config.expand(config.home_relative(KEY)), "someone/projects/heron.os")]
    assert "Read." in done.report.definition.read_text(encoding="utf-8")


def test_an_argument_with_a_space_is_quoted(tmp_path, home):
    path = _project(tmp_path, "loom", on_server("loom", (FIXTURES / "loom.psd1").read_text(encoding="utf-8")))
    done = psd1.import_psd1(path, tmp_path / "out", "", top_of=_top_of)
    assert done.add_line().endswith('--repo "loom-game=D:/Work/studio/Loom Game"')


def test_import_wants_a_brief(tmp_path, home):
    path = _project(tmp_path, "heron", on_server("heron", (FIXTURES / "heron.psd1").read_text(encoding="utf-8")), brief=False)
    with pytest.raises(ConfigError, match="no brief beside it, heron.md"):
        psd1.import_psd1(path, tmp_path / "out", "", top_of=_top_of)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("body, says", [
    ("@{ Tracker = 'x.os'; Repos = @('D:/Work/Heron'); Acount = 1 }", "Acount: not a setting"),
    ("@{ Tracker = 'a/x.os'; Server = 'https://a'; Repos = @('D:/Work/Heron') }", "KeyFile:"),
    ("@{ Tracker = 'C:/nowhere/x.os'; Repos = @('D:/Work/Heron') }", "Tracker: is a file"),
    ("@{ Tracker = 'a/x.os'; Server = 'https://a'; KeyFile = 'k'; Repos = @() }", "Repos:"),
    ("@{ Tracker = 'a/x.os'; Server = 'https://a'; KeyFile = 'k'; Repos = 'D:/Work/Heron'; BuildInTriageUpTo = 'two' }",
     "settings.buildInTriageUpTo"),
])
def test_convert_says_what_is_wrong(tmp_path, body, says):
    path = _project(tmp_path, "x", body)
    with pytest.raises(ConfigError, match=says):
        psd1.convert(path, psd1.parse_file(path), _top_of)


def test_convert_takes_the_project_settings(tmp_path):
    path = _project(tmp_path, "x", """@{
        Tracker = 'a/x.os'; Server = 'https://a'; KeyFile = 'k'; Repos = 'D:/Work/Heron'
        Routes = @{ Low = @{ Model = 'opus'; Effort = 'medium' } }
        Escalation = @{ model = 'opus'; effort = 'high' }
        BuildAfterTriage = 'high'
        BuildInTriageUpTo = 1
    }""")
    found = psd1.convert(path, psd1.parse_file(path), _top_of)
    assert found.source.settings == {
        "routes": {"low": {"model": "opus", "effort": "medium"}},
        "escalation": {"model": "opus", "effort": "high"},
        "buildAfterTriage": ["high"],
        "buildInTriageUpTo": 1,
    }


def test_the_default_account_is_no_folder(tmp_path):
    path = _project(tmp_path, "x", "@{ Tracker = 'a/x.os'; Server = 'https://a'; KeyFile = 'k'; Repos = 'D:/Work/Heron'; Account = '~/.claude' }")
    assert psd1.convert(path, psd1.parse_file(path), _top_of).claude is None
