"""One claude session: its command line, its environment, its output stream and its transcript.

The script's side is `Invoke-Session` in run-agent-loop.ps1, with `Describe-Tool`,
`Find-Transcript`, `Remove-Transcript`, `Clear-Session`, `Get-ResumeCommand`, the `$SessionEnv`
it runs with, the system file it writes once a run, and the COMMIT, NEXT and STOP_LOOP lines
`Complete-Session` reads from the result. Waiting out a usage limit is item usage's, ending a
session item runner's, and the prompts and what a phase cost over several runs item loop's: this
runs one session to its end and says what it saw.

Differences from the script, each deliberate:
- A worker whose `claude` is null runs with no CLAUDE_CONFIG_DIR at all, even one the app was
  started with: docs/settings.md says null is the default account. The script leaves the window's.
- running.json also names claude's own process (`child`), so a kill need not look for it.
- The session's log is written with \\n line ends on every OS; the script's StreamWriter writes
  \\r\\n on Windows. Either is JSON lines.
- The resume command is written for the OS it is shown on: PowerShell's on Windows, as the script
  writes it, and sh's on macOS and Linux (untried here: this machine is Windows).
- system.md says where the session is - the OS, its shell, the Python, the tools, the project's tools and
  each repository's and place's folder - and fills those in where AUTOTASK.md and the brief write
  `{name}`; AUTOTASK.md's Windows part goes in only on Windows (item neutral). The script's
  AUTOTASK.md and briefs name this PC's paths outright.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agentloop import brew, config, tools


class SessionError(Exception):
    """A session that cannot be started: no claude, say."""


# Finding claude

def find_claude(setting: str | None = None) -> str:
    """The claude to run: the settings' `claude` when it names one, else the one on PATH.
    shutil.which honours PATHEXT, so it finds claude.cmd on Windows, which subprocess given the
    bare name would not."""
    if setting:
        path = config.expand(setting)
        if not path.is_file():
            raise SessionError(f"claude not found at {setting}, which the settings name as claude")
        return str(path)
    found = shutil.which("claude")
    if not found:
        raise SessionError("claude not found: put it on PATH, or name it as claude in the settings")
    return found


# What a session is given

# loop-settings.json, as AgenticLoop has it, with the guard hook pointed at this app's guard.py and
# run by the Python running the app. The deny list is the script's, word for word.
_DENY = [
    "Bash(git push*)",
    "Bash(git commit*)",
    "PowerShell(git push*)",
    "PowerShell(git commit*)",
    "Read(**/.env)",
]


def session_settings() -> dict:
    return {
        "permissions": {"deny": list(_DENY)},
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash|PowerShell|Read|Grep|Glob",
                    "hooks": [
                        {
                            "type": "command",
                            "command": _slashes(sys.executable),
                            "args": [_slashes(tools.path("guard.py"))],
                            "timeout": 10,
                        }
                    ],
                }
            ]
        },
    }


def write_session_settings(dest: Path) -> Path:
    """The file a session's --settings names, written once a run, as the script names its own."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes((json.dumps(session_settings(), indent=2) + "\n").encode("utf-8"))
    return dest


# A name AUTOTASK.md or a brief writes for a folder of this machine's: `{docs}`, `{tools}`.
PLACEHOLDER = re.compile(r"\{([a-z0-9_-]+)\}")


def fill(text: str, values: dict[str, str]) -> str:
    """The text with each `{name}` it holds replaced by this machine's value for it. A `{word}`
    that names nothing here is left as it is, so code in a brief comes through whole."""
    return PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def read_text(path: Path) -> str:
    """A text the system prompt is made of, its lines ended with \\n whatever the checkout wrote:
    git's autocrlf on Windows writes AUTOTASK.md or a brief with \\r\\n. The script's ReadAllText
    keeps what is on disk; here the prompt is the same on every OS and checkout."""
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def autotask() -> str:
    """AUTOTASK.md, and its Windows part after it on Windows only: the Git Bash and PowerShell
    tools a session on macOS or Linux does not have."""
    text = read_text(tools.path("AUTOTASK.md"))
    if config.this_system() == "windows":
        text = text.rstrip("\n") + "\n\n" + read_text(tools.path("AUTOTASK-windows.md"))
    return text


