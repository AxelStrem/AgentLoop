import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agentloop import config, psd1
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


# What each converts to, its tracker on the server: the bundle's repos, the worker's claude, and the
# loop's repos.
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
    bundle, worker, loop = psd1.convert(path, psd1.parse_file(path), _top_of)
    repos, claude, placed = CONVERTED[name]
    assert bundle == {"format": 1, "name": name, "tracker": {"server": f"someone/projects/{name}.os"}, "repos": repos, "settings": {}}
    assert worker["claude"] == claude
    # The key file is under `~` on the PC whose home holds it, and written out in full anywhere else.
    assert worker["overseer"] == {"server": SERVER, "keyFile": config.home_relative(KEY)}
    assert loop == {"name": name, "project": name, "worker": "", "repos": placed, "settings": {}}


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


def test_import_writes_bundle_worker_and_loop(tmp_path, home):
    for name in ("quill", "orchard"):
        (tmp_path / f"{name}.psd1").write_text(_psd1(name, tmp_path).read_text(encoding="utf-8"), encoding="utf-8")
        (tmp_path / f"{name}.md").write_text("# brief\n", encoding="utf-8")
    (tmp_path / "quill").mkdir()
    (tmp_path / "quill" / "look.ps1").write_text("# a tool\n", encoding="utf-8")

    first = psd1.import_psd1(tmp_path / "quill.psd1", top_of=_top_of)
    second = psd1.import_psd1(tmp_path / "orchard.psd1", top_of=_top_of)

    # Both are on account 1: the worker is made once, and keeps the server quill gave it.
    assert (first.worker, first.worker_made) == ("account1", True)
    assert (second.worker, second.worker_made) == ("account1", False)
    machine = config.load_machine(home.settings)
    assert sorted(machine.bundles) == ["orchard", "quill"]
    assert len(machine.settings.workers) == 1
    assert machine.settings.workers[0].overseer["server"] == SERVER
    assert [l.name for l in machine.settings.loops] == ["quill", "orchard"]
    assert machine.settings.loop("orchard").repos == CONVERTED["orchard"][2]
    bundle = machine.bundle("quill")
    assert bundle.folder == home.bundles / "quill"
    assert bundle.brief.read_text(encoding="utf-8") == "# brief\n"
    assert (bundle.tools / "look.ps1").is_file()
    assert not (machine.bundle("orchard").tools).exists()


def test_import_refuses_a_second_time_unless_replacing(tmp_path, home):
    path = _project(tmp_path, "heron", on_server("heron", (FIXTURES / "heron.psd1").read_text(encoding="utf-8")))
    psd1.import_psd1(path, top_of=_top_of)
    with pytest.raises(ConfigError, match='a loop named "heron" is there already'):
        psd1.import_psd1(path, top_of=_top_of)
    psd1.import_psd1(path, replace=True, top_of=_top_of)
    settings = config.load_settings(home.settings)
    assert [l.name for l in settings.loops] == ["heron"]
    assert len(settings.bundles) == 1


def test_import_to_another_folder(tmp_path, home):
    path = _project(tmp_path / "projects", "heron", on_server("heron", (FIXTURES / "heron.psd1").read_text(encoding="utf-8")), tools=True)
    (tmp_path / "projects" / "heron" / "__pycache__").mkdir()
    (tmp_path / "projects" / "heron" / "__pycache__" / "x.pyc").write_bytes(b"")
    to = tmp_path / "repo" / "bundles"
    done = psd1.import_psd1(path, top_of=_top_of, to=to)
    assert done.bundle == to / "heron"
    assert (to / "heron" / "brief.md").is_file() and (to / "heron" / "tools" / "look.ps1").is_file()
    assert not (to / "heron" / "tools" / "__pycache__").exists()
    assert not (home.bundles / "heron").exists()
    machine = config.load_machine(home.settings)
    assert machine.bundle("heron").folder == to / "heron"
    # Imported again to the same folder, it is the same bundle, replaced.
    psd1.import_psd1(path, top_of=_top_of, to=to, replace=True)
    assert len(config.load_settings(home.settings).bundles) == 1


def test_import_a_different_server_makes_another_worker(tmp_path, home):
    a = _project(tmp_path, "one", "@{ Tracker = 'a/one.os'; Server = 'https://a/'; KeyFile = 'k'; Repos = @('D:/Work/Heron'); Account = 3 }")
    b = _project(tmp_path, "two", "@{ Tracker = 'a/two.os'; Server = 'https://b'; KeyFile = 'k'; Repos = 'D:/Work/Maple'; Account = 3 }")
    assert psd1.import_psd1(a, top_of=_top_of).worker == "account3"
    assert psd1.import_psd1(b, top_of=_top_of).worker == "account3-2"
    workers = config.load_settings(home.settings).workers
    assert [w.overseer["server"] for w in workers] == ["https://a", "https://b"]


def test_import_wants_a_brief(tmp_path, home):
    path = _project(tmp_path, "heron", on_server("heron", (FIXTURES / "heron.psd1").read_text(encoding="utf-8")), brief=False)
    with pytest.raises(ConfigError, match="no brief beside it, heron.md"):
        psd1.import_psd1(path, top_of=_top_of)
    assert not home.settings.exists()


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
    bundle, _, _ = psd1.convert(path, psd1.parse_file(path), _top_of)
    assert bundle["settings"] == {
        "routes": {"low": {"model": "opus", "effort": "medium"}},
        "escalation": {"model": "opus", "effort": "high"},
        "buildAfterTriage": ["high"],
        "buildInTriageUpTo": 1,
    }


def test_the_default_account_is_no_folder(tmp_path):
    path = _project(tmp_path, "x", "@{ Tracker = 'a/x.os'; Server = 'https://a'; KeyFile = 'k'; Repos = 'D:/Work/Heron'; Account = '~/.claude' }")
    _, worker, _ = psd1.convert(path, psd1.parse_file(path), _top_of)
    assert worker["claude"] is None
