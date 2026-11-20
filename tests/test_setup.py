"""A project's setup script (item project-setup): the tool a definition ticks as the setup for this
system, run in the project's own folder before a loop's first session and again when it changes,
what it leaves applied to every session, a failure keeping the loop from starting, `setup: off`,
and Set up again. The scripts here are Python, so that they run alike on all three systems."""

import hashlib
import json
import os
import sys
import threading
from pathlib import Path

import pytest

from agentloop import config, definition, setup
from test_definition import TrackerProject, served
from test_doc_change import other_system
from test_loop import item, move
from test_tools_fetch import put, sha

# A setup that installs a "tool" in its folder, counts its runs, and says what the sessions get.
GOOD = b"""import os, pathlib
env = pathlib.Path(os.environ["AGENTLOOP_PROJECT_ENV"])
(env / "bin").mkdir(exist_ok=True)
runs = env / "runs.txt"
runs.write_text((runs.read_text() if runs.exists() else "") + "x")
print("setting up", os.environ["AGENTLOOP_PROJECT"], "on", os.environ["AGENTLOOP_SYSTEM"])
(env / "session-env.txt").write_text("# made by the setup\\nPATH={env}/bin\\nPROJECT_THING=here\\n")
"""
BAD = b"""import sys
for n in range(60):
    print("line", n)
print("cargo: not found")
sys.exit(3)
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    return tmp_path


def script(folder: Path, data: bytes, name: str = "setup.py") -> tuple[Path, dict]:
    path = folder / name
    path.write_bytes(data)
    return path, {"file": name, "sha256": hashlib.sha256(data).hexdigest(), "systems": [], "setup": True}


# The file it leaves

def test_the_file_puts_folders_in_front_of_path_in_order_and_sets_variables(tmp_path):
    folder = tmp_path / "env"
    folder.mkdir()
    (folder / setup.ENV_FILE).write_bytes("﻿# tools\n\nPATH={env}/a\nPATH=/opt/b\nCARGO_HOME={env}/cargo\nEMPTY=\n".encode("utf-8"))
    found = setup.parse_env_file(folder / setup.ENV_FILE, folder)
    here = str(folder).replace("\\", "/")
    assert found.paths == [str(Path(f"{here}/a")), str(Path("/opt/b"))]
    assert found.variables == {"CARGO_HOME": f"{here}/cargo", "EMPTY": ""}
    env: dict = {"AGENT_AUTHOR": "me"}
    found.apply(env, {"PATH": "base"})
    assert env["PATH"] == os.pathsep.join([*found.paths, "base"]) and env["CARGO_HOME"] == f"{here}/cargo"
    assert env["AGENT_AUTHOR"] == "me"


def test_no_file_is_nothing_to_apply(tmp_path):
    found = setup.parse_env_file(tmp_path / setup.ENV_FILE, tmp_path)
    env: dict = {}
    found.apply(env, {"PATH": "base"})
    assert env == {}


@pytest.mark.parametrize("line", ["OVERSEER_KEY_FILE=/x", "CLAUDE_CONFIG_DIR=/x", "ANTHROPIC_API_KEY=k", "AGENT_AUTHOR=x",
                                  "AGENTLOOP_HOME=/x", "just words", "1BAD=x", "PATH="])
def test_a_line_the_format_does_not_allow_is_refused_by_its_number(tmp_path, line):
    (tmp_path / setup.ENV_FILE).write_text(f"# fine\n{line}\n", encoding="utf-8")
    with pytest.raises(setup.SetupError) as caught:
        setup.parse_env_file(tmp_path / setup.ENV_FILE, tmp_path)
    assert "line 2" in str(caught.value)


# Running it

def test_a_script_is_started_by_its_kind():
    assert setup.command(Path("s.py")) == [sys.executable, "s.py"]
    assert setup.command(Path("s.ps1"))[1:] == ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", "s.ps1"]
    assert Path(setup.command(Path("s.sh"))[0]).stem in ("bash", "sh") and setup.command(Path("s.sh"))[1] == "s.sh"
    assert setup.command(Path("s.cmd"))[1:] == ["/d", "/c", "s.cmd"]
    assert setup.command(Path("tool")) == ["tool"]


def test_it_runs_in_the_projects_folder_and_records_how_it_went(home):
    path, tool = script(home, GOOD)
    said: list[str] = []
    assert setup.due("proj", tool)
    found = setup.run("proj", path, tool, said.append)
    folder = setup.env_dir("proj")
    assert folder == config.paths().data / "projects" / "proj" / "env"
    assert said == [f"setting up proj on {config.this_system()}"]
    assert found.variables == {"PROJECT_THING": "here"} and found.paths == [str(folder / "bin")]
    last = setup.record("proj")
    assert last["ok"] is True and last["sha256"] == tool["sha256"] and last["system"] == config.this_system()
    assert not setup.due("proj", tool)
    assert setup.state("proj", tool, "auto")["state"] == "ok"
    # Another script, or a forgotten run, is due again.
    assert setup.due("proj", {**tool, "sha256": "0" * 64})
    setup.forget("proj")
    assert setup.due("proj", tool) and setup.state("proj", tool, "auto")["state"] == "due"


def test_a_failing_script_is_an_error_with_the_end_of_its_output(home):
    path, tool = script(home, BAD)
    with pytest.raises(setup.SetupError) as caught:
        setup.run("proj", path, tool, lambda line: None)
    assert "exit code 3" in str(caught.value)
    last = setup.record("proj")
    assert last["ok"] is False and len(last["tail"]) == setup.TAIL
    assert "cargo: not found" in last["tail"] and last["tail"][-1].endswith("exit code 3")
    assert setup.due("proj", tool)
    shown = setup.state("proj", tool, "auto")
    assert shown["state"] == "failed" and "cargo: not found" in shown["tail"]


def test_a_file_left_wrong_fails_the_setup(home):
    path, tool = script(home, b"import os, pathlib\n(pathlib.Path(os.environ['AGENTLOOP_PROJECT_ENV']) / 'session-env.txt')"
                              b".write_text('OVERSEER_URL=http://elsewhere\\n')\n")
    with pytest.raises(setup.SetupError) as caught:
        setup.run("proj", path, tool, lambda line: None)
    assert "OVERSEER_URL" in str(caught.value) and setup.record("proj")["ok"] is False


def test_it_is_never_run_as_root(home, monkeypatch):
    path, tool = script(home, GOOD)
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(setup.SetupError) as caught:
        setup.run("proj", path, tool, lambda line: None)
    assert "root" in str(caught.value)
    assert not (setup.env_dir("proj") / "runs.txt").exists()


def test_the_state_without_a_setup_or_with_it_off():
    tool = {"file": "setup.py", "sha256": "a" * 64, "systems": [], "setup": True}
    assert setup.state("proj", None, "auto") == {"state": "none"}
    assert setup.state("proj", tool, "off") == {"state": "off", "file": "setup.py"}


# The definition

def tool(file: str, systems: list, setup_: bool = True) -> dict:
    return {"file": file, "sha256": "a" * 64, "systems": systems, **({"setup": True} if setup_ else {})}


def test_one_setup_for_each_system():
    config.check_tools([tool("setup.ps1", ["windows"]), tool("setup.sh", ["linux", "macos"]), tool("x.py", [], False)], "d")
    with pytest.raises(config.ConfigError) as caught:
        config.check_tools([tool("setup.ps1", ["windows"]), tool("setup.py", [])], "d")
    assert caught.value.field == "tools" and "windows" in caught.value.message
    with pytest.raises(config.ConfigError) as caught:
        config.check_tools([{**tool("s.py", []), "setup": "yes"}], "d")
    assert caught.value.field == "tools[0].setup"


def test_the_setup_for_this_system_is_the_one_run():
    here, other = config.this_system(), other_system()
    raw = {"tools": [tool("other.py", [other]), tool("probe.py", [], False), tool("here.py", [here])]}
    assert config.setup_tool(raw)["file"] == "here.py"
    assert config.setup_tool({"tools": [tool("other.py", [other])]}) is None
    assert config.setup_tool({}) is None


def test_the_tick_is_read_and_left_out_when_off():
    def entry(file, flag):
        children = [{"name": "file", "parameters": {"value": {"String": file}}},
                    {"name": "sha256", "parameters": {"value": {"String": "a" * 64}}}]
        if flag is not None:
            children.append({"name": "setup", "parameters": {"value": {"Boolean": flag}}})
        return {"children": children}
    node = {"children": [{"name": "Repos", "children": [{"children": [{"name": "name", "parameters": {"value": {"String": "c"}}}]}]},
                         {"name": "Tools", "children": [entry("a.py", True), entry("b.py", False), entry("c.py", None)]}]}
    tools = definition.from_node(node, "d")["tools"]
    assert [t.get("setup") for t in tools] == [True, None, None]
    assert "setup" not in tools[1] and "setup" not in tools[2]


def test_a_tick_that_changed_is_shown():
    old = {"tools": [tool("s.py", [], False)]}
    new = {"tools": [tool("s.py", [])]}
    found = definition.diff(old, new)
    assert found["tools"]["changed"][0]["new_setup"] is True
    assert "~ tool s.py is now the setup script" in definition.diff_lines(found)
    added = definition.diff({}, new)
    assert added["tools"]["added"] == [{"file": "s.py", "systems": [], "setup": True}]
    assert "+ tool s.py, the setup script" in definition.diff_lines(added)


def test_setup_is_the_machines_to_set_not_a_projects():
    config.check_run_settings("s", "defaults", {"setup": "off"})
    with pytest.raises(config.ConfigError):
        config.check_run_settings("s", "defaults", {"setup": "sometimes"})
    with pytest.raises(config.ConfigError) as caught:
        config.check_project({"repos": ["code"], "settings": {"setup": "off"}}, "d")
    assert caught.value.field == "settings.setup"


# The loop

class SettingUp(TrackerProject):
    """A loop on a tracker whose definition ticks `setup.py` as the setup for every system."""

    def __init__(self, tmp_path, monkeypatch, fake_claude, overseer, items, data: bytes = GOOD, settings: dict | None = None):
        self.setup_data = data
        super().__init__(tmp_path, monkeypatch, fake_claude, overseer, items, tools=(("setup.py", sha(data), "", True),))
        put(overseer, self.tracker, "setup.py", data)
        if settings:
            path = config.paths().settings
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["loops"][0]["settings"].update(settings)
            config.save_json(path, raw)
        monkeypatch.setenv("FAKE_CLAUDE_ENV", "PATH,PROJECT_THING")


@pytest.fixture
def setting_up(tmp_path, monkeypatch, fake_claude, overseer):
    def make(*items, **kwargs) -> SettingUp:
        return SettingUp(tmp_path, monkeypatch, fake_claude, overseer, list(items), **kwargs)
    return make


ITEMS = (item("alpha", "ready", complexity="low"), item("beta", "ready", complexity="low"))


def test_the_tick_is_read_through_the_tracker(overseer):
    tracker = served(overseer, item("alpha"), tools=(("setup.py", sha(GOOD), "", True), ("probe.py", sha(b"x"), "")))
    raw = definition.fetch(overseer.url, overseer.key, tracker)
    assert [t.get("setup") for t in raw["tools"]] == [True, None]


def test_the_loop_sets_up_before_its_first_session_and_every_session_gets_it(setting_up, fake_claude):
    p = setting_up(*ITEMS)
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"),
           p.scene(move("beta", "testing"), result="COMMIT: beta: built"))
    assert p.go() == 0
    folder = setup.env_dir("proj")
    assert (folder / "runs.txt").read_text() == "x"
    calls = [c for c in fake_claude.calls() if "prompt" in c]
    assert len(calls) == 2
    for call in calls:
        assert call["env"]["PROJECT_THING"] == "here"
        assert call["env"]["PATH"].split(os.pathsep)[0] == str(folder / "bin")
    text = "\n".join(p.lines)
    assert "Setting up proj's tools: setup.py" in text and f"setting up proj on {config.this_system()}" in text
    system = p.r.system_file.read_text(encoding="utf-8")
    assert f"installed by its setup script: `{str(folder).replace(chr(92), '/')}`" in system
    # Set up already, it is not run again by the next start.
    p.play(p.scene(result="COMMIT: nothing"))
    p.go()
    assert (folder / "runs.txt").read_text() == "x"


def test_a_failing_setup_keeps_the_loop_from_starting(setting_up, fake_claude):
    p = setting_up(*ITEMS, data=BAD)
    assert p.go() == 1
    assert [c for c in fake_claude.calls() if "prompt" in c] == []
    text = "\n".join(p.lines)
    assert "cargo: not found" in text and "proj's setup failed" in text and "exit code 3" in text
    assert setup.record("proj")["ok"] is False


def test_setup_off_runs_nothing(setting_up, fake_claude):
    p = setting_up(*ITEMS, settings={"setup": "off"})
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    assert p.go(maxIterations=1) == 0
    assert not (setup.env_dir("proj") / "runs.txt").exists()
    call = next(c for c in fake_claude.calls() if "prompt" in c)
    assert call["env"].get("PROJECT_THING") is None
    assert "installed by its setup script" not in p.r.system_file.read_text(encoding="utf-8")


def test_a_run_of_setup_alone_runs_no_session_and_again_runs_it_again(setting_up, fake_claude):
    from agentloop import loop

    p = setting_up(*ITEMS)
    machine = config.load_machine()
    assert loop.Run(machine, machine.settings.loop("proj"), out=p.lines.append, clock=p.clock, setup_only=True).run() == 0
    assert loop.Run(machine, machine.settings.loop("proj"), out=p.lines.append, clock=p.clock, setup_only=True).run() == 0
    assert (setup.env_dir("proj") / "runs.txt").read_text() == "x"
    assert "set up already" in p.lines[-1]
    assert loop.Run(machine, machine.settings.loop("proj"), out=p.lines.append, clock=p.clock, setup_only=True, again=True).run() == 0
    assert (setup.env_dir("proj") / "runs.txt").read_text() == "xx"
    assert [c for c in fake_claude.calls() if "prompt" in c] == []


def test_the_page_shows_the_setup_and_sets_up_again(setting_up, fake_claude):
    from agentloop import server
    from test_server import Client

    p = setting_up(*ITEMS, data=BAD)
    p.go()
    srv = server.Server(0)
    started: list = []
    srv.runner.start = lambda name, setup=False: started.append((name, setup))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = Client(srv)
        shown = c.json("GET", "/api/loops/proj")["setup"]
        assert shown["state"] == "failed" and shown["file"] == "setup.py" and "cargo: not found" in shown["tail"]
        again = c.json("POST", "/api/loops/proj/setup", {})
        assert again["setup"]["state"] == "due" and started == [("proj", True)]
    finally:
        srv.close()
