import time
from pathlib import Path

import pytest

from agentloop import session, usage
from agentloop.session import Route, SessionResult, SessionSpec
from agentloop.usage import Probe, Waited


class FakeClock:
    """Time a test moves: sleeping advances it, and nothing waits."""

    def __init__(self, start: float = 1_000_000.0):
        self.t = start
        self.sleeps = 0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.t += seconds


def _run(tmp_path: Path, scenario: str, fake_claude) -> SessionResult:
    fake_claude.scenario(scenario)
    work = tmp_path / "repo"
    work.mkdir(exist_ok=True)
    data = tmp_path / "data"
    spec = SessionSpec(
        claude=session.find_claude(), session_id="11111111-2222-3333-4444-555555555555", resume=False,
        prompt="Work the item.", route=Route("sonnet", "high"), workdir=work,
        system_file=session.write_system_file(data / "system.md", "guide", "about"),
        settings=session.write_session_settings(data / "loop-settings.json"),
        log=data / "loop-1-build.jsonl",
        variables=session.session_variables(author="a", scratch=data / "s", tracker="x/y.os", item="p:thing"),
    )
    return session.run_session(spec)


# Whether a session was stopped by a limit

def test_limited_on_the_recorded_limit(tmp_path, fake_claude):
    result = _run(tmp_path, "usage-limit", fake_claude)
    assert result.rate["status"] == "rejected"
    assert usage.limited(result)


def test_not_limited_when_finished(tmp_path, fake_claude):
    assert not usage.limited(_run(tmp_path, "build-edit", fake_claude))


def test_limited_by_the_words_alone():
    # No report, but the error speaks of a limit; and an error that does not is another failure.
    assert usage.limited(SessionResult(final={"is_error": True, "result": "Claude usage Limit reached"}))
    assert not usage.limited(SessionResult(final={"is_error": True, "result": "boom"}))
    # A result that went through is not stopped, whatever the rate says or the text mentions.
    assert not usage.limited(SessionResult(final={"is_error": False, "result": "limit"}, rate={"status": "rejected"}))
    # No result at all is stopped; the rate decides.
    assert usage.limited(SessionResult(rate={"status": "rejected"}))
    assert not usage.limited(SessionResult(rate={"status": "allowed_warning"}))
    assert not usage.limited(SessionResult())


# When to resume

def test_resume_at():
    assert usage.resume_at({"resetsAt": 5000}, 1000) == 5060
    assert usage.resume_at({"resetsAt": "5000"}, 1000) == 5060
    assert usage.resume_at(None, 1000) == 4600
    assert usage.resume_at({"status": "rejected"}, 1000) == 4600
    # Already past, or about to be: still a minute from now.
    assert usage.resume_at({"resetsAt": 100}, 1000) == 1060
    assert usage.resume_at({"resetsAt": 1010}, 1000) == 1070


def test_resume_prompt_is_the_scripts():
    assert usage.resume_prompt("Do it.") == (
        "Your session was stopped by a usage limit and has been resumed; what you changed before then is still "
        "in the working tree, uncommitted. Carry on from where you left off with what you were asked: Do it.")


# Asking the account

def _probe(tmp_path: Path, fake_claude, scenario: str) -> Probe:
    fake_claude.scenario(scenario)
    logs = tmp_path / "logs"
    logs.mkdir()
    settings = session.write_session_settings(tmp_path / "loop-settings.json")
    variables = session.session_variables(author="a", scratch=tmp_path / "s", tracker="x/y.os", item="p:thing")
    return usage.probe(session.find_claude(), variables, settings, logs)


def test_probe_refused(tmp_path, fake_claude):
    asked = _probe(tmp_path, fake_claude, "probe-refused")
    assert not asked.back
    assert asked.rate["rateLimitType"] == "five_hour"
    (call,) = fake_claude.calls()
    assert call["argv"][:2] == ["-p", "Reply with the word ok."]
    assert call["model"] == "haiku"
    assert "--no-session-persistence" in call["argv"]
    assert call["argv"][call["argv"].index("--max-turns") + 1] == "1"
    assert call["argv"][call["argv"].index("--output-format") + 1] == "stream-json"
    assert Path(call["cwd"]) == (tmp_path / "logs").resolve()
    assert call["env"]["OVERSEER_TRACKER"] == "x/y.os"


