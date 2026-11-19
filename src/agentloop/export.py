"""A bundle written out as what goes on the server beside its tracker (item def-export): the
definition document `<name>.project.os` and its tools, so a project moves from its bundle to its
tracker without anyone writing the document by hand.

What is written mirrors the server's own folder, the one the tracker sits in:

    <out>/<name>.project.os                 the definition, an instance of projects/project_definition.os
    <out>/tools/<name>.project/<path>       each tool, where the server keeps a file beside a document

The definition's one description is the tracker's own description, then the brief - less its
opening paragraph, which says the brief comes after the tracker's description, and less its first
table of `{name}` rows, whose lines become each repository's and place's `about`. Nothing here
writes to a server: putting the files there, and pointing the tracker's mount at the document, is
for a person with the owner's key, and the report says how.

The script has no such thing: its projects are `projects/<name>.psd1` and a brief beside it.
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from agentloop import config, definition

# The template a definition is an instance of, as Overseer's library names it.
TEMPLATE = "projects/project_definition.os/definition"

# Escaped as Overseer's own writer escapes a string, as tracker.py's `quoted` does. json.dumps would
# do for most text, but writes \u escapes for some characters, which Overseer's parser does not read.
ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}

# A brief's opening paragraph, which says where it stands beside the tracker's description; the
# definition is one text, so it goes.
OPENING = "The project brief for the tracker"

# A row of the brief's table of folders: | `{name}` | what it is |
ROW = re.compile(r"^\|\s*`\{([a-z0-9_-]+)\}`\s*\|\s*(.*?)\s*\|\s*$")

# What Python leaves in a tools folder, which is no tool.
SKIPPED_DIRS = {"__pycache__"}
SKIPPED_SUFFIXES = {".pyc", ".pyo"}


def quoted(text: str) -> str:
    return '"' + "".join(ESCAPES.get(c, c) for c in text) + '"'


@dataclass
class Brief:
    """A brief split for a definition: what is left of it, and what its table said each folder is."""

    text: str
    about: dict[str, str] = field(default_factory=dict)
    # Each of these is False when the brief had no such part, so the report can say so.
    opening_dropped: bool = False
    table_dropped: bool = False
    # Table rows naming neither a repository nor a place of the project.
    unknown: list[str] = field(default_factory=list)


def _paragraphs_end(lines: list[str], start: int) -> int:
    end = start
    while end < len(lines) and lines[end].strip():
        end += 1
    return end


def split_brief(text: str, names: list[str]) -> Brief:
    """The brief less its opening paragraph and its first table of `{name}` rows, and each row's
    line by its name. The table stays when a row names something that is neither a repository nor
    a place, so that nothing it says is lost; its lines are still taken."""
    lines = text.replace("\r\n", "\n").split("\n")
    found = Brief(text="")
    # The opening paragraph: the first after the title.
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].startswith("# "):
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and lines[j].startswith(OPENING):
            end = _paragraphs_end(lines, j)
            # The blank lines after it go with it.
            while end < len(lines) and not lines[end].strip():
                end += 1
            lines[j:end] = []
            found.opening_dropped = True
    # The first table holding a `{name}` row.
    start = next((k for k, line in enumerate(lines) if ROW.match(line)), None)
    if start is not None:
        while start > 0 and lines[start - 1].startswith("|"):
            start -= 1
        end = start
        while end < len(lines) and lines[end].startswith("|"):
            end += 1
        for line in lines[start:end]:
            match = ROW.match(line)
            if match:
                found.about[match.group(1)] = match.group(2)
                if match.group(1) not in names:
                    found.unknown.append(match.group(1))
        if not found.unknown:
            lines[start:end] = []
            # The blank line that stood before it, so no two are left where it was.
            if start < len(lines) and start > 0 and not lines[start].strip() and not lines[start - 1].strip():
                del lines[start]
            found.table_dropped = True
            _drop_empty_heading(lines, start)
    found.text = "\n".join(lines).strip("\n") + "\n"
    return found


def _drop_empty_heading(lines: list[str], at: int) -> None:
    """The `## ` heading the table stood under, when nothing is left beneath it before the next."""
    heading = at - 1
    while heading >= 0 and not lines[heading].strip():
        heading -= 1
    if heading < 0 or not lines[heading].startswith("## "):
        return
    following = heading + 1
    while following < len(lines) and not lines[following].strip():
        following += 1
    if following == len(lines) or lines[following].startswith("#"):
        del lines[heading:following]


def _tool_files(folder: Path) -> list[str]:
    """Each tool's path under `tools/`, relative with `/`, in sorted order."""
    if not folder.is_dir():
        return []
    files = []
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if not path.is_file() or path.suffix in SKIPPED_SUFFIXES or any(p in SKIPPED_DIRS for p in relative.parts):
            continue
        files.append(relative.as_posix())
    return files


