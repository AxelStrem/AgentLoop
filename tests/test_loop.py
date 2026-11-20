"""The loop, driven with the fake claude on a tracker of the test's own, on a served copy of
overseer-server (conftest's `overseer`)."""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from agentloop import accounts, colours, config, loop, session, tools, usage


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def make_repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "core.autocrlf", "false")
    for name, text in files.items():
        (path / name).write_text(text, encoding="utf-8", newline="\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "start")
    return path.resolve()


def item(handle: str, stage: str = "filed", points: int = 3, complexity: str | None = None, flags: str | None = None,
         labels: str | None = None) -> str:
    lines = ['        - {', '            - added = "2026-10-01T00:00:00+00:00"', f'            - handle = "{handle}"',
             f'            - title = "The {handle} item"']
    if stage != "filed":
        lines.append(f'            - stage = "{stage}"')
    if labels:
        lines.append(f'            - labels = "{labels}"')
    if flags:
        lines.append(f'            - flags = "{flags}"')
    lines.append(f"            - points = {points}")
    if complexity:
        lines.append(f'            - complexity = "{complexity}"')
    lines.append("        }")
    return "\n".join(lines)


def tracker_text(*items: str) -> str:
    return "\n".join([
        "// A test tracker",
        '<projects/project_template.os/project> project (label="Proj") {',
        '    - name = "Proj"',
        '    - description = "A project for tests."',
        "    - Items {",
        *items,
        "    }",
        "    - Labels {",
        "        - {",
        '            - tag = "core"',
        '            - name = "Core"',
        "            div About {",
        '                - about = "The loop itself."',
        "            }",
        "        }",
        "        - {",
        '            - tag = "ui"',
        '            - name = "UI"',
        "            div About {",
        '                - about = "The page."',
        "            }",
        "        }",
        "    }",
        "    - History {",
        "    }",
        "}",
        "",
    ])


class FakeClock(usage.Clock):
    def __init__(self, start: float | None = None):
        self.t = time.time() if start is None else start
        self.slept = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds
        self.slept += seconds


class Project:
    """A project on this test's PC: a code repository, its tracker on the test's server, a bundle, a
    worker, a loop - and the scenes the fake claude plays."""

    def __init__(self, tmp_path: Path, monkeypatch, fake_claude, overseer, items: list[str], settings: dict | None = None):
        self.tmp = tmp_path
        self.fake = fake_claude
        self.overseer = overseer
        home = tmp_path / "home"
        monkeypatch.setenv("AGENTLOOP_HOME", str(home))
        self.code = make_repo(tmp_path / "code", {"README.md": "code\n"})
        # The tracker's name on the server.
        self.tracker = overseer.document(tracker_text(*items))
        self.account = tmp_path / "account"
        self.account.mkdir()
        bundle = home / "config" / "bundles" / "proj"
        bundle.mkdir(parents=True)
        (bundle / "brief.md").write_text("# The brief\n", encoding="utf-8")
        config.save_json(bundle / "project.json", {"format": 1, "name": "proj", "tracker": {"server": self.tracker},
                                                   "repos": ["code"], "settings": {}})
        # The trial is off unless a test turns it on: its date is near enough to today to change
        # which way a medium item goes.
        self.settings = {"trial": None, **(settings or {})}
        config.save_json(home / "config" / "settings.json", {
            "format": 1, "bundles": [str(bundle)],
            "workers": [{"name": "w", "claude": str(self.account), "overseer": overseer.worker}],
            "loops": [{"name": "proj", "project": "proj", "worker": "w",
                       "repos": {"code": str(self.code)}, "settings": self.settings}],
            "defaults": {},
        })
        self.scenes: list[Path] = []
        self.lines: list[str] = []
        self.clock = FakeClock()

    def scene(self, *steps: dict, result: str = "", cost: float = 0.05, error: bool = False, model: str = "claude-opus-5-5",
              rate: dict | None = None, context: int = 6000, transcript: bool = True) -> Path:
        """A session for the fake claude: its steps, then its final answer."""
        events = [{"type": "system", "subtype": "init", "session_id": "x", "model": model}]
        events.append({"type": "assistant", "message": {"model": model, "content": [{"type": "text", "text": "Working."}],
                                                        "usage": {"input_tokens": 2, "cache_read_input_tokens": context - 1002,
                                                                  "cache_creation_input_tokens": 1000, "output_tokens": 10}},
                       "parent_tool_use_id": None, "session_id": "x"})
        if transcript:
            events.append({"fake": "transcript"})
        events.extend(steps)
        events.append({"type": "rate_limit_event", "session_id": "x", "rate_limit_info": rate or {
            "status": "allowed", "resetsAt": "@now+7200", "rateLimitType": "five_hour",
            "unifiedWindows": {"five_hour": {"utilization": 0.35, "resetsAt": "@now+7200"},
                               "seven_day": {"utilization": 0.29, "resetsAt": "@now+172800"}}}})
        events.append({"type": "result", "subtype": "success", "is_error": error, "result": result, "num_turns": 3,
                       "duration_ms": 60000, "total_cost_usd": cost, "session_id": "x",
                       "modelUsage": {model: {"inputTokens": 10, "cacheReadInputTokens": 5000, "cacheCreationInputTokens": 1000,
                                              "outputTokens": 400}}})
        path = self.tmp / f"scene-{len(self.scenes)}.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        self.scenes.append(path)
        return path

    def play(self, *scenes: Path) -> None:
        self.fake.scenario(*(str(s) for s in scenes))

    def run(self, worker: str | None = None, paint=colours.plain, **overrides) -> loop.Run:
        machine = config.load_machine()
        self.r = loop.Run(machine, machine.settings.loop("proj"), out=self.lines.append, clock=self.clock,
                          overrides=overrides, worker=worker, paint=paint)
        return self.r

    def go(self, **overrides) -> int:
        return self.run(**overrides).run()

    def show(self, handle: str) -> dict:
        return json.loads(self.overseer.tracker(self.tracker, "show", handle, "--json").stdout)

    def attempts(self) -> list[dict]:
        path = config.paths().data / "logs" / "proj" / "attempts.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def subjects(self, repo: Path) -> list[str]:
        return git(repo, "log", "--format=%s").splitlines()[:-1]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def move(handle: str, stage: str, complexity: str | None = None, points: int | None = None) -> dict:
    args = ["move", handle, stage, "--note", f"moved to {stage}"]
    if complexity:
        args += ["--complexity", complexity]
    if points is not None:
        args += ["--points", str(points)]
    return {"fake": "tracker", "args": args}


def write(path: str, text: str = "made by the fake claude\n") -> dict:
    return {"fake": "write", "path": path, "text": text}


@pytest.fixture
def project(tmp_path, monkeypatch, fake_claude, overseer):
    def make(*items: str, **settings) -> Project:
        return Project(tmp_path, monkeypatch, fake_claude, overseer, list(items), settings)
    return make


# Routing

@pytest.mark.parametrize("complexity, route", [
    ("trivial", {"model": "sonnet", "effort": "medium"}),
    ("low", {"model": "sonnet", "effort": "high"}),
    ("medium", {"model": "opus", "effort": "medium"}),
    ("high", {"model": "opus", "effort": "high"}),
    ("unassigned", {"model": "opus", "effort": "medium"}),
])
def test_a_ready_item_goes_by_its_complexity(project, fake_claude, complexity, route):
    p = project(item("alpha", "ready", complexity=None if complexity == "unassigned" else complexity))
    r = p.run()
    plan = r.plan(r.read_tracker("next"))
    assert (plan.kind, plan.route) == ("build", route)


def test_filed_and_planning_are_triaged_and_discuss_gets_thoughts(project, fake_claude):
    p = project(item("alpha"), item("beta", "planning"), item("gamma", "ready", complexity="low", flags="discuss"))
    r = p.run()
    for handle, kind in (("alpha", "triage"), ("beta", "triage"), ("gamma", "discuss")):
        plan = r.plan(r.read_tracker("next", "--prefer", handle))
        assert (plan.kind, plan.route) == (kind, {"model": "opus", "effort": "medium"})


def test_a_model_override_has_no_effort(project, fake_claude):
    p = project(item("alpha", "ready", complexity="high"))
    r = p.run(model="haiku")
    assert r.plan(r.read_tracker("next")).route == {"model": "haiku", "effort": ""}


def test_a_build_escalates_once_its_model_has_had_its_go(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"))
    r = p.run()
    r.attempts.parent.mkdir(parents=True, exist_ok=True)
    r.attempts.write_text(json.dumps({"handle": "alpha", "kind": "triage", "model": "sonnet"}) + "\n", encoding="utf-8")
    assert r.plan(r.read_tracker("next")).route == {"model": "sonnet", "effort": "high"}
    r.attempts.write_text(json.dumps({"handle": "alpha", "kind": "build", "model": "sonnet"}) + "\n", encoding="utf-8")
    assert r.plan(r.read_tracker("next")).route == {"model": "opus", "effort": "medium"}
    assert "  sonnet has had its go at alpha; this build is on opus." in p.lines


def test_a_build_on_another_pc_escalates_by_its_work_record(project, fake_claude):
    # The record names the model the session reported, with its effort; attempts.jsonl here has
    # nothing, as on a PC that never worked the item.
    p = project(item("alpha", "ready", complexity="low"))
    r = p.run()
    p.overseer.tracker(p.tracker, "work", "alpha", "--kind", "triage", "--model", "claude-sonnet-5-5 (high)")
    assert r.plan(r.read_tracker("next")).route == {"model": "sonnet", "effort": "high"}
    p.overseer.tracker(p.tracker, "work", "alpha", "--kind", "build", "--model", "claude-sonnet-5-5 (high)", "--moved", "ready > ready")
    assert not r.attempts.exists()
    assert r.plan(r.read_tracker("next")).route == {"model": "opus", "effort": "medium"}
    assert "  sonnet has had its go at alpha; this build is on opus." in p.lines


@pytest.mark.parametrize("recorded, model, same", [
    ("claude-sonnet-5-5 (high)", "sonnet", True),
    ("claude-sonnet-5-5", "sonnet", True),
    ("claude-opus-5-5 (medium)", "sonnet", False),
    ("claude-sonnet-5-5 (high)", "claude-sonnet-5-5", True),
    ("claude-sonnet-5-5 (high)", "claude-sonnet-4-5", False),
    ("sonnet (high)", "sonnet", True),
    ("", "sonnet", False),
])
def test_a_work_records_model_is_matched_to_a_routes(recorded, model, same):
    assert loop.same_model(recorded, model) is same


def test_the_trial_holds_until_its_date(project, fake_claude):
    trial = {"until": "2026-10-29", "complexity": "medium", "upTo": 3, "route": {"model": "sonnet", "effort": "high"}}
    p = project(item("alpha", "ready", complexity="medium", points=3), item("beta", "ready", complexity="medium", points=4), trial=trial)
    r = p.run()
    p.clock.t = datetime(2026, 10, 28, 23, 0).timestamp()
    assert r.plan(r.read_tracker("next", "--prefer", "alpha")).route == trial["route"]
    assert r.plan(r.read_tracker("next", "--prefer", "beta")).route == {"model": "opus", "effort": "medium"}
    p.clock.t = datetime(2026, 10, 29, 0, 0).timestamp()
    assert r.plan(r.read_tracker("next", "--prefer", "alpha")).route == {"model": "opus", "effort": "medium"}


# Triage, and the build after it

def test_triage_then_build_in_the_same_session(project, fake_claude):
    p = project(item("alpha", labels="core"))
    p.play(p.scene(move("alpha", "ready", "medium", 3), result="Planned.\n\nCOMMIT: alpha: triaged"),
           p.scene(write("built.txt"), move("alpha", "testing"), result="Built.\n\nCOMMIT: alpha: built it", cost=0.15))
    assert p.go() == 0
    calls = fake_claude.calls()
    assert len(calls) == 2
    assert calls[0]["model"] == "opus" and calls[0]["effort"] == "medium" and not calls[0]["resume"]
    assert calls[1]["resume"] and calls[1]["session_id"] == calls[0]["session_id"]
    assert calls[1]["prompt"].startswith("Your triage of alpha has been committed. Now build it as your plan says")
    assert calls[0]["env"]["AGENT_AUTHOR"] == "opus (medium)"
    assert calls[0]["env"]["OVERSEER_ITEM"] == "proj:alpha"
    assert calls[0]["env"]["OVERSEER_TRACKER"] == p.tracker

    # The tracker's edits are on the server; only the code is committed.
    assert p.subjects(p.code) == ["alpha: built it [opus medium]"]
    a = p.attempts()
    assert [(x["kind"], x["from"], x["to"], x["complexity"]) for x in a] == [("triage", "filed", "ready", "medium"), ("build", "ready", "testing", "medium")]
    # The build is charged only what it added to the session the triage began.
    assert a[0]["cost"] == 0.05 and a[1]["cost"] == 0.1
    assert [(c["repo"], c["tracker"]) for c in a[1]["commits"]] == [("code", False)]
    work = p.show("alpha")["work"]
    assert [(w["kind"], w["model"], w["moved"]) for w in work] == [
        ("triage", "claude-opus-5-5 (medium)", "filed > ready"), ("build", "claude-opus-5-5 (medium)", "ready > testing")]
    assert "  build, in the same session" in p.lines
    assert "[QUEUE_EMPTY] - All actionable agent tasks have been fully cleared." in p.lines
    assert p.lines[-1].startswith("  Usage on account: 5-hour 35% used") or p.lines[-2].startswith("Loop finished")
    # Cleared once committed: the transcript and the scratch folder.
    assert not list((p.account / "projects").rglob("*.jsonl"))
    assert not list((p.r.loop_dir / "scratch").iterdir())


def test_a_small_item_is_built_by_its_triage(project, fake_claude):
    p = project(item("alpha"))
    p.play(p.scene(move("alpha", "ready", "low", 2), result="COMMIT: alpha: triaged"),
           p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    p.go()
    calls = fake_claude.calls()
    assert len(calls) == 2 and calls[1]["resume"]


def test_a_low_item_is_left_for_its_own_build(project, fake_claude):
    p = project(item("alpha"))
    p.play(p.scene(move("alpha", "ready", "low", 3), result="COMMIT: alpha: triaged"),
           p.scene(move("alpha", "testing"), result="COMMIT: alpha: built", model="claude-sonnet-5-5"))
    p.go()
    calls = fake_claude.calls()
    assert len(calls) == 2
    assert not calls[1]["resume"] and calls[1]["session_id"] != calls[0]["session_id"]
    assert (calls[1]["model"], calls[1]["effort"]) == ("sonnet", "high")
    assert "Build it as its plan says, check it, and move it to testing" in calls[1]["prompt"]
    assert p.show("alpha")["stage"] == "testing"


def test_the_trial_keeps_the_triage_from_building(project, fake_claude):
    trial = {"until": "2026-10-29", "complexity": "medium", "upTo": 3, "route": {"model": "sonnet", "effort": "high"}}
    p = project(item("alpha"), trial=trial)
    p.clock.t = datetime(2026, 10, 20, 12, 0).timestamp()
    p.play(p.scene(move("alpha", "ready", "medium", 3), result="COMMIT: alpha: triaged"),
           p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    p.go()
    calls = fake_claude.calls()
    assert not calls[1]["resume"] and (calls[1]["model"], calls[1]["effort"]) == ("sonnet", "high")


def test_a_discuss_session(project, fake_claude):
    p = project(item("alpha", flags="discuss"))
    p.play(p.scene({"fake": "tracker", "args": ["ask", "alpha", "Which way?"]}, result="COMMIT: alpha: thoughts asked"))
    p.go()
    calls = fake_claude.calls()
    assert len(calls) == 1
    assert "It is flagged discuss: only your thoughts are wanted." in calls[0]["prompt"]
    assert p.attempts()[0]["kind"] == "discuss" and p.attempts()[0]["to"] == "planning"


# Going on to another item

def chain(project, **settings):
    p = project(item("alpha", "ready", complexity="low"), item("beta", "ready", complexity="low"), item("gamma", "ready", complexity="low"), **settings)
    p.play(p.scene(write("alpha.txt"), move("alpha", "testing"), result="COMMIT: alpha: built\nNEXT: gamma"),
           p.scene(write("gamma.txt"), move("gamma", "testing"), result="COMMIT: gamma: built\nNEXT: beta"),
           p.scene(write("beta.txt"), move("beta", "testing"), result="COMMIT: beta: built"))
    return p


def test_going_on_in_the_same_session(project, fake_claude):
    p = chain(project)
    p.go()
    calls = fake_claude.calls()
    assert len(calls) == 3
    assert len({c["session_id"] for c in calls}) == 1 and calls[1]["resume"] and calls[2]["resume"]
    assert calls[1]["prompt"].startswith("Your work on alpha has been committed. As you asked, go on in this same session with gamma, at ready")
    assert [a.get("went_on_from") for a in p.attempts()] == [None, "alpha", "gamma"]
    assert "  build, at ready, in the same session, as it asked (item 2 of 20)" in p.lines


@pytest.mark.parametrize("settings, why", [
    ({"chainUpTo": 2}, "this session has worked 2 items"),
    ({"maxIterations": 2}, "the run has worked its 2 items"),
    ({"chainContextUpTo": 5000}, "its context is 6k tokens"),
])
def test_going_on_has_its_limits(project, fake_claude, settings, why):
    p = chain(project, **settings)
    p.go()
    if "chainContextUpTo" in settings:
        assert f"  The session asked to go on to gamma; not in this session: {why}." in p.lines
    else:
        assert f"  The session asked to go on to beta; not in this session: {why}." in p.lines


def test_going_on_only_to_an_item_free_to_take(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"), item("beta", "ready", complexity="low", flags="manual"))
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built\nNEXT: beta"))
    p.go()
    assert "  The session asked to go on to beta; not in this session: it is not free to take now." in p.lines


def test_going_on_not_to_a_build_its_model_has_had(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"), item("beta", "ready", complexity="medium"))
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built\nNEXT: beta"),
           p.scene(move("beta", "testing"), result="COMMIT: beta: built"))
    r = p.run()
    r.attempts.parent.mkdir(parents=True, exist_ok=True)
    r.attempts.write_text(json.dumps({"handle": "beta", "kind": "build", "model": "sonnet"}) + "\n", encoding="utf-8")
    r.run()
    assert "  The session asked to go on to beta; not in this session: sonnet has had its go at building it." in p.lines


def test_going_on_not_once_a_stop_is_asked(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"), item("beta", "ready", complexity="low"))
    r = p.run()
    p.play(p.scene(move("alpha", "testing"), write(str(r.stop_file), ""), result="COMMIT: alpha: built\nNEXT: beta"))
    assert r.run() == 0
    assert "  The session asked to go on to beta; not in this session: a stop was asked for." in p.lines
    assert "Stopping as asked." in p.lines
    assert len(fake_claude.calls()) == 1 and not r.stop_file.exists()


# Stopping

def test_stops_on_the_signal(project, fake_claude):
    p = project(item("alpha", "testing"))
    assert p.go() == 0
    assert fake_claude.calls() == []
    assert "[QUEUE_EMPTY] - All actionable agent tasks have been fully cleared." in p.lines
    assert p.lines[-1] == "Loop finished; its sessions came to $0."


def test_stops_on_stop_loop_and_commits_nothing(project, fake_claude):
    p = project(item("alpha"))
    p.play(p.scene(write("half.txt"), result="Cannot.\n\nSTOP_LOOP: the build does not run"))
    assert p.go() == 1
    assert "The agent stopped the loop: the build does not run" in p.lines
    assert p.subjects(p.code) == [] and (p.code / "half.txt").exists()
    assert p.attempts()[0]["to"] == "stopped by the agent: the build does not run"
    # Kept, for whoever looks into why.
    assert list((p.r.loop_dir / "scratch").iterdir())


def test_stops_on_a_failed_session(project, fake_claude):
    p = project(item("alpha"))
    p.play(p.scene(write("half.txt"), result="Something broke", error=True))
    assert p.go() == 1
    assert "The session failed: Something broke. Stopping, with its changes uncommitted; carry it on with: " in p.text
    assert p.attempts()[0]["to"] == "failed"
    assert p.subjects(p.code) == []


def test_stops_on_a_session_without_a_result(project, fake_claude):
    p = project(item("alpha"))
    scene = p.tmp / "cut.jsonl"
    scene.write_text(json.dumps({"type": "system", "subtype": "init", "model": "m"}) + "\n" + json.dumps({"fake": "exit", "code": 1}) + "\n")
    p.play(scene)
    assert p.go() == 1
    assert "The session ended without a result." in p.text
    assert p.attempts()[0]["to"] == "failed"


def test_stops_after_sessions_that_left_an_item_where_it_was(project, fake_claude):
    p = project(item("alpha"))
    p.play(p.scene(result="COMMIT: alpha: looked"))
    assert p.go() == 1
    assert len(fake_claude.calls()) == 2
    assert "alpha is still at filed after 2 sessions on opus left it there. Stopping, for the owner to look." in p.lines


def test_an_item_asked_about_is_not_a_repeat(project, fake_claude):
    p = project(item("alpha"), item("beta"))
    p.play(p.scene({"fake": "tracker", "args": ["ask", "alpha", "Which?"]}, result="COMMIT: alpha: asked"),
           p.scene({"fake": "tracker", "args": ["ask", "beta", "Which?"]}, result="COMMIT: beta: asked"))
    assert p.go() == 0
    assert not p.r.seen


def test_stops_at_max_iterations(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"), item("beta", "ready", complexity="low"))
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    assert p.go(maxIterations=1) == 0
    assert len(fake_claude.calls()) == 1
    assert " Iteration 1 of 1..." in p.lines


def test_a_kill_during_a_session(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"))
    r = p.run()
    p.play(p.scene(write("half.txt"), move("alpha", "testing"), write(str(r.kill_file), ""), result="COMMIT: alpha: built"))
    assert r.run() == 0
    assert "Stopped at once, as asked: alpha is left as the session had it, its changes uncommitted. To carry the session on by hand:" in p.lines
    assert p.subjects(p.code) == [] and p.attempts()[0]["to"] == "killed"
    assert not r.kill_file.exists()


def test_a_kill_between_items(project, fake_claude):
    p = project(item("alpha"), item("beta"))
    r = p.run()
    # One left by a loop that has since ended is not this one's.
    r.loop_dir.mkdir(parents=True, exist_ok=True)
    r.kill_file.write_text("")
    # Asked for in the pause after the first item.
    p.clock.sleep = lambda seconds: r.kill_file.write_text("")
    p.play(p.scene(move("alpha", "ready", "low", 3), result="COMMIT: alpha: triaged"))
    assert r.run() == 0
    assert len(fake_claude.calls()) == 1
    assert "Stopped at once, as asked, between items." in p.lines
    assert not r.kill_file.exists()


# Auto-start

def idling(p, *hooks):
    """Each sleep of the loop while it waits does the next of `hooks`, then the clock moves on."""
    left = list(hooks)
    moved = FakeClock.sleep

    def sleep(seconds: float) -> None:
        if seconds == usage.POLL_SECONDS and left:
            left.pop(0)()
        moved(p.clock, seconds)
    p.clock.sleep = sleep


def test_an_empty_queue_is_waited_on_and_work_taken_as_it_comes(project, fake_claude):
    p = project(item("alpha", "testing"), autoStart=True, autoStartMinutes=1)
    r = p.run()
    states = []
    # Looked at once a minute: 12 polls of 5 seconds, and nothing is there at the first look.
    idling(p, *[lambda: None] * 14,
           lambda: p.overseer.tracker(p.tracker, "file", "beta", "Beta"),
           *[lambda: None] * 12,
           lambda: states.append(json.loads(r.status_file.read_text(encoding="utf-8"))),
           lambda: r.stop_file.write_text(""))
    p.play(p.scene(move("beta", "testing"), result="COMMIT: beta: looked"))
    assert r.run() == 0
    assert len(fake_claude.calls()) == 1
    assert " Nothing to work: looking again every 1 minutes, and as the tracker changes." in p.lines
    assert " Work has come up. Iteration 1 of 20..." in p.lines
    assert "Stopping as asked." in p.lines
    assert p.lines.count(" Iteration 1 of 20...") == 1
    # Waiting again after the item, with nothing left of it on the page.
    assert states[0]["state"] == "idle" and states[0]["until"] and states[0]["item"] is None
    ended = json.loads(r.status_file.read_text(encoding="utf-8"))
    assert ended["state"] == "stopped" and ended["until"] is None


def test_a_change_to_the_tracker_is_looked_at_once_it_settles(project, fake_claude):
    p = project(item("alpha", "testing"), autoStart=True, autoStartMinutes=60)
    r = p.run()
    states = []

    def heard() -> None:
        # The watch stream runs in real time on its own thread: waited for here, as the clock is
        # the test's.
        end = time.monotonic() + 15
        while time.monotonic() < end and not (r.watcher and r.watcher.changes):
            time.sleep(0.05)
    idling(p, lambda: wait_watching(r),
           lambda: states.append(json.loads(r.status_file.read_text(encoding="utf-8"))),
           lambda: p.overseer.tracker(p.tracker, "file", "beta", "Beta"), heard,
           *[lambda: None] * 4,
           lambda: r.stop_file.write_text(""))
    p.play(p.scene(move("beta", "testing"), result="COMMIT: beta: looked"))
    started = p.clock.now()
    assert r.run() == 0
    assert len(fake_claude.calls()) == 1
    assert states[0]["watching"] is True
    # Looked at once the change had been still for SETTLE_SECONDS, not at the hour.
    assert " Work has come up. Iteration 1 of 20..." in p.lines
    assert p.clock.now() - started < 60 * 60
    assert r.watcher is None
    ended = json.loads(r.status_file.read_text(encoding="utf-8"))
    assert ended["watching"] is False


def wait_watching(r) -> None:
    end = time.monotonic() + 15
    while time.monotonic() < end and not (r.watcher and r.watcher.watching):
        time.sleep(0.05)


def test_without_auto_start_an_empty_queue_ends_the_loop(project, fake_claude):
    p = project(item("alpha", "testing"), autoStartMinutes=1)
    assert p.go() == 0
    assert p.clock.slept == 0 and "Nothing to work" not in p.text


def test_a_kill_or_a_stop_ends_the_wait_at_once(project, fake_claude):
    p = project(item("alpha", "testing"), autoStart=True)
    r = p.run()
    idling(p, lambda: r.kill_file.write_text(""))
    assert r.run() == 0
    assert "Stopped at once, as asked, while idle." in p.lines
    assert not r.kill_file.exists() and p.clock.slept == usage.POLL_SECONDS
    r = p.run()
    idling(p, lambda: r.stop_file.write_text(""))
    assert r.run() == 0
    assert "Stopping as asked." in p.lines and not r.stop_file.exists()


def test_the_toggle_turned_off_ends_the_wait(project, fake_claude):
    p = project(item("alpha", "testing"), autoStart=True)

    def off() -> None:
        path = config.paths().settings
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["loops"][0]["settings"]["autoStart"] = False
        config.save_json(path, raw)
    r = p.run()
    idling(p, off)
    assert r.run() == 0
    assert "Auto-start was turned off. Stopping." in p.lines


def test_the_toggle_turned_on_during_a_run_reaches_it(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"))
    r = p.run()

    def on() -> None:
        path = config.paths().settings
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["loops"][0]["settings"]["autoStart"] = True
        config.save_json(path, raw)
    # The pause after the item is as long as a poll, so the first goes to that.
    idling(p, lambda: None, lambda: r.stop_file.write_text(""))
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    on()
    assert r.run() == 0
    assert "Nothing to work" in p.text


def test_a_failed_look_does_not_end_the_wait(project, fake_claude):
    p = project(item("alpha", "testing"), autoStart=True, autoStartMinutes=1)
    r = p.run()
    real = r.read_next
    answers = [None, None, {"signal": "[QUEUE_EMPTY]"}]

    def read_next(*args):
        return answers.pop(0) if answers and r.status.get("state") == "idle" else real(*args)
    r.read_next = read_next
    idling(p, *[lambda: None] * 40, lambda: r.stop_file.write_text(""))
    assert r.run() == 0
    assert p.text.count("Trying again at the next look.") == 1
    assert not answers


def test_work_after_a_wait_is_a_new_run(project, fake_claude):
    p = project(item("alpha", "testing"), autoStart=True, autoStartMinutes=1, maxIterations=1)
    r = p.run()
    r.seen = {"old": {"key": "filed opus", "times": 1}}
    idling(p, lambda: p.overseer.tracker(p.tracker, "file", "gamma", "Gamma"))
    p.play(p.scene(move("gamma", "testing"), result="COMMIT: gamma: looked"))
    assert r.run() == 0
    # Its one item worked, the limit ends it, however it began.
    assert len(fake_claude.calls()) == 1 and r.iteration == 1 and "old" not in r.seen
    assert "Loop finished; its sessions came to $0.05." in p.lines


def test_stops_at_the_weekly_share(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"), item("beta", "ready", complexity="low"))
    rate = {"status": "allowed", "unifiedWindows": {"seven_day": {"utilization": 0.9}}}
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built", rate=rate))
    assert p.go(stopAtWeeklyUse=0.8) == 0
    assert len(fake_claude.calls()) == 1
    assert "The weekly allowance is 90% used (limit 80%). Stopping." in p.lines


def test_the_last_usage_is_kept_for_the_worker(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"))
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    p.go(maxIterations=1)
    at, rate = accounts.last_usage("w")
    assert at <= p.clock.now()
    assert rate["unifiedWindows"]["five_hour"]["utilization"] == 0.35


def test_the_usage_is_kept_as_it_comes(project, fake_claude):
    # During a session, before it ends: the page shows the worker's usage from this file.
    p = project(item("alpha", "ready", complexity="low"))
    loop = p.run(maxIterations=1)
    rate = {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.6}}}
    loop._show(session.Event("rate", data=rate))
    assert accounts.last_usage("w")[1] == rate
    # Nothing is said of it in the output as it comes, as in the script.
    assert not any("Usage on" in line for line in p.lines)


def test_lines_in_the_scripts_colours(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"))
    tool = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]},
            "parent_tool_use_id": None, "session_id": "x"}
    p.play(p.scene(tool, write("built.txt"), move("alpha", "testing"), result="COMMIT: alpha: built"))
    assert p.run(paint=colours.ansi, maxIterations=1).run() == 0
    coloured = {line for line in p.lines}
    # As run-agent-loop.ps1 writes them: the iteration yellow, the item cyan, a tool call dark grey,
    # the session's figures dark grey, its outcome cyan, a commit dark green, the usage dark cyan,
    # the end green; and the agent's own words in the console's own colour.
    for colour, start in (("Yellow", " Iteration 1 of 1..."), ("Cyan", " alpha - "), ("DarkGray", "  > Bash: ls"),
                          ("DarkGray", "  [success: 3 turns"), ("Cyan", "  alpha: ready -> testing"), ("DarkGreen", "  committed in code: "),
                          ("DarkCyan", "  Usage on account: "), ("Green", "Loop finished;"), ("DarkGray", "  model claude-opus-5-5, session ")):
        prefix = f"[{colours.CODES[colour]}m{start}"
        assert any(line.startswith(prefix) and line.endswith("[0m") for line in coloured), (colour, start)
    assert "  Working." in p.lines and "" in p.lines


def test_a_second_loop_on_the_project_is_refused(project, fake_claude):
    p = project(item("alpha"))
    r = p.run()
    with loop.run_lock("proj", r.lock_file):
        with pytest.raises(loop.LoopError, match="The loop proj is already working proj."):
            r.run()


def test_a_tracker_named_as_a_file_is_refused(project, fake_claude):
    p = project(item("alpha"))
    bundle = config.paths().config / "bundles" / "proj"
    raw = json.loads((bundle / "project.json").read_text(encoding="utf-8"))
    config.save_json(bundle / "project.json", {**raw, "tracker": {"repo": "code", "path": "proj.os"}})
    with pytest.raises(config.ConfigError, match="trackers are kept only on an Overseer server"):
        p.go()


def test_a_worker_without_a_server_is_refused(project, fake_claude):
    p = project(item("alpha"))
    settings_file = config.paths().settings
    raw = json.loads(settings_file.read_text(encoding="utf-8"))
    raw["workers"][0]["overseer"] = None
    config.save_json(settings_file, raw)
    with pytest.raises(loop.LoopError, match="names no file holding the agents' key"):
        p.go()


def _sign_in(sign_in: dict) -> None:
    settings_file = config.paths().settings
    raw = json.loads(settings_file.read_text(encoding="utf-8"))
    raw["workers"][0]["signIn"] = sign_in
    config.save_json(settings_file, raw)


def test_a_workers_token_reaches_its_sessions_and_nowhere_else(project, fake_claude, monkeypatch):
    p = project(item("alpha", "ready", complexity="low"))
    _sign_in({"token": "AGENTLOOP_TEST_TOKEN"})
    monkeypatch.setenv("AGENTLOOP_TEST_TOKEN", "tok-very-secret")
    monkeypatch.setenv("FAKE_CLAUDE_ENV", "CLAUDE_CODE_OAUTH_TOKEN,ANTHROPIC_API_KEY")
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    p.go()
    env = p.fake.calls()[0]["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-very-secret" and env["ANTHROPIC_API_KEY"] is None
    # Not in any file of the app's: its logs, status, prompts, system file and settings.
    for path in config.paths().data.parent.rglob("*"):
        if path.is_file():
            assert b"tok-very-secret" not in path.read_bytes(), path


def test_a_workers_token_not_set_is_refused_at_the_start(project, fake_claude, monkeypatch):
    p = project(item("alpha"))
    _sign_in({"token": "AGENTLOOP_TEST_TOKEN"})
    monkeypatch.delenv("AGENTLOOP_TEST_TOKEN", raising=False)
    with pytest.raises(loop.LoopError, match="^AGENTLOOP_TEST_TOKEN is not set"):
        p.go()


def test_an_api_key_workers_failed_session_is_not_waited_on(project, fake_claude, monkeypatch):
    p = project(item("alpha", "ready", complexity="low"))
    _sign_in({"apiKey": "AGENTLOOP_TEST_KEY"})
    monkeypatch.setenv("AGENTLOOP_TEST_KEY", "key")
    p.play(p.scene(result="API Error: 429 rate limit exceeded", error=True))
    p.go()
    assert len(p.fake.calls()) == 1
    assert "Rate limited" not in p.text


# A usage limit

def test_a_usage_limit_is_waited_out_and_the_session_resumed(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"))
    limit = {"status": "rejected", "resetsAt": "@now+120", "rateLimitType": "five_hour"}
    p.play(p.scene(result="You've hit your limit", error=True, cost=0.1, rate=limit),
           p.scene(move("alpha", "testing"), result="COMMIT: alpha: built", cost=0.3))
    assert p.go() == 0
    calls = fake_claude.calls()
    assert len(calls) == 2 and calls[1]["resume"] and calls[1]["session_id"] == calls[0]["session_id"]
    assert calls[1]["prompt"].startswith(usage.RESUME_PROMPT + "AUTOTASK.md and the project's brief")
    assert any(line.startswith("Rate limited (five_hour). Waiting until ") for line in p.lines)
    assert p.clock.slept >= 120
    a = p.attempts()
    assert len(a) == 1 and a[0]["cost"] == 0.3 and a[0]["turns"] == 6
    assert p.r.run_cost == pytest.approx(0.3)


# Records and prompts, as the script writes them

def test_attempts_are_written_as_the_script_writes_them(project, fake_claude):
    script = [json.loads(line) for line in (FIXTURES / "attempts" / "script.jsonl").read_text(encoding="utf-8").splitlines()]
    p = chain(project)
    p.go()
    ours = p.attempts()
    plain = next(s for s in script if "went_on_from" not in s and "points_was" not in s and s["commits"])
    went_on = next(s for s in script if "went_on_from" in s)
    assert list(ours[0]) == list(plain)
    assert list(ours[1]) == list(went_on)
    for mine, theirs in ((ours[0], plain), (ours[1], went_on)):
        for key in theirs:
            assert type(mine[key]) is type(theirs[key]) or {type(mine[key]), type(theirs[key])} <= {int, float}, key
        assert list(mine["tokens"]) == list(theirs["tokens"])
        assert list(mine["commits"][0]) == list(theirs["commits"][0])
    assert datetime.strptime(ours[0]["at"], "%Y-%m-%dT%H:%M:%S")
    assert ours[0]["minutes"] == 1 and type(ours[0]["minutes"]) is int


def test_points_was_when_a_triage_changes_them(project, fake_claude):
    p = project(item("alpha", points=2))
    p.play(p.scene(move("alpha", "ready", "low", 5), result="COMMIT: alpha: triaged"),
           p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    p.go()
    first = p.attempts()[0]
    script = next(json.loads(l) for l in (FIXTURES / "attempts" / "script.jsonl").read_text(encoding="utf-8").splitlines() if "points_was" in l)
    assert list(first) == list(script)
    assert (first["points"], first["points_was"]) == (5, 2)


def test_the_prompts_are_the_scripts(project, fake_claude):
    p = project(item("alpha", labels="core"))
    r = p.run()
    r.scratch = Path("S")
    r.variables = {"AGENT_SCRATCH": "S"}
    shown = p.overseer.tracker(p.tracker, "show", "alpha").stdout.strip()
    repos = str(p.code).replace(chr(92), '/')
    status = (f"Uncommitted changes as the session begins, as git status --short prints them - the owner's work in progress unless they are this item's:\n\n"
              f"{str(p.code).replace(chr(92), '/')}: clean\n\n")
    context = (f"\n\nThe item, as tracker.py show alpha prints it now:\n\n{shown}\n"
               "\nWhat the project says about the tags this item carries:\n\n## core - Core\n\nThe loop itself.\n"
               "\nOther tags with something to say: ui (UI) - tracker.py tag <tag> prints what one says, should the work turn out to reach that part.\n\n")
    close = ("This is an unattended run: nobody will answer a question during it, so anything that needs the owner goes into the tracker as AUTOTASK.md says. "
             "Do not commit: the loop commits your changes when you finish. End your reply with a line 'COMMIT: alpha: <what changed>', under 72 characters, to be the commit message - "
             "or, if nothing on this project can be worked at all, as AUTOTASK.md says under Stopping the loop, with a line 'STOP_LOOP: <what is wrong>' instead. "
             "If another item could be worked straight away with what you have read here - it is beside this one and reaches the same code - you may add a line 'NEXT: <its handle>' after the COMMIT line, as AUTOTASK.md says under Going on to a related item.")
    ask = "if something needs a decision, ask with tracker.py ask, which moves it to planning and flags it asked"
    picked = ("AUTOTASK.md and the project's brief are in your system prompt: follow them, and do not read either file again. "
              f"The loop has picked the item for you: alpha, at filed, in the tracker {p.tracker}. "
              f"The repositories you may change, which the loop commits, are {repos} - run git in those folders and no others. "
              "For files of your own, use the scratch folder S, also in AGENT_SCRATCH. Work that item and no other.")
    alpha = {"handle": "alpha", "stage": "filed"}
    assert r.prompt("triage", alpha, None) == (
        picked + context + status + f"Triage it: investigate, then move it to ready with its complexity, and its points as your plan puts them - or, {ask} - or, if it has to wait for another item first, add that to its after list and leave it where it is. "
        "Do not start building it: the loop decides who builds it, and may ask you to go on in this same conversation. " + close)
    assert r.prompt("build", alpha, None) == picked + context + status + f"Build it as its plan says, check it, and move it to testing - or, {ask}. " + close
    assert r.prompt("continue", alpha, None) == f"Your triage of alpha has been committed. Now build it as your plan says, check it, and move it to testing - or, {ask}. " + close
    assert r.prompt("discuss", alpha, None) == (
        picked + context + status + "It is flagged discuss: only your thoughts are wanted. Investigate, then give your analysis and a recommendation with tracker.py ask, which moves it to planning and flags it asked. "
        "Do not move it to ready, and build nothing. " + close)
    went_on = (f"Your work on beta has been committed. As you asked, go on in this same session with alpha, at filed, in the tracker {p.tracker}: work that item and no other now."
               + context + f"Build it as its plan says, check it, and move it to testing - or, {ask}. " + close)
    assert r.prompt("build", alpha, "beta") == went_on


def test_the_trackers_beside_it_are_named(project, fake_claude):
    p = project(item("alpha"))
    p.overseer.document(tracker_text(item("x")), project="other", folder=p.tracker.rpartition("/")[0])
    r = p.run()
    r.variables = {"AGENT_SCRATCH": "S"}
    assert (" The other projects' trackers beside this one: other. An item of one is named <project>:<handle>: tracker.py show reads it, "
            "after waits on it, and file --into <project> files an item there - as AUTOTASK.md says under Other projects.") in r.prompt("triage", {"handle": "alpha", "stage": "filed"}, None)


def test_the_system_file_and_the_logs(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"))
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    p.go()
    system = p.r.system_file.read_text(encoding="utf-8")
    assert system.startswith(tools.path("AUTOTASK.md").read_text(encoding="utf-8")[:200])
    assert "# What the project is, as its tracker describes it\n\nA project for tests.\n\n---\n\n# The brief\n" in system
    logs = sorted(p.r.log_dir.glob("loop-*-1-build.jsonl"))
    assert len(logs) == 1
    assert not p.r.state_file.exists()


def test_the_system_file_says_where_the_session_is(project, fake_claude, monkeypatch):
    p = project(item("alpha", "ready", complexity="low"))
    monkeypatch.setenv("FAKE_CLAUDE_ENV", "OVERSEER_LIBRARY")
    # An Overseer library named in settings written before the server handed out templates: let
    # be, and passed on no more - the session's tracker.py asks the server.
    monkeypatch.delenv("OVERSEER_LIBRARY", raising=False)
    bundle = config.paths().config / "bundles" / "proj"
    (bundle / "brief.md").write_text("# The brief\n\nRun `{bundle}/tools/check` in {code}, and {tool}/x, not {other}.\n", encoding="utf-8")
    tool = p.tmp / "Program Files" / "tool"
    tool.mkdir(parents=True)
    raw = json.loads((bundle / "project.json").read_text(encoding="utf-8"))
    config.save_json(bundle / "project.json", {**raw, "places": ["tool"]})
    settings_file = config.paths().settings
    raw = json.loads(settings_file.read_text(encoding="utf-8"))
    raw["loops"][0]["places"] = {"tool": str(tool)}
    raw["overseer"] = {"library": str(p.tmp / "library")}
    config.save_json(settings_file, raw)
    p.play(p.scene(move("alpha", "testing"), result="COMMIT: alpha: built"))
    p.go()
    system = p.r.system_file.read_text(encoding="utf-8")
    slashed = lambda path: str(path.resolve()).replace("\\", "/")
    assert f"{session.python_command()} {slashed(tools.path('tracker.py').parent)}/tracker.py show <handle>" in system
    assert f"  - `code`: `{slashed(p.code)}`\n" in system
    assert f"  - `tool`: `{slashed(tool)}`" in system
    assert f"Run `{slashed(bundle)}/tools/check` in {slashed(p.code)}, and {slashed(tool)}/x, not {{other}}." in system
    assert system.index("# Where you are") < system.index("# What the project is")
    assert "OVERSEER_LIBRARY" not in p.r.tracker_env
    assert p.fake.calls()[0]["env"].get("OVERSEER_LIBRARY") is None


def test_a_project_is_refused_on_a_system_it_does_not_run_on(project, monkeypatch):
    p = project(item("alpha"))
    bundle = config.paths().config / "bundles" / "proj"
    raw = json.loads((bundle / "project.json").read_text(encoding="utf-8"))
    others = [s for s in config.SYSTEMS if s != config.this_system()]
    config.save_json(bundle / "project.json", {**raw, "systems": others})
    with pytest.raises(loop.LoopError, match=f"^proj/project.json: systems: proj runs on {', '.join(others)}, not on {config.this_system()}$"):
        p.run()
    config.save_json(bundle / "project.json", {**raw, "systems": [config.this_system()]})
    p.run()


# The dry start

def test_a_dry_start_writes_the_prompts_and_runs_nothing(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"), item("beta"))
    assert p.run().dry() == 0
    dry = p.r.loop_dir / "dry"
    system = (dry / "system.md").read_text(encoding="utf-8")
    assert "# The brief\n" in system
    prompt = (dry / "prompt.md").read_text(encoding="utf-8")
    assert prompt.startswith("AUTOTASK.md and the project's brief are in your system prompt")
    assert "The loop has picked the item for you: alpha, at ready" in prompt
    assert "Build it as its plan says" in prompt
    assert "  build, at ready, complexity low: sonnet high" in p.text
    # Nothing a start leaves: no session, no record, no commit, no status, no scratch folder made.
    assert fake_claude.calls() == []
    assert p.attempts() == []
    assert not (p.r.loop_dir / "system.md").exists()
    assert not p.r.status_file.exists()
    assert not (p.r.loop_dir / "scratch").exists()
    assert p.subjects(p.code) == []


def test_a_dry_start_on_an_item_named(project, fake_claude):
    p = project(item("alpha", "ready", complexity="low"), item("beta", flags="discuss"))
    assert p.run().dry("beta") == 0
    prompt = (p.r.loop_dir / "dry" / "prompt.md").read_text(encoding="utf-8")
    assert "The loop has picked the item for you: beta, at filed" in prompt
    assert "It is flagged discuss" in prompt
    assert p.run().dry("nothing") == 1
    assert "tracker.py has no item nothing." in p.text
    assert fake_claude.calls() == []


def test_a_dry_start_while_the_loop_runs(project, fake_claude):
    p = project(item("alpha"))
    with loop.run_lock("proj", loop.lock_path("proj", "proj")):
        assert p.run().dry() == 0


def test_figures_as_powershell_writes_them():
    assert loop.figure(0.0) == "0" and loop.figure(1.5) == "1.5" and loop.figure(round(0.3686, 2)) == "0.37" and loop.figure(12.0) == "12"
