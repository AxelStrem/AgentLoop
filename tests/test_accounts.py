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


def test_status_never_makes_a_folder(home, fake_claude, monkeypatch):
    for name in config.SIGN_IN.values():
        monkeypatch.delenv(name, raising=False)
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


# A token or a key (item claude-container)

def test_status_with_a_token_needs_no_folder_and_shows_no_secret(home, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_ENV", "CLAUDE_CODE_OAUTH_TOKEN,ANTHROPIC_API_KEY")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "inherited-key")
    login = accounts.status(claude_exe(), None, sign_in={"token": "MY_TOKEN"}, environ={"MY_TOKEN": "tok-secret"})
    assert (login.state, login.method) == ("in", "oauth_token")
    assert "tok-secret" not in login.line()
    env = fake_claude.calls()[-1]["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-secret" and env["ANTHROPIC_API_KEY"] is None
    key = accounts.status(claude_exe(), None, sign_in={"apiKey": "MY_KEY"}, environ={"MY_KEY": "k"})
    assert (key.state, key.method) == ("in", "api_key")


def test_status_with_a_token_the_app_was_started_with_needs_no_folder(home, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_ENV", "CLAUDE_CODE_OAUTH_TOKEN")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-secret")
    login = accounts.status(claude_exe(), None)
    assert (login.state, login.method) == ("in", "oauth_token")
    assert not (home / ".claude").exists()


def test_status_with_a_variable_not_set_asks_no_claude(home, fake_claude):
    login = accounts.status(claude_exe(), None, sign_in={"token": "MY_TOKEN"}, environ={})
    assert login.line() == "not signed in: MY_TOKEN is not set"
    assert fake_claude.calls() == []


def test_the_sign_in_line_names_the_variable_never_the_secret(home):
    assert accounts.sign_in_line(None, {"token": "T"}, {"T": "secret"}) == "signs in with a token from T"
    assert accounts.sign_in_line(None, {"token": "T"}, {}) == "signs in with a token from T - T is not set"
    assert accounts.sign_in_line(None, {"apiKey": "K"}, {"K": "s"}) == "signs in with an API key, billed per use, from K"
    assert accounts.sign_in_line(None, None, {"CLAUDE_CODE_OAUTH_TOKEN": "s"}) == \
        "signs in with a token from CLAUDE_CODE_OAUTH_TOKEN, as the app was started"
    said = accounts.sign_in_line("~/.claude-account1", None, {})
    assert said.startswith("signs in by the login in ") and said.endswith("/.claude-account1")
    assert "setup-token" in accounts.sign_in_hint({"token": "T"}) and "T" in accounts.sign_in_hint({"token": "T"})


def test_login_run_makes_the_folder_and_runs_claude_here(home, monkeypatch):
    ran = []

    def run(argv, env):
        ran.append((argv, env))
        return accounts.subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(accounts.subprocess, "run", run)
    assert accounts.run_login("claude", "~/.claude-account3", None) == 0
    argv, env = ran[-1]
    assert argv == ["claude", "auth", "login"]
    assert (home / ".claude-account3").is_dir()
    assert Path(env["CLAUDE_CONFIG_DIR"]) == (home / ".claude-account3").resolve()
    assert accounts.run_login("claude", None, {"token": "T"}) == 0
    assert ran[-1][0] == ["claude", "setup-token"]
    with pytest.raises(accounts.session.SessionError):
        accounts.run_login("claude", None, {"apiKey": "K"})


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
    assert "signs in by the login in " in out[1]
    assert out[2].startswith("  five ") and out[2].endswith("no such folder: not logged in")
    assert "log in with:" in out[4] and "claude auth login" in out[4]
    assert out[5].startswith("  account2 ") and out[5].endswith("agentloop accounts add ~/.claude-account2")
    assert cli.main(["accounts", "default", "five"]) == 0
    assert cli.main(["accounts", "login", "five"]) == 0
    assert capsys.readouterr().out.strip().endswith("claude auth login")
    assert cli.main(["accounts", "login", "nobody"]) == 2
    assert 'no worker named "nobody"' in capsys.readouterr().err


# Going on past a worker's limits (item h87x)

def test_approving_a_limit(home):
    worker = config.Worker("one", None, None, limits={"five_hour": 0.8, "seven_day": 0.9})
    now = 1000.0
    assert accounts.over_limit(worker, now) is None and accounts.approvals("one") == {}
    with pytest.raises(ValueError, match="one is not past any of its limits"):
        accounts.approve(worker, now=now)
    accounts.save_usage("one", {"unifiedWindows": {"five_hour": {"utilization": 0.85, "resetsAt": now + 600},
                                                   "seven_day": {"utilization": 0.95, "resetsAt": now + 9000}}}, at=now)
    assert accounts.over_limit(worker, now).window == "five_hour"
    # The weekly one asked of by name, though the 5-hour comes first.
    assert accounts.approve(worker, "seven_day", now=now).window == "seven_day"
    assert accounts.approvals("one") == {"seven_day": now + 9000}
    assert accounts.over_limit(worker, now).window == "five_hour"
    assert accounts.approve(worker, now=now).window == "five_hour"
    assert accounts.over_limit(worker, now) is None
    with pytest.raises(ValueError, match="not past its 5-hour limit"):
        accounts.approve(worker, "five_hour", now=now)
    # Once the 5-hour window resets its approval lapses, and is let go of at the next.
    later = now + 700
    accounts.save_usage("one", {"unifiedWindows": {"five_hour": {"utilization": 0.9, "resetsAt": later + 18000},
                                                   "seven_day": {"utilization": 0.95, "resetsAt": now + 9000}}}, at=later)
    assert accounts.over_limit(worker, later).window == "five_hour"
    accounts.approve(worker, now=later)
    assert accounts.approvals("one") == {"seven_day": now + 9000, "five_hour": later + 18000}
    assert sorted(p.name for p in accounts.approvals_file("one").parent.iterdir()) == ["approvals.json", "usage.json"]
    accounts.approvals_file("one").write_text("not json")
    assert accounts.approvals("one") == {}


def test_approve_limit_command(home, capsys):
    config.save_json(config.paths().settings, {"format": 1, "workers": [
        {"name": "one", "claude": None, "overseer": None, "limits": {"five_hour": 0.5}}], "loops": [], "defaults": {}})
    assert cli.main(["approve-limit", "one"]) == 1
    assert "one is not past any of its limits" in capsys.readouterr().err
    accounts.save_usage("one", {"unifiedWindows": {"five_hour": {"utilization": 0.6, "resetsAt": 4e9}}})
    assert cli.main(["approve-limit", "one", "five_hour"]) == 0
    assert "one's loops go on past its 5-hour limit until" in capsys.readouterr().out
    assert accounts.approvals("one") == {"five_hour": 4e9}
    assert cli.main(["approve-limit", "nobody"]) == 2
    assert "no worker named nobody" in capsys.readouterr().err
