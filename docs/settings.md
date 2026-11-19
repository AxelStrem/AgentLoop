# Bundles and machine settings

What replaces `AgenticLoop/projects/<name>.psd1`: what each file holds, how it is written, and where
it lives. This is the design; item `config` writes the code that reads and writes these files, and
the examples in `docs/examples/` are what its tests load.

Today's `.psd1` mixes what belongs to a project with what belongs to a PC. Here they are three
things, as the owner put them:

| | is | lives in | handed to someone? |
| --- | --- | --- | --- |
| **Project** | its definition, a document beside its tracker (below); or a bundle: a folder with the brief, the tools and `project.json` | on the Overseer server; a bundle wherever its owner keeps it | by the tracker's address; a bundle whole |
| **Worker** | who does the work: a Claude account and an Overseer account | machine settings | no |
| **Loop** | a project, a worker, and where the project's repositories are on this PC | machine settings | no |

A loop is what the page starts, stops, kills and starts again. A bundle holds nothing of a PC: no
path, no account, no key, no address of a server's key.

## Format

JSON, read and written with the standard library. It has no comments, so what each field means is
here, and in the page's one-line help. Every file has `"format": 1`, so a later change can be
detected and converted rather than guessed at.

- Paths are written with forward slashes (`C:/src/Overseer`), which every OS and both
  shells read; a backslash would have to be doubled. A leading `~` is the user's home folder.
  Nothing else is expanded: no `$HOME`, no `%APPDATA%`.
- A file is written whole to a temporary file beside it and renamed over it, so a crash leaves the
  old file, not half of a new one. Keys the app does not know are kept as they were.
- A secret is never in these files: `keyFile` is the path of a file that holds the key.

## The agents' key

A key pasted into **Paste a key** - beside the key file, in a worker's editor and in **Add from a
tracker** - or given to `agentloop add --key-stdin` (item `paste-key`) is sent to its server once,
`GET /account/me`, as the check that it signs in; nothing is written for one the server refuses. It
is then written to `keys/<host>-<account>.key` in the config folder, and `keyFile` holds that path:

- macOS and Linux: `keys/` is `0700` and the file `0600`.
- Windows: `keys/`, before any key is written into it, and then the file have an ACL naming the
  current user alone, with nothing inherited (`icacls`); the app checks the ACL after setting it,
  and removes the file rather than leave it readable by others.

The key is not written to the settings, a log or any answer of the page. The server it is for is
the one the worker's editor names, or in **Add from a tracker** the address's, else the worker's.
Pasting a key for the same server and account again overwrites its file. A file is never deleted
with the setting that named it: a worker whose server is turned off says where its key file is left.

## The bundle

A folder named after the project, handed over whole (zipped, or as a repository of its own):

```
quill/
  project.json    the project's settings, below
  brief.md        today's projects/<name>.md
  tools/          today's projects/<name>/: the project's own scripts, e.g. look.ps1
```

`tracker.py`, `features.py`, `guard.py` and `AUTOTASK.md` are not in a bundle: they are the app's
(item `tools`). The bundle's name is its tracker's name - `quill` for `quill.os` -
because `<project>:<handle>` names depend on it. A folder whose name differs from its `project.json`
`name` is an error on load.

### `project.json`

| field | what it is | today's `.psd1` |
| --- | --- | --- |
| `format` | `1` | |
| `name` | the project, equal to the folder's name and the tracker's name without `.os` | the `.psd1`'s name |
| `tracker` | where the tracker is, below | `Tracker`, `Server` |
| `repos` | the repositories the project works in, in order; sessions run in the first. Each is a name, or `{"name", "about"}` with a line saying what it is | `Repos` |
| `places` | the folders the brief names that are not repositories (`press`), each a name or `{"name", "about"}`; may be absent | paths in the brief |
| `systems` | the systems the project runs on, from `windows`, `macos` and `linux`; absent for every one | |
| `settings` | the project's overrides of the run settings, below; may be empty or absent | `Routes`, `Escalation`, `BuildAfterTriage`, `BuildInTriageUpTo` |

