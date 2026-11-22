"""Read and edit a project tracker the way AUTOTASK.md asks, without breaking the file.

    python tracker.py queue [--owner <name>]   what is free to take, in order, and the signals
    python tracker.py next [--prefer <handle>] [--owner <name>]
                                               the first of those, or the signal, as JSON - or the
                                               item preferred, if it is free to take; with --owner,
                                               that user's items come before everything flagged first;
                                               an item mentioning an account (@<name>) in its
                                               title, or at the end of its last comment not by a
                                               loop, is only for the loops signed in as that account
    python tracker.py claim <handle> [--worker <name>]
                                               mark an item as an agent loop's to work - refused
                                               if another loop has it - as JSON, with the name of
                                               the agent at it for the page to show
    python tracker.py release <handle>         let go of it again
    python tracker.py show <handle> [--json]   one item in full, open or closed: its fields, the
                                               items around it - parent, after, what it holds up,
                                               parts, siblings - and its conversation
    python tracker.py about                    the project, as its tracker describes it
    python tracker.py tag [<tag>] [--json]     what the project says about a tag, or which say
                                               anything
    python tracker.py move <handle> <stage> [--complexity c] [--points n] [--note text]
                                               set an open item's stage, how hard it is, how
                                               much work it is, and what the move did
    python tracker.py note <handle> <text>     add a comment to an open item's conversation,
                                               whatever its stage or flags
    python tracker.py ask <handle> <question>  add a question to it, move it to planning and flag
                                               it asked: it waits on a person
    python tracker.py after <handle> <h> ...   add items it waits on
    python tracker.py file <handle> <title> [--labels a,b] [--points N] [--parent h]
                                         [--after a,b] [--note text]   a new item, at filed
                           [--ask question]    ... at planning, flagged asked, the question asked
                           [--into project]    ... in another project's tracker, for work that is
                                               its
    python tracker.py work <handle> --kind k --model m [--moved 'a > b'] [--points n] [--cost $]
                         [--read k] [--written k] [--output k] [--turns n] [--minutes m] [--lines n]
                                               the loop's record of a session on an item
    python tracker.py check                    ask the server for its open list: does it parse
    python tracker.py attach <picture>         keep a picture on the server, and print the
                                               path a comment shows it by: ![what it shows](path)
    python tracker.py projects                 the other projects beside this one, a line each

An item's conversation is a list of comments, each dated and signed: AGENT_AUTHOR signs what this
adds - the loop sets it to the model and effort of the session - and 'agent' when nothing does.
A comment is markdown, and keeps its lines.

Any text - a comment, a question - can be given as @<path> instead, read from that file: text
written with an editor never passes through a shell, which would run what it quotes in backticks
or $( ).

The trackers in one folder of the server are open to each other. An item of another project is named
project:handle - engine:physics, the project being its tracker's file name - and can be read
(show), waited on (after, file --after), and filed into (file --into); nothing else in another
project's tracker is an agent's to change. An item filed --into begins with where it came from,
OVERSEER_ITEM, which the loop sets to the item its session works.

An item's owner is the person it belongs to. One filed - here or --into another project - is given
the owner of OVERSEER_ITEM, the item the session works, if it has one. An item an agent loop is
working on is marked working: the loop claims it once it has picked it, and releases it however the
session ends. queue and next leave a claimed item to the loop that holds it; the other commands do
not refuse one, as the session working it is the one that holds it. On a server, the claim is the
template's claim button pressed, which refuses an item already claimed: one write, so two loops
claiming at once cannot both have it. The claim then writes the item's `worker` - --worker, else
AGENT_AUTHOR - which the page shows on the item while it is worked; release takes both out.

A comment names an item as <#handle>, which the page draws as the item's chip; show adds the
item's title after it - <#handle> (Its title) - so it reads without asking, and leaves code alone.

A tracker either writes the whole project out, or inherits the project template - its root
`<projects/project_template.os/project> project (...) {` holding `- name`, `- description` and the
entries of `- Items {`, `- Labels {` and `- History {` - and leaves the stages and complexities to
the template. The server names the library templates a document inherits (`bases`) and hands
out their text as `library:<path>`, so the template read is the one the server runs with; its
`template_version` must be one this tracker.py knows (TEMPLATE_VERSIONS), or the command stops
saying which is newer.

A tracker is a document on an Overseer server, never a file here: --doc is its name there
(alice/projects/game.os), on the server OVERSEER_URL names, reached as the account whose key is
kept in the file OVERSEER_KEY_FILE names. It is read as written, and each edit is sent as it is
made - a field set, an entry appended - so nothing is committed afterwards and nothing needs a
lock. A comment there is by that account, with what AGENT_AUTHOR names - the model - as its
status; the pictures its comments show are fetched into AGENT_SCRATCH to be opened. With
OVERSEER_CACHE naming a folder, a copy of each tracker read is kept there with its tag, and the
server sends the text again only once it changed. History kept in part files, a week to each, is
read from them too, each part a copy of its own.

Every command takes --doc, the tracker; OVERSEER_TRACKER sets the default. Edits refuse what the
rules forbid an agent: touching an item waiting on
a person - at testing or later, or flagged manual or asked - or on its parts, at parts, beyond a
comment, which any open item takes; an after
that names an item's own part, which it waits on already, or its parent or one above; finishing
or cancelling one, moving one flagged discuss to ready, moving one to ready while its complexity is
still unassigned, moving one to parts that has no open parts, and touching History. Of the flags, an agent sets only two, and only this way: asked, by ask or by
file --ask, and auto, which file sets by itself when the agent loop runs it (AGENTIC_LOOP is set) -
an item filed because a person asked for it is not auto.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT = os.environ.get('OVERSEER_TRACKER')
# The versions of the project template this tracker.py reads: its `template_version`, which
# Overseer raises whenever the stages, complexities, flags or an item's fields change.
TEMPLATE_VERSIONS = (1,)
# How the server names a document of its libraries (overseer:library-source).
LIBRARY_PREFIX = 'library:'

# The order a save writes an entry's fields in: the template's. After them come the item's lists,
# each in the div that folds it away: its conversation, then the agents' work on it.
ORDER = ['added', 'handle', 'owner', 'title', 'stage', 'parent', 'after', 'labels', 'flags', 'points', 'complexity',
         'pinned', 'working', 'touched_at']
TALK = ('Talk', 'Comments', ['at', 'author', 'status', 'body'])
WORK = ('Agents', 'Work', ['at', 'kind', 'model', 'moved', 'points', 'cost', 'read', 'written', 'output',
                           'turns', 'minutes', 'lines'])
KINDS = [TALK, WORK]
# The stages a person moves an item out of.
OWNERS = {'testing', 'later'}
# The stage of an item with nothing left on it but its open parts: the template moves it to testing
# by itself once the last of them closes, so no agent touches it meanwhile.
PARTS = 'parts'
# The stages that close an item, moving it to History: the owner's to pick, never an agent's.
CLOSING = {'finished', 'cancelled'}
PRIORITY = ['ready', 'planning', 'filed']
# The flags that hold an item for a person: no agent takes it, or touches it, while it has one.
HELD = ['manual', 'asked']
# The template's complexity: an item with no complexity line has not been judged yet.
UNASSIGNED = 'unassigned'
# What the template's claim button says when another loop has the item.
TAKEN = 'another agent is working on it'
EMPTY = '[QUEUE_EMPTY] - All actionable agent tasks have been fully cleared.'
FIELD = re.compile(r'^(\s*)- (\w+) = (.*?)\s*$')


class Refused(Exception):
    """The server refused an event the document declares: a button's own refusal, in its words."""


# What Server.call answers a 304 with: the text the caller holds is still the document's.
NOT_MODIFIED = object()


