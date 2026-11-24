# Projects and machine settings

What each of AgentLoop's settings holds, how it is written, and where it lives. The example settings
in [`examples/`](examples/) are loaded by the tests, so they stay ones that load.

A loop is made of three things:

| | is | lives in | handed to someone? |
| --- | --- | --- | --- |
| **Project** | its definition, a document beside its tracker, and its tools beside that | on the Overseer server | by the tracker's address, and a share |
| **Worker** | who does the work: a Claude account and an Overseer account | machine settings | no |
| **Loop** | a project, a worker, and where the project's repositories are on this PC | machine settings | no |

A loop is what the page starts, stops, kills and starts again. A project holds nothing of a PC: no
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


## The project

A project is defined by a document beside its tracker: `<name>.os` keeps it in
`<name>.project.os`, made from Overseer's `projects/project_definition.os` and mounted read-only as
`definition`, so an agent's key can read it and the server refuses it a write. AgentLoop needs
only the tracker's address: one `GET /v1/node` of `project/definition/definition` through the
tracker gives the lot. [`examples/quill.project.os`](examples/quill.project.os) is one.

| field | what it is |
| --- | --- |
| `description` | what the project is, its rules and how to check work on it: given to sessions as `# What the project is`, with `{name}` filled in (see "What a session is told of the machine") |
| `Repos` | the repositories the project works in, in order, each with `name` and `about`, a line saying what it is; sessions run in the first |
| `Places` | the folders the description names that are not repositories (`press`), each with `name` and `about`; may be empty |
| `systems` | the systems the project runs on, from `windows`, `macos` and `linux`, comma-separated; empty for every one |
| `settings` | the project's overrides of the run settings (below), as JSON text; may be empty |
| `Tools` | the project's own scripts: each `file` with its `sha256` and the `systems` it is for (empty for every one) |

The project's name is the tracker's, without `.os` - `quill` for `quill.os` - because
`<project>:<handle>` names depend on it. The tracker itself is never a file in a repository: every
edit is sent to the server as it is made.

A repository name is lower case letters, digits, `-` and `_`. It is the project's own word for a
repository (`orchard`, `orchard-server`, `orchard-docs`), not a folder name on any PC. A place is
named like a repository, and is neither a repository's name nor `tools`, `bundle` or `python`
(below). A loop on a project whose `systems` leaves out this OS is refused on start, saying which
systems the project runs on. An error names the field: `alice/projects/x.os definition:
settings.nope: no such setting`.

Not in the definition: the server's address and the key file (the worker's), and the
repositories' folders (the loop's).

### Its tools

The tools are plain files kept beside the definition document on the server, in
`tools/<name>.project/`, which a read share is enough to fetch and only an edit share can change.
`tracker.py`, `features.py`, `guard.py` and `AUTOTASK.md` are not among them: they are the app's.
The ones for this machine's system are fetched into `loops/<loop>/project/tools/`, and `{bundle}`
names `loops/<loop>/project`, so a description runs `{bundle}/tools/probe.ps1`. A file whose
sha256 is not the one listed is never written, and refuses the start or the add, naming the file.
A text file's sha256 is of its text with CRLF and a lone CR read as LF, so a copy whose line
endings git rewrote is still the file listed; a file with a NUL byte in its first 8000 bytes is
binary and hashed as it is. A fetched file is written as the server gave it, but a `.cmd` or
`.bat` on Windows is written with CRLF, which cmd.exe needs. A project that lists none for this
system has no `{bundle}`. A path in `Tools` is relative, with `/`, plain names and no `..`.

### Its setup script

A tool ticked **Setup** in the definition is the script that installs what the project's sessions
need: a compiler, Node, a browser. It is the setup for the systems that tool is for, and a system
has at most one, so a project gives `setup.sh` for `linux, macos` and `setup.ps1` for `windows`, or
one `setup.py` for every system. It is fetched and checked by its sha256 like any tool. A changed
one is a changed definition, so under `acceptDefinition` `"ask"` it does not run until the change
is accepted.

