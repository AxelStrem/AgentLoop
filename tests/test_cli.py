"""The package imports, and the command starts: what an install breaks first."""
import argparse
import importlib
import os
import pkgutil
import py_compile
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

import agentloop
from agentloop import cli, runner

PACKAGE = Path(agentloop.__file__).resolve().parent


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


def _modules():
    # __main__ runs the command when imported; the tests of the command below start it.
    return [name for _, name, _ in pkgutil.walk_packages([str(PACKAGE)], "agentloop.")
            if all(part.isidentifier() for part in name.split(".")) and name != "agentloop.__main__"]


@pytest.mark.parametrize("name", _modules())
def test_each_module_imports(name):
    importlib.import_module(name)


def test_every_script_compiles(tmp_path):
    # The tools a session runs are scripts, some with names no import reaches (review-run.py).
    for script in PACKAGE.rglob("*.py"):
        py_compile.compile(str(script), cfile=str(tmp_path / "x.pyc"), doraise=True)


def _commands():
    subparsers = next(a for a in cli._parser()._actions if isinstance(a, argparse._SubParsersAction))
    return sorted(subparsers.choices)


@pytest.mark.parametrize("command", _commands())
def test_each_command_has_help(command, capsys):
    with pytest.raises(SystemExit) as done:
        cli.main([command, "--help"])
    assert done.value.code == 0
    assert capsys.readouterr().out.startswith("usage: agentloop ")


def test_version(agentloop_cmd):
    for args in (["version"], ["--version"]):
        done = _run(agentloop_cmd, *args)
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == f"agentloop {agentloop.__version__} (from source)"


def test_no_command_prints_help(agentloop_cmd):
    done = _run(agentloop_cmd)
    assert done.returncode == 2
    assert "serve" in done.stderr and "version" in done.stderr


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
    assert re.match(r"Serving the page at http://127\.0\.0\.1:\d+/", line), line
