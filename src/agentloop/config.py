"""Bundles and machine settings: where they live, reading and checking them, layering the run
settings, placing a bundle's repositories on this PC, and writing the files back.

What each file holds is docs/settings.md. The script's side is the start of run-agent-loop.ps1:
its `$Routes`, `$Escalation`, `$BuildAfterTriage`, `$BuildInTriageUpTo` and `$Trial`, its
parameters, the merge of a project's `.psd1` over them, and `Get-RepoTop`.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

FORMAT = 1

# A repository's, a project's, a worker's or a loop's name.
NAME = re.compile(r"^[a-z0-9_-]+$")

# The systems a bundle can say it runs on, as `this_system` names them.
SYSTEMS = ("windows", "macos", "linux")

# What the brief and AUTOTASK.md write as `{tools}`, `{bundle}` and `{python}`, filled in for this
# machine as the repositories and places are; so no repository or place may be named so.
RESERVED = ("tools", "bundle", "python")

COMPLEXITIES = ("trivial", "low", "medium", "high", "unassigned")
ROUTE_KEYS = ("triage", *COMPLEXITIES)

# The app's run settings: the script's values, as run-agent-loop.ps1 sets them and its parameters'
# defaults. The trial's `until` is a date, compared as the script compares `[datetime]"2026-10-29"`.
DEFAULTS: dict[str, Any] = {
    "routes": {
        "triage": {"model": "opus", "effort": "medium"},
        "trivial": {"model": "sonnet", "effort": "medium"},
        "low": {"model": "sonnet", "effort": "high"},
        "medium": {"model": "opus", "effort": "medium"},
        "high": {"model": "opus", "effort": "high"},
        "unassigned": {"model": "opus", "effort": "medium"},
    },
    "escalation": {"model": "opus", "effort": "medium"},
    "buildAfterTriage": ["medium", "high"],
    "buildInTriageUpTo": 2,
    "trial": {"until": "2026-10-29", "complexity": "medium", "upTo": 3, "route": {"model": "sonnet", "effort": "high"}},
    "maxIterations": 20,
    "stopAtWeeklyUse": 1.0,
    "askEveryMinutes": 30,
    "chainUpTo": 3,
    "repeatUpTo": 2,
    "chainContextUpTo": 120000,
    "model": None,
    # Not the script's: it never pushes (item git-sync).
    "push": False,
    "pushTries": 3,
    # Not the script's: it ends on an empty queue (item auto-start).
    "autoStart": False,
    "autoStartMinutes": 10,
    # Not the script's, which has no project definition (item doc-change): "auto" runs a loop on
    # its tracker's definition as soon as it changes, "ask" holds the loop until the change is
    # accepted. The machine's to say, in its defaults or a loop's settings - never a project's.
    "acceptDefinition": "auto",
    # Not the script's (item project-setup): "auto" runs the setup script a project's definition
    # names for this system before its sessions, "off" never does - for a machine that has the
    # tools already. The machine's to say, as acceptDefinition is.
    "setup": "auto",
}

# What acceptDefinition may be.
ACCEPT_DEFINITION = ("auto", "ask")

# What setup may be.
SETUP = ("auto", "off")


class ConfigError(Exception):
    """A setting that is missing or wrong, said as `<file>: <field>: <what is wrong>`."""

    def __init__(self, where: str, field: str, message: str):
        super().__init__(f"{where}: {field}: {message}" if field else f"{where}: {message}")
        self.where = where
        self.field = field
        self.message = message


# Where things live

@dataclass(frozen=True)
class Paths:
    config: Path
    data: Path

    @property
    def settings(self) -> Path:
        return self.config / "settings.json"

    @property
    def bundles(self) -> Path:
        return self.config / "bundles"


def paths() -> Paths:
    """This OS's places for the app's settings and its data, or both under AGENTLOOP_HOME."""
    home = os.environ.get("AGENTLOOP_HOME")
    if home:
        root = expand(home)
        return Paths(root / "config", root / "data")
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Paths(Path(roaming) / "agentloop", Path(local) / "agentloop")
    if sys.platform == "darwin":
        support = Path.home() / "Library" / "Application Support" / "agentloop"
        return Paths(support, support)
    return Paths(_xdg("XDG_CONFIG_HOME", ".config") / "agentloop", _xdg("XDG_STATE_HOME", ".local/state") / "agentloop")


def this_system() -> str:
    """This OS, as a bundle's `systems` names it."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _xdg(variable: str, default: str) -> Path:
    # The XDG spec says a relative value is to be ignored.
    value = os.environ.get(variable)
    return Path(value) if value and Path(value).is_absolute() else Path.home() / default


def expand(path: str) -> Path:
    """A path as the settings write it: forward slashes, and a leading `~` for the home folder.
    Nothing else is expanded."""
    if path == "~":
        return Path.home()
    if path.startswith(("~/", "~\\")):
        return Path.home() / path[2:]
    return Path(path)