class Server:
    """An Overseer server the trackers are reached through, as the account whose key it holds:
    OVERSEER_URL, and OVERSEER_KEY_FILE, the file the key is kept in - read here and sent only to
    the server, never printed or passed on. The server's errors are sentences naming no path or
    secret, so they are shown as they come."""

    def __init__(self, url, key_file):
        self.url = url.rstrip('/')
        if not key_file or not Path(key_file).is_file():
            sys.exit('OVERSEER_URL names a server, but OVERSEER_KEY_FILE names no file holding the key to reach it with')
        self._key = Path(key_file).read_text(encoding='utf-8').strip()
        self._me = None
        self._people = None

    @classmethod
    def from_environment(cls):
        url = os.environ.get('OVERSEER_URL')
        return cls(url, os.environ.get('OVERSEER_KEY_FILE')) if url else None

    def call(self, method, route, query=None, body=None, refusable=False, tagged=False):
        """One request, answered as JSON; a refusal stops the command, saying what was refused -
        or, `refusable`, a 422 is raised as Refused, for the caller to take as an answer. A 304,
        which only a read naming the version it holds gets, is answered NOT_MODIFIED. `tagged`
        answers (JSON, ETag) instead, the tag unquoted, or None when the server sends none."""
        import gzip
        import urllib.parse
        url = f'{self.url}{route}' + (f'?{urllib.parse.urlencode(query)}' if query else '')
        data = json.dumps(body).encode('utf-8') if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={
            'Authorization': f'Bearer {self._key}', 'Accept-Encoding': 'gzip',
            **({'Content-Type': 'application/json'} if data is not None else {})})
        try:
            with urllib.request.urlopen(request, timeout=120) as answer:
                raw = answer.read()
                if answer.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
                tag = answer.headers.get('ETag')
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return (NOT_MODIFIED, None) if tagged else NOT_MODIFIED
            try:
                raw = e.read()
                if e.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
                said = json.loads(raw.decode('utf-8')).get('error', '')
            except (ValueError, OSError):
                said = ''
            if refusable and e.code == 422:
                raise Refused(said) from None
            sys.exit(f'the server refused {method} {route}: {e.code} {said}'.rstrip())
        except urllib.error.URLError as e:
            sys.exit(f'the server at {self.url} cannot be reached: {e.reason}')
        parsed = json.loads(raw.decode('utf-8')) if raw else None
        if tagged:
            return parsed, tag.strip().removeprefix('W/').strip('"') if tag else None
        return parsed

    def source(self, name):
        """A document's text and version, and `parts`: the part files its lists keep their entries
        in, by name (overseer:histparts). With OVERSEER_CACHE, the copy kept there names its tag
        and the server answers 304 while the text, and which parts there are, are still those - a
        few hundred bytes, where a tracker is most of a megabyte; a server that sends no 304 is
        just read in full. The tag is the answer's ETag, not its version: for a document with
        parts the server folds the part names into the ETag, and a `since` naming the plain
        version gets the whole answer every time. A copy kept before tags were, or from a server
        that sends no ETag, names its version - the ETag of a document without parts. The copy
        keeps the `bases` the server named too - None where it named none - since a 304 hands it
        back in place of the answer; a copy kept before it did is read again whole, else a 304
        would read as a server too old to name them (bases-cache)."""
        copy = self._copy(name)
        held = None
        if copy:
            try:
                held = json.loads(copy.read_text(encoding='utf-8'))
                if not (all(isinstance(held.get(k), str) for k in ('document', 'version', 'text'))
                        and isinstance(held.get('tag', ''), str) and isinstance(held.get('parts', {}), dict)
                        and 'bases' in held):
                    held = None
            except (OSError, ValueError, AttributeError):
                held = None
        if held:
            held.setdefault('parts', {})
            held.setdefault('tag', held['version'])
        query = {'document': name, **({'since': held['tag']} if held else {})}
        answer, tag = self.call('GET', '/v1/source', query, tagged=True)
        if answer is NOT_MODIFIED:
            return held
        answer.setdefault('parts', {})
        # None where the server names no bases: a held copy answers the same.
        answer.setdefault('bases', None)
        answer['tag'] = tag or answer.get('version')
        if copy and isinstance(answer.get('version'), str) and isinstance(answer['tag'], str):
            self._keep(copy, answer)
        return answer

    def _copy(self, name):
        """Where the copy of a document is kept: under OVERSEER_CACHE, in a folder of this server's
        own - a hash of its address, so two servers never share a copy - by the document's name.
        Every loop and account on the machine shares it: the server refuses a reader before it
        compares versions, so a copy gets no one past a refusal."""
        folder = os.environ.get('OVERSEER_CACHE')
        if not folder:
            return None
        # A library document's name holds a `:` - `library:projects/...` - which Windows refuses in a
        # file name; no document name holds a `~`.
        parts = [p.replace(':', '~') for p in name.replace('\\', '/').split('/') if p not in ('', '.', '..')]
        server = hashlib.sha256(self.url.encode('utf-8')).hexdigest()[:16]
        return Path(folder, server, *parts[:-1], parts[-1] + '.json') if parts else None

    @staticmethod
    def _keep(copy, answer):
        """Write a copy whole or not at all: several loops on a machine read and write these, so
        it goes to a file of its own first and is put in place in one step."""
        try:
            copy.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=copy.parent, suffix='.tmp',
                                             delete=False) as f:
                json.dump({k: answer[k] for k in ('document', 'version', 'tag', 'parts', 'text', 'bases')}, f)
            os.replace(f.name, copy)
        except OSError:
            # A copy not kept costs the next read its text, nothing more.
            try:
                os.unlink(f.name)
            except (OSError, NameError):
                pass

    def documents(self):
        return self.call('GET', '/documents').get('documents', [])

    def set(self, name, address, value):
        return self.call('POST', '/v1/node', {'document': name, 'address': address}, {'value': value})

    def append(self, name, address, fields):
        return self.call('POST', '/v1/append', {'document': name, 'address': address}, {'fields': fields})

    def event(self, name, address, event):
        """Press something the document declares - the template's claim button."""
        return self.call('POST', '/v1/event', {'document': name, 'address': address}, {'event': event}, refusable=True)

    def me(self):
        """The username the key acts as - who the comments it adds are by."""
        return self.account()['username']

    def account(self):
        """The account the key acts as, as /account/me answers: its username and display name."""
        if self._me is None:
            self._me = self.call('GET', '/account/me')
        return self._me

    def people(self, t):
        """Everyone the server keeps an account for, as (username, display name): the directory it
        writes at its root (overseer's people.rs), as tracker `t` mounts it - `mount people` in its
        project, which is how a document reaches it; the document API names no file at the root.
        Read once. A tracker that mounts none, or a server that keeps none, gives only the account
        the key acts as: a mention of it still counts."""
        if self._people is None:
            found = []
            try:
                answer = self.call('GET', '/v1/node', {'document': t.name, 'address': f'{t.root()}/people/People'},
                                   refusable=True)
                for entry in (answer or {}).get('node', {}).get('children', []):
                    said = {c.get('name'): written(c) for c in entry.get('children', [])}
                    if said.get('tag'):
                        found.append((said['tag'], said.get('name') or ''))
            except (SystemExit, Refused, AttributeError):
                found = []
            me = self.account()
            if not any(tag == me['username'] for tag, _ in found):
                found.append((me['username'], me.get('name') or ''))
            self._people = found
        return self._people

    def bridge(self, name, command, args):
        """One of the page's own commands - the images, which the document API has no route for."""
        return self.call('POST', f'/api/{command}', None, {'document': name, 'args': args})


def written(node):
    """A string node's value as /v1/node answers it: what it works out to, where it is computed."""
    given = node.get('parameters') or {}
    value = given.get('_computed_value') or given.get('value') or {}
    return value.get('String') if isinstance(value, dict) else None


# How the server is to be told a field's value: its type, by the field's name in the templates;
# anything not named is text.
TYPES = {'added': 'Timestamp', 'at': 'Timestamp', 'finished_at': 'Timestamp', 'touched_at': 'Timestamp',
         'points': 'Integer', 'read': 'Integer', 'written': 'Integer', 'output': 'Integer', 'turns': 'Integer',
         'lines': 'Integer', 'cost': 'Float', 'minutes': 'Float', 'working': 'Boolean', 'pinned': 'Boolean'}
# What a field is when it says nothing of its own - the template's - written to take a value out.
DEFAULTS = {'stage': 'filed', 'complexity': UNASSIGNED, 'standing': 'proposed'}


def typed(name, written):
    """A value as the server takes it, from the way this file writes it: a quoted text, or a
    number as it is; None is the template's own."""
    kind = TYPES.get(name, 'String')
    if written is None:
        return {kind: DEFAULTS.get(name, '')} if kind == 'String' else {kind: False} if kind == 'Boolean' else {kind: 0}
    text = value_of(written)
    if kind == 'Integer':
        return {kind: int(float(text))}
    if kind == 'Float':
        return {kind: float(text)}
    if kind == 'Boolean':
        return {kind: text == 'true'}
    return {kind: text}


def open_tracker(doc):
    """The tracker a --doc names: a document of the server OVERSEER_URL names."""
    if not doc:
        sys.exit('no tracker: give its name on the server with --doc, or in OVERSEER_TRACKER')
    server = Server.from_environment()
    if server is None:
        sys.exit(f'no tracker {doc}: trackers are on an Overseer server, and no OVERSEER_URL names one')
    return Tracker(doc, server)


def check_template_version(version, name):
    """Stop, in one line naming both, where the server's project template is not a version this
    tracker.py knows: a newer one wants a newer AgentLoop, an older one a newer Overseer."""
    if version in TEMPLATE_VERSIONS:
        return
    known = (f'{TEMPLATE_VERSIONS[0]}-{TEMPLATE_VERSIONS[-1]}' if len(TEMPLATE_VERSIONS) > 1
             else str(TEMPLATE_VERSIONS[0]))
    if version is not None and version > TEMPLATE_VERSIONS[-1]:
        sys.exit(f"the server's project template ({name}) is version {version}; this AgentLoop knows "
                 f'{known}: update AgentLoop')
    said = f'version {version}' if version is not None else 'from before template versions'
    sys.exit(f"the server's project template ({name}) is {said}; this AgentLoop knows {known}: "
             "update the server's Overseer")


