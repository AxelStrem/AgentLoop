import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentloop import build, tools

SRC = Path(__file__).resolve().parents[1] / "src"


def _run(*cmd, cwd, stdin=None):
    # -I keeps the current folder and PYTHON* variables out of the path, so what a tool imports
    # can only come from beside it.
    return subprocess.run([sys.executable, "-I", *map(str, cmd)], cwd=cwd, input=stdin,
                          capture_output=True, text=True, timeout=60)


def test_every_tool_is_beside_the_package():
    for name in tools.NAMES:
        assert tools.path(name).is_file()
        assert tools.path(name).parent == (SRC / "agentloop" / "tools").resolve()


def test_an_unknown_tool_is_refused():
    with pytest.raises(ValueError):
        tools.path("loop-settings.json")


@pytest.mark.parametrize("name", ["tracker.py", "features.py"])
def test_the_commands_run_standalone(name, tmp_path):
    done = _run(tools.path(name), "--help", cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert "usage:" in done.stdout


def test_features_imports_the_tracker_beside_it(tmp_path):
    folder = tools.path("features.py").parent
    done = _run("-c", f"import sys; sys.path.insert(0, {str(folder)!r}); import features; "
                      "print(features.tracker.__file__)", cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert Path(done.stdout.strip()).resolve() == tools.path("tracker.py")


@pytest.mark.parametrize("call, refused", [
    ({"tool_name": "Bash", "tool_input": {"command": "git -C repo push origin main"}}, True),
    ({"tool_name": "Read", "tool_input": {"file_path": "D:/x/.env"}}, True),
    ({"tool_name": "Read", "tool_input": {"file_path": "D:/x/.env.example"}}, False),
    ({"tool_name": "Bash", "tool_input": {"command": "ls -la"}}, False),
])
def test_the_guard_refuses_what_it_should(call, refused, tmp_path, monkeypatch):
    monkeypatch.delenv("OVERSEER_KEY_FILE", raising=False)
    done = _run(tools.path("guard.py"), cwd=tmp_path, stdin=json.dumps(call))
    assert done.returncode == (2 if refused else 0), done.stderr


def test_a_build_carries_the_tools(tmp_path):
    built = build.build(tmp_path / "out", SRC / "agentloop")
    copied = tmp_path / "out" / "versions" / built.id / "agentloop" / "tools"
    for name in tools.NAMES:
        assert (copied / name).read_bytes() == tools.path(name).read_bytes()