`tracker` is `{"server": "alice/projects/quill.os"}`: the tracker is a document on an Overseer
server, named as it is there. The server's address and the key file are the **worker's**
(`overseer`). Nothing of the tracker is committed; every edit is sent as it is made.

A tracker is never a file in a repository (item `simplify`): the script's
`{"repo": ..., "path": ...}` form is refused on load, saying to put the tracker on the server.

A repository name is lower case letters, digits, `-` and `_`. It is the project's own word for a
repository (`orchard`, `orchard-server`, `orchard-docs`), not a folder name on any PC.

A place is named like a repository, and is neither a repository's name nor `tools`, `bundle` or
`python` (below). A loop on a project whose `systems` leaves out this OS is refused on start,
saying which systems the project runs on.

Not in the bundle: `Server`, `KeyFile` and `Account` (the worker's), `Repos` as paths (the loop's).

## A project read from its tracker

A project is defined by a document beside its tracker (item `doc-read`): `<name>.os` keeps it in
`<name>.project.os`, made from Overseer's `projects/project_definition.os` and mounted read-only as
`definition`, so an agent's key can read it and the server refuses it a write. AgentLoop needs
only the tracker's address: one `GET /v1/node` of `project/definition/definition` through the
tracker gives the lot.

| in the definition | is, in a bundle |
| --- | --- |
| `description` | `brief.md`, and the tracker's own description: one text, given to sessions as `# What the project is`, with `{name}` filled in |
| `Repos`, `Places` | `repos`, `places`, each with `name` and `about` |
| `systems` | `systems`; empty for every one |
| `settings` | `settings`, as JSON text |
| `Tools` | the bundle's `tools/`: each file with its `sha256` and `systems` (empty for every one) |

It is checked as `project.json` is, and an error names the field: `alice/projects/x.os
definition: settings.nope: no such setting`. The project's name is the tracker's, without `.os`.
A project read so has no bundle folder. Its tools (item `tools-fetch`) are plain files kept beside
the definition document on the server, in `tools/<name>.project/`, which a read share is enough to
fetch and only an edit share can change. `Tools` lists each with its sha256 and the systems it is
for. The ones for this machine's system are fetched into `loops/<loop>/project/tools/`, and
`{bundle}` names `loops/<loop>/project`, so `{bundle}/tools/probe.ps1` reads as it does for a
bundle. A file whose sha256 is not the one listed is never written, and refuses the start or the
add, naming the file. A text file's sha256 is of its text with CRLF and a lone CR read as LF, so
a copy whose line endings git rewrote is still the file listed; a file with a NUL byte in its
first 8000 bytes is binary and hashed as it is (item `tools-eol`). A definition exported before
that lists the hash of the bundle's copy as it was, which is still taken, with LF or CRLF. A
fetched file is written as the server gave it, but a `.cmd` or `.bat` on Windows is written with
CRLF, which cmd.exe needs. A project that lists none for this system has no `{bundle}`. A path in
`Tools` is relative, with `/`, plain names and no `..`.

Each loop keeps the definition it last read in `loops/<loop>/definition.json` (the tracker, a
hash of what was read as its version, when it was read, and the definition), written only once it
passes its checks. A run that cannot reach the server starts from that copy and says so first,
with the tools fetched with it - unless one is missing or was changed on this machine; a run with
neither does not start, and says why. A definition the server gives that fails its
checks stops the run even with a copy kept. The page shows a loop from its copy and never waits on
the server.

### Adding a loop from a tracker's address