class Tracker:
    def __init__(self, path, server=None):
        self.server = server
        if server:
            # A document of the server, by its name there: read as written, written field by field.
            answer = server.source(str(path).replace('\\', '/'))
            self.name = answer['document']
            self.path = Path(self.name)
            raw = answer['text']
            self.parts = answer.get('parts') or {}
            # None where the server is older than the one that names them.
            self.bases = answer.get('bases')
        else:
            # Text given whole - a test's - read for its stages and complexities: never written.
            self.name = None
            self.path = Path(path)
            raw = self.path.read_bytes().decode('utf-8')
            self.parts = {}
            self.bases = []
        self.lines = raw.replace('\r\n', '\n').split('\n')
        self._others = {}
        self._closed = None

    def reload(self):
        """Read the tracker again from the server, after a write that this text does not show."""
        answer = self.server.source(self.name)
        self.lines = answer['text'].replace('\r\n', '\n').split('\n')
        self.parts = answer.get('parts') or {}
        self._closed = None

    def closed(self):
        """Every closed item, as (where, first line, last line, fields): those History holds in the
        tracker's own text, then those it keeps in part files, a week to each, in the order the
        server names them (overseer:listparts). `where` is the text an entry is in - this tracker,
        or a part read as one - for its lines mean nothing in any other. Each part is read through
        Server.source, so with OVERSEER_CACHE a past week is a 304. Read once, and only when asked
        for: only what reads a closed item needs the parts."""
        if self._closed is None:
            found = [(self, *e) for e in self.entries('History')]
            for name in self.parts.get('History', []):
                part = Tracker(name, self.server)
                if part.header('History') is not None:
                    found += [(part, *e) for e in part.entries('History')]
            self._closed = found
        return self._closed

    def root_of(self, at):
        """The name of the top-level node line `at` is in - `project`, `features` - the first step
        of an address there."""
        for i in range(at, -1, -1):
            line = self.lines[i]
            if line and not line[0].isspace() and not line.startswith(('//', '}')):
                m = re.match(r'(?:<[^>]*>|[\w-]+)\s+([\w-]+)', line)
                if m:
                    return m.group(1)
        sys.exit(f'no top-level node above line {at + 1} of {self.path.name}')

    def root(self):
        """The name of the document's top-level node - `project` - where a list the document has not
        written yet is addressed from."""
        first = next((i for i, l in enumerate(self.lines)
                      if l and not l[0].isspace() and not l.startswith(('//', '}'))), None)
        if first is None:
            sys.exit(f'no top-level node in {self.path.name}')
        return self.root_of(first)

    def address_at(self, top):
        """The address of the entry whose first line is `top`: `project/Items/[parser]`."""
        for name in ('Items', 'History', 'Features'):
            start = self.header(name)
            if start is None:
                continue
            _, end, _ = self.span(name)
            if start < top < end:
                handle = self.fields(top, next(k for k in range(top, end + 1)
                                               if self.lines[k] == self.lines[top][:-3] + '}')).get('handle')
                return f'{self.root_of(start)}/{name}/[{handle}]'
        sys.exit(f'line {top + 1} of {self.path.name} is in no list of entries')

    def append_entry(self, name, fields):
        """A new entry at the end of a top-level list, from fields as this file writes them. A list
        the template declares is there to append to before the document writes any of it - a first
        feature - and is addressed from the document's root until then."""
        start = self.header(name)
        root = self.root() if start is None else self.root_of(start)
        self.server.append(self.name, f'{root}/{name}', {k: typed(k, v) for k, v in fields.items()})
        self.reload()

    @property
    def project(self):
        """The project's name, as other trackers name it: the document's, without .os."""
        return self.path.stem

    def other(self, project):
        """Another project's tracker - the document of that name beside this one - read once. Every
        tracker in the folder is open to every other: to read, to wait on, and to file into."""
        if project == self.project:
            return self
        if project not in self._others:
            if not re.fullmatch(r'[a-z0-9_-]+', project or ''):
                sys.exit(f'no project {project!r}: a project is named as its tracker is, without .os')
            if project not in self.neighbours():
                sys.exit(f'no project {project}; the projects beside this one are {", ".join(self.neighbours())}')
            path = self.path.parent / f'{project}.os'
            self._others[project] = Tracker(path.as_posix(), self.server)
        return self._others[project]

    def neighbours(self):
        """The other projects whose trackers are beside this one: the documents in its folder on the
        server that the account may read. A project's name has no dot, so a project's definition
        beside its tracker, `<name>.project.os` (agentloop:doc-read), is no project of its own."""
        if not hasattr(self, '_beside'):
            folder = self.path.parent.as_posix()
            self._beside = sorted(Path(n).stem for n in self.server.documents()
                                  if n.endswith('.os') and Path(n).parent.as_posix() == folder
                                  and re.fullmatch(r'[a-z0-9_-]+', Path(n).stem))
        return [p for p in self._beside if p != self.project]

    def resolve(self, handle):
        """(tracker, handle) for a handle, which may name another project's item as project:handle."""
        if ':' in handle:
            project, own = handle.split(':', 1)
            return self.other(project), own
        return self, handle

    def peek(self, handle):
        """As resolve, but (None, handle) for a project that is not there, rather than stopping:
        a tracker naming a project since removed still reads."""
        if ':' not in handle:
            return self, handle
        project, own = handle.split(':', 1)
        if project != self.project and not (re.fullmatch(r'[a-z0-9_-]+', project)
                                            and project in self.neighbours()):
            return None, own
        return self.other(project), own

    def handles(self):
        """Every handle in this tracker, open or closed."""
        return ({f.get('handle') for _, _, f in self.items()} | {f.get('handle') for *_, f in self.closed()}) - {None}

    def is_open(self, handle):
        """Whether a handle - this project's, or project:handle - names an item still open."""
        t, own = self.peek(handle)
        return t is not None and own in {f.get('handle') for _, _, f in t.items()}

    def knows(self, handle):
        """Whether a handle names an item, open or closed, here or - as project:handle - beside."""
        t, own = self.peek(handle)
        return t is not None and own in t.handles()

    def header(self, name):
        """The line a top-level list starts on, or None. A tracker writing the whole project has
        `list Items (entry=<Item>, ...) {`; one inheriting the project template holds only the
        entries, `- Items {` - or `- Items { }`, empty on one line, as a save writes it. A part file
        holds one list at its top, with nothing of the template's: `list History {`. And the server,
        appending to a list the template declares and the document has not written yet - a first
        feature - writes it so inside the root, `    list Features {`."""
        for i, line in enumerate(self.lines):
            own = line.strip()
            if (own.startswith(f'list {name} (')
                    or own in (f'- {name} {{', f'- {name} {{ }}', f'list {name} {{', f'list {name} {{ }}')):
                return i
        return None

    def span(self, name):
        """Where a list starts and ends, and its indent. Found at whatever depth it sits - Items and
        History are each inside an unnamed tab - and ended by the brace at its own indent; a list
        written empty on one line starts and ends there."""
        start = self.header(name)
        if start is None:
            sys.exit(f'no list {name} in {self.path.name}')
        indent = indent_of(self.lines[start])
        if self.lines[start].rstrip().endswith('{ }'):
            return start, start, indent
        end = next(i for i in range(start + 1, len(self.lines)) if self.lines[i] == indent + '}')
        return start, end, indent

    def base(self):
        """The file of the template this tracker inherits - `projects/project_template.os`, from its
        root `<projects/project_template.os/project> project (...) {` - or None if it holds the
        whole project itself."""
        line = next((l.strip() for l in self.lines if l.strip() and not l.strip().startswith('//')), '')
        m = re.match(r'<([^<>]+?\.os)/[^<>]*>', line)
        return m.group(1) if m else None

    def base_tracker(self):
        """The template this tracker inherits, as the server hands it out: the `library:` name its
        answer lists among `bases`, read through Server.source - so a 304 while it is unchanged -
        and checked to be a version this tracker.py knows."""
        ref = self.base()
        if self.bases is None:
            sys.exit(f'{self.path.name} inherits {ref}, and the server at {self.server.url} is too old to hand '
                     'out the templates a document inherits: update its Overseer')
        name = LIBRARY_PREFIX + ref
        if name not in self.bases:
            sys.exit(f'{self.path.name} inherits {ref}, which the server does not have among its library templates')
        base = Tracker(name, self.server)
        check_template_version(base.template_version(), base.path.name)
        return base

    def template_version(self):
        """The `template_version` a template says it is - `int template_version (hidden=true) = 1` -
        or None where it says none, as templates older than the field do."""
        for line in self.lines:
            m = re.match(r'\s*int template_version\b.*=\s*(\d+)\s*$', line)
            if m:
                return int(m.group(1))
        return None

    def declares(self, name):
        """Whether the template this tracker inherits declares a top-level list `name` - Items,
        History, Labels, Features - so that a tracker writing no line of it still has it, empty.
        A tracker holding the whole project, or a template without the list, does not."""
        if not self.base():
            return False
        if not hasattr(self, '_base'):
            self._base = self.base_tracker()
        return self._base.header(name) is not None

    def entries(self, name):
        """The entries of a top-level list, as (first line, last line, fields). A list the tracker
        has not written yet but its template declares - a new project's History or Labels - has
        none; one declared nowhere stops, so a wrong name or document is not read as empty."""
        if self.header(name) is None and self.declares(name):
            return []
        start, end, _ = self.span(name)
        return [(i, j, self.fields(i, j)) for i, j in self.braces(start, end)]

    def braces(self, start, end):
        """The entries between a list's two lines, each as (first line, last line): `- {` and the
        brace that closes it at the same indent. The indent is the first entry's, whatever it is -
        the server has written a week's entries at the depth they had in Items."""
        first = next((i for i in range(start + 1, end) if self.lines[i].strip() == '- {'), None)
        if first is None:
            return []
        own = indent_of(self.lines[first])
        found, i = [], first
        while i < end:
            if self.lines[i] == own + '- {':
                j = next(k for k in range(i, end) if self.lines[k] == own + '}')
                found.append((i, j))
                i = j
            i += 1
        return found

    def fields(self, top, bottom):
        """An entry's own fields: the lines at its own field indent. The comments and session
        records nested in it have fields too - a session's points among them - and they are not
        the entry's. The indent is the shallowest any field in it has, not one step in from the
        entry's brace: the language does not depend on indentation, and the server has moved an
        entry into History with the deeper indent it had in Items."""
        found = [m for m in map(FIELD.match, self.lines[top + 1:bottom]) if m]
        own = min((m.group(1) for m in found), key=len, default='')
        return {m.group(2): value_of(m.group(3)) for m in found if m.group(1) == own}

    def block(self, top, bottom, header):
        """Where a block directly under line `top` starts and ends - `div Talk {` and the brace at
        its indent that closes it - or None. A block written empty on one line, `list Comments { }`,
        starts and ends there. It is the shallowest block of that header between the two lines,
        whatever its indent: an entry's insides are not always one step in from its brace."""
        named = [i for i in range(top + 1, bottom) if self.lines[i].lstrip().startswith(header + ' ')]
        if not named:
            return None
        at = min(named, key=lambda i: len(indent_of(self.lines[i])))
        own = indent_of(self.lines[at])
        if self.lines[at].rstrip().endswith('}'):
            return at, at
        return at, next(i for i in range(at + 1, bottom) if self.lines[i] == own + '}')

    def nested(self, top, bottom, kind):
        """The entries of a list an entry holds in a div - its comments, or its sessions - each as
        its fields, in order."""
        div, name, _ = kind
        outer = self.block(top, bottom, f'div {div}')
        inner = outer and self.block(outer[0], outer[1], f'list {name}')
        if not inner:
            return []
        return [self.fields(i, j) for i, j in self.braces(*inner)]

    def add_nested(self, top, bottom, kind, values):
        """Add an entry at the end of a list the entry at `top` holds. The template gives every entry
        its folds and their lists, so the list is there to append to whether or not the document
        has written it yet."""
        div, name, order = kind
        self.server.append(self.name, f'{self.address_at(top)}/{div}/{name}',
                           {k: typed(k, values[k]) for k in order if k in values})
    def stages(self):
        return self.vocabulary('Stages')

    def complexities(self):
        return self.vocabulary('Complexities')

    def vocabulary(self, name):
        """A list of the project's own words - its stages, its complexities - which a tracker
        inheriting the project template leaves to the template."""
        if self.header(name) is None and self.base():
            if not hasattr(self, '_base'):
                self._base = self.base_tracker()
            return self._base.vocabulary(name)
        start, end, _ = self.span(name)
        return [m.group(1) for l in self.lines[start:end] for m in [re.match(r'\s*- tag = "([^"]*)"', l)] if m]

    def labels(self):
        return [e[2].get('tag') for e in self.entries('Labels')]

    def items(self):
        found = []
        for top, bottom, f in self.entries('Items'):
            f.setdefault('stage', 'filed')
            f.setdefault('complexity', UNASSIGNED)
            f.setdefault('flags', '')
            found.append((top, bottom, f))
        return found

    def item(self, handle):
        hits = [e for e in self.items() if e[2].get('handle') == handle]
        if not hits:
            finished = any(f.get('handle') == handle for *_, f in self.closed())
            sys.exit(f'{handle} is finished - History is not an agent\'s to change' if finished
                     else f'no open item {handle}')
        if len(hits) > 1:
            sys.exit(f'{handle} is on the open list {len(hits)} times - tell the owner rather than guess')
        return hits[0]

    def set_field(self, handle, name, value):
        """Write a field of an open item; None gives it the template's own value."""
        top, _, _ = self.item(handle)
        self.server.set(self.name, f'{self.address_at(top)}/{name}', typed(name, value))