def test_probe_back(tmp_path, fake_claude):
    asked = _probe(tmp_path, fake_claude, "probe-ok")
    assert asked.back
    assert asked.rate["status"] == "allowed"


def test_probe_without_a_result_is_not_back(tmp_path, fake_claude):
    asked = _probe(tmp_path, fake_claude, "no-result")
    assert not asked.back


# Waiting

def _wait(rate, probes, *, killed=lambda: False, clock=None, ask_every=30):
    """Runs wait_out with a probe answering from the list, one per asking."""
    clock = clock or FakeClock()
    answers = list(probes)
    asked = []

    def probe():
        asked.append(clock.now())
        return answers.pop(0)

    told = []
    waited = usage.wait_out(rate, ask_every=ask_every, probe=probe, killed=killed, clock=clock, on_wait=told.append)
    return waited, asked, told, clock


def test_wait_until_the_reset():
    clock = FakeClock()
    rate = {"rateLimitType": "five_hour", "resetsAt": clock.t + 3600}
    refused = Probe(False, {"status": "rejected"})
    waited, asked, told, clock = _wait(rate, [refused, refused], clock=clock)
    assert waited.outcome == "reset"
    assert waited.rate == {"status": "rejected"}
    # A minute past the reset, asked at 30 and 60 minutes and not again after.
    assert clock.t >= 1_000_000.0 + 3660
    assert [round(t - 1_000_000.0) for t in asked] == [1800, 3600]
    # Told as it began and after each asking, with the next asking moving on.
    assert len(told) == 3
    assert told[0].limit == "five_hour"
    assert (told[0].until - told[0].next_ask).total_seconds() == 3660 - 1800
    assert (told[1].next_ask - told[0].next_ask).total_seconds() == 1800
    assert "Waiting until" in told[0].line() and "asking every 30 minutes" in told[0].line()


def test_wait_lifted_at_the_second_asking():
    clock = FakeClock()
    rate = {"rateLimitType": "seven_day", "resetsAt": clock.t + 172800}
    first = Probe(False, {"status": "rejected", "n": 1})
    second = Probe(True, {"status": "allowed", "n": 2})
    waited, asked, told, clock = _wait(rate, [first, second], clock=clock)
    assert waited == Waited("lifted", {"status": "allowed", "n": 2})
    assert len(asked) == 2
    assert round(clock.t - 1_000_000.0) == 3600
    assert len(told) == 2


def test_wait_killed_midway():
    clock = FakeClock()
    rate = {"resetsAt": clock.t + 172800}
    # Killed once an hour has gone by: the asking at 30 minutes has been made, not the one at 60.
    waited, asked, told, clock = _wait(rate, [Probe(False)], killed=lambda: clock.t - 1_000_000.0 >= 3000, clock=clock)
    assert waited.outcome == "killed"
    assert len(asked) == 1


def test_wait_killed_at_once():
    clock = FakeClock()
    waited, asked, told, clock = _wait({"resetsAt": clock.t + 3600}, [], killed=lambda: True, clock=clock)
    assert waited.outcome == "killed"
    assert asked == [] and clock.sleeps == 0


def test_wait_without_a_reset_is_an_hour():
    clock = FakeClock()
    waited, asked, told, clock = _wait(None, [Probe(False)], clock=clock, ask_every=45)
    assert waited.outcome == "reset"
    assert round(clock.t - 1_000_000.0) in range(3600, 3606)
    assert len(asked) == 1
    assert told[0].limit is None


def test_wait_with_a_stop_asked_for_waits_on():
    # A stop is the caller's flag, which wait_out is never given: nothing but a kill ends it early.
    clock = FakeClock()
    waited, *_ = _wait({"resetsAt": clock.t + 600}, [], clock=clock, ask_every=60)
    assert waited.outcome == "reset"


def test_wait_with_the_real_clock_default():
    rate = {"resetsAt": time.time() - 1000}
    calls = []
    clock = FakeClock(time.time())
    usage.wait_out(rate, ask_every=1, probe=lambda: Probe(True), killed=lambda: calls.append(1) or True, clock=clock)
    assert calls


# What the account has used

def test_usage_line_on_the_recorded_report(tmp_path, fake_claude):
    rate = _probe(tmp_path, fake_claude, "probe-ok").rate
    line = usage.usage_line(rate, ".claude")
    assert line.startswith("Usage on .claude: 5-hour 35% used, resets ")
    assert "; weekly 29% used, resets " in line
    # Local time, three-letter day: "resets Mon 14:05".
    assert line.split("resets ")[1].split(";")[0][3] == " "