A loop on a tracker is added from its address (item `loop-from-url`): on **Loops**, **Add from a
tracker**, or `agentloop add <address>` on a machine with no browser. The address is what
Overseer's page shows for the tracker, `https://host[:port][/prefix]/doc/<document>` or
`...?doc=<document>`. The server is everything before `/doc/`. The tracker's name alone,
`alice/projects/x.os`, means the chosen worker's server. With the address come the key file for
that server, left empty for the worker's, and the worker.

AgentLoop reads the definition and shows the first paragraph of its description and its headings,
the systems, and each repository and place with its `about`. It then asks for a folder for each,
the loop's name (the project's, by default) and the run settings. All of it is checked before
anything is saved: the name is free, this system is one of the project's, each repository is a
folder inside a git repository, and each place exists. Every field that is wrong is said at once.
The tools for this system are fetched before anything is saved: one the server does not give, or
gives with another sha256, refuses the add. Once saved, the loop's `definition.json` is the
definition just read, so its first run starts from it, and a later edit is a change (below).

`agentloop add` takes `--repo name=path` and `--place name=path` for each, and `--key-stdin` for a
key read from standard input rather than a key file (see "The agents' key"). On a terminal it asks
for the ones left out, and asks again for one that is refused. Without a terminal it names every
flag still needed and saves nothing.

When the server or the key file is not the worker's, the loop carries them as its `overseer`
(below), and the page shows the server's host beside its project. The worker is still the Claude
account it runs on.

A loop already on a bundle moves to its tracker with `agentloop retarget <loop> [<address>]` (item
`retarget`), keeping its paths. The tracker is the bundle's `tracker.server` unless an address is
given. The definition is read and checked as for an add, against the paths the loop has now, under
the names the bundle used. The tracker must be the bundle's own project, since the loop's folder
and run lock are named after it. A running loop is refused, and so is a copy (`like`): retarget the
loop it is like, and its copies move with it. Once the checks pass, the loop gets `tracker` in place
of `project`, its `definition.json` is kept and its tools fetched, and the bundle leaves `bundles`
once no loop uses it. Any path in `repos` or `places` inside a bundle's folder - this one's or
another's in `bundles` - is named as one to repoint, since a bundle need not stay once its loops
have moved.

### When the definition changes

A definition can change while a loop runs on it (item `doc-change`). A tool's sha256 is part of it,
so a changed tool is a change like any other: its files are fetched with it, wait in
`project-pending/` while it waits, and the ones it replaced stay in `project-previous/` until it
is dismissed. The diff names each tool added, removed or changed, with a changed text file's lines. `definition.json` is the
definition the person running the loop has **accepted**. The first read is accepted as it is:
starting the loop is agreeing to it. After that, the loop asks again between sessions, after a wait
for work, and when a run starts. Between sessions it asks with `GET /v1/source?since=<version>` of
`<name>.project.os`, which costs a `304` while the document has not changed. It reads the definition
again only when the document has changed, and only a definition that differs by its hash is a
change: the document written again with the same content is not one. A server that cannot be
reached between sessions is not a change either: the run goes on, and says so once.

What a change does is the loop's `acceptDefinition`. Only the machine says it, in its `defaults` or
a loop's `settings`. A project's settings may not, since it is the project that changes:

- **`"auto"`**, the default: the run goes on with the new definition at once. Its settings are taken
  again, and its system prompt is written again. The definition it replaced is kept in
  `definition-previous.json`. That is the oldest one not yet dismissed, so the change shown is all
  of it. The page marks the loop `definition changed` until someone dismisses it.
- **`"ask"`**: the change is kept in `definition-pending.json`, and `definition.json` is left as it
  is. The loop finishes the session it is in, then runs no more sessions. It is `held` on the page,
  marked `definition changed: accept?`, until the change is accepted. A run started while a change
  waits is held before its first session. Stop and kill still work while it is held. A change put
  back on the server before it was accepted is no change any more.

Under either setting:

- A repository or place in the new definition that the loop has no path for holds the loop, `held`,
  until the path is given in the loop's settings. Accepting does not give the path.