# Escaped as Overseer's own writer escapes a string (escape_string in src-tauri/src/file_ops.rs),
# so a value written here reads the same to its parser, and the owner's next save writes the
# same bytes back. A note about C++ or Windows paths needs both quotes and backslashes.
ESCAPES = {'\\': '\\\\', '"': '\\"', '\n': '\\n', '\r': '\\r', '\t': '\\t'}
UNESCAPES = {'n': '\n', 'r': '\r', 't': '\t', '"': '"', '\\': '\\'}


def quoted(text):
    return '"' + ''.join(ESCAPES.get(c, c) for c in text) + '"'


def value_of(written):
    """A field's value as its line writes it: a string unquoted and unescaped, a number as it is, and
    `null` - which a page emptying a field once wrote, `- flags = null` - as nothing, not the word."""
    if written == 'null':
        return ''
    return unescape(written[1:-1]) if written.startswith('"') and written.endswith('"') else written


def indent_of(line):
    return line[:len(line) - len(line.lstrip())]


def now():
    """The time as the tracker writes one: UTC, to the microsecond, as `file` dates an item."""
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def unescape(text):
    """The other half of quoted, as Overseer's parser reads a string: an escape nobody defined
    keeps both of its characters."""
    out, i = [], 0
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text):
            out.append(UNESCAPES.get(text[i + 1], text[i:i + 2]))
            i += 2
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


def split(text):
    return [t.strip() for t in (text or '').split(',') if t.strip()]


def open_parts(t, handle, items=None):
    """The open items whose parent an item is. `items` is the open list, when the caller has it."""
    items = [f for _, _, f in t.items()] if items is None else items
    return [g['handle'] for g in items if g.get('parent') == handle]


def ancestors(t, handle):
    """An item's parent, its parent's parent, and so on up - each open, since an item cannot be
    finished while a part of it is open."""
    parents = {f.get('handle'): f.get('parent') for _, _, f in t.items()}
    found = []
    while parents.get(handle) and parents[handle] not in found:
        handle = parents[handle]
        found.append(handle)
    return found


def waiting_on(t, item, items=None):
    """What an item still waits on: each handle in its after list that names an open item - in this
    tracker, or as project:handle in another's - and then its open parts. An item comes after its
    parts without naming them: it is done by doing them, so a loop that took it first would have
    nothing to do on it but what its parts already hold."""
    after_ = [h for h in split(item.get('after')) if t.is_open(h)]
    return after_ + [h for h in open_parts(t, item.get('handle'), items) if h not in after_]


def flagged(item, flag):
    return flag in split(item.get('flags'))


def held(item):
    """The flags that hold an item for a person, if it has any."""
    return [flag for flag in HELD if flagged(item, flag)]


#: What a mention ends at: a name followed by a letter, a digit, `-` or `_` is a longer name.
NAME_GOES_ON = re.compile(r'[\w-]')

#: What may stand between and after the mentions a comment ends with: spaces, commas and closing
#: punctuation - `... rework it. @Zombo Bot`, `@a, @b.` (item assign-bot).
TRAILING = set(' \t\r\n,.;:!?)]}\'"')

ICON = re.compile(r'<icon=[^<>]*>')
PUNCTUATION = set('!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~')


def plain(name):
    """A display name with its markup taken out, as Overseer's `people::plain` takes it out - the
    two must agree, as a mention is of the name the page shows: `<color=... | x>` and
    `<motion=... | x>` are `x`, a `<#key>` its key, an icon nothing, and the marks of bold, italic,
    strikethrough and code go - an underscore only where it opens or closes a word."""
    chars = ICON.sub(' ', name)
    out, i = [], 0
    while i < len(chars):
        c = chars[i]
        if c == '\\' and i + 1 < len(chars) and chars[i + 1] in PUNCTUATION:
            out.append(chars[i + 1])
            i += 2
            continue
        if c == '<':
            end = chars.find('>', i)
            if end >= 0:
                tag = chars[i + 1:end]
                taken = None
                for prefix in ('color=', 'motion='):
                    if tag.startswith(prefix):
                        inner = tag[len(prefix):]
                        taken = plain(inner[inner.index('|') + 1:]) if '|' in inner else None
                        break
                else:
                    if tag.startswith('#'):
                        taken = tag[1:]
                if taken is not None:
                    out.append(taken)
                    i = end + 1
                    continue
        inside_a_word = 0 < i < len(chars) - 1 and chars[i - 1].isalnum() and chars[i + 1].isalnum()
        if c in '*~`' or (c == '_' and not inside_a_word):
            i += 1
            continue
        out.append(c)
        i += 1
    return ' '.join(''.join(out).split())