def python_command() -> str:
    """The Python the app runs on, as a command: quoted when its path holds a space, as it would
    otherwise be cut there."""
    python = _slashes(sys.executable)
    return f'"{python}"' if " " in python else python


def placeholders(files: Path | None, folders: dict[str, Path], places: dict[str, Path]) -> dict[str, str]:
    """What each `{name}` stands for on this machine, with forward slashes, which every program and
    shell reads. `files` is the folder the project's tools were fetched into - `{bundle}`, as a
    project's own folder was named once - or None when it lists none for this system."""
    values = {name: _slashes(folder.resolve()) for name, folder in {**folders, **places}.items()}
    values.update(tools=_slashes(tools.path("tracker.py").parent), python=python_command())
    if files is not None:
        values["bundle"] = _slashes(files.resolve())
    return values


def where_you_are(files: Path | None, folders: dict[str, Path], places: dict[str, Path],
                  about: dict[str, str] | None = None, project_env: Path | None = None,
                  brewfile: Path | None = None) -> str:
    """The section of system.md that tells a session the machine it is on, the same for every
    session of a run. `about` is what the project says each repository and place is, beside its
    folder where it says it. `files` is the folder the project had its tools fetched into from its
    tracker (item tools-fetch), when it has any. `project_env` is the folder the project's
    setup script installed its tools in (item project-setup), when it ran. `brewfile` is the
    project's Brewfile, on a machine that has Homebrew on for its sessions (item brew-tools)."""
    values = placeholders(files, folders, places)
    about = about or {}
    here = config.this_system()
    system = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}[here]
    if here == "windows":
        shell = "the Bash tool runs Git Bash, and the PowerShell tool Windows PowerShell"
    else:
        shell = f"the Bash tool runs {Path(os.environ.get('SHELL') or 'sh').name}"

    def folder(name: str) -> str:
        return f"  - `{name}`: `{values[name]}`" + (f" - {about[name]}" if about.get(name) else "")

    lines = [
        "# Where you are",
        "",
        "This machine, as AUTOTASK.md and the brief name its folders: where they write `{name}`, it",
        "is this machine's folder of that name, and it is filled in already.",
        "",
        f"- **System:** {system}; {shell}.",
        f"- **Python:** `{values['python']}` - run the tools with it.",
        f"- **The app's tools** (`tools`): `{values['tools']}`, where `tracker.py` is.",
        *([f"- **The project's tools** (`bundle`): `{values['bundle']}`, its tools in `tools/`, fetched from its tracker."]
          if files is not None else []),
        *([f"- **The project's own tools,** installed by its setup script: `{_slashes(project_env)}`. What it put in "
            f"front of PATH, and the variables it set, are in this session's environment already; `{_slashes(project_env)}/"
            "session-env.txt` lists them."] if project_env is not None else []),
        *([f"- **Installing a tool:** only through Homebrew and the project's Brewfile, `{_slashes(brewfile)}`, which "
           "the loop installs before each session and commits with your work. To add one, add its line there "
           f"(`brew \"jq\"`) and run `{' '.join(brew.install_command(brewfile))}`, then go on. "
           "Never `brew install` directly - the guard refuses it - and there is no root: apt and sudo do not work."]
          if brewfile is not None else []),
        "- **Repositories:**",
        *(folder(name) for name in folders),
    ]
    if places:
        lines += ["- **Other folders,** not repositories, never committed:", *(folder(name) for name in places)]
    return "\n".join(lines) + "\n"


def write_system_file(dest: Path, autotask: str, described: str, *, where: str = "",
                      values: dict[str, str] | None = None) -> Path:
    """AUTOTASK.md, then what the project is, with where the session is after AUTOTASK.md, and this
    machine's folders filled in where AUTOTASK.md and the project's text name them. Given to every
    session as part of its system prompt, so sessions of a project share that start in the prompt
    cache; written once a run, so the whole run works to one version of it.

    `described` is the definition's one description, under `# What the project is` - not the
    script's, which joins its tracker's description and a brief.md: it has no definitions (item
    doc-read)."""
    values = values or {}
    text = fill(autotask, values) + "\n\n---\n\n"
    if where:
        text += where + "\n---\n\n"
    dest.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8 without a BOM.
    dest.write_bytes((text + fill(f"# What the project is\n\n{described.strip()}\n", values)).encode("utf-8"))
    return dest