A loop runs it before its first session, and again before the next session whenever its sha256
changes or its last run failed. It runs as the person running AgentLoop, never as root: a loop
running as root refuses it. It is started by its kind: `.ps1` with `pwsh` (else Windows
PowerShell), `-NoProfile -ExecutionPolicy Bypass -File`; `.sh` with `bash` (else `sh`); `.py` with
the Python the app runs on; `.cmd` and `.bat` with `cmd /c`; anything else as itself. Its working
folder is the project's own, `<data>/projects/<project>/env`, and it gets the app's environment
with:

| Variable | What it is |
| --- | --- |
| `AGENTLOOP_PROJECT_ENV` | the project's folder, with forward slashes: install here |
| `AGENTLOOP_SYSTEM` | `windows`, `macos` or `linux` |
| `AGENTLOOP_PROJECT` | the project's name |

That folder is shared by every loop of the project on the machine, and outlives upgrades, and a
container's redeploys when the data folder is a volume. The script writes what it installs there,
`CARGO_HOME`, `RUSTUP_HOME`, a Node folder or a browser cache, and should be safe to run again: it
skips what is already there. Installing system packages is not its job, since that needs root.
On a bare machine it checks for them, and fails naming what is missing.

It leaves `session-env.txt` in the folder to say what every session gets, one line each:

```
# comments and blank lines are skipped
PATH={env}/cargo/bin
CARGO_HOME={env}/cargo
RUSTUP_HOME={env}/rustup
```

`PATH=<folder>` puts a folder in front of PATH, in the order written. `NAME=value` sets a variable.
`{env}` is the project's folder, so the file stays right if the data folder moves. A variable the
app sets for a session itself, anything starting `OVERSEER_`, `CLAUDE_`, `ANTHROPIC_`, `AGENT_` or
`AGENTLOOP_`, is refused, as is a line that is neither form. Every command a session runs gets
these, including the hooks Claude runs, and its system prompt names the folder.

The script's output goes to the loop's output on the page. If it exits with anything but 0, or
leaves a file that does not read, the loop does not start. The loop's row on the page then shows
"setup failed" with the end of the output. **Set up again** in the row runs the script again. On a
loop that is stopped it runs at once, in a run that sets up and ends. On a loop that is running it
runs before the next session. `agentloop setup <loop>` runs it in a terminal if it is due, and
`--again` runs it even if it is not. What the last run did is kept in
`<data>/projects/<project>/setup.json`.

A machine that already has the tools, such as the owner's own, sets `setup` to `"off"`, in its
`defaults` or in a loop's settings.

A project's own script can keep its results in its tracker, in a list the project's document
declares of its own - a tab it added, as Brocessor keeps its benchmark runs:

```sh
"$AGENTLOOP_PYTHON" "$AGENTLOOP_TRACKER" append <list> @record.json
```

Every session has `AGENTLOOP_TRACKER`, the path of `tracker.py`, and `AGENTLOOP_PYTHON`, the
Python to run it with; the server, the key file and the tracker are set for it already, so the
script never handles the key. `<list>` is the list's address under the tracker's root, and the
record is a JSON object: each field typed by its JSON - text, a whole number, any other number,
true or false - or given as `{"Timestamp": "..."}`, and a list of objects appended under the new
entry. The lists `tracker.py` governs - Items, History, Features and the template's others - are
refused. Run by hand, outside a session, the same is `agentloop tracker <loop> append <list>
@record.json`, which runs `tracker.py` with that loop's server, key and tracker.

```sh
#!/bin/sh
# setup.sh: Rust for the project's sessions, in its own folder.
set -e
export CARGO_HOME="$AGENTLOOP_PROJECT_ENV/cargo" RUSTUP_HOME="$AGENTLOOP_PROJECT_ENV/rustup"
command -v cc >/dev/null || { echo "missing: a C compiler (build-essential)"; exit 1; }
[ -x "$CARGO_HOME/bin/cargo" ] || curl -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path
cat > "$AGENTLOOP_PROJECT_ENV/session-env.txt" <<'EOF'
PATH={env}/cargo/bin
CARGO_HOME={env}/cargo
RUSTUP_HOME={env}/rustup
EOF
```