def mentions(text, people):
    """The mentions in a text, in order, as (where the @ is, where the name ends, usernames): `@`
    and an account's display name, plain, or its username, whatever the case. The longest name
    that fits is the one meant - `@Zombo Bot` before `@Zombo` - and two accounts it names alike are
    both meant. An @ inside a word - an address - is no mention."""
    names = sorted({(n.lower(), tag) for tag, name in people for n in (tag, plain(name)) if n},
                   key=lambda nt: -len(nt[0]))
    found, low = [], text.lower()
    for at in (m.start() for m in re.finditer('@', text)):
        if at and NAME_GOES_ON.match(text[at - 1]):
            continue
        fits = [(n, tag) for n, tag in names if low.startswith(n, at + 1)
                and not NAME_GOES_ON.match(text[at + 1 + len(n):at + 2 + len(n)] or ' ')]
        if fits:
            longest = len(fits[0][0])
            found.append((at, at + 1 + longest, sorted({tag for n, tag in fits if len(n) == longest})))
    return found


def trailing(text, people):
    """The accounts a text ends by mentioning: the run of mentions with nothing after them but
    spaces, commas and closing punctuation. A mention in the middle of a text is not one."""
    named, end = [], len(text)
    found = mentions(text, people)
    while True:
        while end and text[end - 1] in TRAILING:
            end -= 1
        last = next((m for m in found if m[1] == end), None)
        if not last:
            return named
        named += [tag for tag in last[2] if tag not in named]
        end = last[0]


def by_bot(comment):
    """Whether a comment was written by a loop's session: tracker.py signs those with the model
    as their status (`comment`). A person's has none."""
    return bool(comment.get('status'))


def last_said(t, top, bottom):
    """The last comment on an item not written by a loop's session, or None."""
    return next((c for c in reversed(t.nested(top, bottom, TALK)) if not by_bot(c)), None)


def bots(item, said, people):
    """The accounts an item is for (item assign-bot): those its title mentions, anywhere, and
    those the last comment not by a loop ends by mentioning - `said`'s body. Empty when it is for
    any loop. A later comment of a person's, mentioning no one at its end, hands it back to all."""
    named = [tag for *_, tags in mentions(item.get('title', ''), people) for tag in tags]
    named += trailing(said or '', people)
    return list(dict.fromkeys(named))


def claimed(item):
    """Whether an agent loop is working on an item: the template's hidden `working`."""
    return item.get('working') == 'true'


def leave_alone(item, handle):
    """Refuse to touch an item that is waiting on a person, saying why."""
    if item['stage'] in OWNERS:
        sys.exit(f'{handle} is at {item["stage"]}, waiting on a person - leave it')
    if item['stage'] == PARTS:
        sys.exit(f'{handle} is at {PARTS}, waiting on its open parts - leave it')
    if held(item):
        sys.exit(f'{handle} is flagged {", ".join(held(item))}, waiting on a person - leave it')


class Queue:
    """The open items by stage, and those in ready, planning and filed split into what is free to
    take, what waits on another item - one in its after list, or one of its own open parts - and
    what a flag holds for a person. What is free comes in the order a loop takes it: every item
    flagged first, whatever its stage, before anything else - the owner flags one to have it done
    next, not merely sooner than its neighbours. Then, and among the flagged ones too, by stage;
    within a stage, those holding up the most other open items, then the fewest points. The sorts are stable, so the file's order holds among the rest. An item an agent
    loop has claimed is none of these: it is that loop's, whatever else it is.

    A worker with a manager passes the manager's username as `owner`: the items whose `owner` it is
    then come before everything flagged first - owned and first, owned, others' first, the rest.
    The script has no manager, so without `owner` the order is exactly the script's.

    An item that mentions accounts - in its title, or at the end of its last comment not by a
    loop (`bots`) - is free only to a loop whose key signs in as one of them; to any other it is
    held for those accounts, in `others` as (stage, item, usernames), and is neither free nor taken
    by `--prefer`. The server's directory, and who the loop is - `me` - are asked of it only when
    some item holds an @ where a mention would count; `names` gives each username's plain display
    name, for saying who an item is held for."""

    def __init__(self, t, owner=None):
        self.owner = owner
        entries = t.items()
        items = [f for _, _, f in entries]
        said = {id(f): (last_said(t, top, bottom) or {}).get('body', '') for top, bottom, f in entries}
        maybe = any('@' in f.get('title', '') or '@' in said[id(f)] for f in items)
        people = t.server.people(t) if maybe else []
        named_by = {id(f): bots(f, said[id(f)], people) for f in items}
        self.names = {tag: plain(name) or tag for tag, name in people}
        self.me = t.server.me() if any(named_by.values()) else None
        open_handles = {f.get('handle') for f in items}
        holds = {h: sum(h in split(f.get('after')) for f in items) for h in open_handles}
        self.by_stage = {}
        for f in items:
            self.by_stage.setdefault(f['stage'], []).append(f)

        def order(f):
            points = str(f.get('points', '1'))
            return -holds.get(f.get('handle'), 0), int(points) if points.isdigit() else 1

        self.free, self.waiting, self.held, self.claimed, self.others = [], [], [], [], []
        for stage in PRIORITY:
            for f in sorted(self.by_stage.get(stage, []), key=order):
                if claimed(f):
                    self.claimed.append((stage, f))
                elif held(f):
                    self.held.append((stage, f))
                elif waits := waiting_on(t, f, items):
                    self.waiting.append((stage, f, waits))
                elif (named := named_by[id(f)]) and self.me not in named:
                    self.others.append((stage, f, named))
                else:
                    self.free.append((stage, f))
        self.free.sort(key=lambda sf: self.rank(sf[1]))
        self.asked = sum(flagged(f, 'asked') for f in items)
        # However many items wait on a person, the queue is empty only when nothing is free:
        # questions piling up are no reason to stop working what can be worked.
        self.signal = EMPTY if not self.free else None


    def mine(self, f):
        """Whether an item is the worker's manager's: always false for a worker with none."""
        return bool(self.owner) and f.get('owner') == self.owner

    def rank(self, f):
        """What puts a free item ahead of the rest: the manager's, then flagged first."""
        return not self.mine(f), not flagged(f, 'first')


def queue(t, args):
    q = Queue(t, args.owner)
    print('open items by stage: ' + ', '.join(f'{s} {len(q.by_stage.get(s, []))}' for s in t.stages() if s not in CLOSING))
    for stage, f in q.held:
        print(f'  held     {stage:8} {f["handle"]:16} flagged {", ".join(held(f))}')
    for stage, f, waits in q.waiting:
        print(f'  waiting  {stage:8} {f["handle"]:16} on {", ".join(waits)}')
    for stage, f in q.claimed:
        print(f'  claimed  {stage:8} {f["handle"]:16} an agent loop is working on it')
    for stage, f, named in q.others:
        print(f'  for      {stage:8} {f["handle"]:16} held for {", ".join(q.names.get(n, n) for n in named)}')
    if q.me:
        print(f'this loop signs in as {q.names.get(q.me, q.me)} ({q.me}): items mentioning it, '
              'or no one, are its to take')
    print()
    if q.free:
        print('free to take, in the order a loop takes them ' +
              (f'(@ is owned by {args.owner}, the manager: those come before everything else; ' if args.owner else '(') +
              '! is flagged first, before anything else; then by '
              'stage, what holds up the most, the fewest points - past that, use '
              'judgement; ? is flagged discuss, for your thoughts only):')
        for stage, f in q.free:
            mark = '@' if q.mine(f) else '!' if flagged(f, 'first') else '?' if flagged(f, 'discuss') else ' '
            print(f' {mark}{stage:8} {f["handle"]:16} [{f.get("labels", "")}] {f.get("points", "1")} pts '
                  f'{f["complexity"]:10} {f.get("title", "")}')
    print(f'\nflagged asked: {q.asked}')
    if q.signal:
        print(q.signal)


