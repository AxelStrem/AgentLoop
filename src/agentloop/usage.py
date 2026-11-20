"""Usage limits: waiting them out, asking whether they lifted early, the usage line, the weekly share.

The script's side is `Test-UsageBack`, `Write-Usage`, `Test-WeekUsed` and the waiting inside
`Invoke-Phase` (the limit test, `$until`, `$resumeAt`, the asking every `AskEveryMinutes`, the prompt a
resumed session is given). These are library functions for item loop, which runs the sessions and
decides whether to resume or begin afresh (`Find-Transcript`); this reads no settings and no tracker.
A kill comes from item runner as a callable, which this takes as it is.

Differences from the script, each deliberate:
- Times are Unix seconds (floats) through a `Clock` the caller may replace, so a test moves time
  instead of waiting; `Waiting` carries local datetimes for whoever shows them.
- `wait_out` returns what the wait ended in and the last rate the asking reported, where the script
  sets `$LastRate` and `$Killed` itself.
- `week_used` returns the message to print (or None), `usage_line` the line (or None), where the
  script writes them to the host. `usage_line` says nothing when no window is reported, where the
  script would print an empty list.
- A probe that has not answered in `PROBE_TIMEOUT` seconds counts as not back, where the script waits
  for claude as long as it takes.
- A worker on an API key (item claude-container) has no five-hour or weekly window: it is billed per
  use. Its session counts as stopped by a limit only when a rate report refuses it, never because
  the error's text says "limit" - an API rate limit or a spent balance is a failed session, not an
  hour of waiting - and its usage line says it is billed per use.
- The probe's command line is a list, never a shell string. Nothing here was tried on macOS or
  Linux: this machine is Windows.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from agentloop import session
from agentloop.session import SessionResult

# `rate_limit_info.status` values that mean the account can be used.
_ALLOWED = ("allowed", "allowed_warning")

# How often a wait looks at the kill, in seconds (`Start-Sleep -Seconds 5`).
POLL_SECONDS = 5

# How long a probe may take before it counts as not answered.
PROBE_TIMEOUT = 180

PROBE_PROMPT = "Reply with the word ok."

# What a session stopped by a limit is told when it is resumed, followed by the prompt it was asked.
RESUME_PROMPT = (
    "Your session was stopped by a usage limit and has been resumed; what you changed before then is "
    "still in the working tree, uncommitted. Carry on from where you left off with what you were asked: "
)

LIFTED_LINE = "The limit has lifted before its time. Resuming."


def resume_prompt(asked: str) -> str:
    return RESUME_PROMPT + asked


# Time

class Clock:
    """The real clock; a test passes one with the same two methods that it moves itself."""

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def local(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp).astimezone()


# Whether a session was stopped by a limit

def blocked(rate: dict | None) -> bool:
    return bool(rate) and rate.get("status") not in _ALLOWED


def limited(result: SessionResult, per_use: bool = False) -> bool:
    """Whether the session was stopped by the five-hour or the weekly limit: it ended without a
    result or with an error, and either the last rate report refused it or the error speaks of a
    limit (`Invoke-Phase`). `per_use` is an API key's account, which has neither limit: only a rate
    report refusing it counts then."""
    final = result.final
    stopped = not final or bool(final.get("is_error"))
    if per_use:
        return stopped and blocked(result.rate)
    text = str((final or {}).get("result") or "")
    return stopped and (blocked(result.rate) or re.search("limit", text, re.I) is not None)


def resume_at(rate: dict | None, now: float) -> float:
    """When to resume: a minute after the limit resets, or an hour from now when the report names no
    reset; never less than a minute from now (`$until`, `$resumeAt`)."""
    reset = _number((rate or {}).get("resetsAt"))
    until = reset + 60 if reset is not None else now + 3600
    return now + max(60.0, until - now)


def _number(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


# Asking the account

@dataclass
class Probe:
    back: bool
    rate: dict | None = None


def probe(claude: str, variables: dict[str, str | None], settings: Path, logdir: Path) -> Probe:
    """Whether the account can be used again, asked of the account itself (`Test-UsageBack`): one
    line from the smallest model, outside any session, in the environment sessions run in. A limit
    still in force refuses it, which costs nothing; once usage is back it costs one short reply. Run
    in the logs folder, which holds no CLAUDE.md, so the question carries nothing it needs not."""
    argv = [
        claude, "-p", PROBE_PROMPT, "--model", "haiku", "--max-turns", "1", "--no-session-persistence",
        "--settings", str(settings), "--output-format", "stream-json", "--verbose",
    ]
    try:
        done = subprocess.run(
            argv, cwd=logdir, env=session.build_env(variables), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=PROBE_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return Probe(False)
    rate = final = None
    for line in done.stdout.decode("utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if e.get("type") == "rate_limit_event":
            rate = e.get("rate_limit_info")
        elif e.get("type") == "result":
            final = e
    return Probe(bool(final and not final.get("is_error") and not blocked(rate)), rate)


# Waiting

@dataclass
class Waiting:
    """What a loop waits for: what the page's pane and row show."""

    limit: str | None
    until: datetime
    next_ask: datetime
    ask_every: float

    def line(self) -> str:
        return (f"Rate limited ({self.limit}). Waiting until {self.until:%Y-%m-%d %H:%M}, asking every "
                f"{self.ask_every:g} minutes whether it has lifted sooner...")


