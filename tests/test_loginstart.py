"""Starting serve at login (item loginstart): the file each system gets, as text, and on, off and
status with the home folder moved into the test's own, and the Run key and systemctl stood in for.
Nothing here touches this machine's own Run key, Startup folder, LaunchAgents or systemd: the one
test of the registry calls themselves uses a key of its own."""
import ast
import io
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentloop import build, cli, config, loginstart, server

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def run_key(tmp_path, monkeypatch):
    """The Run key, stood in for by a dict, and a pythonw where every system can find one."""
    values = {}
    monkeypatch.setattr(loginstart, "_run_get", lambda: values.get(loginstart.RUN_VALUE))
    monkeypatch.setattr(loginstart, "_run_set", lambda line: values.__setitem__(loginstart.RUN_VALUE, line))
    monkeypatch.setattr(loginstart, "_run_delete", lambda: values.pop(loginstart.RUN_VALUE, None) is not None)
    pythonw = tmp_path / "python" / "pythonw.exe"
    pythonw.parent.mkdir()
    pythonw.write_text("", encoding="utf-8")
    monkeypatch.setattr(loginstart, "_pythonw", lambda: pythonw)
    return values


@pytest.fixture
def home(tmp_path, monkeypatch, run_key):
    """Every place login-start writes, moved under tmp_path, and the Run key stood in for."""
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

def _starter_globals(text: str) -> dict:
    """What the starter's ARGS and ENV come to, read without running what it starts."""
    tree = ast.parse(text)
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    return found


def test_starter_holds_the_arguments_as_they_are():
    args = [r"C:\Users\Анна Б\bin\agentloop.cmd", "serve", "--log", r"C:\a & b\100% \"x\"\serve.log"]
    text = loginstart.starter_text(args, {"PATH": "x", "AGENTLOOP_HOME": r"D:\al'b"})
    compile(text, "agentloop-serve.pyw", "exec")
    # PATH is the user's own at login already.
    assert _starter_globals(text) == {"ARGS": args, "ENV": {"AGENTLOOP_HOME": r"D:\al'b"}}
    assert "subprocess.CREATE_NO_WINDOW" in text


def test_run_command_quotes_both_paths_and_refuses_a_quote():
    pythonw, starter = Path("C:/Program Files/Python/pythonw.exe"), Path("C:/Users/Анна Б/agentloop-serve.pyw")
    assert loginstart.run_command(pythonw, starter) == f'"{pythonw}" -I "{starter}"'
    with pytest.raises(loginstart.LoginStartError):
        loginstart.run_command(Path('C:/a"b/pythonw.exe'), Path("C:/s.pyw"))


@pytest.mark.skipif(sys.platform != "win32", reason="pythonw and CREATE_NO_WINDOW are Windows things")
def test_starter_runs_its_command_with_a_hidden_console(tmp_path):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pytest.skip("no pythonw.exe beside this Python")
    # A stand-in for serve: it says whether it has a console (GetConsoleCP is 0 without one) and
    # whether that console has a window (GetConsoleWindow is 0 for a hidden one).
    out = tmp_path / "Анна Б" / "out.txt"
    out.parent.mkdir()
    probe = ("import ctypes, sys; k = ctypes.windll.kernel32; "
             "open(sys.argv[1], 'w').write(f'{k.GetConsoleCP() != 0} {k.GetConsoleWindow() != 0} {sys.argv[2]}')")
    starter = tmp_path / "agentloop-serve.pyw"
    starter.write_text(loginstart.starter_text([sys.executable, "-c", probe, str(out), "a & b"], {}), encoding="utf-8")
    subprocess.run([str(pythonw), "-I", str(starter)], timeout=60, check=True)
    deadline = time.monotonic() + 60
    while not (out.is_file() and out.read_text(encoding="utf-8")) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert out.read_text(encoding="utf-8") == "True False a & b"


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

@pytest.mark.skipif(sys.platform != "win32", reason="the registry is a Windows thing")
def test_registry_calls_on_a_key_of_their_own(monkeypatch):
    import winreg
    # A key of the test's own, never the Run key, whose values would run at the next login.
    key = rf"Software\agentloop-test-{os.getpid()}"
    monkeypatch.setattr(loginstart, "RUN_KEY", key)
    try:
        assert loginstart._run_get() is None and loginstart._run_delete() is False
        loginstart._run_set('"C:/Анна Б/pythonw.exe" -I "C:/s.pyw"')
        assert loginstart._run_get() == '"C:/Анна Б/pythonw.exe" -I "C:/s.pyw"'
        assert loginstart._run_delete() is True and loginstart._run_get() is None
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)


def _old_vbs(home) -> Path:
    path = home / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / loginstart.OLD_VBS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe")
    return path


def test_windows_on_off(home, monkeypatch, tmp_path, run_key):
    _system(monkeypatch, "windows")
    assert not loginstart.status().on
    path, args = loginstart.turn_on()
    assert path == tmp_path / "app" / "data" / "loginstart" / "agentloop-serve.pyw"
    assert path.read_text(encoding="utf-8") == loginstart.starter_text(args, loginstart.carried_env())
    assert run_key == {loginstart.RUN_VALUE: f'"{tmp_path / "python" / "pythonw.exe"}" -I "{path}"'}
    found = loginstart.status()
    assert found.on and found.path == path and found.note is None
    assert loginstart.turn_off() == path
    assert not path.exists() and run_key == {} and not loginstart.status().on
    assert loginstart.turn_off() is None


def test_windows_on_needs_pythonw(home, monkeypatch, tmp_path, run_key):
    _system(monkeypatch, "windows")
    monkeypatch.setattr(loginstart, "_pythonw", lambda: tmp_path / "none" / "pythonw.exe")
    with pytest.raises(loginstart.LoginStartError, match="pythonw"):
        loginstart.turn_on()
    assert run_key == {} and not loginstart.starter_path().exists()


def test_windows_on_and_off_remove_the_old_vbs(home, monkeypatch):
    _system(monkeypatch, "windows")
    old = _old_vbs(home)
    loginstart.turn_on()
    assert not old.exists()
    old = _old_vbs(home)
    loginstart.turn_off()
    assert not old.exists()
    # The old start alone is removed too, and named.
    old = _old_vbs(home)
    assert loginstart.turn_off() == old and not old.exists()


@pytest.mark.parametrize("vbscript", [True, False])
def test_windows_status_names_the_old_vbs(home, monkeypatch, capsys, vbscript):
    _system(monkeypatch, "windows")
    monkeypatch.setattr(loginstart, "_has_vbscript", lambda: vbscript)
    old = _old_vbs(home)
    found = loginstart.status()
    # Without VBScript the old start does nothing at login, so it is off, and the page says to run on.
    assert found.on is vbscript and found.path == old
    assert "login-start on replaces it" in found.note
    assert cli.main(["login-start", "status"]) == (0 if vbscript else 1)
    assert "login-start on replaces it" in capsys.readouterr().out


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
