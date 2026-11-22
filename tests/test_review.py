"""review-run.py on a recorded run, and `agentloop review` handing it the app's logs and tracker."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentloop import config, review, tools
from test_loop import item, make_repo, tracker_text

STAMP = "20261024-063000"


def _session(sid: str, model: str, cost: float, calls: list[tuple[str, str, bool]]) -> list[dict]:
    """A session's stream-json log: its tool calls, each with what came back and whether it failed."""
    events = [{"type": "system", "subtype": "init", "session_id": sid, "model": model}]
    for n, (command, said, failed) in enumerate(calls):
        events.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{n}", "name": "Bash", "input": {"command": command}}]}})
        events.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{n}", "content": said, "is_error": failed}]}})
    events.append({"type": "result", "subtype": "success", "num_turns": 4, "duration_ms": 90000,
                   "total_cost_usd": cost, "session_id": sid})
    return events


def _attempt(sid: str, kind: str, model: str, frm: str, to: str, cost: float) -> dict:
    return {"at": "2026-10-24T06:31:00", "handle": "alpha", "kind": kind, "model": model, "effort": "medium",
            "from": frm, "to": to, "points": 2, "complexity": "low", "session": sid, "cost": cost}


def make_logs(folder: Path) -> Path:
    """A run of two sessions on alpha: a triage on opus, and a build on sonnet whose shell found no
    command once, and once went through with a path a program could not find."""
    folder.mkdir(parents=True)
    sessions = {
        f"loop-{STAMP}-1-triage.jsonl": _session("s1", "claude-opus-5-5", 0.40, [("ls", "a b", False)]),
        f"loop-{STAMP}-2-build.jsonl": _session("s2", "claude-sonnet-5-5", 1.10, [
            ("frob --all", "bash: frob: command not found", True),
            ("python /tmp/x.py", "can't open file: No such file or directory", False)]),
    }
    for name, events in sessions.items():
        (folder / name).write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    attempts = [_attempt("s1", "triage", "opus", "filed", "ready", 0.40),
                _attempt("s2", "build", "sonnet", "ready", "testing", 1.10)]
    (folder / "attempts.jsonl").write_text("".join(json.dumps(a) + "\n" for a in attempts), encoding="utf-8")
    return folder