- A system the machine is not on stops the run, as it refuses a start.

To see the change and act on it:

- **On the page:** click the chip. It shows the diff: the description line by line, and the
  repositories, places, systems and settings added, removed or changed. Then **Accept** (or
  **Dismiss**, under `auto`), which is `POST /api/loops/<loop>/accept-definition`.
- **In a terminal:** `agentloop show-definition <loop>` prints the definition and the same diff.
  `agentloop accept <loop>` accepts a waiting change, or dismisses one already in use.

A running loop sees what is accepted within a few seconds, and goes on. A loop's JSON gains
`definitionChange` (`{"pending", "read_at", "diff"}`, or `null`) and `acceptDefinition`. While a
change waits, `definition` and `missing` are judged on the changed definition, so its paths can be
given before it is accepted.

A local bundle still works, for trying a brief out before it goes on the server; the page marks a
loop on one `local bundle`, and **Add from a bundle** and `import-psd1` still make them. They are
to be dropped eventually, so that a project is only ever read from its tracker (item
`drop-bundle-source`).

### Moving a bundle to its tracker

`agentloop export-definition <bundle> --out <folder>` writes a bundle out as what goes on the server
(item `def-export`), laid out as the tracker's folder there: `<name>.project.os`, and each tool in
`tools/<name>.project/` with its sha256 in the definition's `Tools`, for every system. The
description is the tracker's own, then the brief, less its opening paragraph ("The project brief for
the tracker ...") and its first table of `` `{name}` `` rows, whose lines become each repository's
and place's `about`. The tracker's description is read through the default worker's server, or
`--worker`'s, or given in a file with `--tracker-description`. The folder must be empty or missing.

Nothing is written to the server. The command prints what is left, for a person with a key that
may edit the tracker's folder: put the document beside the tracker; add
`- definition (source="<name>.project.os/definition", lazy=false)` to the tracker's project block,
so its mount reads it; put the tools there, with the `curl` line printed for each; and share the
document with the agents' account to read only. It also names any repository or place no line
describes, and a table left in the description because a row names a folder the project does not.

## Machine settings

One `settings.json`, in this OS's place for an app's settings.

| field | what it is |
| --- | --- |
| `format` | `1` |
| `bundles` | the paths of the bundle folders this PC knows. Each can sit wherever its owner keeps it; a new or imported one goes in `<config>/bundles/<name>` and is listed here. Item `config` reads each one's `project.json`. |
| `workers` | a list of workers, below |
| `defaultWorker` | the name of the worker a loop made on the page starts with; absent or `null` is the first worker (item `accounts`) |
| `loops` | a list of loops, below |
| `defaults` | run settings this PC changed from the app's, below |
| `install` | where the build is installed (item `build`); absent until a build is |
| `server` | the page's `host` (`127.0.0.1`) and `port` (item `ui-server`) |
| `claude` | the path of the `claude` to run, for a PC where it is not on `PATH`; absent or `null` finds it on `PATH` (`claude.cmd` on Windows) (item `session`) |
| `overseer` | none now. In settings written earlier, `{"library": ...}` named Overseer's library folder, where `tracker.py` found the template a tracker inherits, and `{"bin": ...}` the `overseer-server` it once served tracker files with. Both are let be and mean nothing: the server a worker names hands out the template too (item `own-server`) |

The "other projects' trackers" a session's prompt names are the other documents in its tracker's
folder on the server, as `tracker.py projects` lists them, where the script listed every `.psd1`.

### Workers

| field | what it is | today |
| --- | --- | --- |
| `name` | for the page; unique | |
| `claude` | the Claude config folder (`~/.claude-account2`), or `null` for the default account | `Account` |
| `overseer` | `{"server": "https://...", "keyFile": "~/.overseer/agents.key"}`, or `null` | `Server`, `KeyFile` |
| `manager` | an Overseer username (lower case letters, digits, `-`, `_`), or absent or `null`: the loops of this worker take the items whose `owner` it is first, before those flagged `first` | |