def config_dir(claude: str | None) -> Path:
    """The Claude config folder a session runs on: the worker's, or ~/.claude for the default
    account, which is what claude uses with no CLAUDE_CONFIG_DIR."""
    return config.expand(claude).resolve() if claude else Path.home() / ".claude"


def session_variables(
    *,
    author: str,
    scratch: Path,
    tracker: str,
    item: str,
    claude: str | None = None,
    overseer: dict | None = None,
    sign_in: dict | None = None,
    environ: dict[str, str] | None = None,
    project_env: Any = None,
) -> dict[str, str | None]:
    """The variables a session runs with, as the script's `$SessionEnv`; None removes one.

    `tracker` is what OVERSEER_TRACKER names - the tracker's document name on the server - and
    `item` is `<project>:<handle>`. `overseer` is the worker's: the server, and its key file.
    `sign_in` is the worker's too (`sign_in_variables`), read from `environ`, the app's own.
    The template a tracker inherits comes from that server too, so nothing of the machine's
    Overseer is passed on.

    `project_env` is what the project's setup left for its sessions (`setup.SessionEnv`, item
    project-setup): its folders in front of PATH, its variables set. The hooks claude runs, the
    guard among them, and every command of the session inherit them."""
    env: dict[str, str | None] = {
        # tracker.py flags what a session files auto: filed by an agent of its own accord.
        "AGENTIC_LOOP": "1",
        # What signs the comments the session adds to the tracker.
        "AGENT_AUTHOR": author,
        "AGENT_SCRATCH": _slashes(scratch),
        # The agent's own tracker.py commands then reach this project's tracker without naming it.
        "OVERSEER_TRACKER": tracker,
        # And where an item it files into another project came from.
        "OVERSEER_ITEM": item,
        # No variable at all for the default account, not one naming ~/.claude: given one, claude
        # keeps its state inside it and starts as if new.
        "CLAUDE_CONFIG_DIR": str(config_dir(claude)) if claude else None,
    }
    if overseer:
        env["OVERSEER_URL"] = str(overseer["server"]).rstrip("/")
        env["OVERSEER_KEY_FILE"] = str(config.expand(overseer["keyFile"]).resolve())
        # Where tracker.py keeps its copy of each tracker it reads, for the server to send the text
        # again only once it changed: the machine's, shared by every loop on it.
        env["OVERSEER_CACHE"] = _slashes(config.paths().data / "trackers")
    if os.name == "nt":
        # The native Windows PowerShell tool, as the script asks for it.
        env["CLAUDE_CODE_USE_POWERSHELL_TOOL"] = "1"
    env.update(sign_in_variables(sign_in, environ))
    if project_env is not None:
        project_env.apply(env, environ)
    return env


def sign_in_variables(sign_in: dict | None, environ: dict[str, str] | None = None) -> dict[str, str | None]:
    """The variables a worker's `signIn` gives its sessions (item claude-container): the secret, read
    from the variable it names in the app's environment, as the variable claude reads it from, and
    the other of the two removed, so that one worker never runs on both. A worker with none leaves
    both as the app has them: a container that sets one for every worker keeps it.

    A variable named but not set is a SessionError naming the variable, never a value."""
    if not sign_in:
        return {}
    kind, variable = next(iter(sign_in.items()))
    value = (os.environ if environ is None else environ).get(variable)
    if not value:
        raise SessionError(f"{variable} is not set: the worker signs in to Claude with the {_SIGN_IN_WHAT[kind]} it holds.")
    return {name: value if k == kind else None for k, name in config.SIGN_IN.items()}


_SIGN_IN_WHAT = {"token": "token (claude setup-token)", "apiKey": "API key"}


