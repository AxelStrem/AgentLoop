import os
import re
import shutil
import subprocess
import sys
import sysconfig

import pytest

import agentloop
from agentloop import runner


def _console_script():
    # pip puts it with the scripts of the environment, or of the user when it installs there.
    folders = [sysconfig.get_path("scripts"), sysconfig.get_path("scripts", sysconfig.get_preferred_scheme("user"))]
    return shutil.which("agentloop", path=os.pathsep.join(folders))


def _runners():
    return {
        "module": [sys.executable, "-m", "agentloop"],
        "script": [_console_script()] if _console_script() else None,
    }


@pytest.fixture(params=["module", "script"])
def agentloop_cmd(request):
    cmd = _runners()[request.param]
    if cmd is None:
        pytest.skip("the agentloop console script is not installed")
    return cmd


def _run(cmd, *args):
    return subprocess.run([*cmd, *args], capture_output=True, text=True, timeout=60)


def test_version(agentloop_cmd):
    done = _run(agentloop_cmd, "version")
    assert done.returncode == 0
    assert done.stdout.strip() == f"agentloop {agentloop.__version__} (from source)"


def test_version_flag(agentloop_cmd):
    # What the release workflow runs after installing the wheel (item package).
    done = _run(agentloop_cmd, "--version")
    assert done.returncode == 0
    assert done.stdout.strip() == f"agentloop {agentloop.__version__} (from source)"


def test_serve_prints_its_address(agentloop_cmd, tmp_path):
    env = {**os.environ, "AGENTLOOP_HOME": str(tmp_path / "home")}
    # end_tree ends a process group on macOS and Linux, so the server is started as the leader of
    # one, as the runner starts what it ends; otherwise killpg finds no group and the server lives.
    group = {} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen([*agentloop_cmd, "serve", "--port", "0"], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, env=env, **group)
    try:
        line = process.stdout.readline()
    finally:
        # The console script on Windows is a launcher with python as its child: the tree is ended.
        runner.end_tree(process.pid)
        process.communicate()
    assert re.fullmatch(r"Serving the page at http://127\.0\.0\.1:\d+/\?token=[\w-]{40,}\n", line), line


def test_run_wants_a_loop(agentloop_cmd):
    done = _run(agentloop_cmd, "run")
    assert done.returncode == 2
    assert "loop" in done.stderr


def test_run_names_the_loops_there_are(agentloop_cmd, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    done = _run(agentloop_cmd, "run", "someproject")
    assert done.returncode == 2
    assert done.stdout == ""
    assert "agentloop run: No loop named someproject. The loops are: none" in done.stderr


def test_run_takes_an_item_only_when_dry(agentloop_cmd, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    done = _run(agentloop_cmd, "run", "someproject", "--item", "alpha")
    assert done.returncode == 2
    assert "--item is for --dry" in done.stderr
    done = _run(agentloop_cmd, "run", "someproject", "--dry", "--item", "alpha")
    assert "No loop named someproject" in done.stderr


def test_run_colours_only_when_asked_or_to_a_terminal(agentloop_cmd, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("NO_COLOR", raising=False)
    # A pipe is no terminal: plain.
    assert "[" not in _run(agentloop_cmd, "run", "someproject").stderr
    # Red, as the script writes what keeps it from starting.
    said = _run(agentloop_cmd, "run", "someproject", "--colour", "always").stderr
    assert said.startswith("[91magentloop run: No loop named someproject.") and said.rstrip().endswith("[0m")


def test_no_command_prints_help(agentloop_cmd):
    done = _run(agentloop_cmd)
    assert done.returncode == 2
    assert "serve" in done.stderr and "version" in done.stderr


def _psd1(tmp_path, name, body):
    (tmp_path / f"{name}.psd1").write_text(body, encoding="utf-8")
    (tmp_path / f"{name}.md").write_text("# brief\n", encoding="utf-8")
    return tmp_path / f"{name}.psd1"


def test_import_psd1(agentloop_cmd, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    (tmp_path / "Code").mkdir()
    path = _psd1(tmp_path, "proj", f"@{{ Tracker = 'a/proj.os'; Server = 'https://s'; KeyFile = 'k'; Repos = @('{tmp_path / 'Code'}') }}")
    described = tmp_path / "described.md"
    described.write_text("Proj.\n", encoding="utf-8")
    out = tmp_path / "out"
    done = _run(agentloop_cmd, "import-psd1", str(path), "--out", str(out), "--tracker-description", str(described))
    assert done.returncode == 0, done.stderr
    assert f"definition  {out / 'proj.project.os'}" in done.stdout
    assert "agentloop add https://s/doc/a/proj.os --key-file k --repo code=" in done.stdout
    assert (out / "proj.project.os").is_file()
    # The export is all it writes: no settings, no bundle.
    assert not (tmp_path / "home").exists()
    again = _run(agentloop_cmd, "import-psd1", str(path), "--out", str(out), "--tracker-description", str(described))
    assert again.returncode == 2
    assert again.stderr == f"agentloop import-psd1: {out}: out: is not an empty folder; nothing is written over\n"


def test_import_psd1_says_what_is_wrong(agentloop_cmd, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    path = _psd1(tmp_path, "proj", "@{\n Tracker = \"$HOME/x.os\"\n}")
    done = _run(agentloop_cmd, "import-psd1", str(path), "--out", str(tmp_path / "out"))
    assert done.returncode == 2
    assert done.stderr.startswith("agentloop import-psd1: proj.psd1: line 2: a double-quoted string with a $")
    assert "Traceback" not in done.stderr
