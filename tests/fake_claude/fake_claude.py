"""A stand-in for the `claude` command, for tests: it plays a recorded stream-json session.

Started where the real one would be (the `fake_claude` fixture puts a launcher named `claude` first
on PATH), it takes the arguments `run-agent-loop.ps1` gives claude (`Invoke-Session`,
`Test-UsageBack`), reads the prompt from stdin, or from the argument after `-p`, and writes the
events of a session file to stdout, one JSON line each. Never a real session, never the network.

Environment:
  FAKE_CLAUDE_SCENARIO  the session to play: a name in sessions/ (without .jsonl), or the path of a
                        .jsonl file. Several, comma separated, play one per call, the last one
                        again after that - a usage limit, then a session that goes through.
                        The call's number is the length of FAKE_CLAUDE_LOG, so that needs it set.
  FAKE_CLAUDE_LOG       a file to append one JSON line to per call: its argv, cwd, prompt, session
                        id and the environment variables listed below. Unset, nothing is recorded.
  FAKE_CLAUDE_ENV       more variable names to record, comma separated. Only the names listed here
                        and those below are recorded, so nothing else from the real environment
                        (a key, say) ever reaches the record.

Recorded always: CLAUDE_CONFIG_DIR, OVERSEER_TRACKER, OVERSEER_ITEM and every AGENT_*.

`claude auth status --json` plays no session but answers as the real one does: logged in when the
folder in CLAUDE_CONFIG_DIR holds a file named `fake-login` (its text, if any, the plan), logged out
otherwise, exit 1 then; and it writes `.claude.json` into the folder, as the real one does. With no
CLAUDE_CONFIG_DIR it writes nothing - the default account's folder is in the home folder - and is
logged in when FAKE_CLAUDE_DEFAULT_LOGIN is set. FAKE_CLAUDE_AUTH set to `hang` makes it wait a
minute, and to `garbage` print what is not JSON.

A session file holds one event per line. A line with a "fake" key is not played but done:
  {"fake": "write", "path": "notes.txt", "text": "..."}  writes a file, relative to the cwd
  {"fake": "stderr", "text": "..."}                       writes to stderr
  {"fake": "copy", "from": "a", "to": "b"}                copies a file, as it is mid-session
  {"fake": "exit", "code": 1}                             ends the process there
  {"fake": "sleep", "seconds": 60}                        waits there, so that a kill lands mid-session
  {"fake": "tracker", "args": ["move", "x", "ready"]}     runs the app's tracker.py with those
                                                          arguments, on OVERSEER_TRACKER, as an
                                                          agent's own command would; if it
                                                          fails, the session ends with its exit
                                                          code, unless the step has
                                                          "may_fail": true
  {"fake": "transcript"}                                  saves a stand-in transcript where claude
                                                          saves a session's, under
                                                          CLAUDE_CONFIG_DIR/projects; never in the
                                                          home folder, so only with that set
Any other line is written as it is, with "session_id" set to the id the call was given, and a
string "@now+N" anywhere in it becomes the Unix time N seconds from now (for "resetsAt").
"@item", in a tracker step's arguments or a line's strings, is the handle of the item the session
was given (OVERSEER_ITEM less its project), for a scene that any of several loops may play.
"""

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

SESSIONS = Path(__file__).resolve().parent / "sessions"
TRACKER = Path(__file__).resolve().parents[2] / "src" / "agentloop" / "tools" / "tracker.py"

# Options of claude that take a value, so that the argument after one is not taken for the prompt.
_WITH_VALUE = {
    "--model", "--effort", "--settings", "--session-id", "--resume", "--append-system-prompt-file",
    "--output-format", "--max-turns",
}

_ALWAYS_RECORDED = ("CLAUDE_CONFIG_DIR", "OVERSEER_TRACKER", "OVERSEER_ITEM")


def parse_args(argv: list[str]) -> dict:
    """What a call asked for, from claude's command line as the script builds it."""
    options: dict[str, str] = {}
    flags: list[str] = []
    positional: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in _WITH_VALUE and i + 1 < len(argv):
            options[arg] = argv[i + 1]
            i += 2
            continue
        if arg.startswith("-"):
            flags.append(arg)
        else:
            positional.append(arg)
        i += 1
    return {"options": options, "flags": flags, "positional": positional}


def read_prompt(parsed: dict) -> tuple[str, str]:
    """The prompt and where it came from. As an argument (`-p "..."`) it is not on stdin, which
    is then left unread: a caller that never closed it would block this."""
    if parsed["positional"]:
        return " ".join(parsed["positional"]), "argument"
    return sys.stdin.buffer.read().decode("utf-8"), "stdin"


def recorded_env() -> dict[str, str | None]:
    names = set(_ALWAYS_RECORDED)
    names.update(n for n in os.environ if n.startswith("AGENT_"))
    names.update(n.strip() for n in os.environ.get("FAKE_CLAUDE_ENV", "").split(",") if n.strip())
    return {name: os.environ.get(name) for name in sorted(names)}


