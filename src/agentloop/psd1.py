"""Today's `projects/<name>.psd1` files: read, and converted into a bundle, a worker and a loop.

A PowerShell data file is a subset of PowerShell, and the parser reads only what the six files
use: hashtables, arrays, strings, numbers, `$true`, `$false`, `$null` and comments. Anything
else is refused, naming the line, rather than guessed at.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any, Callable

from agentloop import config
from agentloop.config import ConfigError


# Parsing

class _Parser:
    def __init__(self, text: str, where: str):
        self.text = text
        self.where = where
        self.at = 0

    def fail(self, message: str, at: int | None = None) -> ConfigError:
        line = self.text.count("\n", 0, self.at if at is None else at) + 1
        return ConfigError(self.where, f"line {line}", message)

    def skip(self, newlines: bool = True) -> None:
        """Past blanks and comments; past line ends too, unless they separate entries here."""
        text = self.text
        while self.at < len(text):
            c = text[self.at]
            if c in " \t\r" or (c == "\n" and newlines) or c == "﻿":
                self.at += 1
            elif text.startswith("<#", self.at):
                end = text.find("#>", self.at + 2)
                if end < 0:
                    raise self.fail("a <# comment is never closed")
                self.at = end + 2
            elif c == "#":
                end = text.find("\n", self.at)
                self.at = len(text) if end < 0 else end
            elif c == "`" and text.startswith("\n", self.at + 1):
                self.at += 2
            elif c == "`" and text.startswith("\r\n", self.at + 1):
                self.at += 3
            else:
                return

    def peek(self) -> str:
        return self.text[self.at] if self.at < len(self.text) else ""

    def document(self) -> dict:
        self.skip()
        if not self.text.startswith("@{", self.at):
            raise self.fail("a data file holds one hashtable, @{ ... }")
        value = self.hashtable()
        self.skip()
        if self.at < len(self.text):
            raise self.fail("something after the hashtable")
        return value

    def value(self) -> Any:
        """A value, or several separated by commas, which PowerShell makes an array."""
        first = self.single()
        self.skip(newlines=False)
        if self.peek() != ",":
            return first
        items = [first]
        while self.peek() == ",":
            self.at += 1
            self.skip()
            items.append(self.single())
            self.skip(newlines=False)
        return items

    def single(self) -> Any:
        text = self.text
        if text.startswith("@{", self.at):
            return self.hashtable()
        if text.startswith("@(", self.at):
            return self.array()
        c = self.peek()
        if c == "'":
            return self.single_quoted()
        if c == '"':
            return self.double_quoted()
        number = re.compile(r"[-+]?\d+(\.\d+)?(?![\w.])").match(text, self.at)
        if number:
            self.at = number.end()
            return float(number.group()) if number.group(1) else int(number.group())
        word = re.compile(r"\$(true|false|null)\b", re.IGNORECASE).match(text, self.at)
        if word:
            self.at = word.end()
            return {"true": True, "false": False, "null": None}[word.group(1).lower()]
        raise self.fail("only hashtables, arrays, strings, numbers, $true, $false and $null are read here")

    def hashtable(self) -> dict:
        self.at += 2
        result: dict[str, Any] = {}
        seen: set[str] = set()
        while True:
            self.skip()
            if self.peek() == "}":
                self.at += 1
                return result
            if self.peek() == ";":
                self.at += 1
                continue
            start = self.at
            key = self.key()
            if key.lower() in seen:
                raise self.fail(f"the key {key} is given twice", start)
            seen.add(key.lower())
            self.skip(newlines=False)
            if self.peek() != "=":
                raise self.fail(f"= expected after {key}")
            self.at += 1
            self.skip()
            result[key] = self.value()
            self.skip(newlines=False)
            if self.peek() not in (";", "\n", "}"):
                raise self.fail("a ; or a new line expected between entries")

    def key(self) -> str:
        c = self.peek()
        if c == "'":
            return self.single_quoted()
        if c == '"':
            return self.double_quoted()
        bare = re.compile(r"[A-Za-z_][\w-]*").match(self.text, self.at)
        if not bare:
            raise self.fail("a key expected")
        self.at = bare.end()
        return bare.group()

    def array(self) -> list:
        self.at += 2
        items: list[Any] = []
        while True:
            self.skip()
            if self.peek() == ")":
                self.at += 1
                return items
            value = self.value()
            items.extend(value if isinstance(value, list) else [value])
            self.skip(newlines=False)
            if self.peek() not in (";", "\n", ")"):
                raise self.fail(", or ) expected in the array")
            if self.peek() == ";":
                self.at += 1

    def single_quoted(self) -> str:
        out = []
        self.at += 1
        while self.at < len(self.text):
            c = self.text[self.at]
            if c == "'":
                if self.text.startswith("''", self.at):
                    out.append("'")
                    self.at += 2
                    continue
                self.at += 1
                return "".join(out)
            out.append(c)
            self.at += 1
        raise self.fail("a string is never closed")

    def double_quoted(self) -> str:
        escapes = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "`": "`", '"': '"', "$": "$", "'": "'"}
        out = []
        start = self.at
        self.at += 1
        text = self.text
        while self.at < len(text):
            c = text[self.at]
            if c == "`" and self.at + 1 < len(text):
                nxt = text[self.at + 1]
                out.append(escapes.get(nxt, nxt))
                self.at += 2
            elif c == '"':
                if text.startswith('""', self.at):
                    out.append('"')
                    self.at += 2
                    continue
                self.at += 1
                return "".join(out)
            elif c == "$" and re.match(r"[\w{(:?]", text[self.at + 1:self.at + 2]):
                # PowerShell would put a variable's value here, which a data file may not hold.
                raise self.fail("a double-quoted string with a $ in it; a data file cannot hold variables", start)
            else:
                out.append(c)
                self.at += 1
        raise self.fail("a string is never closed", start)


def parse(text: str, where: str = "data file") -> dict:
    return _Parser(text, where).document()


def parse_file(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ConfigError(path.name, "", f"cannot be read ({type(error).__name__})") from None
    return parse(text, path.name)


# Converting

# The keys run-agent-loop.ps1 reads from a .psd1, as the app's settings name them.
_KEYS = {
    "tracker": "Tracker", "server": "Server", "keyfile": "KeyFile", "repos": "Repos", "account": "Account",
    "routes": "routes", "escalation": "escalation", "buildaftertriage": "buildAfterTriage",
    "buildintriageupto": "buildInTriageUpTo",
}


@dataclass
class Imported:
    bundle: Path
    worker: str
    worker_made: bool
    loop: str


def _slash(path: str) -> str:
    return path.replace("\\", "/")


def _repo_name(top: PurePath) -> str:
    name = re.sub(r"[^a-z0-9_-]", "-", top.name.lower()).strip("-")
    return name or "repo"


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def convert(path: Path, data: dict, top_of: Callable[[Path], Path | None] = config.repo_top) -> tuple[dict, dict, dict]:
    """A .psd1's values as a bundle's project.json, a worker and a loop - the worker and loop not
    yet named against any settings. As the start of run-agent-loop.ps1 reads them."""
    where = path.name
    project = path.stem
    if not config.NAME.match(project):
        raise ConfigError(where, "", f'"{project}" is not a project name: lower case letters, digits, - and _')
    values: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() not in _KEYS:
            raise ConfigError(where, key, "not a setting the script reads: " + ", ".join(sorted(set(_KEYS.values()), key=str.lower)))
        values[_KEYS[key.lower()]] = value

    # Repos: each named by the top of its repository, placed at the folder as written.
    repos = values.get("Repos")
    repos = [repos] if isinstance(repos, str) else repos
    if not isinstance(repos, list) or not repos or not all(config._text(r) for r in repos):
        raise ConfigError(where, "Repos", "a list of the repositories' folders")
    names: list[str] = []
    placed: dict[str, str] = {}
    tops: dict[str, Path] = {}

    def name_for(top: Path) -> str:
        for name, known in tops.items():
            if _same(known, top):
                return name
        name, n = _repo_name(top), 2
        while name in tops:
            name, n = f"{_repo_name(top)}-{n}", n + 1
        tops[name] = top
        return name

    for written in repos:
        folder = Path(_slash(written))
        top = top_of(folder) or folder
        name = name_for(top)
        if name not in placed:
            placed[name] = config.home_relative(written)
            names.append(name)

    # The tracker: its name on the server. One that is a file is refused - the app keeps trackers on
    # a server only (item simplify) - rather than guessed at a name there.
    tracker_value = values.get("Tracker")
    if not config._text(tracker_value):
        raise ConfigError(where, "Tracker", "the tracker's name on the server")
    server = values.get("Server")
    if not server:
        raise ConfigError(where, "Tracker", "is a file, and the app works only trackers on an Overseer server: "
                          "put the tracker on the server, and name it and the server in the .psd1 (Tracker, Server, KeyFile)")
    if not config._text(values.get("KeyFile")):
        raise ConfigError(where, "KeyFile", "the tracker is on a server, and no file holding the agents' key is named")
    tracker = {"server": _slash(tracker_value)}
    overseer = {"server": str(server).rstrip("/"), "keyFile": config.home_relative(values["KeyFile"])}

    settings = {key: values[key] for key in ("routes", "escalation", "buildAfterTriage", "buildInTriageUpTo") if key in values}
    if "routes" in settings and isinstance(settings["routes"], dict):
        settings["routes"] = {k.lower(): ({kk.lower(): vv for kk, vv in v.items()} if isinstance(v, dict) else v)
                              for k, v in settings["routes"].items()}
    if isinstance(settings.get("escalation"), dict):
        settings["escalation"] = {k.lower(): v for k, v in settings["escalation"].items()}
    if isinstance(settings.get("buildAfterTriage"), str):
        settings["buildAfterTriage"] = [settings["buildAfterTriage"]]
    bundle = {"format": config.FORMAT, "name": project, "tracker": tracker, "repos": names, "settings": settings}
    config.check_bundle(bundle, where, project)

    # Account: N is ~/.claude-accountN, nothing is 1, and the default ~/.claude is no folder at
    # all - the script's Account handling.
    account = values.get("Account")
    account = "1" if account in (None, "") else str(account)
    claude: str | None = f"~/.claude-account{account}" if re.fullmatch(r"\d+", account) else config.home_relative(account)
    if claude in ("~/.claude", "~/.claude/"):
        claude = None
    worker = {"name": "", "claude": claude, "overseer": overseer}
    loop = {"name": project, "project": project, "worker": "", "repos": placed, "settings": {}}
    return bundle, worker, loop


def _worker_name(claude: str | None, taken: set[str]) -> str:
    if claude is None:
        base = "default"
    else:
        base = PurePath(claude.rstrip("/")).name.lower()
        base = re.sub(r"^\.claude-", "", base).lstrip(".")
        base = re.sub(r"[^a-z0-9_-]", "-", base) or "worker"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def import_psd1(path: Path, settings_path: Path | None = None, replace: bool = False,
                top_of: Callable[[Path], Path | None] = config.repo_top, to: Path | None = None) -> Imported:
    """One .psd1 made a bundle in `<config>/bundles/<name>`, or in `<to>/<name>` when given, with its
    brief and tools, and a worker and a loop in this machine's settings. Everything is checked
    before anything is written."""
    settings_path = settings_path or config.paths().settings
    bundle, worker, loop = convert(path, parse_file(path), top_of)
    name = bundle["name"]
    brief = path.with_suffix(".md")
    if not brief.is_file():
        raise ConfigError(path.name, "", f"no brief beside it, {brief.name}")
    tools = path.parent / name

    current = config.load_settings(settings_path)
    raw = current.raw
    folder = (to.resolve() if to else settings_path.parent / "bundles") / name
    listed = [b for b in raw.setdefault("bundles", []) if config.expand(b).name == name]
    others = [b for b in listed if not _same(config.expand(b), folder)]
    if (others or folder.exists() or current.loop(name)) and not replace:
        found = "a loop" if current.loop(name) else "a bundle"
        raise ConfigError(settings_path.name, "", f'{found} named "{name}" is there already; --replace replaces it')

    # The worker: one on the same Claude account is reused, given the server if it had none.
    workers = raw.setdefault("workers", [])
    match = next((w for w in workers if w.get("claude") == worker["claude"]
                  and (w.get("overseer") is None or worker["overseer"] is None or w.get("overseer") == worker["overseer"])), None)
    if match is not None:
        if match.get("overseer") is None and worker["overseer"] is not None:
            match["overseer"] = worker["overseer"]
        worker_name, made = match["name"], False
    else:
        worker["name"] = _worker_name(worker["claude"], {w.get("name") for w in workers})
        workers.append(worker)
        worker_name, made = worker["name"], True
    loop["worker"] = worker_name

    loops = raw.setdefault("loops", [])
    index = next((i for i, l in enumerate(loops) if l.get("name") == name), None)
    if index is None:
        loops.append(loop)
    else:
        loops[index] = loop
    raw["bundles"] = [b for b in raw["bundles"] if b not in listed] + [config.home_relative(folder)]
    config.parse_settings(raw, settings_path)

    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    config.save_bundle(folder, bundle)
    shutil.copyfile(brief, folder / "brief.md")
    if tools.is_dir():
        # What Python leaves beside a tool it ran is this PC's, not the project's.
        shutil.copytree(tools, folder / "tools", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    config.save_settings(raw, settings_path)
    return Imported(folder, worker_name, made, name)