`claude: null` means no `CLAUDE_CONFIG_DIR` in the session's environment at all, not one naming
`~/.claude`: given one, `claude` keeps its state inside it and starts as if new (the script's
`Account` handling). A worker's `claude` that is not an existing folder is an error on start.
A loop on a project whose tracker is on a server
needs a worker with `overseer`; the page offers only those, and a start without one is refused.
The server and key are the worker's, not the machine's, so one machine works trackers on two
servers through two workers, one for each (as in [the example](examples/settings.json)).
`tracker.py` reads the project template a tracker inherits from that same server, so nothing of
Overseer is needed on the machine.

**The Claude half** (item `accounts`, `agentloop accounts` until the page has it):
- **Found:** `~/.claude` as the default account and every `~/.claude-account*` folder that no
  worker names yet are offered to be added, named `default` or the folder's name less `.claude-`.
  `~/.claude` named by its path is written `null`.
- **Logged in or not** is asked of claude itself: `claude auth status --json`, in the environment a
  session gets. It makes no model call, so it costs no usage, and it reads the keychain on macOS,
  where `.credentials.json` means nothing. It writes into the folder it is given, so it is never
  run on a folder that does not exist. If claude cannot answer, `.credentials.json` is looked at
  instead, except on macOS. The email and organisation it also says are never kept or shown.
- **Logging in** is a person's: the command is `claude auth login` with `CLAUDE_CONFIG_DIR` set to
  the folder (removed for the default account), written for PowerShell on Windows and sh elsewhere.
- **Two loops on one account** share its usage: two workers on the same Claude folder are one
  account, whatever their `overseer`.
- **The last usage** a worker's sessions reported - the stream's `rate_limit_info`, and when - is
  kept in `<data>/workers/<name>/usage.json`, so the page can show it before a loop runs. Nothing
  asks for it outside a session: that would be a model call.

### Loops

| field | what it is | today |
| --- | --- | --- |
| `name` | unique on this machine; the page's name for it | |
| `project` | a bundle's name; absent when `tracker` is given | the script's `$Project` |
| `tracker` | in place of `project`: the tracker's name on the server, e.g. `alice/projects/x.os`, whose definition the loop reads; the project is `x` | |
| `overseer` | absent for the worker's, or `{"server": address, "keyFile": path}`: the server and key file this loop reaches its tracker with in place of its worker's - a tracker on another server. A copy takes the loop's | |
| `worker` | a worker's name | |
| `repos` | a map from each repository name the bundle uses, the tracker's included, to a folder on this PC | `Repos` |
| `places` | a map from each place the bundle names to a folder on this PC; never checked with git, never committed | |
| `settings` | this loop's overrides of the run settings | the script's parameters |
| `like` | absent, or another loop's name: this loop is a copy of it | |

Each path in `repos` is a folder inside a git repository; the repository is its top
(`git rev-parse --show-toplevel`, as `Get-RepoTop` does), and that is what is committed. So
`orchard-docs` may be placed at `.../Orchard-Docs/shared`, and `path` is relative to it.
Two names that resolve to one repository are one repository. A name the bundle uses and the loop
does not place, or a folder in no repository, is an error on start, and the page asks for it.

**A copy of a loop** (`like`) is how a second loop on a project is written without repeating the
first. It takes the other loop's `project`, `worker`, `places` and `settings`, and each repository
it does not place itself, so it is usually three keys:

```json
{"name": "agentloop-2", "like": "agentloop", "repos": {"agentloop": "~/src/AgentLoop-2"}}
```

- It may set `worker` too, to run on another Claude account, which is usually why a second loop
  is run. Its `settings` are laid over the other loop's, as any layer is; its `places` replace the
  other's one by one, as its `repos` do. A `project` or `tracker` other than the other loop's is refused.
