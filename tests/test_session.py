import json
import os
import sys
from pathlib import Path

import pytest

from agentloop import config, session, tools
from agentloop.session import Event, Route, SessionError, SessionSpec


def _spec(tmp_path: Path, *, resume=False, route=None, prompt="Work the item.", variables=None, state=True) -> SessionSpec:
    work = tmp_path / "repo"
    work.mkdir(exist_ok=True)
    data = tmp_path / "data"
    return SessionSpec(
        claude=session.find_claude(),
        session_id="11111111-2222-3333-4444-555555555555",
        resume=resume,
        prompt=prompt,
        route=route or Route("sonnet", "high"),
        workdir=work,
        system_file=session.write_system_file(data / "system.md", "guide", "about"),
        settings=session.write_session_settings(data / "loop-settings.json"),
        log=data / "loop-1-build.jsonl",
        variables=variables if variables is not None else session.session_variables(
            author="sonnet (high)", scratch=data / "scratch" / "s1", tracker="alice/projects/p.os", item="p:thing"),
        state_file=data / "running.json" if state else None,
        item="thing",
    )


# Finding claude

def test_finds_claude_on_path(fake_claude):
    found = session.find_claude()
    assert Path(found).parent == fake_claude.bin
    if os.name == "nt":
        assert found.lower().endswith("claude.cmd")


def test_settings_name_claude(tmp_path):
    exe = tmp_path / "my-claude"
    exe.write_text("")
    assert session.find_claude(str(exe)) == str(exe)
    with pytest.raises(SessionError, match="settings name"):
        session.find_claude(str(tmp_path / "missing"))


def test_no_claude_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(SessionError, match="put it on PATH"):
        session.find_claude()


def test_settings_claude_key(tmp_path):
    raw = config.empty_settings() | {"claude": "~/bin/claude"}
    assert config.parse_settings(raw, tmp_path / "settings.json").claude == "~/bin/claude"
    assert config.parse_settings(config.empty_settings(), tmp_path / "settings.json").claude is None
    with pytest.raises(config.ConfigError, match="claude"):
        config.parse_settings(config.empty_settings() | {"claude": 3}, tmp_path / "settings.json")


# The command line

def test_command_line_fresh_and_resumed(tmp_path):
    settings, system = tmp_path / "s.json", tmp_path / "system.md"
    fresh = session.command_line("claude", "id1", Route("opus", "medium"), settings=settings, system_file=system, resume=False)
    assert fresh == [
        "claude", "-p", "--dangerously-skip-permissions", "--output-format", "stream-json", "--verbose",
        "--settings", str(settings), "--model", "opus",
        "--append-system-prompt-file", str(system), "--exclude-dynamic-system-prompt-sections",
        "--session-id", "id1", "--effort", "medium",
    ]
    resumed = session.command_line("claude", "id1", Route("haiku"), settings=settings, system_file=system, resume=True)
    assert resumed[-2:] == ["--resume", "id1"]
    assert "--effort" not in resumed and "--session-id" not in resumed


# The environment

def test_variables(tmp_path, monkeypatch):
    keyfile = tmp_path / "agent.key"
    variables = session.session_variables(
        author="opus (medium)", scratch=tmp_path / "scratch", tracker="alice/projects/p.os", item="p:thing",
        claude=str(tmp_path / "acct"), overseer={"server": "https://example.test/", "keyFile": str(keyfile)})
    assert variables["AGENTIC_LOOP"] == "1"
    assert variables["AGENT_AUTHOR"] == "opus (medium)"
    assert variables["AGENT_SCRATCH"] == str(tmp_path / "scratch").replace("\\", "/")
    assert variables["OVERSEER_TRACKER"] == "alice/projects/p.os"
    assert variables["OVERSEER_ITEM"] == "p:thing"
    assert variables["CLAUDE_CONFIG_DIR"] == str((tmp_path / "acct").resolve())
    assert variables["OVERSEER_URL"] == "https://example.test"
    assert variables["OVERSEER_KEY_FILE"] == str(keyfile.resolve())
    assert ("CLAUDE_CODE_USE_POWERSHELL_TOOL" in variables) == (os.name == "nt")
    assert variables["OVERSEER_CACHE"] == str(config.paths().data / "trackers").replace("\\", "/")

    plain = session.session_variables(author="a", scratch=tmp_path, tracker="t", item="p:x")
    assert plain["CLAUDE_CONFIG_DIR"] is None
    # A project's script finds tracker.py and its Python from these (item bench-record).
    assert plain["AGENTLOOP_TRACKER"].endswith("/tracker.py") and "\\" not in plain["AGENTLOOP_TRACKER"]
    assert Path(plain["AGENTLOOP_TRACKER"]).is_file() and Path(plain["AGENTLOOP_PYTHON"]).is_file()
    assert "OVERSEER_URL" not in plain and "OVERSEER_KEY_FILE" not in plain and "OVERSEER_CACHE" not in plain