def _review_run(*args, cwd, env=None):
    return subprocess.run([sys.executable, "-I", str(tools.path("review-run.py")), *map(str, args)], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", timeout=60, env=env)


def test_a_run_from_a_logs_folder(tmp_path):
    logs = make_logs(tmp_path / "somewhere" / "proj")
    done = _review_run("proj", "--logs", logs, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    out = done.stdout
    assert out.startswith(f"run {STAMP}\n")
    lines = out.splitlines()
    triage = next(l for l in lines if " triage " in l)
    build = next(l for l in lines if " build " in l)
    assert "alpha" in triage and "claude-opus-5-5" in triage and "$ 0.40" in triage and "filed -> ready" in triage
    assert "claude-sonnet-5-5" in build and "$ 1.10" in build and "ready -> testing" in build and "1 failed" in build
    assert "$1.50 in all" in out
    # Cost by item and by model; no tracker, so where it is now is not known.
    by_item = next(l for l in lines if l.strip().startswith("alpha") and "pts" in l)
    assert "$ 1.50" in by_item and "opus $0.40" in by_item and "sonnet $1.10" in by_item
    assert "now ?" in out
    # Failed calls, and trouble in calls that went through, by kind.
    assert "Tool calls that failed, by kind:" in out
    assert "command not found - 1" in out
    assert f"{STAMP} #2 build" in out and "frob --all" in out
    assert "Shell trouble in calls that went through, by kind:" in out
    assert "path not found - 1" in out


def test_one_run_and_every_run(tmp_path):
    logs = make_logs(tmp_path / "proj")
    (logs / "loop-20261025-010000-1-triage.jsonl").write_text(
        "\n".join(json.dumps(e) for e in _session("s3", "claude-opus-5-5", 0.2, [])) + "\n", encoding="utf-8")
    latest = _review_run("proj", "--logs", logs, cwd=tmp_path).stdout
    assert latest.startswith("run 20261025-010000\n") and f"run {STAMP}" not in latest
    one = _review_run("proj", STAMP, "--logs", logs, cwd=tmp_path).stdout
    assert one.startswith(f"run {STAMP}\n") and "20261025" not in one
    every = _review_run("proj", "--all", "--logs", logs, cwd=tmp_path).stdout
    assert f"run {STAMP}" in every and "run 20261025-010000" in every
    missing = _review_run("proj", "20990101-000000", "--logs", logs, cwd=tmp_path)
    assert missing.returncode != 0 and "no run 20990101-000000" in missing.stderr


def test_without_options_it_reads_the_logs_beside_it_as_the_script_does(tmp_path):
    here = tmp_path / "tools"
    here.mkdir()
    for name in ("review-run.py", "tracker.py"):
        shutil.copy(tools.path(name), here / name)
    make_logs(here / "logs" / "proj")
    done = subprocess.run([sys.executable, "-I", str(here / "review-run.py"), "proj"], cwd=tmp_path,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    assert "$1.50 in all" in done.stdout


def test_a_value_is_wanted(tmp_path):
    done = _review_run("proj", "--logs", cwd=tmp_path)
    assert done.returncode != 0 and "--logs wants a value" in done.stderr


def test_a_tracker_that_cannot_be_read_leaves_now_out(tmp_path):
    logs = make_logs(tmp_path / "proj")
    env = {k: v for k, v in os.environ.items() if not k.startswith("OVERSEER_")}
    done = _review_run("proj", "--logs", logs, "--tracker", "alice/projects/none.os", cwd=tmp_path, env=env)
    assert done.returncode == 0, done.stderr
    assert "now ?" in done.stdout
    assert "the tracker could not be read" in done.stderr


# agentloop review, on a project of the app's

@pytest.fixture
def home(tmp_path, monkeypatch, overseer):
    """A project proj with a loop, its tracker on the test's server, alpha in it back at ready - sent
    back after testing - and the run's logs where the app keeps them. Its tracker's name."""
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    for name in list(os.environ):
        if name.startswith("OVERSEER_"):
            monkeypatch.delenv(name)
    code = make_repo(tmp_path / "code", {"README.md": "code\n"})
    tracker = overseer.document(tracker_text(item("alpha", "ready", complexity="low")))
    config.save_json(tmp_path / "home" / "config" / "settings.json", {
        "format": 1,
        "workers": [{"name": "w", "claude": None, "overseer": overseer.worker}],
        "loops": [{"name": "proj", "tracker": tracker, "worker": "w", "repos": {"code": str(code)}, "settings": {}}],
        "defaults": {},
    })
    make_logs(config.paths().data / "logs" / "proj")
    return tracker


def _agentloop(*args):
    return subprocess.run([sys.executable, "-m", "agentloop", "review", *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)


def test_review_gives_the_apps_logs_and_tracker(home, overseer):
    found = review.arguments(config.load_machine(), "proj")
    assert found.logs == config.paths().data / "logs" / "proj"
    assert found.tracker == home
    assert (found.env["OVERSEER_URL"], found.env["OVERSEER_KEY_FILE"]) == (overseer.url, str(overseer.key.resolve()))
    assert found.why_no_tracker is None


def test_review_without_the_workers_key_leaves_the_tracker_out(home):
    path = config.paths().settings
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["workers"][0]["overseer"] = None
    config.save_json(path, raw)
    found = review.arguments(config.load_machine(), "proj")
    assert found.tracker is None and "names no file holding the agents' key" in found.why_no_tracker


def test_review_end_to_end(home):
    done = _agentloop("proj")
    assert done.returncode == 0, done.stderr
    assert f"run {STAMP}" in done.stdout and "$1.50 in all" in done.stdout
    # The served tracker has alpha at ready again, after a build that sent it to testing.
    assert "now ready - sent back" in done.stdout
    assert "command not found - 1" in done.stdout


def test_review_without_a_loop_leaves_the_tracker_out(home):
    path = config.paths().settings
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["loops"] = []
    config.save_json(path, raw)
    done = _agentloop("proj")
    assert done.returncode == 0, done.stderr
    assert "no loop on proj says where its tracker is" in done.stderr
    assert "now ?" in done.stdout


def test_review_refuses_what_it_cannot_review(home):
    done = _agentloop("nope")
    assert done.returncode == 2 and "No project named nope. The projects are: proj" in done.stderr
    done = _agentloop("proj", "--loop", "other")
    assert done.returncode == 2 and "No loop named other" in done.stderr
    shutil.rmtree(config.paths().data / "logs" / "proj")
    done = _agentloop("proj")
    assert done.returncode == 2 and "No logs of proj yet" in done.stderr