def _entries(name: str, entries: list[tuple[str, str]], indent: str) -> list[str]:
    if not entries:
        return [f"{indent}- {name} {{ }}"]
    lines = [f"{indent}- {name} {{"]
    for entry_name, about in entries:
        lines += [f"{indent}    - {{", f"{indent}        - name = {quoted(entry_name)}"]
        if about:
            lines.append(f"{indent}        - about = {quoted(about)}")
        lines.append(f"{indent}    }}")
    lines.append(f"{indent}}}")
    return lines


def definition_text(description: str, repos: list[tuple[str, str]], places: list[tuple[str, str]],
                    tools: list[tuple[str, str]], systems: list[str] | None, settings: dict) -> str:
    """The definition document's text: `tools` are (file, sha256), each for every system."""
    lines = [f"<{TEMPLATE}> definition {{", f"    - description = {quoted(description)}"]
    lines += _entries("Repos", repos, "    ")
    lines += _entries("Places", places, "    ")
    if tools:
        lines.append("    - Tools {")
        for file, sha256 in tools:
            lines += ["        - {", f"            - file = {quoted(file)}", f"            - sha256 = {quoted(sha256)}",
                      '            - systems = ""', "        }"]
        lines.append("    }")
    lines.append(f"    - systems = {quoted(', '.join(systems or []))}")
    lines.append(f"    - settings = {quoted(json.dumps(settings, ensure_ascii=False) if settings else '')}")
    lines += ["}", ""]
    return "\n".join(lines)


@dataclass
class Report:
    name: str
    tracker: str
    definition: Path
    tools: list[str]
    tools_dir: Path
    # The line the tracker needs in its project block, so its mount reads this document.
    override: str
    # Repositories and places the brief's table gave no line for.
    no_about: list[str]
    brief: Brief

    @property
    def document(self) -> str:
        """The definition document's name on the server, beside the tracker."""
        folder, _, _ = self.tracker.rpartition("/")
        return f"{folder}/{self.name}.project.os" if folder else f"{self.name}.project.os"

    def put_lines(self, server: str | None) -> list[str]:
        """A curl line per tool, for a person with a key that may edit the definition."""
        base = (server or "<server>").rstrip("/")
        out = []
        for path in self.tools:
            query = urllib.parse.urlencode({"doc": self.document, "path": path})
            local = (self.tools_dir / path).as_posix()
            out.append(f'curl -H "Authorization: Bearer $KEY" -X PUT --data-binary "@{local}" "{base}/v1/file?{query}"')
        return out

    def steps(self, server: str | None) -> list[str]:
        """What the person does next, in order, as the CLI prints it."""
        folder = self.tracker.rpartition("/")[0] or "the tracker's folder"
        lines = [
            f"1. Put {self.definition.name} beside {self.tracker.rpartition('/')[2]}, in {folder} on the server.",
            f"2. In {self.tracker.rpartition('/')[2]}, inside its project block, add the line that points its mount at it:",
            f"     {self.override}",
        ]
        if self.tools:
            # The server keeps them in tools/<document stem>/ beside the definition. A copy through
            # git may have its line endings rewritten, which the sha256 sets aside (item tools-eol).
            kept = definition.kept_path(self.document, "")
            lines += [f"3. Put the {len(self.tools)} tool(s) beside it, where the server keeps them: {kept}/. The safer "
                      "way, with KEY set to a key that may edit the definition:"]
            lines += [f"     {line}" for line in self.put_lines(server)]
            lines += [f"   Or copy tools/ as it is into {folder} in the documents repository, so they land in {kept}/. "
                      "git may change their line endings on the way; the sha256 the definition lists is of the text "
                      "with its line endings set aside, so they still match."]
        else:
            lines.append("3. It lists no tools.")
        lines.append("4. Share it with the agents' account to read only.")
        notes = []
        if not self.brief.opening_dropped:
            notes.append("the brief has no opening paragraph to leave out; read the description's start")
        if self.brief.unknown:
            notes.append("the brief's table names " + ", ".join(self.brief.unknown) + ", which the project does not: "
                         "the table was left in the description")
        elif not self.brief.table_dropped:
            notes.append("the brief has no table of {name} rows")
        if self.no_about:
            notes.append("no line says what these are: " + ", ".join(self.no_about))
        if notes:
            lines.append("Check by hand:")
            lines += [f"  - {note}" for note in notes]
        return lines