- The loop it names may not be a copy itself: one level only, so there is no chain and no cycle.
- A change to the original reaches its copies, and the file keeps only what a copy gives itself.
- A loop that has copies cannot be deleted or renamed; the refusal names them.

On the page, each loop's **Copy** opens a new loop with `like` set, asking for its name and the
folders of its repositories; the other fields show what is copied, greyed, and can be set here.

**Several loops on one project** run at once, each in its own clones, and claims keep them from
taking the same item (item `tracker-claim`). Starting one beside a loop of the project that commits
in a repository it does (their placed tops overlap) is refused, with the other loop named: each
commits in its own.

A loop that cannot start for that reason, or because the script works the project, reads `busy`
on the page, saying which. `attempts.jsonl` is the project's, appended by each of its loops under a
lock, and repeats are counted over all of them: the item is the same.

## Run settings

One set of keys, wherever they are written:

| key | default | the script's |
| --- | --- | --- |
| `routes` | `triage` opus/medium, `trivial` sonnet/medium, `low` sonnet/high, `medium` opus/medium, `high` opus/high, `unassigned` opus/medium | `$Routes` |
| `escalation` | opus/medium | `$Escalation` |
| `buildAfterTriage` | `["medium", "high"]` | `$BuildAfterTriage` |
| `buildInTriageUpTo` | `2` | `$BuildInTriageUpTo` |
| `trial` | the script's, or `null` for none: `{"until": "2026-10-29", "complexity": "medium", "upTo": 3, "route": {...}}` | `$Trial` |
| `maxIterations` | `20` | `-MaxIterations` |
| `stopAtWeeklyUse` | `1.0` | `-StopAtWeeklyUse` |
| `askEveryMinutes` | `30` | `-AskEveryMinutes` |
| `chainUpTo` | `3` | `-ChainUpTo` |
| `repeatUpTo` | `2` | `-RepeatUpTo` |
| `chainContextUpTo` | `120000` | `-ChainContextUpTo` |
| `model` | none: every session on its route | `-Model` |
| `push` | `false` | none: the script never pushes |
| `pushTries` | `3` | none |
| `autoStart` | `false` | none: the script ends on an empty queue |
| `autoStartMinutes` | `10` | none |
| `acceptDefinition` | `"auto"`; or `"ask"`. Not in a project's settings | none: see "When the definition changes" |

A route is `{"model": "opus", "effort": "medium"}`.

With `autoStart` on (item auto-start, the Auto button in the loop's row), a loop that finds nothing
to work does not end: it stays alive, `idle`, and asks the tracker what comes next every
`autoStartMinutes`, which spends no session. It holds its run lock and the script's mutex while it
waits, as a running loop does. An item that comes up begins a new run, its `maxIterations` and
repeats counted afresh. A stop or a kill ends the wait at once, and so does turning the setting off,
which a running loop reads from this file; everything else that ends a loop still ends it, and a
loop that has ended is not started again.

With `push` on (item git-sync), loops on several machines work one branch. Before each session the
loop fetches each clone's upstream and fast-forwards a clone that is only behind it; a clone whose
uncommitted changes are in files the pull would change is left as it is. After the session's
commit it fetches again, replays the clone's commits onto the upstream if it has moved on, and
pushes `HEAD` to the upstream's branch, never with `--force`; a push refused because another loop
pushed first is fetched, replayed and tried again, `pushTries` times in all. A clone with no
upstream, or on a detached HEAD, is neither pulled nor pushed. The push sends every commit the
clone has that the upstream has not, the owner's own included.

The replay runs in a worktree of its own, `logs/<project>/loops/<loop>/sync/<repository>`, never in
the clone's working tree, which may hold the owner's uncommitted work; the clone is then moved to
its result with `git reset --keep`, which refuses, changing nothing, if a file it would change has
local changes - the commits are then left local, and said so. A replay that conflicts resumes the
session that made the commit, with a prompt of its own: it resolves the files in that worktree and
ends with `MERGED: <how>`, or leaves them and ends with `UNMERGED: <why>`. Unresolved - or with
conflict markers left, or anything changed in the clone itself - the replay is aborted, the commit
left local, and the loop stops. A push that fails otherwise - the remote out of reach - leaves the
commits local, and the next session's push carries them.

Nothing in the app keeps a push from rewriting the remote's history but its never using `--force`;
a ruleset on the branch that blocks force pushes and deletions, with no bypass, makes sure of it.

Read in this order, each layer overriding the one before:

1. the app's defaults (the script's values, in the package);
2. the machine's `defaults`;
3. the bundle's `settings`;
4. the loop's `settings`.

