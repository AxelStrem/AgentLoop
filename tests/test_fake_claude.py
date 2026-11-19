"""The fake claude, reached as the real one is: `claude` found on PATH, started without a shell."""

import json
import shutil
import subprocess

import pytest


def _claude() -> str:
    path = shutil.which("claude")
    assert path, "the fixture put no claude on PATH"
    return path


def _run(cwd, *args, prompt="Do the item."):
    # The prompt goes in as bytes: text mode would turn each newline in it into a Windows one.
    return subprocess.run([_claude(), *args], input=prompt.encode("utf-8"), capture_output=True, cwd=cwd,
                          timeout=60)


def _events(done):
    return [json.loads(line) for line in done.stdout.decode("utf-8").splitlines()]


def _of_type(events, type_, subtype=None):
    return [e for e in events if e["type"] == type_ and (subtype is None or e.get("subtype") == subtype)]


SESSION_ARGS = ["-p", "--dangerously-skip-permissions", "--output-format", "stream-json", "--verbose",
                "--model", "opus", "--effort", "medium", "--session-id", "11111111-2222-3333-4444-555555555555"]


@pytest.mark.parametrize("name", ["triage-ready", "build-edit", "next", "stop-loop", "usage-limit",
                                  "no-result", "probe-ok", "probe-refused"])
def test_every_line_is_json_with_the_given_session_id(fake_claude, tmp_path, name):
    fake_claude.scenario(name)
    done = _run(tmp_path, *SESSION_ARGS)
    events = _events(done)
    assert events
    assert {e["session_id"] for e in events} == {"11111111-2222-3333-4444-555555555555"}
    assert events[0]["type"] == "system" and events[0]["subtype"] == "init"


def test_triage_session_ends_with_a_result_and_a_commit_line(fake_claude, tmp_path):
    fake_claude.scenario("triage-ready")
    done = _run(tmp_path, *SESSION_ARGS)
    assert done.returncode == 0
    events = _events(done)
    (result,) = _of_type(events, "result")
    assert result["is_error"] is False
    assert result["total_cost_usd"] > 0
    assert result["modelUsage"]
    assert "\nCOMMIT: " in result["result"]
    assert _of_type(events, "rate_limit_event")[0]["rate_limit_info"]["status"] == "allowed"
    assert any(b["type"] == "tool_use" for e in _of_type(events, "assistant") for b in e["message"]["content"])


def test_build_session_changes_the_working_tree(fake_claude, tmp_path):
    fake_claude.scenario("build-edit")
    repo = tmp_path / "repo"
    repo.mkdir()
    done = _run(repo, *SESSION_ARGS)
    assert done.returncode == 0
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "built by the fake claude\n"


def test_next_and_stop_lines(fake_claude, tmp_path):
    fake_claude.scenario("next")
    (result,) = _of_type(_events(_run(tmp_path, *SESSION_ARGS)), "result")
    assert "\nNEXT: other" in result["result"]
    fake_claude.scenario("stop-loop")
    (result,) = _of_type(_events(_run(tmp_path, *SESSION_ARGS)), "result")
    assert "\nSTOP_LOOP: " in result["result"]
    assert "COMMIT:" not in result["result"]


def test_usage_limit_is_rejected_with_a_reset_in_the_future(fake_claude, tmp_path):
    import time

    fake_claude.scenario("usage-limit")
    events = _events(_run(tmp_path, *SESSION_ARGS))
    (rate,) = _of_type(events, "rate_limit_event")
    assert rate["rate_limit_info"]["status"] == "rejected"
    assert rate["rate_limit_info"]["rateLimitType"] == "seven_day"
    assert rate["rate_limit_info"]["resetsAt"] > time.time()
    (result,) = _of_type(events, "result")
    assert result["is_error"] is True
    assert "limit" in result["result"]


def test_no_result_ends_early_and_fails(fake_claude, tmp_path):
    fake_claude.scenario("no-result")
    done = _run(tmp_path, *SESSION_ARGS)
    assert done.returncode == 1
    assert not _of_type(_events(done), "result")
    assert "ended early" in done.stderr.decode("utf-8")


