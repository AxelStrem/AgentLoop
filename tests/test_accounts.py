"""This machine's Claude accounts, against a home folder of the test's own and the fake claude."""

import json
import shutil
from pathlib import Path

import pytest

from agentloop import accounts, cli, config


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home folder of the test's own, and the app's settings and data in another."""
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setenv("USERPROFILE", str(folder))
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "app"))
    return folder


def workers(*pairs) -> list[config.Worker]:
    return [config.Worker(name, claude, None) for name, claude in pairs]


def claude_exe() -> str:
    return shutil.which("claude")


# Finding them

def test_found_lists_the_default_and_the_numbered_folders(home):
    for name in (".claude", ".claude-account1", ".claude-account2", ".claude-account-A", ".claude-other"):
        (home / name).mkdir()
    (home / ".claude-account3").write_text("not a folder")
    found = accounts.found([], home)
    assert [(f.name, f.claude) for f in found] == [
        ("default", None),
        ("account-a", "~/.claude-account-A"),
        ("account1", "~/.claude-account1"),
        ("account2", "~/.claude-account2"),
    ]


def test_found_leaves_out_what_a_worker_names(home):
    for name in (".claude", ".claude-account1", ".claude-account2"):
        (home / name).mkdir()
    taken = workers(("account2", None), ("mine", str(home / ".claude-account1")))
    found = accounts.found(taken, home)
    # The default account is taken by a null worker, account1 by its full path; account2's name is
    # taken, so the folder is offered under another.
    assert [(f.name, f.claude) for f in found] == [("account2-2", "~/.claude-account2")]


def test_claude_named_by_its_path_is_the_default_account(home):
    (home / ".claude").mkdir()
    assert accounts.normalize(str(home / ".claude")) is None
    assert accounts.normalize("~/.claude") is None
    assert accounts.normalize("default") is None
    assert accounts.normalize(str(home / ".claude-account1")) == "~/.claude-account1"
    assert accounts.found(workers(("x", "~/.claude")), home) == []
    assert accounts.suggest_name("~/.claude-account7") == "account7"
    assert accounts.suggest_name(None) == "default"


# Logged in, or not

def test_status_logged_in_and_nothing_personal_kept(home, fake_claude):
    folder = home / ".claude-account1"
    folder.mkdir()
    (folder / "fake-login").write_text("pro")
    login = accounts.status(claude_exe(), "~/.claude-account1")
    assert login == accounts.Login("in", "claude", "claude.ai", "pro")
    assert login.line() == "logged in (claude.ai, pro)"
    assert "example.com" not in repr(login)
    call = fake_claude.calls()[-1]
    assert call["argv"] == ["auth", "status", "--json"]
    assert Path(call["env"]["CLAUDE_CONFIG_DIR"]) == folder.resolve()


def test_status_logged_out(home, fake_claude):
    (home / ".claude-account1").mkdir()
    login = accounts.status(claude_exe(), "~/.claude-account1")
    assert (login.state, login.source) == ("out", "claude")


def test_status_of_the_default_account_has_no_config_dir(home, fake_claude, monkeypatch):
    (home / ".claude").mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "elsewhere"))
    monkeypatch.setenv("FAKE_CLAUDE_DEFAULT_LOGIN", "1")
    assert accounts.status(claude_exe(), None).state == "in"
    assert fake_claude.calls()[-1]["env"]["CLAUDE_CONFIG_DIR"] is None


def test_status_never_makes_a_folder(home, fake_claude):
    assert accounts.status(claude_exe(), "~/.claude-account9") == accounts.Login("no-folder", "folder")
    assert accounts.status(claude_exe(), None).state == "no-folder"
    assert not (home / ".claude-account9").exists()
    assert not (home / ".claude").exists()
    assert fake_claude.calls() == []


def test_status_falls_back_on_the_file_when_claude_cannot_answer(home, fake_claude, monkeypatch):
    folder = home / ".claude-account1"
    folder.mkdir()
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", "garbage")
    login = accounts.status(claude_exe(), "~/.claude-account1", macos=False)
    assert (login.state, login.source) == ("out", "file")
    assert login.detail == "claude auth status failed (JSONDecodeError)"
    (folder / ".credentials.json").write_text("{}")
    assert accounts.status(claude_exe(), "~/.claude-account1", macos=False).line() == "logged in (by its .credentials.json)"
    # On macOS the login is in the keychain, and the file says nothing.
    assert accounts.status(claude_exe(), "~/.claude-account1", macos=True).state == "unknown"


def test_status_ends_a_claude_that_does_not_answer(home, fake_claude, monkeypatch):
    (home / ".claude-account1").mkdir()
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", "hang")
    login = accounts.status(claude_exe(), "~/.claude-account1", timeout=3, macos=True)
    assert login.state == "unknown"
    assert login.detail == "claude auth status failed (TimeoutExpired)"


def test_status_without_claude(home):
    (home / ".claude-account1").mkdir()
    login = accounts.status(str(home / "no-claude.exe"), "~/.claude-account1", macos=True)
    assert login.detail == "claude auth status failed (FileNotFoundError)"


def test_the_login_command_for_each_shell(home):
    folder = home / "it's" / ".claude-account1"
    claude = str(folder)
    assert accounts.login_command(claude, windows=True) == f"$env:CLAUDE_CONFIG_DIR = '{str(folder.resolve()).replace(chr(39), chr(39) * 2)}'; claude auth login"
    assert accounts.login_command(claude, windows=False).endswith(" claude auth login")
    assert accounts.login_command(claude, windows=False).startswith("CLAUDE_CONFIG_DIR='")
    assert "'\"'\"'" in accounts.login_command(claude, windows=False)
    assert accounts.login_command(None, windows=True) == "Remove-Item Env:CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue; claude auth login"
    assert accounts.login_command(None, windows=False) == "env -u CLAUDE_CONFIG_DIR claude auth login"


# Adding one, and the default

def test_add_worker_keeps_the_rest_of_the_file(home):
    path = config.paths().settings
    raw = config.empty_settings()
    raw["unknown"] = {"kept": True}
    config.save_settings(raw, path)
    accounts.add_worker("account1", str(home / ".claude-account1"))
    accounts.add_worker("default", "~/.claude")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["unknown"] == {"kept": True}
    assert saved["workers"] == [
        {"name": "account1", "claude": "~/.claude-account1", "overseer": None},
        {"name": "default", "claude": None, "overseer": None},
    ]


@pytest.mark.parametrize("name, claude, says", [
    ("account1", "~/.claude-account5", 'there is a worker named "account1" already'),
    ("again", "~/.claude-account1", "worker account1 is on that account already"),
    ("again", None, "worker default is on that account already"),
    ("Not A Name", "~/.claude-account5", "is not a name"),
])
def test_add_worker_refuses(home, name, claude, says):
    accounts.add_worker("account1", "~/.claude-account1")
    accounts.add_worker("default", None)
    with pytest.raises(config.ConfigError, match=says):
        accounts.add_worker(name, claude)


def test_the_default_worker(home):
    accounts.add_worker("one", "~/.claude-account1")
    accounts.add_worker("two", "~/.claude-account2")
    assert config.load_settings().default_worker.name == "one"
    accounts.set_default("two")
    assert config.load_settings().default_worker.name == "two"
    with pytest.raises(config.ConfigError, match='no worker named "three"'):
        accounts.set_default("three")
    raw = config.load_settings().raw
    raw["defaultWorker"] = "gone"
    with pytest.raises(config.ConfigError, match='defaultWorker: no worker named "gone"'):
        config.parse_settings(raw, Path("settings.json"))


# Two loops on one account

def test_sharing_names_the_loops_on_the_same_account(home):
    accounts.add_worker("one", "~/.claude-account1")
    accounts.add_worker("two", "~/.claude-account2")
    raw = config.load_settings().raw
    # A second worker on account1, with an Overseer half: the same Claude account all the same.
    raw["workers"].append({"name": "one-server", "claude": str(home / ".claude-account1"),
                           "overseer": {"server": "http://localhost:1", "keyFile": "~/key"}})
    settings = config.save_settings(raw)
    running = {"alpha": "one-server", "beta": "two"}
    assert accounts.sharing("one", running, settings) == ["alpha"]
    assert accounts.sharing("two", running, settings) == ["beta"]
    assert accounts.sharing("one", {}, settings) == []
    assert accounts.sharing_line("one", ["alpha"]) == (
        "alpha is already running on one's account; the loops share its 5-hour and weekly usage.")
    assert accounts.sharing_line("one", []) is None


# The last usage reported

def test_last_usage_round_trip(home):
    assert accounts.last_usage("one") is None
    rate = {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.5}}}
    accounts.save_usage("one", rate, at=1000.0)
    assert accounts.last_usage("one") == (1000.0, rate)
    assert [p.name for p in accounts.usage_file("one").parent.iterdir()] == ["usage.json"]
    accounts.usage_file("one").write_text("not json")
    assert accounts.last_usage("one") is None


# The command

def test_the_accounts_command(home, fake_claude, capsys):
    (home / ".claude-account1").mkdir()
    (home / ".claude-account1" / "fake-login").write_text("max")
    (home / ".claude-account2").mkdir()
    assert cli.main(["accounts", "add", str(home / ".claude-account1")]) == 0
    assert cli.main(["accounts", "add", "~/.claude-account5", "--name", "five"]) == 0
    capsys.readouterr()
    assert cli.main(["accounts"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("* account1 ") and out[0].endswith("logged in (claude.ai, max)")
    assert out[1].startswith("  five ") and out[1].endswith("no such folder: not logged in")
    assert "log in with:" in out[2] and "claude auth login" in out[2]
    assert out[3].startswith("  account2 ") and out[3].endswith("agentloop accounts add ~/.claude-account2")
    assert cli.main(["accounts", "default", "five"]) == 0
    assert cli.main(["accounts", "login", "five"]) == 0
    assert capsys.readouterr().out.strip().endswith("claude auth login")
    assert cli.main(["accounts", "login", "nobody"]) == 2
    assert 'no worker named "nobody"' in capsys.readouterr().err