The project's choice beats a machine-wide default, and a loop's own beats both. `routes` merge per
key, as the script merges `$Routes` (a layer that names `low` replaces `low` only); every other key
replaces. An unknown key is an error, so a misspelling does not pass silently. `loop-settings.json`
(permissions and the guard hook) is not a setting: it stays the app's, and is written for each
session with this machine's paths (item `tools`).

## Where things live

Found with `pathlib` and the environment; no dependency.

| | Windows | macOS | Linux |
| --- | --- | --- | --- |
| **config** (`settings.json`, `bundles/`) | `%APPDATA%\agentloop` | `~/Library/Application Support/agentloop` | `$XDG_CONFIG_HOME/agentloop`, default `~/.config/agentloop` |
| **data** (logs, state) | `%LOCALAPPDATA%\agentloop` | `~/Library/Application Support/agentloop` | `$XDG_STATE_HOME/agentloop`, default `~/.local/state/agentloop` |

The data folder is large and is not for roaming, hence `%LOCALAPPDATA%`. `AGENTLOOP_HOME`, when
set, puts both in that one folder (`config/` and `data/` inside it); tests and a trial copy use it.
Only Windows has been tried; the macOS and Linux paths follow those systems' conventions and are
tried by item `other-os`.

### Logs, records and loop state

Never in a bundle: they hold transcripts of the owner's code, and a bundle is handed to others.

```
<data>/logs/<project>/
    attempts.jsonl          per project: Test-Tried and repeat detection are per item
    attempts.lock           taken by each loop of the project to append to it
    <session logs>          one JSON-lines file per session, as the script's logs\<project>
    loops/<loop>/
        running.json        the session at work, for kill to end and name
        stop                made by stop; the loop ends once the item at work is done
        kill                made by kill; the loop ends at once
        system.md           AUTOTASK.md, the tracker's description and the brief, once a run;
                            for a project read from its tracker, the definition's description
        definition.json     the definition last read and accepted, also for a run that cannot
                            reach the server
        definition-pending.json   a changed definition waiting to be accepted (acceptDefinition ask)
        definition-previous.json  the definition a change used at once replaced, until dismissed
        project/tools/      the project's tools fetched from its tracker for definition.json;
                            project-pending/ and project-previous/ for the other two
        session-settings.json  the settings file each session's --settings names
        scratch/            the sessions' scratch folders
        status.json         its state, the item and session at work, the run's cost, the usage
                            line, and how it ended: what the page shows of it
        started.json        the pid the runner started it as, for the moment before it says so
        output.log          what it printed this run; output.prev.log, the run before
```

A loop started from the page is a process of its own (`agentloop start`), and goes on when the
app closes; an app started again finds it from these files. It is alive while it holds its own
lock, `<data>/loops/<project>/<loop>.lock`, the OS's lock on that file, which the OS lets go of
when the loop dies.

On Windows the script's own `Local\run-agent-loop-<project>` mutex keeps the app and the script from
working one project at once. A mutex has one owner: the first of the app's loops on the project
takes it, and a later one goes on without it only while another of the app's loops on the project
is alive - otherwise the script has it, and the start is refused. A loop without the mutex tries to
take it again between items, so it holds it once the loop that had it ends. That leaves a short gap,
the first loop ended and the script started before the other retakes it, which is let be: the
script only adds a loop that ignores claims. macOS and Linux have no script, and only the loops'
own locks.