def test_a_workers_token_or_key_reaches_the_session(tmp_path):
    environ = {"TOKEN_ONE": "tok-1", "KEY_TWO": "key-2"}
    token = session.session_variables(author="a", scratch=tmp_path, tracker="t", item="p:x",
                                      sign_in={"token": "TOKEN_ONE"}, environ=environ)
    assert token["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-1"
    # The other way in is taken away, so that one worker never runs on both.
    assert "ANTHROPIC_API_KEY" in token and token["ANTHROPIC_API_KEY"] is None
    key = session.session_variables(author="a", scratch=tmp_path, tracker="t", item="p:x",
                                    sign_in={"apiKey": "KEY_TWO"}, environ=environ)
    assert key["ANTHROPIC_API_KEY"] == "key-2" and key["CLAUDE_CODE_OAUTH_TOKEN"] is None
    env = session.build_env(token, {"ANTHROPIC_API_KEY": "inherited", "PATH": "p"})
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-1" and "ANTHROPIC_API_KEY" not in env

    # A worker with none leaves what the app was started with, as a container sets it for all.
    plain = session.session_variables(author="a", scratch=tmp_path, tracker="t", item="p:x")
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in plain and "ANTHROPIC_API_KEY" not in plain
    assert session.build_env(plain, {"CLAUDE_CODE_OAUTH_TOKEN": "inherited"})["CLAUDE_CODE_OAUTH_TOKEN"] == "inherited"


def test_a_token_not_set_is_named_and_never_shown(tmp_path):
    with pytest.raises(session.SessionError) as error:
        session.sign_in_variables({"token": "TOKEN_ONE"}, {"TOKEN_ONE": ""})
    assert "TOKEN_ONE is not set" in str(error.value)
    with pytest.raises(session.SessionError):
        session.sign_in_variables({"apiKey": "KEY"}, {})


def test_powershell_tool_only_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(session.os, "name", "posix")
    assert "CLAUDE_CODE_USE_POWERSHELL_TOOL" not in session.session_variables(author="a", scratch=tmp_path, tracker="t", item="p:x")
    monkeypatch.setattr(session.os, "name", "nt")
    assert session.session_variables(author="a", scratch=tmp_path, tracker="t", item="p:x")["CLAUDE_CODE_USE_POWERSHELL_TOOL"] == "1"


def test_build_env_removes_and_sets():
    env = session.build_env({"A": "1", "CLAUDE_CONFIG_DIR": None}, base={"CLAUDE_CONFIG_DIR": "x", "B": "2"})
    assert env == {"A": "1", "B": "2"}


# Running a session

def test_runs_a_session(fake_claude, tmp_path, monkeypatch):
    fake_claude.scenario("triage-ready")
    # The app's own account must not reach a session on the default account.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "apps-own"))
    monkeypatch.setenv("FAKE_CLAUDE_ENV", "AGENTIC_LOOP,CLAUDE_CODE_USE_POWERSHELL_TOOL,OVERSEER_URL")
    before = dict(os.environ)
    spec = _spec(tmp_path, prompt='Quotes " and \' and `ticks` $(x) and ü — ✓')
    events: list[Event] = []
    started = []
    # running.json as the session runs: it names claude's process then, for a kill.
    during = []
    result = session.run_session(spec, events.append, lambda process: (started.append(process), during.append(json.loads(spec.state_file.read_text(encoding="utf-8")))))

    assert dict(os.environ) == before
    [call] = fake_claude.calls()
    assert call["cwd"] == str(spec.workdir)
    assert call["prompt"] == spec.prompt and call["prompt_source"] == "stdin"
    assert call["session_id"] == spec.session_id and not call["resume"]
    assert (call["model"], call["effort"]) == ("sonnet", "high")
    assert call["argv"] == session.command_line("x", spec.session_id, spec.route, settings=spec.settings, system_file=spec.system_file, resume=False)[1:]
    env = call["env"]
    assert env["CLAUDE_CONFIG_DIR"] is None
    assert env["AGENTIC_LOOP"] == "1"
    assert env["AGENT_AUTHOR"] == "sonnet (high)"
    assert env["OVERSEER_TRACKER"] == "alice/projects/p.os"
    assert env["OVERSEER_ITEM"] == "p:thing"
    assert env["OVERSEER_URL"] is None
    assert env["CLAUDE_CODE_USE_POWERSHELL_TOOL"] == ("1" if os.name == "nt" else None)

    assert result.exit_code == 0
    assert result.final["subtype"] == "success" and not result.final["is_error"]
    assert result.model == "claude-sonnet-5-5"
    assert result.rate["status"] == "allowed"
    assert result.turns == 3 and result.ms == 12000
    assert result.spent.cost == pytest.approx(0.05)
    assert result.context > 0
    assert session.parse_final(result.final["result"]).commit == "thing: triage, filed to ready"

    kinds = [e.kind for e in events]
    assert kinds[0] == "init" and kinds[-1] == "result"
    tools_used = [e.text for e in events if e.kind == "tool"]
    assert tools_used == ["Bash: tracker.py show thing", "Bash: tracker.py move thing ready --note @note.md"]
    assert any(e.kind == "text" and "COMMIT:" in e.text for e in events)

    # Every line of the stream, kept as it came.
    logged = spec.log.read_text(encoding="utf-8").splitlines()
    played = (Path(__file__).parent / "fake_claude" / "sessions" / "triage-ready.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(logged) == len([line for line in played if line.strip() and '"fake"' not in line])
    assert all(json.loads(line)["session_id"] == spec.session_id for line in logged)

    state = json.loads(spec.state_file.read_text(encoding="utf-8"))
    assert during[0]["pid"] == os.getpid() and during[0]["child"] == started[0].pid
    # Once it has ended, no child: its pid may be another program's by now.
    assert state["pid"] == os.getpid() and state["child"] is None
    assert state["session"] == spec.session_id and state["item"] == "thing"
    assert "claude --resume " + spec.session_id in state["resume"]


def test_resumed_session_on_an_account(fake_claude, tmp_path):
    fake_claude.scenario("build-edit")
    account = tmp_path / "acct"
    account.mkdir()
    variables = session.session_variables(author="a", scratch=tmp_path / "s", tracker="t", item="p:x", claude=str(account))
    spec = _spec(tmp_path, resume=True, route=Route("opus"), variables=variables)
    result = session.run_session(spec)
    [call] = fake_claude.calls()
    assert call["resume"] and call["effort"] is None
    assert call["env"]["CLAUDE_CONFIG_DIR"] == str(account.resolve())
    assert (spec.workdir / "notes.txt").read_text(encoding="utf-8") == "built by the fake claude\n"
    assert result.spent.read == 15000 and result.spent.output == 600


def test_session_without_result(fake_claude, tmp_path):
    fake_claude.scenario("no-result")
    result = session.run_session(_spec(tmp_path))
    assert result.final is None and result.exit_code == 1
    assert result.rate is not None and result.model == "claude-sonnet-5-5"


def test_usage_limit_session(fake_claude, tmp_path):
    fake_claude.scenario("usage-limit")
    result = session.run_session(_spec(tmp_path))
    assert result.final["is_error"]
    assert result.rate["status"] == "rejected" and result.rate["rateLimitType"] == "seven_day"
    assert isinstance(result.rate["resetsAt"], int)
    assert result.spent.cost == 0


def test_two_results_take_the_most_and_sum_turns():
    reader = session.StreamReader()
    for cost, read in ((0.10, 100), (0.30, 300)):
        reader.line(json.dumps({"type": "result", "num_turns": 2, "duration_ms": 1000, "total_cost_usd": cost,
                                "modelUsage": {"a": {"cacheReadInputTokens": read - 50}, "b": {"cacheReadInputTokens": 50}}}))
    r = reader.result
    assert (r.turns, r.ms, r.spent.cost, r.spent.read) == (4, 2000, 0.30, 300)


def test_stream_events_and_subagents():
    events: list[Event] = []
    reader = session.StreamReader(events.append)
    reader.line("not json")
    reader.line(json.dumps({"type": "assistant", "parent_tool_use_id": "t1", "message": {
        "usage": {"input_tokens": 999}, "content": [{"type": "text", "text": "  inner  "}]}}))
    reader.line(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "is_error": True, "content": [{"type": "text", "text": "boom\nmore"}]}]}}))
    assert [(e.kind, e.text, e.indent) for e in events] == [
        ("raw", "not json", "  "), ("text", "inner", "      "), ("tool_error", "boom", "  ")]
    # A subagent's call is not the session's context.
    assert reader.result.context == 0
    assert session.format_event(events[1]) == ["", "      inner"]
    assert session.format_event(events[2]) == ["  ! boom"]