@dataclass
class Waited:
    """How a wait ended - `reset` (the time came), `lifted` (an asking found the account usable) or
    `killed` - and the last rate report an asking gave, if any, for the loop to keep as its last."""

    outcome: str
    rate: dict | None = None


def wait_out(
    rate: dict | None,
    *,
    ask_every: float,
    probe: Callable[[], Probe],
    killed: Callable[[], bool],
    clock: Clock | None = None,
    on_wait: Callable[[Waiting], None] | None = None,
) -> Waited:
    """Waits out a usage limit (`Invoke-Phase`). A wait can last hours - days, for the weekly limit.
    A stop asked for meanwhile is not looked at: the item at work is finished once the limit lifts,
    and the loop stops after it. Only a kill ends the wait early, or an asking, every `ask_every`
    minutes, that finds the account usable again. `on_wait` is called as the wait begins and after
    each asking."""
    clock = clock or Clock()
    started = clock.now()
    resume = resume_at(rate, started)
    ask_at = started + ask_every * 60
    last: dict | None = None

    def tell() -> None:
        if on_wait:
            on_wait(Waiting((rate or {}).get("rateLimitType"), local(resume), local(ask_at), ask_every))

    tell()
    while clock.now() < resume:
        if killed():
            return Waited("killed", last)
        if clock.now() >= ask_at:
            asking = probe()
            if asking.rate:
                last = asking.rate
            if asking.back:
                return Waited("lifted", last)
            ask_at = clock.now() + ask_every * 60
            tell()
        clock.sleep(POLL_SECONDS)
    return Waited("reset", last)


# What the account has used

def account_name(config_dir: str | None) -> str:
    """The account as the usage line names it: its config folder's leaf, `.claude` for the default."""
    return Path(config_dir).name if config_dir else ".claude"


def usage_line(rate: dict | None, account: str) -> str | None:
    """How much of the account's allowance is used, five-hour and weekly, and when each resets, as the
    last report said (`Write-Usage`); None until a session has reported."""
    windows = (rate or {}).get("unifiedWindows")
    if not windows:
        return None
    parts = []
    for key, name in (("five_hour", "5-hour"), ("seven_day", "weekly")):
        w = windows.get(key)
        if not w:
            continue
        resets = ""
        at = _number(w.get("resetsAt"))
        if at:
            resets = f", resets {local(at):%a %H:%M}"
        parts.append(f"{name} {round(float(w.get('utilization') or 0) * 100)}% used{resets}")
    return f"Usage on {account}: {'; '.join(parts)}" if parts else None


def per_use_line(account: str) -> str:
    """The usage line of a worker on an API key, which has no allowance to show."""
    return f"Usage on {account}: billed per use (API key), no 5-hour or weekly limit"


def week_used(rate: dict | None, stop_at: float) -> str | None:
    """The message for stopping between items once `stop_at` of the week is used, or None
    (`Test-WeekUsed`). Never at 1 or more: then the whole week is the loop's, and the next session
    meets the weekly limit and waits for it like any other."""
    if stop_at >= 1:
        return None
    weekly = (((rate or {}).get("unifiedWindows") or {}).get("seven_day") or {}).get("utilization")
    if weekly is not None and float(weekly) >= stop_at:
        return f"The weekly allowance is {round(float(weekly) * 100)}% used (limit {round(stop_at * 100)}%). Stopping."
    return None