def test_an_api_key_is_limited_only_by_a_refusing_report():
    # An API rate limit or a spent balance is a failed session, not hours of waiting.
    said = SessionResult(final={"is_error": True, "result": "API Error: 429 rate limit exceeded"})
    assert usage.limited(said) and not usage.limited(said, per_use=True)
    assert not usage.limited(SessionResult(final={"is_error": True, "result": "Credit balance is too low"}), per_use=True)
    assert not usage.limited(SessionResult(), per_use=True)
    assert usage.limited(SessionResult(rate={"status": "rejected"}), per_use=True)
    assert usage.per_use_line(".claude-x") == "Usage on .claude-x: billed per use (API key), no 5-hour or weekly limit"


def test_usage_line_nothing_reported():
    assert usage.usage_line(None, ".claude") is None
    assert usage.usage_line({"status": "allowed"}, ".claude") is None


def test_usage_line_one_window_without_a_reset():
    rate = {"unifiedWindows": {"seven_day": {"utilization": 0.5}}}
    assert usage.usage_line(rate, "acct") == "Usage on acct: weekly 50% used"


def test_account_name():
    assert usage.account_name(None) == ".claude"
    assert usage.account_name("C:/Users/someone/.claude-account1") == ".claude-account1"




# A worker's own limits (item h87x)

def test_over_limit():
    now = 1000.0
    rate = {"unifiedWindows": {"five_hour": {"utilization": 0.82, "resetsAt": now + 600},
                               "seven_day": {"utilization": 0.5, "resetsAt": now + 9000}}}
    over = usage.over_limit(rate, {"five_hour": 0.8, "seven_day": 0.9}, {}, now)
    assert (over.window, over.used, over.limit, over.resets) == ("five_hour", 0.82, 0.8, now + 600)
    assert over.line("w").startswith("Paused: worker w is at 82% of its 5-hour allowance (its limit 80%), until it resets ")
    assert "agentloop approve-limit w five_hour" in over.line("w")
    # At the limit is past it; under it, or with no limit for the window, is not.
    assert usage.over_limit(rate, {"five_hour": 0.82}, {}, now).window == "five_hour"
    assert usage.over_limit(rate, {"five_hour": 0.9, "seven_day": 0.9}, {}, now) is None
    assert usage.over_limit(rate, {"five_hour": None, "seven_day": 0.5}, {}, now).window == "seven_day"
    assert usage.over_limit(rate, None, {}, now) is None and usage.over_limit(None, {"five_hour": 0.1}, {}, now) is None
    # A window claude does not report today is checked once a report names it.
    assert usage.over_limit(rate, {"seven_day_sonnet": 0.1}, {}, now) is None
    rate["unifiedWindows"]["seven_day_sonnet"] = {"utilization": 0.2, "resetsAt": now + 9000}
    assert usage.over_limit(rate, {"seven_day_sonnet": 0.1}, {}, now).line("w").startswith(
        "Paused: worker w is at 20% of its seven day sonnet allowance")
    # Approved until a time: past it again after that.
    assert usage.over_limit(rate, {"five_hour": 0.8}, {"five_hour": now + 600}, now) is None
    assert usage.over_limit(rate, {"five_hour": 0.8}, {"five_hour": now - 1}, now).window == "five_hour"
    # A window that has reset since the report is not counted: the report is older than it.
    assert usage.over_limit(rate, {"five_hour": 0.8}, {}, now + 601) is None
    assert over.until(now) == now + 600
    assert usage.Over("x", 0.5, 0.1, None).until(now) == now + usage.APPROVAL_FALLBACK


def test_over_limit_on_extra_usage():
    now = 1000.0
    rate = {"resetsAt": now + 600, "overageResetsAt": now + 90000, "isUsingOverage": True, "unifiedWindows": {}}
    over = usage.over_limit(rate, {"overage": 0}, {}, now)
    assert (over.window, over.used, over.resets) == ("overage", None, now + 600)
    assert over.line("w").startswith("Paused: worker w is using extra usage, which it allows none of, until it resets ")
    assert usage.over_limit({**rate, "isUsingOverage": False}, {"overage": 0}, {}, now) is None
    assert usage.over_limit(rate, {"overage": 0}, {}, now + 601) is None