def test_describe_tool():
    assert session.describe_tool({"name": "Bash", "input": {"command": "ls", "description": "List"}}) == "Bash: List"
    assert session.describe_tool({"name": "Read", "input": {"file_path": "a\nb"}}) == "Read: a"
    assert session.describe_tool({"name": "X", "input": {"n": 1}}) == 'X: {"n":1}'
    long = session.describe_tool({"name": "Grep", "input": {"pattern": "p" * 200}})
    assert long == "Grep: " + "p" * 140 + "..."


# Files a session is given

def test_system_file(tmp_path):
    path = session.write_system_file(tmp_path / "system.md", "guide\r\n", "  about it ü\n")
    assert path.read_bytes() == "guide\r\n\n\n---\n\n# What the project is\n\nabout it ü\n".encode("utf-8")


def test_placeholders_are_filled_and_other_braces_left():
    values = {"code": "D:/Repos/Code", "tools": "T", "python": "py"}
    text = "{python} {tools}/tracker.py; see {code}/src, not {other}, f'{x!r}', {{code}} or { code }"
    assert session.fill(text, values) == "py T/tracker.py; see D:/Repos/Code/src, not {other}, f'{x!r}', {D:/Repos/Code} or { code }"


def test_the_system_file_fills_in_the_machine(tmp_path):
    values = {"code": "C:/c", "tools": "C:/t"}
    path = session.write_system_file(tmp_path / "system.md", "run {tools}/tracker.py", "about {code}/src",
                                     where="# Where you are\n", values=values)
    assert path.read_bytes() == (b"run C:/t/tracker.py\n\n---\n\n# Where you are\n\n---\n\n"
                                 b"# What the project is\n\nabout C:/c/src\n")