def next_item(t, args):
    """For a loop: the item it takes next, or the signal that stops it, as one line of JSON.

    `--prefer` names the item a session asked to go on to with what it has read. It is taken if
    it is free to take - not waiting, not held for a person - and nothing flagged first is free
    while it is not: the owner's first comes before an agent's convenience - and so do the
    manager's items, when `--owner` names one. Otherwise the queue's own first, as if nothing had
    been asked."""
    q = Queue(t, args.owner)
    # Who this loop is, and what is held for other bots, for the page to warn about an item
    # whose bot no loop runs: on the signal too, since a loop with nothing left is one to warn.
    bot = ({'me': q.me} if q.me else {}) | (
        {'others': [{'handle': f['handle'], 'bots': named, 'names': [q.names.get(n, n) for n in named]}
                    for _, f, named in q.others]} if q.others else {})
    if q.signal:
        print(json.dumps({'signal': q.signal} | bot))
        return
    stage, f = q.free[0]
    wanted = next(((s, g) for s, g in q.free if g['handle'] == args.prefer), None) if args.prefer else None
    if wanted and q.rank(wanted[1]) == q.rank(f):
        stage, f = wanted
    print(json.dumps({'handle': f['handle'], 'stage': stage, 'complexity': f['complexity'],
                      'flags': f['flags'], 'discuss': flagged(f, 'discuss'),
                      'points': f.get('points', '1'), 'title': f.get('title', ''),
                      'preferred': bool(args.prefer) and f['handle'] == args.prefer} | bot))


#: How many closed parts, and closed siblings, `show` lists - the most recently closed. A large
#: item can have dozens; the nearest in time are the ones most likely to matter.
CLOSED_SHOWN = 10


def related(t, f, handle):
    """The items around one, as (relation, handle, stage, title): its parent, what it waits on,
    what waits on it, its parts and its siblings. Closed ones are among them, at the status they
    closed with - how a neighbour was done is often the best guide to this one - and a closed
    part or sibling only among the most recent CLOSED_SHOWN of each, with how many more there are."""
    opened = [g for _, _, g in t.items()]
    # An entry that reads with no handle names nothing: left out, so that one odd entry never
    # stands for the parent of every item that has none.
    opened = [g for g in opened if g.get('handle')]
    closed = sorted((g for *_, g in t.closed() if g.get('handle')), key=lambda g: g.get('finished_at', ''), reverse=True)
    known = {g['handle']: (g, g['stage']) for g in opened}
    for g in closed:
        known.setdefault(g['handle'], (g, g.get('status') or 'finished'))
    row = lambda rel, h: (rel, h, known[h][1], known[h][0].get('title', ''))
    rows, more = [], []
    if f.get('parent') and f['parent'] in known:
        rows.append(row('parent', f['parent']))
    for h in split(f.get('after')):
        if h in known:
            rows.append(row('after', h))
        elif ':' in h and (there := stage_and_title(t, h)):
            rows.append(('after', h, *there))
    rows += [row('holds up', g['handle']) for g in opened if handle in split(g.get('after'))]
    # And what other projects' items wait on this one - named there as project:handle.
    for project in t.neighbours():
        other = t.other(project)
        rows += [('holds up', f'{project}:{g["handle"]}', g['stage'], g.get('title', ''))
                 for _, _, g in other.items() if f'{t.project}:{handle}' in split(g.get('after'))]
    for rel, family in (('part', lambda g: g.get('parent') == handle),
                        ('sibling', lambda g: f.get('parent') and g.get('parent') == f.get('parent')
                         and g.get('handle') != handle)):
        rows += [row(rel, g['handle']) for g in opened if family(g)]
        ended = [g for g in closed if family(g)]
        rows += [row(rel, g['handle']) for g in ended[:CLOSED_SHOWN]]
        if len(ended) > CLOSED_SHOWN:
            more.append(f'and {len(ended) - CLOSED_SHOWN} {rel}s closed before those')
    return rows, more


def stage_and_title(t, handle):
    """(stage, title) of an item named here or as project:handle, open or closed; None if none is."""
    there, own = t.peek(handle)
    if there is None:
        return None
    for _, _, g in there.items():
        if g.get('handle') == own:
            return g['stage'], g.get('title', '')
    for *_, g in there.closed():
        if g.get('handle') == own:
            return g.get('status') or 'finished', g.get('title', '')
    return None


#: An item named in a comment, as the page draws it into a chip: <#handle>, or <#project:handle>.
MENTION = re.compile(r'<#([^<>\s]+)>')


def with_titles(t, text):
    """A comment's text with each item it names followed by that item's title -
    `<#handle> (Its title)` - so whoever reads it knows what is meant without asking, and each
    image it holds at its full path - see IMAGE. Inside code, fenced or in backticks, both are
    text and stay as they are, as the page leaves them; so does a handle no item has."""
    titles = {}

    def titled(m):
        h = m.group(1)
        if h not in titles:
            found = stage_and_title(t, h)
            titles[h] = found[1] if found else None
        return f'{m.group(0)} ({titles[h]})' if titles[h] else m.group(0)
    def located(m):
        target = m.group(2).strip()
        if re.match(r'^[a-z][a-z0-9+.-]*:', target, re.I) or Path(target).is_absolute():
            return m.group(0)
        here = fetched_image(t, target)
        return f'![{m.group(1)}]({here.as_posix()})' if here else m.group(0)
    parts = re.split(r'(```.*?```|`[^`\n]*`)', text, flags=re.S)
    return ''.join(p if i % 2 else IMAGE.sub(located, MENTION.sub(titled, p)) for i, p in enumerate(parts))


#: An image in a comment, as the page pastes one: ![](images/<project>/<name>.png), a path relative
#: to the tracker's folder. Fetched, and given whole in what show prints, so whoever reads it can
#: open the file as it stands - an agent's file-reading tool shows images - instead of hunting for it.
IMAGE = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)\)')


def fetched_image(t, target):
    """An image the tracker names, fetched into the scratch folder - or the temporary one -
    under the path the comment gives it, so it can be opened here; None if the server has none."""
    import base64
    folder = Path(os.environ.get('AGENT_SCRATCH') or tempfile.gettempdir()) / 'tracker-images' / t.project
    here = folder / Path(target.replace('\\', '/')).name
    if not here.is_file():
        try:
            got = t.server.bridge(t.name, 'load_overseer_image', {'path': t.name, 'image': target})['result']
            data = base64.b64decode(got['data'])
        except (SystemExit, KeyError, TypeError, ValueError):
            return None
        here.parent.mkdir(parents=True, exist_ok=True)
        here.write_bytes(data)
    return here.resolve()


def find(t, handle):
    """An item wherever it is: (where, first line, last line, fields, closed) - `where` the text
    its lines are in, the tracker's own or a part of its History's. Reading a closed one is every
    agent's to do - it is changing one that is a person's."""
    hits = [e for e in t.items() if e[2].get('handle') == handle]
    if hits:
        return (t, *hits[0], False)
    hits = [e for e in t.closed() if e[3].get('handle') == handle]
    if hits:
        return (*hits[0], True)
    sys.exit(f'no item {handle}')


def show(t, args):
    # Another project's item, read in its own tracker: what is around it is around it there.
    t, handle = t.resolve(args.handle)
    where, top, bottom, f, closed = find(t, handle)
    said = where.nested(top, bottom, TALK)
    rows, more = related(t, f, handle)
    if args.json:
        print(json.dumps(dict(f, closed=closed, waiting=[] if closed else waiting_on(t, f),
                              related=[dict(zip(('relation', 'handle', 'stage', 'title'), r)) for r in rows],
                              comments=said, work=where.nested(top, bottom, WORK))))
        return
    for name in (['finished_at', 'status'] if closed else []) + ORDER:
        if name in f and f[name] != '':
            print(f'{name:10} {f[name]}')
    if t.project != Path(args.doc).stem:
        print(f'project    {t.project} - another project\'s item: read it here, change nothing in it')
    if closed:
        print('closed     - it can be read, and only a person changes it')
    else:
        held_by = waiting_on(t, f)
        parts = [h for h in held_by if h not in split(f.get('after'))]
        named = [', '.join(h for h in held_by if h not in parts)] + ([f'its parts {", ".join(parts)}'] if parts else [])
        print(f'waiting    {"on " + "; ".join(x for x in named if x) if held_by else "no"}')
    if rows:
        width = max(len(r[1]) for r in rows)
        print('\nrelated - tracker.py show <handle> for any of them:')
        for rel, h, stage, title in rows:
            print(f'  {rel:9} {h:{width}}  {stage:10} {title}')
        for line in more:
            print(f'  {line}')
    print(f'\nconversation, oldest first: {len(said) or "nothing said yet"}')
    for c in said:
        print(f'\n--- {c.get("at", "")[:16].replace("T", " ")}  {c.get("author", "")}\n{with_titles(t, c.get("body", ""))}')


def about(t, args):
    """What the project is, as its tracker describes it to everyone who opens it - the loop puts it
    in every session's system prompt, between AUTOTASK.md and the brief."""
    if t.base():
        # Inheriting the project template, the tracker sets the root's field: `- description = "..."`
        # one level in from the root. Not set, it says nothing - not the template's placeholder.
        # A `text description` further down is some other template's, as a feature list's is.
        root = next(l for l in t.lines if l.strip() and not l.strip().startswith('//'))
        line = next((l for l in t.lines if l.startswith(indent_of(root) + '    - description = ')), None)
    else:
        line = next((l for l in t.lines if l.lstrip().startswith('text description (')), None)
    m = line and re.search(r'= "(.*)"\s*$', line)
    if m:
        print(unescape(m.group(1)).strip())


