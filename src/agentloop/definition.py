"""A project's definition, read from its tracker's address (item doc-read).

A tracker `<name>.os` on an Overseer server keeps its project's definition in `<name>.project.os`
beside it, which it mounts read-only as `definition` (overseer:project-definition). One read of
`project/definition/definition` through the tracker gives all of it: the description, the
repositories and places each with what it is, the systems and the run settings. The agents' key
reads it; the server refuses it a write.

Each loop keeps the definition it last read in `definition.json` in its folder, so a run still
starts from it when the server cannot be reached; a run with neither does not start.

That copy is the definition the person running the loop has accepted (item doc-change). A read that
differs from it is a change: under the loop's `acceptDefinition` "auto" it is used at once, and the
copy it replaced is kept in `definition-previous.json` until the change is dismissed, for the page
to show what changed; under "ask" it is kept in `definition-pending.json` and the loop runs no more
sessions until it is accepted. Between sessions the loop asks the server whether the definition
document has changed at all, by the version it holds, which costs a 304 while it has not.

A definition can list the project's tools (item tools-fetch): plain files kept beside the definition
document, each with its sha256 and the systems it is for (overseer:definition-files). Those for this
machine's system are fetched into `project/tools/` in the loop's folder, which `{bundle}` names as it
names a bundle's folder, so a brief's `{bundle}/tools/probe.ps1` reads the same either way. A file
whose hash is not the one the definition lists is never written. The hashes are part of the
definition, so a changed tool is a changed definition: its files wait in `project-pending/` while
the change does, and the ones it replaced stay in `project-previous/` until it is dismissed, for
the change shown to hold a diff of each.

The script has no such thing: its projects are `projects/<name>.psd1` and a brief beside it.
"""

from __future__ import annotations

import difflib
import gzip
import hashlib
import json
import os
import shutil
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from agentloop import clones, config

# Where the definition is, read through the tracker: the mount `definition` in `project`, then the
# definition document's own root (overseer:project-definition).
ADDRESS = "project/definition/definition"

# The file in a loop's folder that keeps the definition last read and accepted.
COPY = "definition.json"
# A changed definition waiting to be accepted, under acceptDefinition "ask".
PENDING = "definition-pending.json"
# The accepted definition a change replaced under "auto", until the change is dismissed.
PREVIOUS = "definition-previous.json"
# The folders in a loop's folder that hold the project's tools in `tools/`, fetched for the copy, a
# pending definition and a previous one, as the three files above (item tools-fetch).
FILES = "project"
FILES_PENDING = "project-pending"
FILES_PREVIOUS = "project-previous"


class Unreachable(Exception):
    """The server could not be asked, or could not answer: what the copy kept is for. The text
    names the exception's type, never the address or the key."""


@dataclass
class Loaded:
    """A loop's project, and what the loop should say about where it came from: None when it is
    the definition just read, or a bundle."""

    project: config.Project | None
    note: str | None = None
    # When the copy the project came from was read; None when it was not a copy.
    read_at: str | None = None
    # A changed definition waits to be accepted, and `project` is the one accepted before it.
    pending: bool = False
    # The definition document's version on the server as last asked, for the check between
    # sessions; None when it was not asked or the server would not say.
    tag: str | None = None