```powershell
# setup.ps1: the same on Windows.
$ErrorActionPreference = 'Stop'
$env:CARGO_HOME = "$env:AGENTLOOP_PROJECT_ENV/cargo"; $env:RUSTUP_HOME = "$env:AGENTLOOP_PROJECT_ENV/rustup"
if (-not (Test-Path "$env:CARGO_HOME/bin/cargo.exe")) {
    Invoke-WebRequest https://win.rustup.rs -OutFile rustup-init.exe
    ./rustup-init.exe -y --no-modify-path; if ($LASTEXITCODE) { exit $LASTEXITCODE }
}
Set-Content -Encoding utf8 "$env:AGENTLOOP_PROJECT_ENV/session-env.txt" "PATH={env}/cargo/bin`nCARGO_HOME={env}/cargo`nRUSTUP_HOME={env}/rustup"
```

### Its Brewfile

On a machine where `AGENTLOOP_BREW` is `1`, which the container image sets (item `brew-tools`),
a project's tools come from Homebrew, listed in a `Brewfile` at the top of its first repository, in
Homebrew's own format (`brew "jq"`, `brew "node@22"`). Homebrew itself is the machine's, one prefix
shared by every project (in the container, a volume: [container.md](container.md)).

| | |
| --- | --- |
| Before each session | `brew bundle check`; when something is missing, `brew bundle install --no-upgrade --file=<Brewfile>`, its output in the loop's |
| A session that needs a tool | adds its line to the Brewfile and runs that same install itself, then goes on; the loop commits the Brewfile with its work |
| `brew install`, `upgrade`, `tap`, `uninstall` | refused by the guard, so the Brewfile stays the one record |
| An install that fails | the loop does not start, and says so; fix the Brewfile and start it again |
| No Brewfile | nothing is installed; the session is still told how to start one |