def tracker_description(server: str, key_file: Path, tracker: str, timeout: float = 30.0) -> str:
    """The tracker's own description, read from the server: one GET /v1/node of
    `project/description`, as `definition.fetch` reads the definition. An error names no address or
    key."""
    where = f"{tracker} description"
    key = key_file.read_text(encoding="utf-8").strip()
    query = urllib.parse.urlencode({"document": tracker, "address": "project/description"})
    request = urllib.request.Request(f"{server.rstrip('/')}/v1/node?{query}",
                                     headers={"Authorization": f"Bearer {key}", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            body = answer.read()
            if answer.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
    except urllib.error.HTTPError as error:
        raise config.ConfigError(where, "description", f"the server answered {error.code}") from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as error:
        reason = getattr(error, "reason", error)
        raise config.ConfigError(where, "description", f"the server could not be reached ({type(reason).__name__})") from None
    try:
        value = json.loads(body.decode("utf-8"))["node"]["parameters"]["value"]
    except (ValueError, KeyError, TypeError):
        raise config.ConfigError(where, "description", "the server's answer is not a node") from None
    if isinstance(value, dict) and len(value) == 1:
        value = next(iter(value.values()))
    if not isinstance(value, str):
        raise config.ConfigError(where, "description", "is not a text")
    return value


def export(bundle: Path, out: Path, description: str) -> Report:
    """Writes the bundle's definition and tools under `out`, which must be empty or missing, and
    says what is left to do. `description` is the tracker's own."""
    project = config.load_bundle(bundle)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise config.ConfigError(str(out), "out", "is not an empty folder; nothing is written over")
    names = project.repos + project.places
    brief_path = project.brief
    brief_text = brief_path.read_text(encoding="utf-8") if brief_path.is_file() else ""
    brief = split_brief(brief_text, names)
    about = {**project.about, **brief.about}
    parts = [p for p in (description.strip(), brief.text.strip()) if p]
    tools_src = project.tools
    files = _tool_files(tools_src) if tools_src else []
    tools = []
    for path in files:
        problem = config.tool_path_problem(path)
        if problem:
            raise config.ConfigError(f"{project.name}/tools", path, problem)
        tools.append((path, definition.tool_sha256((tools_src / path).read_bytes())))
    text = definition_text(
        "\n\n".join(parts) + "\n" if parts else "",
        [(n, about.get(n, "")) for n in project.repos],
        [(n, about.get(n, "")) for n in project.places],
        tools, project.systems, project.settings)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{project.name}.project.os"
    target.write_text(text, encoding="utf-8", newline="\n")
    tools_dir = out / "tools" / f"{project.name}.project"
    for path in files:
        dest = tools_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tools_src / path, dest)
    return Report(
        name=project.name, tracker=project.tracker.server, definition=target, tools=files, tools_dir=tools_dir,
        override=f'- definition (source="{project.name}.project.os/definition", lazy=false)',
        no_about=[n for n in names if not about.get(n)], brief=brief)