def fetch(server: str, key_file: Path, tracker: str, timeout: float = 30.0) -> dict:
    """The tracker's definition, as the shape `project.json` has - `repos` and `places` as
    {"name", "about"} in order, `systems` a list or None, `settings` an object - checked as a
    bundle's project is. Raises Unreachable when the server cannot be asked, and ConfigError when it
    answers with something that is no definition."""
    where = f"{tracker} definition"
    key = key_file.read_text(encoding="utf-8").strip()
    query = urllib.parse.urlencode({"document": tracker, "address": ADDRESS})
    request = urllib.request.Request(f"{server.rstrip('/')}/v1/node?{query}",
                                     headers={"Authorization": f"Bearer {key}", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            body = answer.read()
            if answer.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            name = config.tracker_project(tracker) or "<name>"
            raise config.ConfigError(where, "definition", f"the tracker mounts no definition: {name}.project.os beside it, "
                                     "named in the tracker's definition line") from None
        if error.code in (401, 403):
            raise config.ConfigError(where, "definition", f"the server refused the agents' key ({error.code})") from None
        raise Unreachable(f"the server answered {error.code}") from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as error:
        reason = getattr(error, "reason", error)
        raise Unreachable(f"the server could not be reached ({type(reason).__name__})") from None
    try:
        node = json.loads(body.decode("utf-8"))["node"]
    except (ValueError, KeyError, TypeError):
        raise config.ConfigError(where, "definition", "the server's answer is not a node") from None
    raw = from_node(node, where)
    config.check_project(raw, where)
    return raw


def _child(node: dict, name: str) -> dict | None:
    return next((c for c in node.get("children") or [] if c.get("name") == name), None)


def _value(node: dict | None) -> str:
    """A field's text: its value as the server sends it, `{"String": ...}`; empty when absent."""
    if node is None:
        return ""
    value = (node.get("parameters") or {}).get("value")
    if isinstance(value, dict) and len(value) == 1:
        value = next(iter(value.values()))
    return value if isinstance(value, str) else ""


def _flag(node: dict | None) -> bool:
    """A bool field: true only when the server sends it so, `{"Boolean": true}`; absent is false."""
    if node is None:
        return False
    value = (node.get("parameters") or {}).get("value")
    if isinstance(value, dict) and len(value) == 1:
        value = next(iter(value.values()))
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _tools(node: dict | None) -> list[dict]:
    """The Tools list's entries as {"file", "sha256", "systems"}, in order; systems empty for every
    system, as the definition's own `systems` is. A tool ticked as the setup script has `"setup":
    true` as well (item project-setup) - only then, so that a definition without one keeps the
    version it had before setups were read."""
    tools = []
    for entry in (node or {}).get("children") or []:
        systems = [s.strip() for s in _value(_child(entry, "systems")).split(",") if s.strip()]
        tool = {"file": _value(_child(entry, "file")).strip(), "sha256": _value(_child(entry, "sha256")).strip().lower(),
                "systems": systems}
        if _flag(_child(entry, "setup")):
            tool["setup"] = True
        tools.append(tool)
    return tools


def _entries(node: dict | None, fields: tuple[str, ...] = ("about",)) -> list[dict]:
    """A list's entries as {"name", "about"}, in order; an entry with no name is still one, for
    the checks to name. A field left empty is left out, so that an entry without it reads as it
    did before the field was there."""
    entries = []
    for entry in (node or {}).get("children") or []:
        item = {"name": _value(_child(entry, "name"))}
        for field in fields:
            value = _value(_child(entry, field))
            if value:
                item[field] = value
        entries.append(item)
    return entries


def from_node(node: dict, where: str) -> dict:
    """The definition document's root node, with its subtree, as a project's raw settings."""
    systems = [s.strip() for s in _value(_child(node, "systems")).split(",") if s.strip()]
    settings_text = _value(_child(node, "settings")).strip()
    try:
        settings = json.loads(settings_text) if settings_text else {}
    except json.JSONDecodeError as error:
        raise config.ConfigError(where, "settings", f"not JSON: {error.msg}") from None
    raw = {
        "description": _value(_child(node, "description")),
        # A repository may name where it is cloned from, and its branch (item clone).
        "repos": _entries(_child(node, "Repos"), ("about", "url", "branch")),
        "places": _entries(_child(node, "Places")),
        # Empty means every system, as a bundle's absent `systems` does.
        "systems": systems or None,
        "settings": settings,
    }
    tools = _tools(_child(node, "Tools"))
    # Only when there are any: a definition with none keeps the version it had before tools were
    # read, so a loop's copy is no change after an upgrade.
    if tools:
        raw["tools"] = tools
    return raw


def version(raw: dict) -> str:
    """The version of a definition as read: a hash of what it holds. The node read carries no
    document version; `source_tag` asks for the definition document's, which says only whether
    to read it again."""
    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def project(tracker: str, raw: dict, read_version: str | None = None, loop_dir: Path | None = None) -> config.Project:
    """A project from a checked definition. Given the loop's folder, its tools are the ones fetched
    there for the copy, when it lists any for this system."""
    repos, about = config.entry_names(raw["repos"])
    places, place_about = config.entry_names(raw.get("places"))
    return config.Project(
        name=config.tracker_project(tracker) or tracker,
        folder=None,
        tracker=config.Tracker(server=tracker),
        repos=repos,
        settings=raw.get("settings") or {},
        raw=raw,
        systems=list(raw["systems"]) if raw.get("systems") else None,
        places=places,
        about={**about, **place_about},
        description=raw.get("description") or "",
        source="tracker",
        version=read_version or version(raw),
        files=loop_dir / FILES if loop_dir is not None and config.tools_here(raw) else None,
    )


# The tools (item tools-fetch)

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# How far into a file a NUL byte is looked for to call it binary, as git does.
BINARY_SNIFF = 8000


def _binary(data: bytes) -> bool:
    return b"\0" in data[:BINARY_SNIFF]


def _normalised(data: bytes) -> bytes:
    """A text file's bytes with CRLF and a lone CR read as LF; a binary one's, one with a NUL in
    its first few KB, as they are (item tools-eol)."""
    if _binary(data):
        return data
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _crlf(data: bytes) -> bytes:
    """A text file's bytes with CRLF line endings; a binary one's as they are."""
    return data if _binary(data) else _normalised(data).replace(b"\n", b"\r\n")


def tool_sha256(data: bytes) -> str:
    """A tool's sha256 as a definition lists it: of its bytes with their line endings normalised,
    so a copy that went through git on another clone, with its line endings rewritten, is still
    the file the owner reviewed (item tools-eol)."""
    return _sha256(_normalised(data))


def tool_matches(data: bytes, sha256: str) -> bool:
    """Whether a tool's bytes are the file a definition lists by `sha256`: its normalised hash; or,
    as definitions exported before item tools-eol list it, the hash of the bundle's copy as it was,
    which had LF or CRLF - so the hash of this text with either is taken too."""
    return sha256 in (tool_sha256(data), _sha256(data), _sha256(_crlf(data)))


def file_getter(server: str, key_file: Path, tracker: str, timeout: float = 30.0) -> Callable[[str], bytes]:
    """What fetches one of a definition's tools by its path: `GET /v1/file` on the definition
    document, with the agents' key, which a read share is enough for (overseer:definition-files).
    A file the server does not keep, or will not give, is a ConfigError naming the file; a server
    that cannot be asked is Unreachable. Neither names the address or the key."""
    where = f"{tracker} definition"
    doc = document(tracker)

    def get(path: str) -> bytes:
        key = key_file.read_text(encoding="utf-8").strip()
        query = urllib.parse.urlencode({"doc": doc, "path": path})
        request = urllib.request.Request(f"{server.rstrip('/')}/v1/file?{query}", headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as answer:
                return answer.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                if _refused_by_overseer(error):
                    raise config.ConfigError(where, "tools", f"lists {path}, and the server keeps no such file: it looks "
                                             f"for {kept_path(doc, path)}, beside {doc}") from None
                raise config.ConfigError(where, "tools", f"the server has no /v1/file to give {path}: it is older than "
                                         "overseer:definition-files, or something in front of it answered") from None
            if error.code in (400, 401, 403):
                raise config.ConfigError(where, "tools", f"the server would not give {path} ({error.code})") from None
            raise Unreachable(f"the server answered {error.code}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as error:
            reason = getattr(error, "reason", error)
            raise Unreachable(f"the server could not be reached ({type(reason).__name__})") from None
    return get


def _refused_by_overseer(error: urllib.error.HTTPError) -> bool:
    """Whether a 404 is Overseer's own refusal - a JSON body with an `error` - rather than a bare
    404 from a server with no such route (one older than overseer:definition-files) or from
    something in front of it. Only the former means the file itself is missing."""
    try:
        body = json.loads(error.read(64 * 1024).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(body, dict) and "error" in body


def kept_path(doc: str, path: str) -> str:
    """Where the server keeps a file listed by the definition document `doc`, relative to its root:
    `tools/<document stem>/` beside the document (`kept_folder` in Overseer's app_api.rs). These
    are the server's names, so they are joined as text, not as this machine's paths."""
    folder, _, leaf = doc.rpartition("/")
    stem = leaf[: -len(".os")] if leaf.endswith(".os") else leaf
    return "/".join(part for part in (folder, "tools", stem, path) if part)


def _matching(path: Path, sha256: str) -> bytes | None:
    """A file's bytes when it is there and has that hash; else None."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data if tool_matches(data, sha256) else None


def tool_problems(raw: dict, folder: Path) -> list[str]:
    """What is wrong with the tools fetched into `folder` for a definition: each file it lists for
    this system that is missing there, or is not the file it lists. Empty when all are right."""
    problems = []
    for tool in config.tools_here(raw):
        path = folder / "tools" / tool["file"]
        if not path.is_file():
            problems.append(f"{tool['file']} is missing")
        elif _matching(path, tool["sha256"]) is None:
            problems.append(f"{tool['file']} is not the file the definition lists")
    return problems


def _files_in(folder: Path) -> list[str]:
    """The files under a folder, as paths relative to it with `/`."""
    if not folder.is_dir():
        return []
    return [p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()]


def _remove_tree(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)


def _rename(source: Path, dest: Path) -> None:
    """A folder put in another's place, or that place left empty when there is no folder."""
    _remove_tree(dest)
    if source.is_dir():
        os.replace(source, dest)


def place_tools(raw: dict, dest: Path, get: Callable[[str], bytes] | None, reuse: tuple[Path, ...] = (),
                where: str = "definition") -> None:
    """The tools a definition lists for this system, in `dest/tools/`, and nothing else there; no
    `dest` at all when it lists none. A file already in `dest` or one of the `reuse` folders with
    the right hash is copied, not fetched again; the rest are fetched with `get`. A fetched file
    whose sha256 is not the one listed is a ConfigError naming it, and nothing is written: the files
    are put together beside `dest` and swapped in whole, so `dest` is the old set or the new one."""
    wanted = config.tools_here(raw)
    if not wanted:
        _remove_tree(dest)
        return
    if not tool_problems(raw, dest) and sorted(_files_in(dest / "tools")) == sorted(t["file"] for t in wanted):
        return
    staging = dest.with_name(dest.name + ".new")
    _remove_tree(staging)
    try:
        for tool in wanted:
            data = None
            for folder in (dest, *reuse):
                data = _matching(folder / "tools" / tool["file"], tool["sha256"])
                if data is not None:
                    break
            if data is None:
                if get is None:
                    raise Unreachable("no server to fetch the tools from")
                data = get(tool["file"])
                if not tool_matches(data, tool["sha256"]):
                    raise config.ConfigError(where, "tools", f"{tool['file']} on the server is not the file the definition "
                                             "lists (its sha256 differs), so it is not used")
            target = staging / "tools" / tool["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_as_written(tool["file"], data))
            if data.startswith(b"#!") and os.name != "nt":
                # A script that names its interpreter runs as itself, as it would from a checkout.
                target.chmod(0o755)
    except BaseException:
        _remove_tree(staging)
        raise
    _rename(staging, dest)


def _as_written(name: str, data: bytes) -> bytes:
    """A fetched tool's bytes as written to this machine: as the server gave them, but for a batch
    file on Windows, which is given CRLF - cmd.exe misreads labels and `goto` in one with LF only.
    PowerShell, Python, node and the rest read either, so nothing else is rewritten. The hash is
    checked on the normalised bytes, so the copy still matches the definition (item tools-eol)."""
    if os.name != "nt" or Path(name).suffix.lower() not in (".cmd", ".bat"):
        return data
    return _crlf(data)


# The copy kept

def keep(loop_dir: Path, tracker: str, raw: dict, read_at: str | None = None, name: str = COPY) -> None:
    """The definition just read, written whole as the loop's copy - or, by `name`, as its pending
    one."""
    config.save_json(loop_dir / name, {
        "tracker": tracker,
        "version": version(raw),
        "read_at": read_at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "definition": raw,
    })


def kept(loop_dir: Path, tracker: str) -> dict | None:
    """The copy the loop keeps of its tracker's definition - {"tracker", "version", "read_at",
    "definition"} - or None when there is none, it is of another tracker, or it no longer passes
    the checks."""
    return _held(loop_dir / COPY, tracker)


def _held(path: Path, tracker: str) -> dict | None:
    """A copy as `keep` writes it, or None when there is none, it is of another tracker, or it no
    longer passes the checks."""
    if not path.is_file():
        return None
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(held, dict) or held.get("tracker") != tracker or not isinstance(held.get("definition"), dict):
            return None
        config.check_project(held["definition"], f"{tracker} definition")
    except (OSError, ValueError, config.ConfigError):
        return None
    return held


def project_for(machine: config.Machine, loop: config.Loop, loop_dir: Path, *, read: bool = True,
                timeout: float = 30.0, worker: str | None = None) -> Loaded:
    """The project a loop works. A bundle's, from the machine. A tracker's: with `read`, the
    definition read from the server and settled against the copy accepted (`settle`), or the copy
    when the server cannot be reached, saying so; without, only the copy - the page's, which never
    waits on the network - or none. A change waiting to be accepted leaves the project the one
    accepted before it, with `pending` set.

    A definition that the server gives and that fails its checks is a ConfigError even when a copy
    is kept: a wrong definition is not an outage, and is to be put right on the server."""
    if loop.tracker is None:
        found = machine.bundle(loop.project)
        if found is None:
            raise config.ConfigError(f'loop "{loop.name}"', "project", f'no bundle named "{loop.project}" in bundles')
        return Loaded(found)
    tracker = loop.tracker
    held = kept(loop_dir, tracker)
    waiting = pending(loop_dir, tracker) is not None
    if not read:
        if held is None:
            return Loaded(None)
        return Loaded(project(tracker, held["definition"], held.get("version"), loop_dir), read_at=held.get("read_at"),
                      pending=waiting)
    worker_name = worker or loop.worker
    overseer = config.overseer_for(machine.settings, loop, worker_name)
    key = config.expand(overseer["keyFile"]) if overseer else None
    if not overseer or not key.is_file():
        if loop.overseer:
            raise config.ConfigError(f'loop "{loop.name}"', "overseer.keyFile", f"{loop.project}'s definition is on a server, and "
                                     "the loop's key file is not there")
        raise config.ConfigError(f'loop "{loop.name}"', "worker", f"{loop.project}'s definition is on a server, and worker "
                                 f"{worker_name} names no file holding the agents' key")
    server = str(overseer["server"])
    try:
        # The version first, so that a change made between the two reads is seen at the next check.
        _, tag = source_tag(server, key, tracker, None, timeout)
        raw = fetch(server, key, tracker, timeout)
        settled = settle(loop_dir, tracker, raw, config.accept_mode(machine, loop), file_getter(server, key, tracker, timeout))
    except Unreachable as why:
        if held is None:
            raise config.ConfigError(f"{tracker} definition", "", f"could not be read ({why}), and this loop keeps no copy "
                                     "of it, so the run does not start") from None
        # The copy's tools are the ones fetched with it: one missing, or changed on this machine,
        # cannot be put right without the server (item tools-fetch).
        wrong = tool_problems(held["definition"], loop_dir / FILES)
        if wrong:
            raise config.ConfigError(f"{tracker} definition", "tools", f"could not be read ({why}), and of the tools fetched "
                                     f"with the copy kept, {'; '.join(wrong)}, so the run does not start") from None
        note = (f"{loop.project}'s definition could not be read ({why}), so this run starts from the copy read "
                f"{held.get('read_at') or 'before'}.")
        return Loaded(project(tracker, held["definition"], held.get("version"), loop_dir), note, held.get("read_at"),
                      pending=waiting)
    if settled == "pending":
        held = kept(loop_dir, tracker)
        assert held is not None
        return Loaded(project(tracker, held["definition"], held.get("version"), loop_dir), read_at=held.get("read_at"),
                      pending=True, tag=tag)
    note = None
    if settled == "changed":
        note = (f"{loop.project}'s definition has changed since this loop last read it, and this run is on the new one "
                f"(acceptDefinition is auto). agentloop show-definition {loop.name} shows what changed.")
    return Loaded(project(tracker, raw, loop_dir=loop_dir), note, tag=tag)


# A change, seen and accepted (item doc-change)

def document(tracker: str) -> str:
    """The definition document's name on the server: `<name>.project.os` beside the tracker, as
    the tracker's definition line names it (overseer:project-definition)."""
    folder, _, leaf = tracker.replace("\\", "/").rpartition("/")
    name = leaf[: -len(".os")] if leaf.endswith(".os") else leaf
    return f"{folder}/{name}.project.os" if folder else f"{name}.project.os"


def source_tag(server: str, key_file: Path, tracker: str, since: str | None, timeout: float = 30.0) -> tuple[bool, str | None]:
    """Whether the definition document may have changed since the version `since`, and its version
    now: (False, since) when the server answers 304, (True, its version) when it sends the text.
    (True, None) when it will not say - an older server, a document under another name - for the
    caller to read the definition itself. Raises Unreachable as `fetch` does."""
    key = key_file.read_text(encoding="utf-8").strip()
    query = {"document": document(tracker)}
    if since:
        query["since"] = since
    request = urllib.request.Request(f"{server.rstrip('/')}/v1/source?{urllib.parse.urlencode(query)}",
                                     headers={"Authorization": f"Bearer {key}", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            # Only that the text was sent, and its version, are wanted.
            answer.read()
            etag = answer.headers.get("ETag")
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return False, since
        return True, None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as error:
        reason = getattr(error, "reason", error)
        raise Unreachable(f"the server could not be reached ({type(reason).__name__})") from None
    tag = etag.strip().removeprefix("W/").strip('"') if etag else None
    return True, tag or None


def pending(loop_dir: Path, tracker: str) -> dict | None:
    """The changed definition waiting to be accepted, as `kept` gives the copy; None when none is."""
    return _held(loop_dir / PENDING, tracker)


def previous(loop_dir: Path, tracker: str) -> dict | None:
    """The definition a change used at once replaced, until the change is dismissed."""
    return _held(loop_dir / PREVIOUS, tracker)


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def settle(loop_dir: Path, tracker: str, raw: dict, mode: str, get: Callable[[str], bytes] | None = None) -> str:
    """A definition just read, against the copy accepted:
    - "first" when there was no copy, and it is kept: starting the loop is agreeing to it;
    - "same" when it is the copy's;
    - "changed" when it differs and `mode` is "auto": the read is kept, and the copy it replaced is
      kept as the previous one - the oldest not yet dismissed, so the change shown is all of it;
    - "pending" when it differs and `mode` is "ask": the read is kept as the pending one, and the
      copy left as it is.
    Changed means by a hash of what it holds (`version`), so a document written again with the same
    definition is no change.

    The tools it lists for this system are fetched with `get` beside the copy they go with (item
    tools-fetch), before any copy is written: a tool that cannot be fetched, or is not the file
    listed, leaves everything as it was, and raises as `place_tools` does."""
    where = f"{tracker} definition"
    files, files_pending, files_previous = loop_dir / FILES, loop_dir / FILES_PENDING, loop_dir / FILES_PREVIOUS
    held = kept(loop_dir, tracker)
    if held is None:
        place_tools(raw, files, get, where=where)
        keep(loop_dir, tracker, raw)
        _remove(loop_dir / PENDING)
        _remove(loop_dir / PREVIOUS)
        _remove_tree(files_pending)
        _remove_tree(files_previous)
        return "first"
    if version(raw) == held.get("version"):
        # Fetched again only when one is missing or was changed on this machine.
        place_tools(raw, files, get, (files_pending,), where=where)
        # A change put back before it was accepted is no change any more.
        _remove(loop_dir / PENDING)
        _remove_tree(files_pending)
        return "same"
    if mode == "ask":
        place_tools(raw, files_pending, get, (files,), where=where)
        waiting = pending(loop_dir, tracker)
        if waiting is None or waiting.get("version") != version(raw):
            keep(loop_dir, tracker, raw, name=PENDING)
        return "pending"
    upcoming = loop_dir / (FILES + "-next")
    place_tools(raw, upcoming, get, (files, files_pending), where=where)
    before = previous(loop_dir, tracker)
    if before is None:
        config.save_json(loop_dir / PREVIOUS, held)
        _rename(files, files_previous)
    keep(loop_dir, tracker, raw)
    _rename(upcoming, files)
    _remove(loop_dir / PENDING)
    _remove_tree(files_pending)
    if before is not None and before.get("version") == version(raw):
        _remove(loop_dir / PREVIOUS)
        _remove_tree(files_previous)
    return "changed"


def accept(loop_dir: Path, tracker: str) -> str | None:
    """The change accepted: "accepted" when one was waiting, and it is the copy now; "dismissed"
    when one was used at once and is no longer to be shown; None when there was nothing to accept.
    A loop that is running watches the files, and goes on."""
    if pending(loop_dir, tracker) is not None:
        # The tools first: a loop that sees the copy change finds the files it lists in place.
        _rename(loop_dir / FILES_PENDING, loop_dir / FILES)
        os.replace(loop_dir / PENDING, loop_dir / COPY)
        _remove(loop_dir / PREVIOUS)
        _remove_tree(loop_dir / FILES_PREVIOUS)
        return "accepted"
    _remove(loop_dir / PENDING)
    _remove_tree(loop_dir / FILES_PENDING)
    if (loop_dir / PREVIOUS).exists():
        _remove(loop_dir / PREVIOUS)
        _remove_tree(loop_dir / FILES_PREVIOUS)
        return "dismissed"
    return None


def change(loop_dir: Path, tracker: str) -> dict | None:
    """The change to show, or None: {"pending", "read_at", "diff"}. Pending when it waits to be
    accepted, its diff from the copy; else one used at once, its diff from the copy it replaced."""
    held = kept(loop_dir, tracker)
    if held is None:
        return None
    waiting = pending(loop_dir, tracker)
    if waiting is not None:
        return {"pending": True, "read_at": waiting.get("read_at"),
                "diff": diff(held["definition"], waiting["definition"], loop_dir / FILES, loop_dir / FILES_PENDING)}
    before = previous(loop_dir, tracker)
    if before is not None:
        return {"pending": False, "read_at": held.get("read_at"),
                "diff": diff(before["definition"], held["definition"], loop_dir / FILES_PREVIOUS, loop_dir / FILES)}
    return None


def _by_name(entries: list | None) -> dict[str, str]:
    return {e["name"]: e.get("about", "") for e in entries or [] if isinstance(e, dict)}


def _entries_diff(old: list | None, new: list | None) -> dict:
    before, after = _by_name(old), _by_name(new)
    return {
        "added": [{"name": n, "about": after[n]} for n in after if n not in before],
        "removed": [{"name": n, "about": before[n]} for n in before if n not in after],
        "changed": [{"name": n, "old": before[n], "new": after[n]} for n in after if n in before and before[n] != after[n]],
    }


def _text(path: Path | None) -> list[str] | None:
    """A tool's lines, when it is there and is text."""
    if path is None:
        return None
    try:
        return path.read_bytes().decode("utf-8").replace("\r\n", "\n").splitlines()
    except (OSError, UnicodeDecodeError):
        return None


def _same_tool(a: Path | None, b: Path | None) -> bool:
    """Whether two fetched copies of a tool are the same file, line endings set aside."""
    if a is None or b is None:
        return False
    try:
        return _normalised(a.read_bytes()) == _normalised(b.read_bytes())
    except OSError:
        return False


def _tools_diff(old: list | None, new: list | None, old_files: Path | None, new_files: Path | None) -> dict:
    """The tools added, removed, and changed - in their contents or the systems they are for. A
    changed file's `lines` are a unified diff of its text, when both versions were fetched to this
    machine and are text; else None, and only that it changed is said."""
    before = {t["file"]: t for t in old or [] if isinstance(t, dict)}
    after = {t["file"]: t for t in new or [] if isinstance(t, dict)}
    changed = []
    for name, tool in after.items():
        was = before.get(name)
        setup_changed = bool(was and was.get("setup")) != bool(tool.get("setup"))
        if was is None or (was.get("sha256") == tool.get("sha256") and was.get("systems") == tool.get("systems")
                           and not setup_changed):
            continue
        contents = was.get("sha256") != tool.get("sha256")
        if contents and _same_tool(old_files / "tools" / name if old_files else None,
                                   new_files / "tools" / name if new_files else None):
            # Listed by another hash - the raw one before, the normalised one now - but the same
            # file once line endings are set aside: nothing to review (item tools-eol).
            contents = False
            if was.get("systems") == tool.get("systems") and not setup_changed:
                continue
        lines = None
        if contents:
            a = _text(old_files / "tools" / name if old_files else None)
            b = _text(new_files / "tools" / name if new_files else None)
            if a is not None and b is not None:
                lines = list(difflib.unified_diff(a, b, lineterm="", n=2))[2:]
        changed.append({"file": name, "contents": contents, "old_systems": was.get("systems") or [],
                        "new_systems": tool.get("systems") or [], "lines": lines,
                        "old_setup": bool(was.get("setup")), "new_setup": bool(tool.get("setup"))})
    return {
        # A setup script says so; any other tool reads as it did before setups were read.
        "added": [{"file": n, "systems": t.get("systems") or [], **({"setup": True} if t.get("setup") else {})}
                  for n, t in after.items() if n not in before],
        "removed": [{"file": n, "systems": t.get("systems") or [], **({"setup": True} if t.get("setup") else {})}
                    for n, t in before.items() if n not in after],
        "changed": changed,
    }


def diff(old: dict, new: dict, old_files: Path | None = None, new_files: Path | None = None) -> dict:
    """What changed from one definition to another: the description as a unified diff's lines; the
    repositories and places added, removed, and changed in what each is; the systems before and
    after (None for every system), or None when they are the same; the settings added, removed and
    changed, by key; the tools added, removed and changed, a changed one's text diffed from the
    folders each definition's tools were fetched into (`_tools_diff`)."""
    lines = list(difflib.unified_diff((old.get("description") or "").splitlines(),
                                      (new.get("description") or "").splitlines(), lineterm="", n=2))
    old_settings, new_settings = old.get("settings") or {}, new.get("settings") or {}
    old_systems, new_systems = old.get("systems") or None, new.get("systems") or None
    return {
        # Past the two header lines, which name no files: the hunks are what is read.
        "description": lines[2:],
        "repos": _entries_diff(old.get("repos"), new.get("repos")),
        "places": _entries_diff(old.get("places"), new.get("places")),
        "systems": None if old_systems == new_systems else {"old": old_systems, "new": new_systems},
        "tools": _tools_diff(old.get("tools"), new.get("tools"), old_files, new_files),
        "settings": {
            "added": {k: v for k, v in new_settings.items() if k not in old_settings},
            "removed": {k: v for k, v in old_settings.items() if k not in new_settings},
            "changed": {k: {"old": old_settings[k], "new": v} for k, v in new_settings.items()
                        if k in old_settings and old_settings[k] != v},
        },
    }


def diff_lines(found: dict) -> list[str]:
    """A diff as lines to print, as `agentloop show-definition` prints it; empty when nothing in it
    changed."""
    out: list[str] = []
    if found["description"]:
        out.append("description:")
        out += [f"  {line}" for line in found["description"]]
    for part, what in (("repos", "repository"), ("places", "place")):
        d = found[part]
        for e in d["added"]:
            out.append(f"+ {what} {e['name']}" + (f" - {e['about']}" if e["about"] else ""))
        for e in d["removed"]:
            out.append(f"- {what} {e['name']}")
        for e in d["changed"]:
            out.append(f"~ {what} {e['name']}: {e['old'] or '(nothing said)'} -> {e['new'] or '(nothing said)'}")
    if found["systems"]:
        def said(systems):
            return ", ".join(systems) if systems else "every system"
        out.append(f"~ systems: {said(found['systems']['old'])} -> {said(found['systems']['new'])}")
    s = found["settings"]
    out += [f"+ setting {k} = {json.dumps(v)}" for k, v in s["added"].items()]
    out += [f"- setting {k} (was {json.dumps(v)})" for k, v in s["removed"].items()]
    out += [f"~ setting {k}: {json.dumps(v['old'])} -> {json.dumps(v['new'])}" for k, v in s["changed"].items()]
    tools = found.get("tools") or {"added": [], "removed": [], "changed": []}

    def systems_said(systems: list) -> str:
        return ", ".join(systems) or "every system"
    out += [f"+ tool {t['file']}" + (f" ({systems_said(t['systems'])})" if t["systems"] else "")
            + (", the setup script" if t.get("setup") else "") for t in tools["added"]]
    out += [f"- tool {t['file']}" for t in tools["removed"]]
    for t in tools["changed"]:
        if t["old_systems"] != t["new_systems"]:
            out.append(f"~ tool {t['file']} systems: {systems_said(t['old_systems'])} -> {systems_said(t['new_systems'])}")
        if t.get("old_setup") != t.get("new_setup"):
            out.append(f"~ tool {t['file']} " + ("is now the setup script" if t.get("new_setup") else "is no longer the setup script"))
        if t["contents"]:
            out.append(f"~ tool {t['file']} changed" + (":" if t["lines"] else ""))
            out += [f"  {line}" for line in t["lines"] or []]
    return out


# A loop added from a tracker's address (item loop-from-url)

class Refusals(config.ConfigError):
    """Everything wrong with a loop being added, not only the first, for the page to mark each
    field: the first is this error's own field and text, all of them in `problems`."""

    def __init__(self, problems: list[config.ConfigError]):
        first = problems[0]
        super().__init__(first.where, first.field, first.message)
        self.problems = problems


def summary(description: str, limit: int = 600) -> dict:
    """What the form shows of a project's description before the person reads all of it: its
    first paragraph that is no heading, cut at `limit` characters, and its headings in order."""
    lines = description.replace("\r\n", "\n").split("\n")
    headings = [line.lstrip("#").strip() for line in lines if line.startswith("#") and line.lstrip("#").strip()]
    lead = ""
    for paragraph in "\n".join(lines).split("\n\n"):
        text = paragraph.strip()
        if text and not text.startswith("#"):
            # Plain text, for a line of the form or the terminal: markdown's emphasis and code
            # marks are dropped, the words kept.
            lead = " ".join(text.replace("**", "").replace("__", "").replace("`", "").split())
            break
    if len(lead) > limit:
        lead = lead[: limit - 1].rstrip() + "…"
    return {"lead": lead, "headings": headings}


# What a refusal of a loop being added says it is about.
NEW = "the new loop"


def _reach(settings: config.Settings, address: str, key_file: str | None, worker: str) -> tuple[str, str, str, bool]:
    """(server, keyFile, tracker, own) for a tracker's address: the server the address names, else
    the worker's; the key file given, else the worker's; `own` when either is not the worker's, and
    the loop then carries them itself. Refusals name every field that is wrong."""
    problems: list[config.ConfigError] = []
    found = settings.worker(worker)
    if found is None:
        problems.append(config.ConfigError(NEW, "worker", f"no worker named {worker or '(none)'}"))
    theirs = (found.overseer if found else None) or {}
    try:
        server, tracker = config.parse_address(address)
    except config.ConfigError as error:
        problems.append(error)
        server, tracker = None, ""
    if server is None and tracker and found is not None:
        if theirs.get("server"):
            server = str(theirs["server"])
        else:
            problems.append(config.ConfigError(NEW, "address", f"names no server, and worker {worker} reaches none: "
                                               "give Overseer's whole address of the tracker"))
    key_file = (key_file or "").strip() or theirs.get("keyFile") or ""
    if not key_file:
        problems.append(config.ConfigError(NEW, "keyFile", "the file holding the agents' key for that server"))
    elif not config.expand(key_file).is_file():
        # Looked for, never read here.
        problems.append(config.ConfigError(NEW, "keyFile", "no such file"))
    if problems:
        raise Refusals(problems)
    assert server is not None
    own = server.rstrip("/") != str(theirs.get("server", "")).rstrip("/") or key_file != theirs.get("keyFile")
    return server.rstrip("/"), key_file, tracker, own


def preview(settings: config.Settings, address: str, key_file: str | None, worker: str, timeout: float = 30.0) -> dict:
    """What the person adding a loop is shown once the tracker's definition is read: the
    description's summary, the systems and whether this one is among them, the repositories and
    places each with what it is, and the project's own run settings. Raises Refusals for a wrong
    address, worker or key file, ConfigError for a definition the server will not give, and
    Unreachable when it cannot be asked."""
    server, key, tracker, own = _reach(settings, address, key_file, worker)
    raw = fetch(server, config.expand(key), tracker, timeout)
    found = project(tracker, raw)
    try:
        config.check_system(found)
        system_problem = None
    except config.ConfigError as error:
        system_problem = error.message
    return {
        "tracker": tracker,
        "project": found.name,
        "server": server,
        "keyFile": key,
        "ownServer": own,
        "summary": summary(raw.get("description") or ""),
        "systems": raw.get("systems"),
        "system": config.this_system(),
        "systemProblem": system_problem,
        "repos": raw["repos"],
        "places": raw.get("places") or [],
        # The folder a repository with an address is offered to be cloned under, as
        # `<clones>/<repo>` (item clone).
        "clones": clones.base(found.name).as_posix(),
        "settings": raw.get("settings") or {},
        # The project's tools, each with whether it is for this machine's system and so fetched
        # when the loop is added (item tools-fetch).
        # The setup script says so (item project-setup).
        "tools": [{"file": t["file"], "systems": t["systems"], "here": t in config.tools_here(raw),
                   **({"setup": True} if t.get("setup") else {})}
                  for t in raw.get("tools") or []],
    }


def add(settings: config.Settings, *, name: str, address: str, key_file: str | None, worker: str,
        repos: dict[str, str], places: dict[str, str], run_settings: dict | None = None,
        save: Callable[[dict], None] | None = None, timeout: float = 30.0,
        top_of: Callable[[Path], Path | None] = config.repo_top) -> dict:
    """A loop on a tracker, added from its address: the definition read again, everything checked
    before anything is written - the name free, this system among the project's, each repository
    a folder in a git repository, each place there - then the loop saved by `save` (appended to
    the settings when left out) and the definition kept as its copy, so that it starts from one
    and a later edit is a change (item doc-change). Returns the loop as saved.

    Refusals name every field that is wrong at once; Unreachable is the server not answering."""
    from agentloop import loop as loop_mod  # loop imports this module

    server, key, tracker, own = _reach(settings, address, key_file, worker)
    raw = fetch(server, config.expand(key), tracker, timeout)
    found = project(tracker, raw)
    problems: list[config.ConfigError] = []
    if not isinstance(name, str) or not config.NAME.match(name):
        problems.append(config.ConfigError(NEW, "name", "lower case letters, digits, - and _"))
    elif settings.loop(name) is not None:
        problems.append(config.ConfigError(NEW, "name", f"there is a loop named {name} already"))
    try:
        config.check_system(found)
    except config.ConfigError as error:
        problems.append(error)
    repos = {k: v.strip() for k, v in (repos or {}).items() if isinstance(v, str) and v.strip()}
    places = {k: v.strip() for k, v in (places or {}).items() if isinstance(v, str) and v.strip()}
    for kind, given, named in (("repos", repos, found.repos), ("places", places, found.places)):
        for extra in sorted(set(given) - set(named)):
            problems.append(config.ConfigError(NEW, f"{kind}.{extra}", f"{found.name} names no {extra} among its {kind}"))
    candidate = config.Loop(name if isinstance(name, str) else "", found.name, worker, repos, run_settings or {}, places,
                            tracker=tracker)
    for problem in config.missing(candidate, found, top_of):
        problems.append(config.ConfigError(NEW, problem.field, problem.message))
    try:
        config.check_run_settings(NEW, "settings", run_settings or {})
    except config.ConfigError as error:
        problems.append(error)
    if problems:
        raise Refusals(problems)
    # The tools for this system fetched before anything is saved, so that one the server does not
    # give, or gives otherwise than the definition lists, refuses the add as it refuses a start
    # (item tools-fetch). They go where the loop's copy will be kept.
    home = loop_mod.loop_dir(found.name, name)
    try:
        place_tools(raw, home / FILES, file_getter(server, config.expand(key), tracker, timeout), where=f"{tracker} definition")
    except config.ConfigError as error:
        raise Refusals([config.ConfigError(NEW, "tools", error.message)]) from None

    new: dict = {"name": name, "tracker": tracker, "worker": worker, "repos": repos}
    if places:
        new["places"] = places
    if own:
        new["overseer"] = {"server": server, "keyFile": key}
    if run_settings:
        new["settings"] = run_settings
    if save is None:
        changed = json.loads(json.dumps(settings.raw))
        changed.setdefault("loops", []).append(new)
        config.save_settings(changed, settings.path)
    else:
        save(new)
    keep(home, tracker, raw)
    return new


def clone(settings: config.Settings, address: str, key_file: str | None, worker: str, repo: str, folder: str,
          *, interactive: bool = False, timeout: float = 30.0) -> dict:
    """A repository the tracker's definition names, cloned from the address the definition gives it
    into `folder` - or the folder used as it is, when it is a clone of that address already (item
    clone). The address is the definition's, read again here, never one the caller sends. Answers
    {"folder", "cloned"}; refuses, on `repos.<repo>`, a repository with no address, a folder
    holding anything else and a clone that fails."""
    from agentloop import clones

    server, key, tracker, _own = _reach(settings, address, key_file, worker)
    raw = fetch(server, config.expand(key), tracker, timeout)
    entry = next((e for e in raw["repos"] if e.get("name") == repo), None)
    field = f"repos.{repo}"
    if entry is None:
        raise config.ConfigError(NEW, field, f"{config.tracker_project(tracker) or tracker} names no repository {repo}")
    if not entry.get("url"):
        raise config.ConfigError(NEW, field, f"the definition gives {repo} no address to clone it from")
    if not isinstance(folder, str) or not folder.strip():
        raise config.ConfigError(NEW, field, "the folder to clone it into")
    where = config.expand(folder.strip())
    if not where.is_absolute():
        raise config.ConfigError(NEW, field, "a whole path, from the top of its drive")
    cloned = clones.clone(repo, where, entry["url"], entry.get("branch", ""), interactive=interactive)
    return {"folder": folder.strip(), "cloned": cloned}


# Moving a loop from its bundle to its tracker (item retarget)

def _inside(path: Path, folder: Path) -> bool:
    try:
        return path.resolve().is_relative_to(folder.resolve())
    except OSError:
        return False


def retarget(machine: config.Machine, name: str, address: str | None = None, *, timeout: float = 30.0,
             top_of: Callable[[Path], Path | None] = config.repo_top) -> dict:
    """A loop on a bundle moved to its tracker, keeping this machine's paths: the definition read
    from the tracker - the address given, else the bundle's `tracker.server` - and checked as `add`
    checks a new loop, against the paths the loop has now; then the loop saved with `tracker` in
    place of `project`, the definition kept as its copy with its tools, and the bundle dropped from
    `bundles` once no loop uses it.

    The loops that are copies of it (`like`) work its project, and move with it, each checked and
    given its copy too. A running loop is refused, as is a tracker whose project is not the
    bundle's: the loop's folder - its output, its status, its run lock - is named after the
    project, and would be left behind. Nothing is saved while anything is wrong.

    Returns {"loop", "tracker", "server", "moved" (the loops moved), "bundleDropped", "repoint"
    (each path inside a bundle's folder, as "repos.x" or "places.x", for the bundles are going
    away)}. Refusals name every problem at once; Unreachable is the server not answering."""
    from agentloop import loop as loop_mod  # loop imports this module
    from agentloop import runner

    settings = machine.settings
    where = f'loop "{name}"'
    found = settings.loop(name)
    if found is None:
        raise Refusals([config.ConfigError(where, "name", f"no loop named {name}")])
    if found.like is not None:
        raise Refusals([config.ConfigError(where, "like", f"a copy works the project of the loop it is like: retarget {found.like}")])
    if found.tracker is not None:
        raise Refusals([config.ConfigError(where, "tracker", f"reads its project from {found.tracker} already")])
    bundle = machine.bundle(found.project)
    assert bundle is not None and bundle.folder is not None  # load_machine refuses a loop on no bundle
    moving = [found] + [l for l in settings.loops if l.like == name]

    # The loop's own server, when it has one, reaches the tracker as it reached it before; a name
    # alone is put on it, since `_reach` would otherwise take the worker's.
    address = (address or "").strip() or bundle.tracker.server
    if found.overseer and "://" not in address:
        address = f"{str(found.overseer['server']).rstrip('/')}/doc/{address}"
    key_file = (found.overseer or {}).get("keyFile")
    server, key, tracker, own = _reach(settings, address, key_file, found.worker)

    problems: list[config.ConfigError] = []
    for each in moving:
        if runner.held(each):
            problems.append(config.ConfigError(f'loop "{each.name}"', "running", "is running: stop it first"))
    raw = fetch(server, config.expand(key), tracker, timeout)
    project_found = project(tracker, raw)
    if project_found.name != bundle.name:
        problems.append(config.ConfigError(where, "tracker", f"{tracker} is the project {project_found.name}, and the loop's is "
                                           f"{bundle.name}: its folder and run lock are named after it"))
    try:
        config.check_system(project_found)
    except config.ConfigError as error:
        problems.append(error)
    for each in moving:
        candidate = config.Loop(each.name, project_found.name, each.worker, each.repos, each.settings, each.places,
                                tracker=tracker)
        problems.extend(config.missing(candidate, project_found, top_of))
    if problems:
        raise Refusals(problems)

    # The tools for this system fetched for each loop before anything is saved, as `add` does.
    get = file_getter(server, config.expand(key), tracker, timeout)
    for each in moving:
        try:
            place_tools(raw, loop_mod.loop_dir(project_found.name, each.name) / FILES, get, where=f"{tracker} definition")
        except config.ConfigError as error:
            raise Refusals([config.ConfigError(where, "tools", error.message)]) from None

    changed = json.loads(json.dumps(settings.raw))
    names = {each.name for each in moving}
    for entry in changed.get("loops", []):
        if entry.get("name") == name:
            entry.pop("project", None)
            entry["tracker"] = tracker
            if own:
                entry["overseer"] = {"server": server, "keyFile": key}
        elif entry.get("name") in names:
            # A copy follows the loop it is like; one naming the bundle's project would now differ.
            entry.pop("project", None)
    used = any(l.tracker is None and l.project == bundle.name and l.name not in names for l in settings.loops)
    dropped = False
    if not used:
        kept_bundles = [b for b in changed.get("bundles", []) if config.expand(b).resolve() != bundle.folder.resolve()]
        dropped = len(kept_bundles) != len(changed.get("bundles", []))
        changed["bundles"] = kept_bundles
    config.save_settings(changed, settings.path)
    for each in moving:
        keep(loop_mod.loop_dir(project_found.name, each.name), tracker, raw)

    # A path into any bundle's folder, not only this one's: a loop may name another project's
    # tools (a game naming its engine fork's), and every bundle is leaving.
    folders = [b.folder for b in machine.bundles.values() if b.folder is not None]
    repoint = sorted({f"{kind}.{key_name}"
                      for each in moving
                      for kind, given in (("repos", each.repos), ("places", each.places))
                      for key_name, path in given.items()
                      if any(_inside(config.expand(path), folder) for folder in folders)})
    return {"loop": name, "tracker": tracker, "server": server, "moved": [each.name for each in moving],
            "bundleDropped": dropped, "repoint": repoint}