def tag_texts(t):
    """Each tag with its name and what the project says about it - its `About` text, empty if
    nothing is said."""
    out = {}
    for top, bottom, f in t.entries('Labels'):
        block = t.block(top, bottom, 'div About')
        text = ''
        if block and block[0] != block[1]:
            own = indent_of(t.lines[block[0]]) + '    '
            for line in t.lines[block[0] + 1:block[1]]:
                m = FIELD.match(line)
                if m and m.group(1) == own and m.group(2) == 'about':
                    text = value_of(m.group(3))
        out[f.get('tag')] = (f.get('name') or f.get('tag'), text.strip())
    return out


def tag(t, args):
    texts = tag_texts(t)
    if args.json:
        print(json.dumps({k: {'name': n, 'about': a} for k, (n, a) in texts.items()}))
        return
    if not args.tag:
        for k, (name, text) in texts.items():
            print(f'{k:14} {name:20} {"says something - tracker.py tag " + k if text else ""}')
        return
    if args.tag not in texts:
        sys.exit(f'no tag {args.tag}; the tags are {", ".join(texts)}')
    name, text = texts[args.tag]
    print(f'{args.tag} - {name}\n\n{text or "(nothing is written about it)"}')


def move(t, args):
    _, _, f = t.item(args.handle)
    if args.stage == 'asked':
        sys.exit('asked is a flag, not a stage: tracker.py ask <handle> <question> adds the question to the '
                 'note, moves the item to planning and flags it asked')
    if args.stage == 'discuss':
        sys.exit('discuss is a flag a person sets; the stage is planning')
    if args.stage not in t.stages():
        sys.exit(f'no stage {args.stage}; the stages are {", ".join(t.stages())}')
    if args.stage in CLOSING:
        sys.exit(f'{args.stage} is for a person to pick - move it to testing, or ask')
    leave_alone(f, args.handle)
    # The template withholds parts from an item without open parts: with none, nothing would ever
    # move it on to testing.
    if args.stage == PARTS and not open_parts(t, args.handle):
        sys.exit(f'{args.handle} has no open parts - {PARTS} is for an item that waits only on them; '
                 f'file its parts with --parent {args.handle} first')
    if args.stage == 'ready' and flagged(f, 'discuss'):
        sys.exit(f'{args.handle} is flagged discuss: only your thoughts are wanted - give them with '
                 f'tracker.py ask, which moves it to planning and flags it asked')
    if args.complexity is not None and args.complexity not in t.complexities():
        sys.exit(f'no complexity {args.complexity}; the scale is {", ".join(t.complexities())}')
    complexity = args.complexity or f['complexity']
    # Whoever moves an item to ready has just made its plan, so they are the one to judge how hard
    # it is to build; on an unattended run nobody else will.
    if args.stage == 'ready' and complexity == UNASSIGNED:
        sys.exit(f'{args.handle} goes to ready with its complexity - add --complexity '
                 f'{"|".join(c for c in t.complexities() if c != UNASSIGNED)}')
    # Points filed with an item are a guess made before anyone looked at the code; whoever plans it
    # knows better, and says so with the move. They are what it is worth in the task history once
    # finished, so a change is printed, for the note to say why.
    was = str(f.get('points', '1'))
    if args.points is not None and args.points < 1:
        sys.exit('points are 1 or more: 1 the smallest thing worth writing down, 8 most of a day')
    # The comment goes with the move, saying what the move did: once at testing, the item is a
    # person's, and a comment after it reads as something said to them, not as the move's own.
    said = add_comment(t, args.handle, args.note) if args.note else None
    t.set_field(args.handle, 'stage', None if args.stage == 'filed' else quoted(args.stage))
    if args.complexity is not None:
        t.set_field(args.handle, 'complexity', None if complexity == UNASSIGNED else quoted(complexity))
    if args.points is not None:
        t.set_field(args.handle, 'points', str(args.points))
    changed = f' ({f["complexity"]} -> {complexity})' if complexity != f['complexity'] else ''
    if args.points is not None and str(args.points) != was:
        changed += f' (points {was} -> {args.points})'
    print(f'{args.handle}: {f["stage"]} -> {args.stage}{changed}' + (f'; {said}' if said else ''))


def text_of(given):
    """The text given, or with @ in front of a path, what that file holds. Text written to a file
    with an editor never passes through a shell, which in Bash would run anything in backticks or
    $( ) inside double quotes - and a comment quoting a formula holds both. A comment is markdown,
    so the file's lines are kept."""
    if given.startswith('@'):
        path = Path(given[1:])
        if not path.is_file():
            sys.exit(f'no file {path} - write the text there first, or give it without the @')
        given = path.read_text(encoding='utf-8').replace('\r\n', '\n')
    return given.strip()


def author():
    return os.environ.get('AGENT_AUTHOR') or 'agent'


def add_comment(t, handle, text, literal=False):
    """Add a comment at the end of an open item's conversation, dated now and signed by whoever
    AGENT_AUTHOR names - `literal` when the text is already read, so an @ at its start is text. Says
    what it added."""
    body = text.strip() if literal else text_of(text)
    if not body:
        sys.exit('a comment with nothing in it')
    top, bottom, _ = t.item(handle)
    values, signed = comment(t, body)
    t.add_nested(top, bottom, TALK, values)
    return f'comment added, signed {signed}, {len(body)} characters'


def comment(t, body):
    """A comment's fields, dated now, and who it is signed by: the account the key acts as - drawn
    as its chip - with what AGENT_AUTHOR names, the model, as its status beside it."""
    values = {'at': quoted(now()), 'author': quoted(author()), 'body': quoted(body)}
    model = os.environ.get('AGENT_AUTHOR', '')
    values['author'] = quoted(t.server.me())
    values['status'] = quoted(model)
    return values, t.server.me() + (f' ({model})' if model else '')


def note(t, args):
    """A comment on any open item, whatever its stage or flags: it changes neither, so an item
    waiting on a person or on its parts takes one as well - something its reader should know. A
    closed one does not: t.item refuses History."""
    t.item(args.handle)
    said = add_comment(t, args.handle, args.text)
    print(f'{args.handle}: {said}')


def ask(t, args):
    """A question for a person, in one step: a comment asks it, the item goes to planning - its
    plan is not finished while the question is open - and the asked flag holds it until they
    answer and take the flag off. Never a flag without its question."""
    _, _, f = t.item(args.handle)
    leave_alone(f, args.handle)
    said = add_comment(t, args.handle, args.question)
    if f['stage'] != 'planning':
        t.set_field(args.handle, 'stage', quoted('planning'))
    flags = split(f['flags'])
    t.set_field(args.handle, 'flags', quoted(', '.join(flags + ['asked'])))
    print(f'{args.handle}: {f["stage"]} -> planning, flagged asked; {said}')


def after(t, args):
    _, _, f = t.item(args.handle)
    leave_alone(f, args.handle)
    waits = split(f.get('after'))
    for h in args.handles:
        h = own_name(t, h)
        if h == args.handle:
            sys.exit('an item cannot wait on itself')
        if not t.knows(h):
            sys.exit(f'no item {h}, open or finished')
        refuse_family(t, args.handle, h)
        if h not in waits:
            waits.append(h)
    t.set_field(args.handle, 'after', quoted(', '.join(waits)))
    print(f'{args.handle}: after {", ".join(waits)}')


def refuse_family(t, handle, h):
    """Refuse an after that names the item's own part, which it waits on already, or its parent or
    one above, which waits on it in turn: neither could ever be taken."""
    if h in open_parts(t, handle):
        sys.exit(f'{h} is a part of {handle}, which waits on its open parts already - no after is needed')
    if h in ancestors(t, handle):
        sys.exit(f'{h} is above {handle} in its parts, and waits on it - '
                 f'{handle} waiting on {h} would leave neither ever free')


def own_name(t, handle):
    """A handle as this tracker writes it: this project's own items without the project's name."""
    return handle.split(':', 1)[1] if handle.startswith(f'{t.project}:') else handle


def file(t, args):
    """A new item: at filed, or with --ask at planning, flagged asked, its question its first
    comment - one that needs a person before anyone can work it, which would otherwise be handed to
    the next session the moment it is filed. With --into, in another project's tracker, for work
    that belongs there: it says where it came from. Either way it is the
    person's whose item the session works, OVERSEER_ITEM, if that item has an owner."""
    owner = owner_of(t, os.environ.get('OVERSEER_ITEM'))
    if args.into and args.into != t.project:
        there = t.other(args.into)
        # Read again: another loop may have written it since.
        target = Tracker(there.name, t.server)
        file_in(target, args, origin=os.environ.get('OVERSEER_ITEM'), into=True, owner=owner)
        return
    file_in(t, args, owner=owner)


def owner_of(t, handle):
    """The owner of an item - this project's, or project:handle - open or closed, as History keeps
    it too; None when it has none, or there is no such item."""
    if not handle:
        return None
    there, own = t.peek(handle)
    if there is None:
        return None
    for f in [f for _, _, f in there.entries('Items')] + [f for *_, f in there.closed()]:
        if f.get('handle') == own:
            return f.get('owner') or None
    return None