What is per loop (the files that name one running process) is under `loops/<loop>`; what is about
the project's items is above it. A loop deleted or renamed leaves its folder; the page offers to
remove it.

## What a session is told of the machine

`system.md` holds a "Where you are" section after `AUTOTASK.md`: the OS, the shell the Bash tool
runs (and on Windows the PowerShell tool), the Python to run the tools with, the app's tools
folder, the bundle's folder, and each repository's and place's name with its folder on this PC.
`AUTOTASK-windows.md` is added after `AUTOTASK.md` on Windows only.

Where `AUTOTASK.md` or the brief writes `{name}`, it is filled in with this machine's value, with
forward slashes: `{tools}` the app's tools folder (`{tools}/tracker.py`), `{bundle}` the bundle's
folder (`{bundle}/tools/look.ps1`) - for a project read from its tracker, the folder its tools
were fetched into - `{python}` the Python the app runs on (quoted if its path holds
a space), and each repository and place by its name (`{quill}/tools/patchtest`,
`{press}`). A `{word}` that is none of those is left as it is, so code in a brief comes
through whole; a brief can name only what its bundle lists, and a loop that does not place one
is refused on start. The tracker's own description is not filled in.

## Examples

Two made-up projects, in the shapes the script's `.psd1` files take; `config` tests that they
load.

- [`examples/quill/project.json`](examples/quill/project.json) - a tracker on a server.
- [`examples/orchard/project.json`](examples/orchard/project.json) - three repositories, and the
  systems it runs on.
- [`examples/settings.json`](examples/settings.json) - two workers, a loop for each, and a changed
  default.

## Importing a `.psd1`

`agentloop import-psd1 <file>` makes a bundle, a worker and a loop from one of the script's
`projects/<name>.psd1` files. It reads only what those files use: hashtables, arrays, strings,
numbers, `$true`, `$false`, `$null` and comments. Anything else is refused, and the message names
the line.

- **The bundle** goes in `<config>/bundles/<name>`, or in `<folder>/<name>` with `--to <folder>`,
  with `<name>.md` as `brief.md` and `<name>/` as `tools/` (less what Python leaves there,
  `__pycache__`), and is added to `bundles`.
- **Each repository** is named after the folder at its top (`git rev-parse --show-toplevel`),
  lower-cased, with any other character made `-`. `studio\Loom Game` becomes `loom-game`. A
  docs repository cloned at `Notes\shared` has that folder for its top, so it becomes `shared`;
  the examples' `orchard-docs` is a name chosen by hand.
- **The tracker** is the `.psd1`'s `Tracker`, its name on `Server`. A `.psd1` whose tracker is a
  file - one with no `Server` - is refused: the tracker goes on the server first.
- **The worker** comes from `Account` (`N` is `~/.claude-accountN`, none is `1`, `~/.claude` is
  `null`) and from `Server` and `KeyFile`. A worker on the same Claude folder is reused, and given
  the server if it had none. A worker on another server is made anew.
- **The loop** is named after the project, and places each repository at the folder written in
  the `.psd1`.
- **What it cannot know** is left to be done by hand: the bundle's `systems` and `places`, the
  loop's `places`, and the brief's paths written as placeholders.

A bundle or loop of that name already there is refused; `--replace` replaces it.

The projects `run-agent-loop.ps1` worked are read from their trackers now: each one's definition
and tools sit beside its tracker on the server, and the repository holds no bundles of its own
(item `drop-bundles`). AgenticLoop's `projects/` stays as it is while the script runs beside the
app (item `switchover`).

## Not settled here

- Importing a zipped bundle, and the page's editing of all of the above: items `config` and
  `ui-page`.