def record(log: Path, entry: dict) -> int:
    """Appends the call to the log and returns how many calls were logged before it."""
    before = 0
    if log.exists():
        before = sum(1 for line in log.read_text(encoding="utf-8").splitlines() if line.strip())
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return before


def pick_scenario(spec: str, call_index: int) -> Path:
    names = [n.strip() for n in spec.split(",") if n.strip()]
    name = names[min(call_index, len(names) - 1)]
    path = Path(name) if name.endswith(".jsonl") else SESSIONS / f"{name}.jsonl"
    if not path.is_file():
        raise SystemExit(f"fake claude: no session {path}")
    return path


def _substitute(value, now: int, session_id: str):
    if isinstance(value, dict):
        return {k: (session_id if k == "session_id" else _substitute(v, now, session_id)) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, now, session_id) for v in value]
    if isinstance(value, str) and value.startswith("@now+"):
        return now + int(value[len("@now+"):])
    if isinstance(value, str):
        return value.replace("@item", _item())
    return value


def _item() -> str:
    return os.environ.get("OVERSEER_ITEM", "").rpartition(":")[2]


def play(path: Path, session_id: str) -> int:
    now = int(time.time())
    out = sys.stdout.buffer
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        event = json.loads(raw)
        action = event.get("fake")
        if action == "write":
            target = Path.cwd() / event["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(event["text"], encoding="utf-8", newline="")
        elif action == "copy":
            target = Path.cwd() / event["to"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((Path.cwd() / event["from"]).read_bytes())
        elif action == "stderr":
            sys.stderr.write(event["text"])
            sys.stderr.flush()
        elif action == "tracker":
            args = [arg.replace("@item", _item()) for arg in event["args"]]
            done = subprocess.run([sys.executable, str(TRACKER), *args], capture_output=True)
            if done.returncode != 0:
                sys.stderr.write(done.stderr.decode("utf-8", errors="replace"))
                # A refused write ends the session, so that a test expecting the move sees why it
                # did not happen instead of a stage that silently stayed where it was.
                if not event.get("may_fail"):
                    sys.stderr.write(f"fake claude: tracker {' '.join(args)} failed ({done.returncode})\n")
                    out.flush()
                    return done.returncode
        elif action == "transcript":
            folder = os.environ.get("CLAUDE_CONFIG_DIR")
            if not folder:
                raise SystemExit("fake claude: a transcript needs CLAUDE_CONFIG_DIR, so as never to write in the home folder")
            # claude names the folder after the working directory, every other character a -.
            target = Path(folder) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd())) / f"{session_id}.jsonl"
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "summary", "session_id": session_id}) + "\n")
        elif action == "sleep":
            out.flush()
            time.sleep(float(event["seconds"]))
        elif action == "exit":
            out.flush()
            return int(event.get("code", 0))
        elif action is not None:
            raise SystemExit(f"fake claude: unknown step {action!r} in {path}")
        else:
            out.write((json.dumps(_substitute(event, now, session_id)) + "\n").encode("utf-8"))
            out.flush()
    return 0


def auth_status() -> int:
    how = os.environ.get("FAKE_CLAUDE_AUTH")
    if how == "hang":
        time.sleep(60)
    if how == "garbage":
        print("not json")
        return 0
    folder = os.environ.get("CLAUDE_CONFIG_DIR")
    if folder:
        Path(folder, ".claude.json").write_text("{}", encoding="utf-8")
        marker = Path(folder, "fake-login")
        logged = marker.is_file()
        plan = marker.read_text(encoding="utf-8").strip() if logged else ""
    else:
        logged = bool(os.environ.get("FAKE_CLAUDE_DEFAULT_LOGIN"))
        plan = "pro"
    said = {"loggedIn": logged, "authMethod": "claude.ai" if logged else "none", "apiProvider": "firstParty"}
    if logged:
        said.update({"email": "someone@example.com", "orgName": "Someone's Organization", "subscriptionType": plan or "max"})
    print(json.dumps(said, indent=2))
    return 0 if logged else 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:2] == ["auth", "status"]:
        log = os.environ.get("FAKE_CLAUDE_LOG")
        if log:
            record(Path(log), {"argv": argv, "env": recorded_env()})
        return auth_status()
    spec = os.environ.get("FAKE_CLAUDE_SCENARIO")
    if not spec:
        print("fake claude: FAKE_CLAUDE_SCENARIO is not set", file=sys.stderr)
        return 2
    parsed = parse_args(argv)
    options = parsed["options"]
    prompt, source = read_prompt(parsed)
    resumed = "--resume" in options
    session_id = options.get("--resume") or options.get("--session-id") or str(uuid.uuid4())

    call_index = 0
    log = os.environ.get("FAKE_CLAUDE_LOG")
    if log:
        call_index = record(Path(log), {
            "argv": argv,
            "cwd": str(Path.cwd()),
            "prompt": prompt,
            "prompt_source": source,
            "session_id": session_id,
            "resume": resumed,
            "model": options.get("--model"),
            "effort": options.get("--effort"),
            "env": recorded_env(),
        })
    return play(pick_scenario(spec, call_index), session_id)


if __name__ == "__main__":
    sys.exit(main())