def file_in(t, args, origin=None, into=False, owner=None):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', args.handle):
        sys.exit('a handle is short, lower case: letters, digits, - and _')
    if args.handle in t.handles():
        sys.exit(f'{args.handle} is taken in {t.project}')
    labels = split(args.labels)
    unknown = [l for l in labels if l not in t.labels()]
    if unknown:
        sys.exit(f'no such tag {", ".join(unknown)} in {t.project}; its tags are {", ".join(t.labels())}')
    after_ = [own_name(t, h) for h in split(args.after)]
    for h in after_ + ([args.parent] if args.parent else []):
        if not t.knows(h):
            sys.exit(f'no item {h} for {t.project}, open or finished')
    if args.parent and ':' in args.parent:
        sys.exit('a parent is an item of the same project')
    if args.parent:
        above = [args.parent] + ancestors(t, args.parent)
        if any(h in above for h in after_):
            sys.exit(f'a part cannot come after {args.parent} or an item above it: those wait on their open '
                     f'parts, so neither would ever be free')
    question = text_of(args.ask) if args.ask is not None else None
    if args.ask is not None and not question:
        sys.exit('--ask with no question: never a flag without its question')
    fields = {'added': quoted(now()), 'handle': quoted(args.handle)}
    if owner:
        fields['owner'] = quoted(owner)
    fields['title'] = quoted(args.title)
    if question:
        fields['stage'] = quoted('planning')
    if args.parent:
        fields['parent'] = quoted(args.parent)
    if after_:
        fields['after'] = quoted(', '.join(after_))
    if labels:
        fields['labels'] = quoted(', '.join(labels))
    # Filed by an agent of its own accord: the loop says so in the environment it runs sessions
    # in, so the flag is right whether or not the agent remembers it.
    auto = bool(os.environ.get('AGENTIC_LOOP'))
    flags = (['auto'] if auto else []) + (['asked'] if question else [])
    if flags:
        fields['flags'] = quoted(', '.join(flags))
    fields['points'] = str(int(args.points))
    t.append_entry('Items', fields)
    note_ = text_of(args.note) if args.note else ''
    if origin:
        note_ = f'Filed from {origin}.' + (f'\n\n{note_}' if note_ else '')
    said = [add_comment(t, args.handle, note_, literal=True)] if note_ else []
    if question:
        said.append(add_comment(t, args.handle, question, literal=True))
    where = f' in {t.project}' if into else ''
    print(f'filed {args.handle}{where}' + (f', flagged {", ".join(flags)}' if flags else '')
          + (' at planning' if question else '') + ''.join(f'; {s}' for s in said))


def claim(t, args):
    """For a loop: the item it is about to work marked as its own, as one line of JSON - claimed, or
    refused with the template's words when another loop has it already. On a server it is the
    template's claim button pressed, which refuses an item already claimed in the same write that
    claims it."""
    top, _, f = t.item(args.handle)
    if claimed(f):
        print(json.dumps({'handle': args.handle, 'claimed': False, 'refused': TAKEN}))
        return
    try:
        t.server.event(t.name, f'{t.address_at(top)}/claim', 'click')
    except Refused as refused:
        # Only the button's own refusal says another loop has it. Any other - a template with no
        # claim button yet - is not an answer, and every item would read as taken.
        if TAKEN not in str(refused):
            sys.exit(f'the server refused the claim of {args.handle}: {refused}'.rstrip())
        print(json.dumps({'handle': args.handle, 'claimed': False, 'refused': TAKEN}))
        return
    worker = args.worker or os.environ.get('AGENT_AUTHOR')
    if worker:
        # Only for the page to show: the press above is the claim, so a name that cannot be written
        # leaves the item claimed all the same, and the loop is told so.
        try:
            t.set_field(args.handle, 'worker', worker)
        except SystemExit as e:
            print(f'{args.handle}: claimed, but its worker could not be written: {e}', file=sys.stderr)
    print(json.dumps({'handle': args.handle, 'claimed': True}))


def release(t, args):
    """For a loop: an item it claimed let go of again. Quiet when there is nothing to let go of - the
    item is free already, or a person has closed it meanwhile, and History keeps no claim - so a
    release never stops a loop that is ending."""
    hits = [f for _, _, f in t.items() if f.get('handle') == args.handle]
    if not hits or not any(claimed(f) for f in hits):
        print(f'{args.handle}: nothing to release')
        return
    t.set_field(args.handle, 'working', None)
    # The template's own "" back, so the row shows no name once nobody works it.
    t.set_field(args.handle, 'worker', None)
    print(f'{args.handle}: released')


def work(t, args):
    """The loop's record of one session on an item. The item may be at testing by then - the
    session put it there - so nothing about its stage is refused; only History is out of reach."""
    top, bottom, _ = t.item(args.handle)
    values = {'at': quoted(now()), 'kind': quoted(args.kind), 'model': quoted(args.model)}
    if args.moved:
        values['moved'] = quoted(args.moved)
    if args.points is not None:
        values['points'] = str(args.points)
    # Only what is not the template's own default: a nought reads as one anyway.
    for name, figure in (('cost', f'{args.cost:.2f}'), ('read', str(args.read)), ('written', str(args.written)),
                         ('output', str(args.output)), ('turns', str(args.turns)),
                         ('minutes', f'{args.minutes:.1f}'), ('lines', str(args.lines))):
        if float(figure) != 0:
            values[name] = figure
    t.add_nested(top, bottom, WORK, values)
    print(f'{args.handle}: recorded a {args.kind} session on {args.model}')


def attach(t, args):
    """A picture kept with the tracker, for a comment to show: prints the path to write in it as
    `![what it shows](path)`. It is sent to the server, which names it."""
    import base64
    picture = Path(args.file)
    kinds = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}
    if not picture.is_file() or picture.suffix.lower() not in kinds:
        sys.exit(f'no picture {picture}: a .png, .jpg, .gif or .webp file')
    where = t.server.bridge(t.name, 'save_overseer_image', {
        'path': t.name, 'mime': kinds[picture.suffix.lower()],
        'data': base64.b64encode(picture.read_bytes()).decode('ascii')})['result']
    print(where)


def check(t, args):
    """Ask the server, which refuses any write that would not read back, for the open list."""
    # A tracker with no items yet writes no Items line; the server addresses the list the
    # template declares from the document's root then, as append_entry does.
    start = t.header('Items')
    root = t.root() if start is None else t.root_of(start)
    listed = t.server.call('GET', '/v1/node', {'document': t.name, 'address': f'{root}/Items'})
    print(f'parses: the open list has {len(listed.get("child_addresses", []))} items')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--doc', default=DEFAULT)
    # --doc after the command as well as before it: agents write it both ways.
    doc = argparse.ArgumentParser(add_help=False)
    doc.add_argument('--doc', default=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest='command', required=True)
    command = lambda name: sub.add_parser(name, parents=[doc])
    p = command('queue'); p.add_argument('--owner')
    p = command('next'); p.add_argument('--prefer'); p.add_argument('--owner')
    p = command('claim'); p.add_argument('handle'); p.add_argument('--worker')
    p = command('release'); p.add_argument('handle')
    p = command('show'); p.add_argument('handle'); p.add_argument('--json', action='store_true')
    command('about')
    p = command('tag'); p.add_argument('tag', nargs='?'); p.add_argument('--json', action='store_true')
    p = command('move'); p.add_argument('handle'); p.add_argument('stage')
    p.add_argument('--complexity'); p.add_argument('--note'); p.add_argument('--points', type=int)
    p = command('note'); p.add_argument('handle'); p.add_argument('text')
    p = command('ask'); p.add_argument('handle'); p.add_argument('question')
    p = command('after'); p.add_argument('handle'); p.add_argument('handles', nargs='+')
    p = command('file'); p.add_argument('handle'); p.add_argument('title')
    p.add_argument('--labels'); p.add_argument('--points', default=1); p.add_argument('--parent')
    p.add_argument('--after'); p.add_argument('--note'); p.add_argument('--ask'); p.add_argument('--into')
    p = command('work'); p.add_argument('handle')
    p.add_argument('--kind', required=True); p.add_argument('--model', required=True); p.add_argument('--moved')
    p.add_argument('--points', type=int); p.add_argument('--cost', type=float, default=0.0)
    for name in ('read', 'written', 'output', 'turns', 'lines'):
        p.add_argument(f'--{name}', type=int, default=0)
    p.add_argument('--minutes', type=float, default=0.0)
    command('check')
    p = command('attach'); p.add_argument('file')
    command('projects')
    args = parser.parse_args()
    run = {'queue': queue, 'next': next_item, 'claim': claim, 'release': release, 'show': show, 'about': about, 'tag': tag, 'move': move,
           'note': note, 'ask': ask, 'after': after, 'file': file, 'work': work,
           'check': check, 'attach': attach,
           'projects': lambda t, a: print('\n'.join(t.neighbours()))}[args.command]
    run(open_tracker(args.doc), args)


if __name__ == '__main__':
    main()