Anywhere `AGENTLOOP_BREW` is not `1` (a person's own Mac or Windows PC), none of this runs and
sessions are not told of it. It is never on for Windows. It runs before the setup script, which a
project may still have for what Homebrew does not give, so that script can use what the Brewfile
installs: in the container, a script that runs `npm` needs `brew "node"` in the Brewfile.

### The copy a loop keeps

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

A repository the definition gives an address, its `url` and maybe a `branch` (item `clone`), shows
that address beside its folder, and the folder is offered: beside the folder given for another of
the project's repositories, else `<AGENTLOOP_CODE>/<repo>` where that variable names the machine's
one folder of clones (the container's `/code`), else `<home>/<project>/<repo>` under this machine's
home. **Clone here**
clones it into that folder when the folder is missing or empty, and uses it as it is when it is a
clone of that address already, over https or ssh alike. A folder holding anything else is refused,
by name, and nothing in it is touched. The clone is the person's own git with their own
credentials; on the page git asks for no password, so a repository that needs one it does not
keep fails. A GitHub address is cloned in the form this machine signs in with (see "HTTPS or SSH"
below). A clone that fails says which repository and stops: nothing is retried.
`agentloop add --clone` clones each `--repo` path the same way before the add checks it, and on a
terminal `agentloop add` offers the folder and asks before cloning. A repository with no address
asks for a folder, as above.

When the server or the key file is not the worker's, the loop carries them as its `overseer`
(below), and the page shows the server's host beside its project. The worker is still the Claude
account it runs on.

### A project of the script's

A project of `run-agent-loop.ps1` is a `projects/<name>.psd1` with its brief `<name>.md` and its
tools folder `<name>/` beside it. `agentloop import-psd1 <file> --out <folder>` writes it out as
what goes beside its tracker: the definition `<name>.project.os` - the tracker's own description,
then the brief, less its opening paragraph and its table of `{name}` rows, whose lines become each
repository's and place's `about` - and its tools under `tools/<name>.project/`. The tracker's
description is read with the `.psd1`'s `Server` and `KeyFile`, or given with
`--tracker-description <file>`. It prints the steps left - putting the files on the server, the
line that points the tracker's mount at the definition, sharing it with the agents to read only -
and the `agentloop add` line that adds the loop once they are done, with the `.psd1`'s key file and
repositories. It writes nothing to this machine's settings.

There are no local bundles: a project is only ever read from its tracker (item
`drop-bundle-source`). Settings that list `bundles`, or a loop that names a `project` in place of
its `tracker`, are refused, saying so; the empty `bundles` list settings written earlier hold is
let be, and dropped when the settings are next saved.


### Items for one bot

An item is kept for one bot by its `for` column - the template's `assignee`, an account picked from
the server's directory (item `for-col`). An item for an account is free only to the loops whose key
signs in as it; the others list it as `held for <name>`, as below, and a claim of it refuses, saying
whom it is for. A loop that claims an item for no one gives it to its own account, and its release
leaves it so: from then on only that bot's loops take the item, and the build after a triage, or any
later session, stays with it. To hand an item to another bot, or back to every loop, change or clear
its `for` column on the page. An item filed by a session is for no one until a loop takes it.

The column, when set, decides alone: the mentions below count only on an item for no one. An item is
kept for particular bots by mentioning them: `@` and the Overseer account a bot's key
signs in as, by its display name as the page shows it (its bold, colours and icons taken out, as
Overseer's own `people::plain` does) or by its username, in any case. A display name may hold
spaces, `@Zombo Bot`, and the longest name that fits is the one meant. A mention that runs on into a
letter, a digit, `-` or `_` names no one, and neither does an `@` inside a word, as in an address.

A mention counts in two places:

- **The title**, anywhere in it. It holds until the title is edited.
- **The end of the last comment not written by a loop**: the run of mentions it ends with, with
  only spaces, commas and closing punctuation after them, as in `... rework it. @Zombo Bot` or
  `@a, @b`. A mention in the middle of a comment does not count. A comment is a loop's when its
  status names a model, as `tracker.py` signs every comment a session adds, so a bot's replies
  never let go of an item. A person's later comment does: unless it ends with a mention too, the
  item is open to every loop again.

An item mentioning accounts is free only to the loops whose key signs in as one of them. Other loops
never take it, not even through a session's `NEXT:`, and their `tracker.py queue` lists it as
`held for <names>`. Any account can be mentioned, a person's too: an item ending with `@ap31` is
then held for that person, and no loop takes it. An item mentioning no one is open to every loop.

The names come from the server's directory of people, which a tracker mounts as `people` in its
project (`mount people (source="../../people.os/People", ...)`), as the project template expects.
A tracker that does not mount it knows only the loop's own account, by its username and display
name. The directory is read only when some item holds an `@` where a mention would count. The
`bot-<username>` tags of 0.1.8 mean nothing now.

A loop that sees items held for other accounts writes them, with its own account, into its status. The page then marks its row with `N items for bots not run here` when
no running loop on this machine, on the same tracker, signs in as any of those accounts. A loop on
another machine may still run them, so this is a warning and nothing more.


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


## Machine settings

One `settings.json`, in this OS's place for an app's settings.

| field | what it is |
| --- | --- |
| `format` | `1` |
| `workers` | a list of workers, below |
| `defaultWorker` | the name of the worker a loop made on the page starts with; absent or `null` is the first worker (item `accounts`) |
| `loops` | a list of loops, below |
| `defaults` | run settings this PC changed from the app's, below |
| `install` | where the build is installed (item `build`); absent until a build is |
| `server` | the page's `host` (`127.0.0.1`) and `port` (item `ui-server`). Another `host` (`0.0.0.0`) puts the page on the network; `AGENTLOOP_HOST` and `serve --host` go before it. `names`: the Host names it may be reached by when it does, each `host` or `host:port`; absent lets in any. `tokenFile`: the path of a file holding the page's token, at least 16 characters; `AGENTLOOP_TOKEN` goes before it, and with neither a new token is printed at each start. The token itself is never a setting (item `serve-lan`, and `docs/install.md`) |
| `claude` | the path of the `claude` to run, for a PC where it is not on `PATH`; absent or `null` finds it on `PATH` (`claude.cmd` on Windows) (item `session`) |
| `overseer` | none now. In settings written earlier, `{"library": ...}` named Overseer's library folder, where `tracker.py` found the template a tracker inherits, and `{"bin": ...}` the `overseer-server` it once served tracker files with. Both are let be and mean nothing: the server a worker names hands out the template too (item `own-server`) |

The "other projects' trackers" a session's prompt names are the other documents in its tracker's
folder on the server, as `tracker.py projects` lists them.

### Workers

| field | what it is | today |
| --- | --- | --- |
| `name` | for the page; unique | |
| `claude` | the Claude config folder (`~/.claude-account2`), or `null` for the default account | `Account` |
| `overseer` | `{"server": "https://...", "keyFile": "~/.overseer/agents.key"}`, or `null` | `Server`, `KeyFile` |
| `manager` | an Overseer username (lower case letters, digits, `-`, `_`), or absent or `null`: the loops of this worker take the items whose `owner` it is first, before those flagged `first` | |
| `signIn` | absent or `null` for the login in its `claude` folder; `{"token": VARIABLE}` for a token made by `claude setup-token`, or `{"apiKey": VARIABLE}` for an API key, billed per use. `VARIABLE` names the environment variable that holds it where agentloop runs. It is never the secret itself | |
| `limits` | absent or `null` for none, or the share of each usage window its loops may use: `{"five_hour": 0.8, "seven_day": 0.9, "overage": 0}`. A share is 0 to 1, `null` for none, and the page enters it as %. Any window claude's reports name may be given. `overage` is the extra usage past the plan's allowance: reports say whether it is used, never how much, so its one limit is `0`, none of it. See "A worker's limits" below | |

**A worker's limits** (item `h87x`): before each session - and before a triage goes on to its
build, or a session to another item - the loop checks the worker's last usage report, which every
loop on the worker keeps (`workers/<name>/usage.json` in the data folder). Past a window's limit, it
pauses: no session runs, its state is `paused`, and its row and output say which window, how much is
used and when it resets. A session already running is never ended for it, so one may go a little
past. The pause ends by itself once the window resets, or once going on past it is approved: the
page's **Go on past the limit**, on the loop's row or the worker's card
(`POST /api/workers/<name>/approve-limit`, `{"window": ...}` or `{}` for the first), or
`agentloop approve-limit <worker> [window]`. An approval is the worker's, kept in
`workers/<name>/approvals.json`, so every loop on it goes on, and lasts until that window resets;
then the limit holds again. A stop or a kill ends a pause. The worker's weekly limit takes the place
of the loop setting `stopAtWeeklyUse`, which stopped a loop at a share of the week and is retired: a
settings file or a project's definition that still names it loads, and it is ignored.

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
- **A token or an API key** (item `claude-container`, for a container): the worker's `signIn`
  names the variable that holds it. Each session gets it as `CLAUDE_CODE_OAUTH_TOKEN` or
  `ANTHROPIC_API_KEY`, read when the session starts, and the other of the two is removed. A worker
  with no `signIn` leaves both as the app was started with them. A variable that is not set
  refuses the loop's start, naming the variable. Its login is asked of `claude auth status` with
  the variable set, and no folder is needed. The secret is never written to the settings, a log,
  the page or a resume command. A worker on an API key has no five-hour or weekly limit. Its
  session counts as limited only when a rate report refuses it, never by the words of an error, so
  an API rate limit is a failed session and not a wait. Its usage line says it is billed per use.
- **Logging in inside a container:** `agentloop accounts login <worker> --run` runs the login in
  the terminal it is given (`docker exec -it`). It makes the worker's folder first, and runs
  `claude setup-token` for a token worker. claude prints an address to open on any machine and
  reads the code back.
- **Two loops on one account** share its usage: two workers on the same Claude folder are one
  account, whatever their `overseer`.
- **The last usage** a worker's sessions reported - the stream's `rate_limit_info`, and when - is
  kept in `<data>/workers/<name>/usage.json`, so the page can show it before a loop runs. Nothing
  asks for it outside a session: that would be a model call.

### Loops

| field | what it is | today |
| --- | --- | --- |
| `name` | unique on this machine; the page's name for it | |
| `tracker` | the tracker's name on the server, e.g. `alice/projects/x.os`, whose definition the loop reads; the project is `x` | the script's `$Project` |
| `overseer` | absent for the worker's, or `{"server": address, "keyFile": path}`: the server and key file this loop reaches its tracker with in place of its worker's - a tracker on another server. A copy takes the loop's | |
| `worker` | a worker's name | |
| `repos` | a map from each repository the definition names, the tracker's included, to a folder on this PC | `Repos` |
| `places` | a map from each place the definition names to a folder on this PC; never checked with git, never committed | |
| `settings` | this loop's overrides of the run settings | the script's parameters |
| `like` | absent, or another loop's name: this loop is a copy of it | |

Each path in `repos` is a folder inside a git repository; the repository is its top
(`git rev-parse --show-toplevel`, as `Get-RepoTop` does), and that is what is committed. So
`orchard-docs` may be placed at `.../Orchard-Docs/shared`, and `path` is relative to it.
Two names that resolve to one repository are one repository. A name the definition uses and the loop
does not place, or a folder in no repository, is an error on start, and the page asks for it.

**A copy of a loop** (`like`) is how a second loop on a project is written without repeating the
first. It takes the other loop's `tracker`, `overseer`, `worker`, `places` and `settings`, and each repository
it does not place itself, so it is usually three keys:

```json
{"name": "agentloop-2", "like": "agentloop", "repos": {"agentloop": "~/src/AgentLoop-2"}}
```

- It may set `worker` too, to run on another Claude account, which is usually why a second loop
  is run. Its `settings` are laid over the other loop's, as any layer is; its `places` replace the
  other's one by one, as its `repos` do. A `tracker` other than the other loop's is refused.
- The loop it names may not be a copy itself: one level only, so there is no chain and no cycle.
- A change to the original reaches its copies, and the file keeps only what a copy gives itself.
- A loop that has copies cannot be deleted or renamed; the refusal names them.

On the page, each loop's **Copy** opens a new loop with `like` set, asking for its name and the
folders of its repositories; the other fields show what is copied, greyed, and can be set here.

**Several loops on one project** run at once, each in its own clones, and claims keep them from
taking the same item (item `tracker-claim`). The tracker's page frames a claimed item in green,
with the name of the agent at work - its model and effort - over it. Starting one beside a loop of the project that commits
in a repository it does (their placed tops overlap) is refused, with the other loop named: each
commits in its own.

A loop that cannot start for that reason, or because the script works the project, reads `busy`
on the page, saying which. `attempts.jsonl` is the project's, appended by each of its loops under a
lock, and repeats are counted over all of them: the item is the same.

## Run settings

One set of keys, wherever they are written:

| key | default | the script's |
| --- | --- | --- |
| `routes` | `triage` opus/medium, `trivial` sonnet/medium, `low` sonnet/high, `medium` opus/medium, `high` opus/high, `unassigned` opus/medium, `voice` sonnet/medium | `$Routes`; `voice` is not the script's |
| `escalation` | opus/medium | `$Escalation` |
| `buildAfterTriage` | `["medium", "high"]` | `$BuildAfterTriage` |
| `buildInTriageUpTo` | `2` | `$BuildInTriageUpTo` |
| `trial` | the script's, or `null` for none: `{"until": "2026-10-29", "complexity": "medium", "upTo": 3, "route": {...}}` | `$Trial` |
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
| `setup` | `"auto"`; or `"off"`. Not in a project's settings | none: see "Its setup script" |
| `leftSessions` | `3` | none: see "Files left uncommitted" |
| `leftHours` | `24` | none |
| `transcribe` | `{"engine": "off"}`; or `"whisper"` or `"openai"`, with that engine's own keys | none: see [voice.md](voice.md) |

A route is `{"model": "opus", "effort": "medium"}`. The `voice` route is not a session's: it is the
one-turn call that cleans up a voice message's transcript (see [voice.md](voice.md)).

A run has no limit on how many items it works, and items flagged `asked` never stop it, however
many there are (item no-limits): it goes on until nothing on its tracker can be worked, or a stop,
a kill, a failed session or an item left at the same stage `repeatUpTo` times ends it; a worker's
limits pause it. The script's `-MaxIterations`, its `-StopAtWeeklyUse` and its limit of ten asked
items have no setting here: a worker's weekly limit stands for `-StopAtWeeklyUse`. A `maxIterations`
or `stopAtWeeklyUse` still written in a settings file or a project's definition is accepted and
ignored.

### Files left uncommitted

Before each session the loop notes what is already changed in its repositories - the owner's work
in progress - and after it commits only what the session changed besides. A file changed before
the session and again by it holds the owner's work and the session's together, and is left out of
the commit, as the script leaves it (item leftfiles). It is not only said in the output: the loop
keeps a count of such files in `left.json` in its folder, names them in its row on the page and in
a comment on the session's item, and once one has been left `leftSessions` sessions in a row - each
session counts while the file is still changed, whether it touched it or not - or for `leftHours`
hours, the loop takes no item, `held`, until it is committed or stashed; it looks again every few
seconds, and goes on by itself. A file changed before a session and not touched by it is said once
a run in the output, and holds nothing up. Only the session's own hunks of such a file are never
committed: they were written against code that is in no commit, and a commit of them alone may not
build.

An item flagged `trusted` - a flag a person sets, in the project template from version 2 - is
finished by the loop once it reaches `testing` (item trusted-item): right after the session that
moved it there has its work record written, so the record goes to History with it, and before each
pick for one that came to `testing` otherwise - moved there by hand, or a parent moved on from
`parts`. It has no setting: the flag is the choice, item by item. The script has no such flag.

With `autoStart` on (item auto-start, the Auto button in the loop's row), a loop that finds nothing
to work does not end: it stays alive, `idle`, and asks the tracker what comes next every
`autoStartMinutes`, which spends no session. It also listens for changes to its tracker on the
server's watch stream (`/v1/watch`, item instant-auto), and asks again once the tracker has changed
and then been still for ten seconds - so a new item, or an answer to a question, is taken up within
seconds, and a burst of edits is one look. The set times stay as the fallback: for a server without
the stream or a stream that dropped (it is opened again after a pause that grows), and for an item
freed by one closed in another project's tracker, which this tracker's stream does not tell of. The
page's row says `on any change` while the stream is heard. It holds its run lock and the script's
mutex while it waits, as a running loop does. An item that comes up begins a new run, its
repeats counted afresh. A stop or a kill ends the wait at once, and so does turning the setting off,
which a running loop reads from this file; everything else that ends a loop still ends it.

**When serve starts** (item autoloops-start), on every system, it first starts again the loops
nobody ended. A loop's process outlives `serve`, so a loop still running is left as it is; what
comes back is:

- a loop cut off mid-run - its process ended under it by a restart of the machine, a container
  stopped or a crash, while it was working, waiting out a limit or idle - whether or not
  `autoStart` is on;
- a loop with `autoStart` on whose last run ended on an empty queue, `autoStart` having been off
  then;
- a loop with `autoStart` on that ended to move to a newer build, and was not started on it.

A loop a person stopped or killed, or ended by turning `autoStart` off, stays stopped; so does one
that stopped on an error for you to look at, and one never started. Each run says how it ended in
its `status.json` (`endedBy`: `stop`, `kill`, `autoOff`, `empty`, `update`, `error` or `done`). The loop's row
on the page says when and why it was started again, or why it could not be - kept until you start
it yourself - and the page says which loops are waiting to come back while any are.
`agentloop serve --no-restart-loops` starts none of them. Without `serve` started at login, the
loops come back the first time you start it after a restart.

**After an update** (item autoloops-update), a loop is started on the newest build, whichever app
starts it - the page, `agentloop start`, or `serve` begun before the build: for a build
(`agentloop build`), the version `current` names at that moment. A loop already running keeps the
version it started with until it moves: one started by the page or `agentloop start` with
`autoStart` on moves to a newer build between items - never in a session, the item at work
finished and committed first - and within a minute while it waits for work. It says so, ends with
`endedBy` `update`, and hands its start to the new build (`agentloop start <loop> --after <pid>`,
which waits for the old run to end, its output in `handover.log` beside the loop's); the row says
`started again: moved to build <id>`. A newer build is a new `current` for a build, and a new
version installed over the package for one installed with pip, pipx or uv; a loop run from a clone
never moves. A loop run in a terminal with `agentloop run` is only told, once. A handover that
fails leaves the loop for `serve` to start again as it starts.

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

### HTTPS or SSH

GitHub serves each repository at `https://github.com/<owner>/<repo>.git` and at
`git@github.com:<owner>/<repo>.git`, and a definition names one of them (item git-protocol). The
app asks which of the two this machine signs in with, afresh each time it needs to:

| Form | Signs in when |
|---|---|
| HTTPS | git holds a credential for github.com (`git credential fill`, never asking): `GIT_TOKEN` in the container, a credential manager or `.git-credentials` elsewhere |
| SSH | `ssh -T git@github.com` greets a user. Its first contact adds GitHub's host key to `known_hosts` |

- **Cloning** (**Clone here**, `agentloop add --clone`): the address is cloned in its other form
  when this machine signs in with that one and not with the one given.
- **A fetch or push that fails**, pulling before a session or pushing after it: the remote's
  address (and its `pushurl`, if it has one) is switched to the other form the same way, said in
  the log, and the fetch or push is tried once more. A public repository fetches over HTTPS with no
  credential and then fails to push, which this covers.

An address whose own form signs in, or whose other form does not either, is kept. One with a user
name or password in it, and any host but github.com, are never changed.

Nothing in the app keeps a push from rewriting the remote's history but its never using `--force`;
a ruleset on the branch that blocks force pushes and deletions, with no bypass, makes sure of it.

Read in this order, each layer overriding the one before:

1. the app's defaults (the script's values, in the package);
2. the machine's `defaults`;
3. the project's `settings`, from its definition;
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
| **config** (`settings.json`, `keys/`) | `%APPDATA%\agentloop` | `~/Library/Application Support/agentloop` | `$XDG_CONFIG_HOME/agentloop`, default `~/.config/agentloop` |
| **data** (logs, state) | `%LOCALAPPDATA%\agentloop` | `~/Library/Application Support/agentloop` | `$XDG_STATE_HOME/agentloop`, default `~/.local/state/agentloop` |

The data folder is large and is not for roaming, hence `%LOCALAPPDATA%`. `AGENTLOOP_HOME`, when
set, puts both in that one folder (`config/` and `data/` inside it); tests and a trial copy use it.
Only Windows has been tried; the macOS and Linux paths follow those systems' conventions and are
tried by item `other-os`.

### Logs, records and loop state

They stay on this machine: they hold transcripts of the owner's code.

```
<data>/logs/<project>/
    attempts.jsonl          per project: Test-Tried and repeat detection are per item
    attempts.lock           taken by each loop of the project to append to it
    <session logs>          one JSON-lines file per session, as the script's logs\<project>
    loops/<loop>/
        running.json        the session at work, for kill to end and name
        stop                made by stop; the loop ends once the item at work is done
        kill                made by kill; the loop ends at once
        system.md           AUTOTASK.md and the definition's description, once a run
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
folder, the project's tools folder, and each repository's and place's name with its folder on this
PC. `AUTOTASK-windows.md` is added after `AUTOTASK.md` on Windows only. Where Homebrew is on, it
also names the project's Brewfile and how to add a tool to it (see "Its Brewfile").

Where `AUTOTASK.md` or the definition's description writes `{name}`, it is filled in with this
machine's value, with forward slashes: `{tools}` the app's tools folder (`{tools}/tracker.py`),
`{bundle}` the folder the project's tools were fetched into (`{bundle}/tools/look.ps1`), `{python}`
the Python the app runs on (quoted if its path holds a space), and each repository and place by its
name (`{quill}/tools/patchtest`, `{press}`). A `{word}` that is none of those is left as it is, so
code in a description comes through whole; a description can name only what its definition lists,
and a loop that does not place one is refused on start.

## Examples

Made-up projects. The tests load the settings.

- [`examples/quill.project.os`](examples/quill.project.os) - a definition: one repository, a place,
  the one system it runs on, a setting, and a tool, [`look.py`](examples/tools/quill.project/look.py),
  laid out beside it as the server keeps it.
- [`examples/settings.json`](examples/settings.json) - two workers on two servers, a loop for
  `quill` and a copy of it on the other worker that reaches quill's server itself, a loop for a
  project with three repositories, and a changed default.
