"""Starting serve at login (item loginstart): the file each system gets, as text, and on, off and
status with the home folder moved into the test's own and systemctl stood in for. Nothing here
touches this machine's own Startup folder, LaunchAgents or systemd."""
import io
import plistlib
import sys
from pathlib import Path

import pytest

from agentloop import build, cli, config, loginstart, server

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Every place login-start writes, moved under tmp_path."""
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: folder))
    monkeypatch.setenv("APPDATA", str(folder / "Roaming"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "app"))
    monkeypatch.setenv("PATH", "/opt/tools/bin" + (";" if sys.platform == "win32" else ":") + "/usr/bin")
    exe = tmp_path / "bin" / "agentloop"
    exe.parent.mkdir()
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(exe)])
    monkeypatch.setattr(build, "read_build", lambda package=None: None)
    return folder


@pytest.fixture
def systemctl(monkeypatch):
    """systemctl --user, stood in for: the calls it was given, and a user session there."""
    calls = []

    def fake(*args):
        calls.append(args)
        return __import__("subprocess").CompletedProcess(["systemctl", "--user", *args], 0, "", "")

    monkeypatch.setattr(loginstart, "_systemctl", fake)
    monkeypatch.setattr(loginstart, "_no_systemd", lambda: None)
    real_which = loginstart.shutil.which
    monkeypatch.setattr(loginstart.shutil, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else real_which(name))
    return calls


def _system(monkeypatch, name):
    monkeypatch.setattr(config, "this_system", lambda: name)


# What runs

def test_command_is_the_agentloop_it_was_started_as(tmp_path):
    exe = tmp_path / "agentloop"
    exe.write_text("", encoding="utf-8")
    assert loginstart.command(str(exe)) == [str(exe)]


@pytest.mark.skipif(sys.platform != "win32", reason="a console script's .exe is a Windows thing")
def test_command_finds_the_exe_beside_a_name_without_it(tmp_path):
    exe = tmp_path / "agentloop.exe"
    exe.write_text("", encoding="utf-8")
    assert loginstart.command(str(tmp_path / "agentloop")) == [str(exe)]


def test_command_falls_back_to_this_python(tmp_path):
    assert loginstart.command(str(tmp_path / "__main__.py")) == [sys.executable, "-m", "agentloop"]


def test_command_of_a_build_is_its_launcher(tmp_path, monkeypatch):
    # <to>/versions/<id>/agentloop is a build's package, and the launcher runs whichever is current.
    package = tmp_path / "to" / "versions" / "20261124-000000-abc1234" / "agentloop"
    package.mkdir(parents=True)
    launcher = tmp_path / "to" / build.launcher_name()
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setattr(build, "read_build", lambda package=None: {"version": "0"})
    monkeypatch.setattr(build, "package_dir", lambda: package)
    assert loginstart.command(str(tmp_path / "elsewhere" / "agentloop")) == [str(launcher)]


def test_serve_args_log_to_the_app_log_folder(home, tmp_path):
    args = loginstart.serve_args()
    assert args[1:3] == ["serve", "--log"]
    assert Path(args[3]) == tmp_path / "app" / "data" / "logs" / "serve.log"


# The files, as text

def test_vbs_runs_each_argument_quoted_and_hidden():
    text = loginstart.vbs_text([r"C:\Users\Анна Б\bin\agentloop.exe", "serve", "--log", r"C:\a & b\serve.log"],
                               {"PATH": "x", "AGENTLOOP_HOME": r"D:\al"})
    lines = text.split("\r\n")
    assert lines[2] == 'Set shell = CreateObject("WScript.Shell")'
    assert 'shell.Environment("Process")("AGENTLOOP_HOME") = "D:\\al"' in lines
    # PATH is the user's own at login already.
    assert not any("PATH" in line for line in lines if not line.startswith("'"))
    assert ('shell.Run """C:\\Users\\Анна Б\\bin\\agentloop.exe"" ""serve"" ""--log"" '
            '""C:\\a & b\\serve.log""", 0, False') in lines
    assert text.endswith("\r\n")


def test_vbs_refuses_a_quote():
    with pytest.raises(loginstart.LoginStartError):
        loginstart.vbs_text(['C:\\a"b\\agentloop.exe', "serve"], {})


def test_plist_runs_the_arguments_at_load():
    agent = plistlib.loads(loginstart.plist_bytes(["/Users/a b/.local/bin/agentloop", "serve"], {"PATH": "/opt/homebrew/bin:/usr/bin"}))
    assert agent == {"Label": loginstart.LABEL, "ProgramArguments": ["/Users/a b/.local/bin/agentloop", "serve"],
                     "RunAtLoad": True, "ProcessType": "Interactive",
                     "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/bin"}}


def test_unit_quotes_what_systemd_would_read():
    text = loginstart.unit_text(["/home/a b/bin/agentloop", "serve", "--log", "/home/a b/100%/$x\\y\"z.log"],
                                {"PATH": "/home/a b/.local/bin:/usr/bin"})
    lines = text.splitlines()
    assert 'ExecStart="/home/a b/bin/agentloop" "serve" "--log" "/home/a b/100%%/$$x\\\\y\\"z.log"' in lines
    assert 'Environment="PATH=/home/a b/.local/bin:/usr/bin"' in lines
    assert "KillMode=process" in lines
    assert lines[-1] == "WantedBy=default.target"