def build_env(variables: dict[str, str | None], base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment the session's process gets: the app's own with the session's variables set
    on a copy, so the app's process, and the next loop's session, never carry this one's."""
    env = dict(os.environ if base is None else base)
    for name, value in variables.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


@dataclass
class Route:
    model: str
    effort: str | None = None


def command_line(claude: str, session_id: str, route: Route, *, settings: Path, system_file: Path, resume: bool) -> list[str]:
    """claude's arguments, as `Invoke-Session` builds them. stream-json, which needs --verbose,
    emits each message as it happens, one JSON object per line. The prompt goes in on stdin."""
    argv = [
        claude, "-p", "--dangerously-skip-permissions", "--output-format", "stream-json", "--verbose",
        "--settings", str(settings), "--model", route.model,
        # The guide and the brief in the prompt; the sections that differ by machine and folder
        # moved out of it, so they do not split the cached start between sessions.
        "--append-system-prompt-file", str(system_file), "--exclude-dynamic-system-prompt-sections",
    ]
    argv += ["--resume", session_id] if resume else ["--session-id", session_id]
    if route.effort:
        argv += ["--effort", route.effort]
    return argv


# What the stream says, for whoever watches

@dataclass
class Event:
    """One thing a session did, for the terminal in `agentloop run` and the page's pane.

    kind is `init` (text: the model, data: the event), `text` (the agent's words), `tool` (text:
    `Describe-Tool`'s line), `tool_error` (text: the first line of the error), `rate`, `result`
    (data: the result event) or `raw` (a line that is not JSON). indent is "  ", or "      " for a
    subagent's own work."""

    kind: str
    text: str = ""
    indent: str = "  "
    data: dict = field(default_factory=dict)


_DESCRIBED = ("description", "command", "file_path", "pattern", "path", "url", "prompt")


def describe_tool(block: dict) -> str:
    """One line describing a tool call: the tool and what it is acting on (`Describe-Tool`)."""
    given = block.get("input") or {}
    what = None
    for name in _DESCRIBED:
        if isinstance(given, dict) and given.get(name):
            what = str(given[name])
            break
    if not what:
        what = json.dumps(given, separators=(",", ":"), ensure_ascii=False)
    what = what.split("\n")[0]
    if len(what) > 140:
        what = what[:140] + "..."
    return f"{block.get('name')}: {what}"


def _first_error_line(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        text = content
    else:
        text = " ".join(str(part.get("text") or "") for part in content or [] if isinstance(part, dict))
    first = text.split("\n")[0]
    return first[:160] + "..." if len(first) > 160 else first


def format_event(event: Event) -> list[str]:
    """The lines the script writes for an event, for a terminal; nothing for those it keeps quiet."""
    if event.kind == "init":
        return [f"  model {event.data.get('model')}, session {event.data.get('session_id')}"]
    if event.kind == "text":
        return [""] + [event.indent + line.rstrip() for line in event.text.strip().split("\n")]
    if event.kind == "tool":
        return [event.indent + "> " + event.text]
    if event.kind == "tool_error":
        return [event.indent + "! " + event.text]
    if event.kind == "raw":
        return [event.text]
    return []


def event_colour(event: Event) -> str | None:
    """The colour the script writes an event's lines in: tool calls and the session's start dark
    grey, a tool's error dark red, the agent's own words in the console's own."""
    return {"init": "DarkGray", "tool": "DarkGray", "tool_error": "DarkRed"}.get(event.kind)


# Running it

@dataclass
class Spent:
    cost: float = 0.0
    input: int = 0
    read: int = 0
    write: int = 0
    output: int = 0


@dataclass
class SessionResult:
    """What `Invoke-Session` returns: the result event (None if the session ended without one),
    the last rate-limit report, what the whole session has used so far, this run's turns and
    time, the model the name it was started with stood for, and its context at its last call."""

    final: dict | None = None
    rate: dict | None = None
    spent: Spent = field(default_factory=Spent)
    turns: int = 0
    ms: int = 0
    model: str | None = None
    context: int = 0
    exit_code: int | None = None


class StreamReader:
    """What a session's stream says, taken one line at a time as `Invoke-Session` takes it."""

    def __init__(self, on_event: Callable[[Event], None] | None = None):
        self.result = SessionResult()
        self._on_event = on_event or (lambda event: None)

    def line(self, line: str) -> None:
        try:
            e = json.loads(line)
        except ValueError:
            self._on_event(Event("raw", line))
            return
        if not isinstance(e, dict):
            self._on_event(Event("raw", line))
            return
        r = self.result
        indent = "      " if e.get("parent_tool_use_id") else "  "
        kind = e.get("type")
        if kind == "system":
            if e.get("subtype") == "init":
                r.model = e.get("model")
                self._on_event(Event("init", str(r.model), data=e))
        elif kind == "rate_limit_event":
            r.rate = e.get("rate_limit_info")
            self._on_event(Event("rate", data=r.rate or {}))
        elif kind == "assistant":
            message = e.get("message") or {}
            usage = message.get("usage")
            # How much the session's own last call read - its context now - for whether it may
            # go on to another item. A subagent's calls are not its context.
            if usage and not e.get("parent_tool_use_id"):
                r.context = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and str(block.get("text") or "").strip():
                    self._on_event(Event("text", block["text"].strip(), indent))
                elif block.get("type") == "tool_use":
                    self._on_event(Event("tool", describe_tool(block), indent, block))
        elif kind == "user":
            content = (e.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else [content]:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                    self._on_event(Event("tool_error", _first_error_line(block), indent, block))
        elif kind == "result":
            r.final = e
            # Each result says what the whole session has used so far - across the wake-ups a
            # finished background task brings, and across resumes, the triage before a build
            # included - so the most any of them says is where this run of it ends. Turns and
            # time are each result's own.
            r.turns += int(e.get("num_turns") or 0)
            r.ms += int(e.get("duration_ms") or 0)
            if e.get("total_cost_usd") is not None:
                r.spent.cost = max(r.spent.cost, float(e["total_cost_usd"]))
            models = e.get("modelUsage")
            if isinstance(models, dict) and models:
                totals = {"input": 0, "read": 0, "write": 0, "output": 0}
                for used in models.values():
                    totals["input"] += int(used.get("inputTokens") or 0)
                    totals["read"] += int(used.get("cacheReadInputTokens") or 0)
                    totals["write"] += int(used.get("cacheCreationInputTokens") or 0)
                    totals["output"] += int(used.get("outputTokens") or 0)
                for key, value in totals.items():
                    setattr(r.spent, key, max(getattr(r.spent, key), value))
            self._on_event(Event("result", data=e))


@dataclass
class SessionSpec:
    """Everything one session needs, made by its caller (item loop): this reads no tracker and no
    settings."""

    claude: str
    session_id: str
    resume: bool
    prompt: str
    route: Route
    workdir: Path
    system_file: Path
    settings: Path
    log: Path
    variables: dict[str, str | None]
    # For a kill: running.json, and the item it names.
    state_file: Path | None = None
    item: str | None = None


def _popen_options() -> dict:
    # A process group of its own, so that item runner can end claude and everything it started
    # as the OS ends a tree: the group on macOS and Linux, CTRL_BREAK or taskkill /T on Windows.
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def run_session(spec: SessionSpec, on_event: Callable[[Event], None] | None = None, on_start: Callable[[subprocess.Popen], None] | None = None) -> SessionResult:
    """Runs one claude session to its end, passing what it does to on_event as it goes, and keeping
    every line of its stream in spec.log (`Invoke-Session`). on_start is given the process once
    it runs, for whoever may have to end it."""
    reader = StreamReader(on_event)
    env = build_env(spec.variables)
    argv = command_line(spec.claude, spec.session_id, spec.route, settings=spec.settings, system_file=spec.system_file, resume=spec.resume)
    spec.log.parent.mkdir(parents=True, exist_ok=True)
    # stderr is left alone: it goes where the app's own goes.
    process = subprocess.Popen(argv, cwd=spec.workdir, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, **_popen_options())
    try:
        if spec.state_file:
            write_state(spec.state_file, process.pid, spec)
        if on_start:
            on_start(process)
        try:
            process.stdin.write(spec.prompt.encode("utf-8"))
            process.stdin.close()
        except (BrokenPipeError, OSError):
            # A claude that ended before reading its prompt says why on its stream, or nowhere.
            pass
        with spec.log.open("a", encoding="utf-8", newline="\n") as log:
            for raw in process.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                log.write(line + "\n")
                log.flush()
                reader.line(line)
        reader.result.exit_code = process.wait()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        # The session is over, and its claude's pid may soon be another program's: a kill between
        # sessions must find no child to end, but still the session to carry on.
        if spec.state_file:
            write_state(spec.state_file, None, spec)
    return reader.result


# Records, and what a session leaves behind

def resume_command(session_id: str, workdir: Path, claude: str | None = None, windows: bool | None = None) -> str:
    """The command that carries a session on by hand, in the folder it ran in and on its account
    (`Get-ResumeCommand`), for the shell this OS gives."""
    windows = os.name == "nt" if windows is None else windows
    account = str(config_dir(claude)) if claude else None
    if windows:
        quote = lambda text: "'" + text.replace("'", "''") + "'"
        prefix = f"$env:CLAUDE_CONFIG_DIR = {quote(account)}; " if account else ""
        return f"cd {quote(str(workdir))}; {prefix}claude --resume {session_id}"
    prefix = f"CLAUDE_CONFIG_DIR={shlex.quote(account)} " if account else ""
    return f"cd {shlex.quote(str(workdir))} && {prefix}claude --resume {session_id}"


def write_state(path: Path, child: int | None, spec: SessionSpec) -> None:
    """running.json, for a kill: whose claude to end - a child of this process - and how to carry
    its session on by hand afterwards. `child` is None once the session has ended."""
    claude_dir = spec.variables.get("CLAUDE_CONFIG_DIR")
    state = {
        "pid": os.getpid(),
        "child": child,
        "session": spec.session_id,
        "item": spec.item,
        "resume": resume_command(spec.session_id, spec.workdir, claude_dir),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(state, separators=(",", ":")).encode("utf-8"))


def find_transcript(session_id: str, claude: str | None = None) -> Path | None:
    """Where claude saved a session, if it did: <config>/projects/<the working folder>/<id>.jsonl
    (`Find-Transcript`)."""
    sessions = config_dir(claude) / "projects"
    if not sessions.is_dir():
        return None
    for folder in sorted(sessions.iterdir()):
        candidate = folder / f"{session_id}.jsonl"
        if folder.is_dir() and candidate.is_file():
            return candidate
    return None


def remove_transcript(session_id: str, claude: str | None = None) -> None:
    """A session's transcript, and the folder of its subagents' beside it if it had any
    (`Remove-Transcript`)."""
    path = find_transcript(session_id, claude)
    if not path:
        return
    path.unlink()
    beside = path.with_suffix("")
    if beside.is_dir():
        shutil.rmtree(beside)


def clear_session(session_id: str, scratch: Path, claude: str | None = None) -> None:
    """What a session leaves behind once its item's work is committed: its transcript and its
    scratch folder (`Clear-Session`). Both are kept when it fails, for whoever looks into why, so
    this is called only on success."""
    remove_transcript(session_id, claude)
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)


# The final answer's lines

@dataclass
class FinalLines:
    commit: str | None
    next: str | None
    stop: str | None
    # A merge phase's word on the conflict it was given (item git-sync); the script has neither.
    merged: str | None = None
    unmerged: str | None = None


def parse_final(text: str | None) -> FinalLines:
    """The COMMIT, NEXT and STOP_LOOP lines of a session's final answer, matched as
    `Complete-Session` matches them, and a merge phase's MERGED or UNMERGED. Whether a STOP_LOOP
    counts - only in a result that is not an error - is the caller's to judge."""
    text = text or ""
    commit = re.search(r"(?m)^\s*COMMIT:\s*(.+?)\s*$", text)
    nxt = re.search(r"(?m)^\s*NEXT:\s*`?([A-Za-z0-9][A-Za-z0-9_-]*)", text)
    stop = re.search(r"(?m)^\s*STOP_LOOP:\s*(.+?)\s*$", text)
    merged = re.search(r"(?m)^\s*MERGED:\s*(.+?)\s*$", text)
    unmerged = re.search(r"(?m)^\s*UNMERGED:\s*(.+?)\s*$", text)
    return FinalLines(commit and commit.group(1), nxt and nxt.group(1), stop and stop.group(1),
                      merged and merged.group(1), unmerged and unmerged.group(1))


def _slashes(path: Any) -> str:
    return str(path).replace("\\", "/")