def test_the_windows_part_only_on_windows(monkeypatch):
    windows = tools.path("AUTOTASK-windows.md").read_text(encoding="utf-8")
    common = tools.path("AUTOTASK.md").read_text(encoding="utf-8")
    monkeypatch.setattr(session.config.sys, "platform", "linux")
    assert session.autotask() == common
    monkeypatch.setattr(session.config.sys, "platform", "win32")
    assert session.autotask() == common.rstrip("\n") + "\n\n" + windows


def test_the_prompt_has_no_crlf_whatever_the_checkout(tmp_path, monkeypatch):
    # A Windows checkout with autocrlf writes the tools' AUTOTASK.md and a brief with \r\n.
    crlf = tmp_path / "brief.md"
    crlf.write_bytes(b"# Brief\r\n\r\nA line.\r\n")
    assert session.read_text(crlf) == "# Brief\n\nA line.\n"
    for platform in ("linux", "win32"):
        monkeypatch.setattr(session.config.sys, "platform", platform)
        assert "\r" not in session.autotask()


def test_autotask_names_no_folder_of_this_pc():
    # Every path of this machine's comes from "Where you are" and the placeholders, not the text.
    text = session.autotask()
    common = tools.path("AUTOTASK.md").read_text(encoding="utf-8")
    for word in ("E:/", "E:\\", "C:/", "AgenticLoop", "Git Bash", "PowerShell"):
        assert word not in common, word
    assert "{tools}/tracker.py" in text and "{python}" in text


def test_where_you_are(tmp_path, monkeypatch):
    code, tool = tmp_path / "code", tmp_path / "tool"
    monkeypatch.setattr(session.sys, "executable", "C:/Program Files/Python/python.exe")
    values = session.placeholders(tmp_path / "bundle", {"code": code}, {"tool": tool})
    slashed = lambda path: str(path.resolve()).replace("\\", "/")
    assert values == {"code": slashed(code), "tool": slashed(tool), "bundle": slashed(tmp_path / "bundle"),
                      "tools": slashed(tools.path("tracker.py").parent), "python": '"C:/Program Files/Python/python.exe"'}
    text = session.where_you_are(tmp_path / "bundle", {"code": code}, {"tool": tool})
    assert text.startswith("# Where you are\n")
    assert f"  - `code`: `{slashed(code)}`" in text and f"  - `tool`: `{slashed(tool)}`" in text
    assert '`"C:/Program Files/Python/python.exe"`' in text
    monkeypatch.setattr(session.config.sys, "platform", "darwin")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    mac = session.where_you_are(tmp_path / "bundle", {"code": code}, {})
    assert "- **System:** macOS; the Bash tool runs zsh." in mac
    assert "Other folders" not in mac
    monkeypatch.setattr(session.config.sys, "platform", "win32")
    assert "- **System:** Windows; the Bash tool runs Git Bash, and the PowerShell tool Windows PowerShell." in session.where_you_are(tmp_path, {}, {})