def test_unit_refuses_a_line_break():
    with pytest.raises(loginstart.LoginStartError):
        loginstart.unit_text(["/bin/agentloop", "serve"], {"PATH": "a\nb"})


# On, off and status

def test_windows_on_off(home, monkeypatch):
    _system(monkeypatch, "windows")
    assert not loginstart.status().on
    path, args = loginstart.turn_on()
    assert path == home / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "agentloop-serve.vbs"
    data = path.read_bytes()
    assert data.startswith(b"\xff\xfe")
    assert data[2:].decode("utf-16-le") == loginstart.vbs_text(args, loginstart.carried_env())
    assert loginstart.status().on
    assert loginstart.turn_off() == path
    assert not path.exists() and not loginstart.status().on
    assert loginstart.turn_off() is None


def test_macos_on_off(home, monkeypatch):
    _system(monkeypatch, "macos")
    path, args = loginstart.turn_on()
    assert path == home / "Library" / "LaunchAgents" / f"{loginstart.LABEL}.plist"
    agent = plistlib.loads(path.read_bytes())
    assert agent["ProgramArguments"] == args
    assert agent["EnvironmentVariables"]["AGENTLOOP_HOME"] == str(home.parent / "app")
    assert agent["EnvironmentVariables"]["PATH"].startswith("/opt/tools/bin")
    assert loginstart.status().on
    assert loginstart.turn_off() == path
    assert not loginstart.status().on


def test_linux_on_off(home, monkeypatch, systemctl):
    _system(monkeypatch, "linux")
    path, args = loginstart.turn_on()
    assert path == home / ".config" / "systemd" / "user" / "agentloop-serve.service"
    assert systemctl == [("show-environment",), ("daemon-reload",), ("enable", loginstart.UNIT)]
    assert loginstart.unit_text(args, loginstart.carried_env()) == path.read_text(encoding="utf-8")
    # What enable makes, which status looks for.
    link = loginstart.wants_link()
    link.parent.mkdir(parents=True)
    link.write_text("", encoding="utf-8")
    assert loginstart.status().on
    systemctl.clear()
    assert loginstart.turn_off() == path
    assert systemctl == [("disable", loginstart.UNIT), ("daemon-reload",)]
    assert not path.exists() and not link.exists() and not loginstart.status().on


def test_linux_without_a_user_session_writes_nothing(home, monkeypatch, systemctl):
    _system(monkeypatch, "linux")

    def refused(*args):
        systemctl.append(args)
        return __import__("subprocess").CompletedProcess(["systemctl"], 1, "", "Failed to connect to bus")

    monkeypatch.setattr(loginstart, "_systemctl", refused)
    with pytest.raises(loginstart.LoginStartError, match="no systemd user session"):
        loginstart.turn_on()
    assert not loginstart.unit_path().exists()
    assert systemctl == [("show-environment",)]


def test_linux_without_systemd_is_unsupported(home, monkeypatch):
    _system(monkeypatch, "linux")
    monkeypatch.setattr(loginstart, "_no_systemd", lambda: "this system does not run systemd")
    found = loginstart.status()
    assert not found.on and found.unsupported
    with pytest.raises(loginstart.LoginStartError, match="nothing was written"):
        loginstart.turn_on()


# The command, and what the page is told

def test_command_line_status_exits_by_state(home, monkeypatch, capsys):
    _system(monkeypatch, "windows")
    assert cli.main(["login-start", "status"]) == 1
    assert cli.main(["login-start", "on"]) == 0
    assert "serve starts at login" in capsys.readouterr().out
    assert cli.main(["login-start", "status"]) == 0
    assert cli.main(["login-start", "off"]) == 0
    assert cli.main(["login-start", "status"]) == 1


def test_page_is_told(home, monkeypatch):
    _system(monkeypatch, "windows")
    assert server.Handler._login_start() == {"on": False, "supported": True}
    loginstart.turn_on()
    assert server.Handler._login_start() == {"on": True, "supported": True}
    _system(monkeypatch, "linux")
    monkeypatch.setattr(loginstart, "_no_systemd", lambda: "no systemd")
    assert server.Handler._login_start() == {"on": False, "supported": False}


def test_serve_log_appends_and_moves_a_large_one_aside(tmp_path, monkeypatch):
    log = tmp_path / "logs" / "serve.log"
    log.parent.mkdir()
    log.write_text("x" * (cli._LOG_KEEP + 1), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    cli._log_to(log)
    try:
        print("Serving the page at http://127.0.0.1:8765/")
        print("a problem", file=sys.stderr)
    finally:
        sys.stdout.close()
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("--- serve started ")
    assert lines[1:] == ["Serving the page at http://127.0.0.1:8765/", "a problem"]
    assert (tmp_path / "logs" / "serve.log.1").stat().st_size == cli._LOG_KEEP + 1


# The installers

def test_installers_offer_it():
    ps1 = (REPO / "install" / "install.ps1").read_text(encoding="utf-8")
    sh = (REPO / "install" / "install.sh").read_text(encoding="utf-8")
    for text in (ps1, sh):
        assert "AGENTLOOP_LOGIN_START" in text
        assert "Start the page at login, so loops with Auto start come back after a restart? [Y/n]" in text
        assert "login-start status" in text and "login-start on" in text
        # Asked before the page is started, which the script ends on.
        assert text.index("login-start on") < text.rindex("serve --open")
    # Under curl | sh the script is standard input: the answer comes from the terminal.
    assert "read -r answer </dev/tty" in sh
    assert "[Console]::IsInputRedirected" in ps1