def home_relative(path: str | Path) -> str:
    """A path written as the settings write one: under `~` when it is in the home folder."""
    text = str(path).replace("\\", "/")
    home = str(Path.home()).replace("\\", "/").rstrip("/")
    same = (lambda a, b: a.lower() == b.lower()) if os.name == "nt" else (lambda a, b: a == b)
    if same(text, home):
        return "~"
    if same(text[: len(home) + 1], home + "/"):
        return "~/" + text[len(home) + 1:]
    return text


# Checking values

def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (isinstance(value, (int, float))) and not isinstance(value, bool)


def _text(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _check_route(where: str, field: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise ConfigError(where, field, 'a route is {"model": ..., "effort": ...}')
    for key in value:
        if key not in ("model", "effort"):
            raise ConfigError(where, f"{field}.{key}", "a route has only model and effort")
    for key in ("model", "effort"):
        if not _text(value.get(key)):
            raise ConfigError(where, f"{field}.{key}", "missing, or not a name")


def check_run_settings(where: str, field: str, settings: Any) -> None:
    """One layer of run settings: only known keys, each of its type."""
    if settings is None:
        return
    if not isinstance(settings, dict):
        raise ConfigError(where, field, "the run settings are an object")
    for key, value in settings.items():
        at = f"{field}.{key}"
        if key not in DEFAULTS:
            raise ConfigError(where, at, f"no such setting; the settings are {', '.join(DEFAULTS)}")
        if key == "routes":
            if not isinstance(value, dict):
                raise ConfigError(where, at, "routes is an object, a route for each of " + ", ".join(ROUTE_KEYS))
            for name, route in value.items():
                if name not in ROUTE_KEYS:
                    raise ConfigError(where, f"{at}.{name}", "no such route; the routes are " + ", ".join(ROUTE_KEYS))
                _check_route(where, f"{at}.{name}", route)
        elif key == "escalation":
            _check_route(where, at, value)
        elif key == "buildAfterTriage":
            if not isinstance(value, list) or any(c not in COMPLEXITIES for c in value):
                raise ConfigError(where, at, "a list of complexities: " + ", ".join(COMPLEXITIES))
        elif key == "trial":
            if value is None:
                continue
            if not isinstance(value, dict) or set(value) != {"until", "complexity", "upTo", "route"}:
                raise ConfigError(where, at, 'null, or {"until", "complexity", "upTo", "route"}')
            if not isinstance(value["until"], str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", value["until"]):
                raise ConfigError(where, f"{at}.until", "a date, YYYY-MM-DD")
            if value["complexity"] not in COMPLEXITIES:
                raise ConfigError(where, f"{at}.complexity", "one of " + ", ".join(COMPLEXITIES))
            if not _is_int(value["upTo"]) or value["upTo"] < 0:
                raise ConfigError(where, f"{at}.upTo", "a whole number of points, 0 or more")
            _check_route(where, f"{at}.route", value["route"])
        elif key == "model":
            if value is not None and not _text(value):
                raise ConfigError(where, at, "null, or a model's name")
        elif key == "acceptDefinition":
            if value not in ACCEPT_DEFINITION:
                raise ConfigError(where, at, " or ".join(f'"{v}"' for v in ACCEPT_DEFINITION))
        elif key == "setup":
            if value not in SETUP:
                raise ConfigError(where, at, " or ".join(f'"{v}"' for v in SETUP))
        elif key in ("push", "autoStart"):
            if not isinstance(value, bool):
                raise ConfigError(where, at, "true or false")
        elif key == "pushTries":
            if not _is_int(value) or value < 1:
                raise ConfigError(where, at, "a whole number, 1 or more")
        elif key in ("stopAtWeeklyUse", "askEveryMinutes", "autoStartMinutes"):
            if not _is_number(value) or value <= 0:
                raise ConfigError(where, at, "a number above 0")
        elif not _is_int(value) or value < 0:
            raise ConfigError(where, at, "a whole number, 0 or more")


def _check_format(where: str, raw: dict) -> None:
    if raw.get("format") != FORMAT:
        raise ConfigError(where, "format", f"must be {FORMAT}, the only format this version reads")


def _read_json(path: Path, where: str) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(where, "", "no such file") from None
    except OSError as error:
        raise ConfigError(where, "", f"cannot be read ({type(error).__name__})") from None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigError(where, f"line {error.lineno}", f"not JSON: {error.msg}") from None
    if not isinstance(raw, dict):
        raise ConfigError(where, "", "must hold a JSON object")
    return raw


# The bundle

@dataclass
class Tracker:
    # The tracker's name on the Overseer server the loop's worker names. A tracker is kept nowhere
    # else: a file in a repository is no longer one (item simplify).
    server: str


@dataclass
class Project:
    """A project as the loop reads it, wherever it came from: the definition beside its tracker,
    which the tracker mounts (item doc-read, `definition.py`), or a bundle folder on this PC - its
    `project.json` and `brief.md` - kept for trying a brief out (item project-doc)."""

    name: str
    # The bundle's folder; None for a project read from its tracker, which has no folder here.
    folder: Path | None
    tracker: Tracker
    repos: list[str]
    settings: dict
    raw: dict
    # The systems the project runs on; None for every one.
    systems: list[str] | None = None
    # The names of the folders the brief names that are not repositories, each loop placing them.
    places: list[str] = field(default_factory=list)
    # What each repository and place is, by its name: the line a person placing it reads.
    about: dict[str, str] = field(default_factory=dict)
    # What the project is: a definition's one description. A bundle's is its brief.md, read when
    # a run starts, so this stays empty for one.
    description: str = ""
    # "tracker" or "bundle".
    source: str = "bundle"
    # The definition's version as read; None for a bundle.
    version: str | None = None
    # A project read from its tracker: the folder its tools were fetched into, which holds them in
    # `tools/` as a bundle's folder does, and which `{bundle}` names (item tools-fetch); None when
    # it lists no tools for this system, or for a bundle.
    files: Path | None = None

    @property
    def brief(self) -> Path:
        if self.folder is None:
            raise ConfigError(self.name, "", "is read from its tracker, and has no brief.md")
        return self.folder / "brief.md"

    @property
    def tools(self) -> Path | None:
        home = self.folder or self.files
        return home / "tools" if home else None

    def repo_names(self) -> list[str]:
        """Every repository name the project uses."""
        return list(self.repos)


# What the code was written with before a project could come from its tracker.
Bundle = Project


def entry_names(entries: list | None) -> tuple[list[str], dict[str, str]]:
    """A checked list of repositories or places - each a bare name, or {"name", "about"} - as
    their names in order, and the about of each that gives one."""
    names: list[str] = []
    about: dict[str, str] = {}
    for entry in entries or []:
        if isinstance(entry, dict):
            names.append(entry["name"])
            if entry.get("about"):
                about[entry["name"]] = entry["about"]
        else:
            names.append(entry)
    return names, about


def load_bundle(folder: str | Path) -> Project:
    folder = expand(folder) if isinstance(folder, str) else folder
    where = f"{folder.name}/project.json"
    raw = _read_json(folder / "project.json", where)
    check_bundle(raw, where, folder.name)
    tracker = raw["tracker"]
    repos, about = entry_names(raw["repos"])
    places, place_about = entry_names(raw.get("places"))
    return Project(
        name=raw["name"],
        folder=folder,
        tracker=Tracker(server=tracker["server"]),
        repos=repos,
        settings=raw.get("settings") or {},
        raw=raw,
        systems=list(raw["systems"]) if raw.get("systems") is not None else None,
        places=places,
        about={**about, **place_about},
    )


def check_bundle(raw: dict, where: str, folder_name: str) -> None:
    _check_format(where, raw)
    name = raw.get("name")
    if not isinstance(name, str) or not NAME.match(name):
        raise ConfigError(where, "name", "missing, or not lower case letters, digits, - and _")
    if name != folder_name:
        raise ConfigError(where, "name", f'is "{name}", but the bundle\'s folder is "{folder_name}"; they must be the same')
    tracker = raw.get("tracker")
    if isinstance(tracker, dict) and set(tracker) == {"server"}:
        if not _text(tracker["server"]):
            raise ConfigError(where, "tracker.server", "the tracker's name on the server, e.g. alice/projects/x.os")
    elif isinstance(tracker, dict) and "path" in tracker:
        raise ConfigError(where, "tracker", "names a file in a repository, and trackers are kept only on an Overseer "
                          'server now: {"server": its name there, e.g. alice/projects/x.os}')
    else:
        raise ConfigError(where, "tracker", '{"server": the tracker\'s name on the server}')
    check_project(raw, where)


def _check_entries(where: str, key: str, entries: list, what: str, more: tuple[str, ...] = ()) -> list[str]:
    """A list of repositories or places, each a bare name or {"name", "about"}: their names. A
    repository may also name its `url` and `branch` (item clone), given as `more`."""
    names = []
    for index, entry in enumerate(entries):
        at = f"{key}[{index}]"
        if isinstance(entry, dict):
            if set(entry) - {"name", "about", *more}:
                shape = ", ".join(f'"{f}"' for f in ("name", "about", *more))
                raise ConfigError(where, at, f'a {what} is its name, or {{{shape}}}')
            if entry.get("about") is not None and not isinstance(entry["about"], str):
                raise ConfigError(where, f"{at}.about", f"a line saying what the {what} is")
            for field in more:
                if entry.get(field) is not None and not isinstance(entry[field], str):
                    raise ConfigError(where, f"{at}.{field}", "text")
            name = entry.get("name")
            at = f"{at}.name"
        else:
            name = entry
        if not isinstance(name, str) or not NAME.match(name):
            raise ConfigError(where, at, f"a {what}'s name: lower case letters, digits, - and _")
        names.append(name)
    return names


def check_project(raw: dict, where: str) -> None:
    """What a project holds, whether a bundle's project.json or a tracker's definition gives it:
    its repositories, places, systems and run settings."""
    repos = raw.get("repos")
    if not isinstance(repos, list):
        raise ConfigError(where, "repos", "a list of repository names")
    names = _check_entries(where, "repos", repos, "repository", ("url", "branch"))
    if len(set(names)) != len(names):
        raise ConfigError(where, "repos", "names a repository twice")
    if not names:
        raise ConfigError(where, "repos", "names no repository for the sessions to run in")
    for repo in names:
        if repo in RESERVED:
            raise ConfigError(where, "repos", f'"{repo}" is a name the app fills in itself; name the repository otherwise')
    systems = raw.get("systems")
    if systems is not None:
        if not isinstance(systems, list) or not systems or any(s not in SYSTEMS for s in systems):
            raise ConfigError(where, "systems", "absent for every system, or a list of " + ", ".join(SYSTEMS))
    places = raw.get("places")
    if places is not None:
        if not isinstance(places, list):
            raise ConfigError(where, "places", "a list of the names of folders that are not repositories")
        place_names = _check_entries(where, "places", places, "place")
        for index, place in enumerate(place_names):
            if place in RESERVED or place in names:
                raise ConfigError(where, f"places[{index}]", f'"{place}" is a repository\'s name, or one the app fills in itself')
        if len(set(place_names)) != len(place_names):
            raise ConfigError(where, "places", "names a place twice")
    check_tools(raw.get("tools"), where)
    check_run_settings(where, "settings", raw.get("settings"))
    # Whether a changed definition runs before anyone has looked is for the person running the
    # loop to say, not for the project that changes it (item doc-change).
    if isinstance(raw.get("settings"), dict):
        for key in ("acceptDefinition", "setup"):
            if key in raw["settings"]:
                raise ConfigError(where, f"settings.{key}", "is for each machine to set, in its defaults or a loop's settings, not a project")


# A tool's hash, as a definition's manifest gives it: sha256, in lower-case hex.
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def tool_path_problem(path: object) -> str | None:
    """What is wrong with a tool's path in a definition's manifest, or None: it is relative, with
    `/` between plain names, as the server keeps files beside a document (overseer:definition-files)
    - so that it cannot name a file outside the folder the tools are fetched into."""
    if not isinstance(path, str) or not path:
        return "a path relative to the definition's tools, such as tests/probe.py"
    if "\\" in path or ":" in path or path.startswith("/"):
        return "a relative path with / between its parts, no drive and no root"
    if any(part in ("", ".", "..") for part in path.split("/")):
        return "a relative path of plain names, with no . or .. and no empty part"
    return None


def check_tools(tools: object, where: str) -> None:
    """A definition's tools (item tools-fetch): absent, or a list of {"file", "sha256", "systems"},
    each file named once, its hash sha256 in hex, its systems a list of known ones - empty for
    every system."""
    if tools is None:
        return
    if not isinstance(tools, list):
        raise ConfigError(where, "tools", "a list of the project's tool files")
    seen: set[str] = set()
    for index, tool in enumerate(tools):
        field_name = f"tools[{index}]"
        if not isinstance(tool, dict):
            raise ConfigError(where, field_name, "a file, its sha256 and the systems it is for")
        problem = tool_path_problem(tool.get("file"))
        if problem:
            raise ConfigError(where, f"{field_name}.file", problem)
        if tool["file"].lower() in seen:
            raise ConfigError(where, "tools", f"names {tool['file']} twice")
        seen.add(tool["file"].lower())
        if not isinstance(tool.get("sha256"), str) or not SHA256.match(tool["sha256"]):
            raise ConfigError(where, f"{field_name}.sha256", "the file's sha256, 64 lower-case hex digits")
        systems = tool.get("systems")
        if not isinstance(systems, list) or any(s not in SYSTEMS for s in systems):
            raise ConfigError(where, f"{field_name}.systems", "empty for every system, or some of " + ", ".join(SYSTEMS))
        if "setup" in tool and tool["setup"] is not True:
            raise ConfigError(where, f"{field_name}.setup", "true, or absent")
    # At most one setup script for each system (item project-setup): which of two would be the
    # one to run is not for the app to guess.
    for system in SYSTEMS:
        setups = [t["file"] for t in tools if t.get("setup") and (not t["systems"] or system in t["systems"])]
        if len(setups) > 1:
            raise ConfigError(where, "tools", f"marks {' and '.join(setups)} both as the setup for {system}; one system has one setup")


def tools_here(raw: dict) -> list[dict]:
    """The tools a definition lists for this machine's system: those for every system, and those
    that name it."""
    here = this_system()
    return [t for t in raw.get("tools") or [] if not t.get("systems") or here in t["systems"]]


def setup_tool(raw: dict) -> dict | None:
    """The tool a definition marks as the setup script for this machine's system (item
    project-setup), or None when it marks none."""
    return next((t for t in tools_here(raw) if t.get("setup")), None)


# Machine settings

@dataclass
class Worker:
    name: str
    claude: str | None
    overseer: dict | None
    # The Overseer username whose items this worker's loops take first, before items flagged first.
    manager: str | None = None
    # How its sessions sign in to Claude when not by the folder's own login (item claude-container):
    # {"token": VAR} or {"apiKey": VAR}, VAR naming the environment variable that holds the secret.
    # The secret itself is never in the settings.
    sign_in: dict | None = None

    @property
    def sign_in_kind(self) -> str | None:
        """`token`, `apiKey`, or None for the folder's login."""
        return next(iter(self.sign_in)) if self.sign_in else None

    @property
    def sign_in_variable(self) -> str | None:
        return next(iter(self.sign_in.values())) if self.sign_in else None


# What a worker's `signIn` may name, and the variable claude reads each from.
SIGN_IN = {"token": "CLAUDE_CODE_OAUTH_TOKEN", "apiKey": "ANTHROPIC_API_KEY"}
VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class Loop:
    name: str
    project: str
    worker: str
    repos: dict[str, str]
    settings: dict
    # Each folder the bundle names that is not a repository, by its name: never git-checked or
    # committed.
    places: dict[str, str] = field(default_factory=dict)
    # The loop this one is a copy of: what it does not give itself is that loop's. Its fields here
    # are the two put together; the settings' raw dict holds only its own.
    like: str | None = None
    # The tracker the loop reads its project's definition from, as the server names it - the
    # project being its file name without `.os` - or None for a loop on a bundle (item doc-read).
    tracker: str | None = None
    # The Overseer server and key file this loop reaches its tracker with, {"server", "keyFile"},
    # when they are not its worker's: a tracker on another server than the worker's (item
    # loop-from-url). None for the worker's.
    overseer: dict | None = None


def overseer_for(settings: "Settings", loop: Loop, worker: str | None = None) -> dict | None:
    """The server and key file a loop reaches its tracker with: its own, else its worker's - or
    the worker's named in place of it, for a run on another worker. None when neither names one."""
    if loop.overseer:
        return loop.overseer
    found = settings.worker(worker or loop.worker)
    return found.overseer if found else None


def parse_address(text: str) -> tuple[str | None, str]:
    """A tracker's address as a person copies it, made (server, document): Overseer's page at a
    document, `https://host[:port][/prefix]/doc/<document>` or `...?doc=<document>`, as its
    `documentName` reads one; or the document's name alone, `alice/projects/x.os`, the server then
    None - the worker's. Raises ConfigError for anything else, or a document that is no tracker."""
    text = (text or "").strip()
    where, field = "the address", "address"
    accepted = "Overseer's address of the tracker (https://host/doc/alice/projects/x.os), or its name there (alice/projects/x.os)"
    if not text:
        raise ConfigError(where, field, f"missing: {accepted}")
    server: str | None = None
    document = text
    if "://" in text:
        parts = urllib.parse.urlsplit(text)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ConfigError(where, field, f"not an http or https address: {accepted}")
        path = urllib.parse.unquote(parts.path)
        query = urllib.parse.parse_qs(parts.query).get("doc")
        if "/doc/" in path:
            prefix, _, document = path.partition("/doc/")
        elif query:
            # The page at its folder, or at a file of it (`/index.html?doc=...`): the server is the folder.
            prefix, document = path, query[0]
            if "." in prefix.rsplit("/", 1)[-1]:
                prefix = prefix.rsplit("/", 1)[0]
        else:
            raise ConfigError(where, field, f"names no document: {accepted}")
        server = f"{parts.scheme}://{parts.netloc}{prefix.rstrip('/')}"
    document = document.strip().strip("/")
    if tracker_project(document) is None or "\\" in document or any(p in ("", ".", "..") for p in document.split("/")):
        raise ConfigError(where, field, f"{document or 'it'} is not a tracker: {accepted}")
    return server, document


def tracker_project(tracker: str) -> str | None:
    """The project a tracker's name gives - `alice/projects/x.os` is `x` - or None when it names
    no tracker: not a `.os`, or not a project's name before it."""
    leaf = tracker.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if not leaf.endswith(".os"):
        return None
    name = leaf[: -len(".os")]
    return name if NAME.match(name) else None


@dataclass
class Settings:
    path: Path
    raw: dict
    bundles: list[str] = field(default_factory=list)
    workers: list[Worker] = field(default_factory=list)
    loops: list[Loop] = field(default_factory=list)
    defaults: dict = field(default_factory=dict)
    # The claude to run, for a PC where it is not on PATH; None finds it there.
    claude: str | None = None
    # The worker a loop made on the page starts with; None is the first (item accounts).
    default_worker_name: str | None = None

    def worker(self, name: str) -> Worker | None:
        return next((w for w in self.workers if w.name == name), None)

    @property
    def default_worker(self) -> Worker | None:
        if self.default_worker_name:
            return self.worker(self.default_worker_name)
        return self.workers[0] if self.workers else None

    def loop(self, name: str) -> Loop | None:
        return next((l for l in self.loops if l.name == name), None)

    def copies(self, name: str) -> list[str]:
        """The loops that are copies of `name`: what keeps it from being deleted or renamed."""
        return [l.name for l in self.loops if l.like == name]


def empty_settings() -> dict:
    return {"format": FORMAT, "bundles": [], "workers": [], "loops": [], "defaults": {}}


def load_settings(path: Path | None = None) -> Settings:
    """This machine's settings.json; a missing one is an empty one."""
    path = path or paths().settings
    raw = _read_json(path, path.name) if path.exists() else empty_settings()
    return parse_settings(raw, path)


def parse_settings(raw: dict, path: Path) -> Settings:
    where = path.name
    _check_format(where, raw)
    bundles = raw.get("bundles", [])
    if not isinstance(bundles, list) or not all(_text(b) for b in bundles):
        raise ConfigError(where, "bundles", "a list of the bundle folders' paths")

    workers = []
    for index, w in enumerate(_list(raw, "workers", where)):
        at = f"workers[{index}]"
        if not isinstance(w, dict):
            raise ConfigError(where, at, "a worker is an object")
        name = w.get("name")
        if not isinstance(name, str) or not NAME.match(name):
            raise ConfigError(where, f"{at}.name", "missing, or not lower case letters, digits, - and _")
        if any(other.name == name for other in workers):
            raise ConfigError(where, f"{at}.name", f'a second worker named "{name}"')
        claude = w.get("claude")
        if claude is not None and not _text(claude):
            raise ConfigError(where, f"{at}.claude", "the Claude config folder, or null for the default account")
        overseer = w.get("overseer")
        if overseer is not None:
            if not isinstance(overseer, dict) or not _text(overseer.get("server")) or not _text(overseer.get("keyFile")):
                raise ConfigError(where, f"{at}.overseer", 'null, or {"server": address, "keyFile": path}')
        manager = w.get("manager")
        if manager is not None and (not isinstance(manager, str) or not NAME.match(manager)):
            raise ConfigError(where, f"{at}.manager", "null, or an Overseer username: lower case letters, digits, - and _")
        sign_in = w.get("signIn")
        if sign_in is not None and not (
            isinstance(sign_in, dict) and len(sign_in) == 1 and next(iter(sign_in)) in SIGN_IN
            and isinstance(next(iter(sign_in.values())), str) and VARIABLE.match(next(iter(sign_in.values())))
        ):
            raise ConfigError(where, f"{at}.signIn",
                              "null for the folder's login, or {\"token\": VARIABLE} or {\"apiKey\": VARIABLE}: "
                              "the name of the environment variable holding it, never the secret")
        workers.append(Worker(name, claude, overseer, manager, sign_in))

    # Each loop's own keys checked first, then each copy filled in from the loop it is like, which
    # may come after it in the file.
    raws: list[dict] = []
    for index, l in enumerate(_list(raw, "loops", where)):
        at = f"loops[{index}]"
        if not isinstance(l, dict):
            raise ConfigError(where, at, "a loop is an object")
        name = l.get("name")
        if not isinstance(name, str) or not NAME.match(name):
            raise ConfigError(where, f"{at}.name", "missing, or not lower case letters, digits, - and _")
        if any(other["name"] == name for other in raws):
            raise ConfigError(where, f"{at}.name", f'a second loop named "{name}"')
        like = l.get("like")
        if like is not None and (not isinstance(like, str) or not NAME.match(like)):
            raise ConfigError(where, f"{at}.like", "the name of the loop this one is a copy of")
        if "tracker" in l:
            # A loop reads its project from the tracker, or from a bundle; never both.
            if "project" in l:
                raise ConfigError(where, f"{at}.tracker", "a loop names its tracker or a bundle's project, not both")
            if not isinstance(l["tracker"], str) or tracker_project(l["tracker"]) is None:
                raise ConfigError(where, f"{at}.tracker", "the tracker's name on the server, e.g. alice/projects/x.os")
        elif like is None or "project" in l:
            if not isinstance(l.get("project"), str) or not NAME.match(l["project"]):
                raise ConfigError(where, f"{at}.project", "a bundle's name, or a tracker in place of it")
        if like is None or "worker" in l:
            if not any(w.name == l.get("worker") for w in workers):
                raise ConfigError(where, f"{at}.worker", f'no worker named "{l.get("worker")}"')
        repos = l.get("repos", {})
        if not isinstance(repos, dict):
            raise ConfigError(where, f"{at}.repos", "an object from repository names to folders")
        for repo, folder in repos.items():
            if not NAME.match(repo):
                raise ConfigError(where, f"{at}.repos.{repo}", "not a repository name: lower case letters, digits, - and _")
            if not _text(folder):
                raise ConfigError(where, f"{at}.repos.{repo}", "the folder the repository is in on this PC")
        places = l.get("places", {})
        if not isinstance(places, dict):
            raise ConfigError(where, f"{at}.places", "an object from place names to folders")
        for place, folder in places.items():
            if not NAME.match(place):
                raise ConfigError(where, f"{at}.places.{place}", "not a place's name: lower case letters, digits, - and _")
            if not _text(folder):
                raise ConfigError(where, f"{at}.places.{place}", "the folder on this PC")
        overseer = l.get("overseer")
        if overseer is not None:
            if not isinstance(overseer, dict) or not _text(overseer.get("server")) or not _text(overseer.get("keyFile")):
                raise ConfigError(where, f"{at}.overseer", "null for the worker's, or " '{"server": address, "keyFile": path}')
        check_run_settings(where, f"{at}.settings", l.get("settings"))
        raws.append(l)

    loops = []
    for index, l in enumerate(raws):
        at = f"loops[{index}]"
        like = l.get("like")
        if like is None:
            tracker = l.get("tracker")
            project = tracker_project(tracker) if tracker else l["project"]
            loops.append(Loop(l["name"], project, l["worker"], dict(l.get("repos", {})), l.get("settings") or {},
                              dict(l.get("places", {})), tracker=tracker, overseer=l.get("overseer")))
            continue
        # One level only: a copy of a copy would be a chain to follow, and could be a cycle.
        base = next((other for other in raws if other["name"] == like), None)
        if base is None or base is l:
            raise ConfigError(where, f"{at}.like", f'no other loop named "{like}"')
        if base.get("like") is not None:
            raise ConfigError(where, f"{at}.like", f'"{like}" is itself a copy of "{base["like"]}"; name that loop')
        if l.get("project", base.get("project")) != base.get("project"):
            raise ConfigError(where, f"{at}.project", f'a copy works the project of the loop it is like, {base.get("project") or base.get("tracker")}')
        if l.get("tracker", base.get("tracker")) != base.get("tracker"):
            raise ConfigError(where, f"{at}.tracker", f'a copy works the project of the loop it is like, {base.get("tracker") or base.get("project")}')
        tracker = base.get("tracker")
        loops.append(Loop(l["name"], tracker_project(tracker) if tracker else base["project"], l.get("worker", base["worker"]),
                          {**base.get("repos", {}), **l.get("repos", {})},
                          merge_layers(base.get("settings"), l.get("settings")),
                          {**base.get("places", {}), **l.get("places", {})}, like, tracker,
                          l["overseer"] if "overseer" in l else base.get("overseer")))

    check_run_settings(where, "defaults", raw.get("defaults"))
    install = raw.get("install")
    if install is not None and not _text(install):
        raise ConfigError(where, "install", "the folder the build is installed in, or null")
    server = raw.get("server")
    if server is not None and (not isinstance(server, dict) or not _text(server.get("host")) or not _is_int(server.get("port"))):
        raise ConfigError(where, "server", 'the page\'s {"host": ..., "port": ...}')
    claude = raw.get("claude")
    if claude is not None and not _text(claude):
        raise ConfigError(where, "claude", "the path of the claude to run, or null for the one on PATH")
    default_worker = raw.get("defaultWorker")
    if default_worker is not None and not any(w.name == default_worker for w in workers):
        raise ConfigError(where, "defaultWorker", f'no worker named "{default_worker}"')
    # An `overseer` - {"bin": ..., "library": ...}, where tracker.py once found the server that
    # served a tracker file and then Overseer's library - is let be in settings written then, and
    # means nothing now: the template comes from the server a worker names (item own-server).
    return Settings(path, raw, list(bundles), workers, loops, raw.get("defaults") or {}, claude, default_worker)


def _list(raw: dict, key: str, where: str) -> list:
    value = raw.get(key, [])
    if not isinstance(value, list):
        raise ConfigError(where, key, "a list")
    return value


@dataclass
class Machine:
    """The settings, and the bundles they list, each loop's project among them."""

    settings: Settings
    bundles: dict[str, Bundle]

    def bundle(self, name: str) -> Bundle | None:
        return self.bundles.get(name)


def load_machine(path: Path | None = None) -> Machine:
    settings = load_settings(path)
    bundles: dict[str, Bundle] = {}
    for index, folder in enumerate(settings.bundles):
        bundle = load_bundle(folder)
        if bundle.name in bundles:
            raise ConfigError(settings.path.name, f"bundles[{index}]", f'a second bundle named "{bundle.name}"')
        bundles[bundle.name] = bundle
    # A loop on a tracker is not looked for here: its project is read from the server when the loop
    # starts (definition.project_for), and loading the machine never waits on the network.
    for index, loop in enumerate(settings.loops):
        if loop.tracker is None and loop.project not in bundles:
            raise ConfigError(settings.path.name, f"loops[{index}].project", f'no bundle named "{loop.project}" in bundles')
    return Machine(settings, bundles)


# Run settings, layered

def merge_layers(*layers: dict | None) -> dict:
    """Layers of run settings made one, as effective_settings lays them over each other: what a copy
    of a loop runs with is the loop's settings, then its own."""
    result: dict = {}
    for layer in layers:
        for key, value in (layer or {}).items():
            if key == "routes":
                result["routes"] = {**result.get("routes", {}), **copy.deepcopy(value)}
            else:
                result[key] = copy.deepcopy(value)
    return result


def effective_settings(*layers: dict | None) -> dict:
    """The app's defaults, then each layer in turn: the machine's `defaults`, the bundle's
    `settings`, the loop's `settings`. `routes` merge per route, as the script merges a project's
    Routes over `$Routes`; every other key replaces."""
    result = copy.deepcopy(DEFAULTS)
    for layer in layers:
        for key, value in (layer or {}).items():
            if key == "routes":
                result["routes"].update(copy.deepcopy(value))
            else:
                result[key] = copy.deepcopy(value)
    return result


def loop_settings(machine: Machine, loop: Loop, project: Project | None = None) -> dict:
    """What the loop runs with. A loop on a tracker gives the project it read; without it, only the
    machine's and the loop's layers are known."""
    if project is None:
        project = machine.bundles.get(loop.project) if loop.tracker is None else None
    return effective_settings(machine.settings.defaults, project.settings if project else None, loop.settings)


def accept_mode(machine: Machine, loop: Loop) -> str:
    """acceptDefinition for a loop: the machine's defaults, then the loop's own settings - a
    project's settings never say it (check_project)."""
    return effective_settings(machine.settings.defaults, loop.settings)["acceptDefinition"]


def setup_mode(machine: Machine, loop: Loop) -> str:
    """setup for a loop: the machine's defaults, then the loop's own settings - a project's
    settings never say it (check_project)."""
    return effective_settings(machine.settings.defaults, loop.settings)["setup"]


# Repositories, placed

def repo_top(folder: Path) -> Path | None:
    """The top of the git repository a folder is in, or None if it is in none: `Get-RepoTop`."""
    try:
        done = subprocess.run(["git", "-C", str(folder), "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise ConfigError("git", "", "git is not installed, or not on PATH") from None
    top = done.stdout.strip()
    if done.returncode != 0 or not top:
        return None
    return Path(top).resolve()


@dataclass
class Placed:
    """A loop's repositories on this PC: the tops to commit in, sessions running in the first, and
    the tracker's name on the server."""

    tops: list[Path]
    folders: dict[str, Path]
    tracker_name: str
    places: dict[str, Path] = field(default_factory=dict)


def project_where(project: Project) -> str:
    """Where a project's settings are, for an error about them."""
    return f"{project.tracker.server} definition" if project.source == "tracker" else f"{project.name}/project.json"


def check_system(bundle: Project) -> None:
    """Refuses a project that does not run on this OS, as it says."""
    here = this_system()
    if bundle.systems is not None and here not in bundle.systems:
        raise ConfigError(project_where(bundle), "systems", f"{bundle.name} runs on {', '.join(bundle.systems)}, not on {here}")


def place_repos(loop: Loop, bundle: Bundle, top_of: Callable[[Path], Path | None] = repo_top) -> Placed:
    """Each repository name the bundle uses, placed through the loop and named by the top of its
    repository, as the script resolves `Repos`."""
    placed, problems = _place(loop, bundle, top_of)
    if problems:
        raise problems[0]
    return placed


def missing(loop: Loop, bundle: Bundle, top_of: Callable[[Path], Path | None] = repo_top) -> list[ConfigError]:
    """Everything place_repos would refuse in a loop, not only the first: what the page asks for
    before it starts one."""
    return _place(loop, bundle, top_of)[1]


def tops(loop: Loop, bundle: Bundle, top_of: Callable[[Path], Path | None] = repo_top) -> list[Path]:
    """The repository tops the loop commits in, as far as they can be placed now: what two loops of
    one project may not share."""
    placed = _place(loop, bundle, top_of)[0]
    return placed.tops if placed else []


def _place(loop: Loop, bundle: Bundle, top_of: Callable[[Path], Path | None]) -> tuple[Placed | None, list[ConfigError]]:
    where = f'loop "{loop.name}"'
    problems: list[ConfigError] = []
    folders: dict[str, Path] = {}
    tops: list[Path] = []
    for name in bundle.repo_names():
        if name not in loop.repos:
            problems.append(ConfigError(where, f"repos.{name}", f"{bundle.name} uses the repository {name}, and this loop does not say where it is"))
            continue
        folder = expand(loop.repos[name])
        if not folder.is_dir():
            problems.append(ConfigError(where, f"repos.{name}", "no such folder"))
            continue
        top = top_of(folder)
        if top is None:
            problems.append(ConfigError(where, f"repos.{name}", "is not in a git repository. Make it one (git init), or place the repository it belongs to"))
            continue
        folders[name] = folder
        # Two names in one repository are one repository, committed once.
        if top not in tops:
            tops.append(top)
    places: dict[str, Path] = {}
    for name in bundle.places:
        if name not in loop.places:
            problems.append(ConfigError(where, f"places.{name}", f"{bundle.name} names the folder {name}, and this loop does not say where it is"))
            continue
        places[name] = expand(loop.places[name])
        if not places[name].exists():
            problems.append(ConfigError(where, f"places.{name}", "no such folder"))
    return Placed(tops, folders, bundle.tracker.server, places), problems


# Writing

def save_json(path: Path, data: dict) -> None:
    """The whole file to a temporary one beside it, then renamed over it: a crash leaves the old
    file, not half of a new one. Keys keep their order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as out:
            out.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def save_settings(raw: dict, path: Path | None = None) -> Settings:
    """Checks the settings, then writes them; a change that would not load is never written."""
    path = path or paths().settings
    settings = parse_settings(raw, path)
    save_json(path, raw)
    return settings


def save_bundle(folder: Path, raw: dict) -> Bundle:
    check_bundle(raw, f"{folder.name}/project.json", folder.name)
    save_json(folder / "project.json", raw)
    return load_bundle(folder)