def test_each_worker_s_server_reaches_its_session(tmp_path):
    """Two workers on two servers: each session reaches the tracker on its own worker's, with its
    key, so one machine works trackers on both - and nothing of a machine's Overseer is passed on."""
    one = {"server": "https://one.example.com/", "keyFile": str(tmp_path / "one.key")}
    two = {"server": "http://127.0.0.1:9000", "keyFile": str(tmp_path / "two.key")}
    first = session.session_variables(author="a", scratch=tmp_path, tracker="a/p.os", item="p:x", overseer=one)
    second = session.session_variables(author="a", scratch=tmp_path, tracker="b/q.os", item="q:y", overseer=two)
    assert (first["OVERSEER_URL"], second["OVERSEER_URL"]) == ("https://one.example.com", "http://127.0.0.1:9000")
    assert first["OVERSEER_KEY_FILE"] == str((tmp_path / "one.key").resolve())
    assert second["OVERSEER_KEY_FILE"] == str((tmp_path / "two.key").resolve())
    assert not any(k in first for k in ("OVERSEER_LIBRARY", "OVERSEER_BIN"))


def test_session_settings_name_this_guard(tmp_path):
    written = json.loads(session.write_session_settings(tmp_path / "loop-settings.json").read_text(encoding="utf-8"))
    hook = written["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hook["command"] == sys.executable.replace("\\", "/")
    assert hook["args"] == [str(tools.path("guard.py")).replace("\\", "/")]
    assert "Bash(git commit*)" in written["permissions"]["deny"]


# Transcripts and the scratch folder

def _transcript(account: Path, session_id: str, with_subagents: bool) -> Path:
    folder = account / "projects" / "E--Source-Repos-X"
    folder.mkdir(parents=True)
    path = folder / f"{session_id}.jsonl"
    path.write_text("{}\n")
    if with_subagents:
        (folder / session_id).mkdir()
        (folder / session_id / "agent.jsonl").write_text("{}\n")
    return path


def test_transcripts_found_and_removed(tmp_path):
    account = tmp_path / "acct"
    path = _transcript(account, "abc", with_subagents=True)
    assert session.find_transcript("abc", str(account)) == path
    assert session.find_transcript("other", str(account)) is None
    session.remove_transcript("abc", str(account))
    assert not path.exists() and not (path.parent / "abc").exists()
    session.remove_transcript("abc", str(account))  # nothing left: nothing to do


def test_clear_session(tmp_path):
    account = tmp_path / "acct"
    path = _transcript(account, "abc", with_subagents=False)
    scratch = tmp_path / "scratch" / "abc"
    scratch.mkdir(parents=True)
    (scratch / "note.md").write_text("x")
    session.clear_session("abc", scratch, str(account))
    assert not path.exists() and not scratch.exists()


def test_default_account_is_home_claude():
    assert session.config_dir(None) == Path.home() / ".claude"


# Resume command and the final lines

def test_resume_command(tmp_path):
    work = Path("D:/Source/It's")
    win = session.resume_command("id1", work, None, windows=True)
    assert win == f"cd '{str(work).replace(chr(39), chr(39) * 2)}'; claude --resume id1"
    account = tmp_path / "acct"
    win_acct = session.resume_command("id1", work, str(account), windows=True)
    assert f"$env:CLAUDE_CONFIG_DIR = '{account.resolve()}'; claude --resume id1" in win_acct
    sh = session.resume_command("id1", Path("/home/me/it's"), None, windows=False)
    assert sh.startswith("cd ") and sh.endswith(" && claude --resume id1") and "'\"'\"'" in sh


def test_parse_final():
    lines = session.parse_final("Done.\r\n\r\nCOMMIT: thing: write notes.txt  \r\nNEXT: `other`\r\n")
    assert (lines.commit, lines.next, lines.stop) == ("thing: write notes.txt", "other", None)
    assert session.parse_final("STOP_LOOP: the repositories cannot be read").stop == "the repositories cannot be read"
    assert session.parse_final(None) == session.FinalLines(None, None, None)
    assert session.parse_final("say COMMIT: inline").commit is None