def test_probe_with_the_prompt_as_an_argument(fake_claude, tmp_path):
    # Test-UsageBack: `claude -p "Reply with the word ok." --model haiku ...`, nothing on stdin.
    fake_claude.scenario("probe-ok")
    done = subprocess.run([_claude(), "-p", "Reply with the word ok.", "--model", "haiku", "--max-turns", "1",
                           "--no-session-persistence", "--output-format", "stream-json", "--verbose"],
                          capture_output=True, cwd=tmp_path, stdin=subprocess.DEVNULL, timeout=60)
    (result,) = _of_type(_events(done), "result")
    assert result["result"] == "ok"
    (call,) = fake_claude.calls()
    assert call["prompt"] == "Reply with the word ok."
    assert call["prompt_source"] == "argument"
    assert call["model"] == "haiku"


def test_probe_refused(fake_claude, tmp_path):
    fake_claude.scenario("probe-refused")
    events = _events(_run(tmp_path, "-p", "Reply with the word ok."))
    assert _of_type(events, "rate_limit_event")[0]["rate_limit_info"]["status"] == "rejected"
    assert _of_type(events, "result")[0]["is_error"] is True


def test_call_is_recorded(fake_claude, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "C:/accounts/one")
    monkeypatch.setenv("OVERSEER_TRACKER", "alice/projects/x.os")
    monkeypatch.setenv("AGENT_AUTHOR", "claude-opus-5-5 (medium)")
    monkeypatch.setenv("AGENT_SCRATCH", "C:/scratch")
    monkeypatch.setenv("OVERSEER_KEY", "not-for-the-record")
    monkeypatch.setenv("FAKE_CLAUDE_ENV", "SOMETHING_ELSE")
    monkeypatch.setenv("SOMETHING_ELSE", "yes")
    fake_claude.scenario("triage-ready")
    prompt = 'Work "thing".\nIt holds $HOME and `backticks`.'
    _run(tmp_path, *SESSION_ARGS, prompt=prompt)
    (call,) = fake_claude.calls()
    assert call["argv"] == SESSION_ARGS
    assert call["cwd"] == str(tmp_path)
    assert call["prompt"] == prompt
    assert call["prompt_source"] == "stdin"
    assert call["session_id"] == "11111111-2222-3333-4444-555555555555"
    assert call["resume"] is False
    assert call["model"] == "opus" and call["effort"] == "medium"
    assert call["env"]["CLAUDE_CONFIG_DIR"] == "C:/accounts/one"
    assert call["env"]["OVERSEER_TRACKER"] == "alice/projects/x.os"
    assert call["env"]["AGENT_AUTHOR"] == "claude-opus-5-5 (medium)"
    assert call["env"]["SOMETHING_ELSE"] == "yes"
    assert call["env"]["OVERSEER_ITEM"] is None  # recorded always, None when the caller set none
    # Only what it was told to record: never the rest of the environment.
    assert "OVERSEER_KEY" not in call["env"]
    assert "not-for-the-record" not in fake_claude.log.read_text(encoding="utf-8")


def test_resume_uses_the_session_it_is_given(fake_claude, tmp_path):
    fake_claude.scenario("triage-ready")
    done = _run(tmp_path, "-p", "--resume", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "--output-format", "stream-json")
    assert {e["session_id"] for e in _events(done)} == {"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
    (call,) = fake_claude.calls()
    assert call["resume"] is True


def test_scenarios_play_one_per_call_and_the_last_again(fake_claude, tmp_path):
    fake_claude.scenario("usage-limit", "triage-ready")
    outcomes = [_of_type(_events(_run(tmp_path, *SESSION_ARGS)), "result")[0]["is_error"] for _ in range(3)]
    assert outcomes == [True, False, False]
    assert len(fake_claude.calls()) == 3


def test_no_scenario_is_an_error(fake_claude, tmp_path):
    done = _run(tmp_path, *SESSION_ARGS)
    assert done.returncode == 2
    assert "FAKE_CLAUDE_SCENARIO" in done.stderr.decode("utf-8")


def test_unknown_scenario_is_an_error(fake_claude, tmp_path):
    fake_claude.scenario("nonesuch")
    done = _run(tmp_path, *SESSION_ARGS)
    assert done.returncode != 0
    assert "no session" in done.stderr.decode("utf-8")
