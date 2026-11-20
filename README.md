# AgentLoop

The agent loop that works the project trackers: it picks an item, runs a Claude session on it,
and commits what the session changed, item after item. A local web page starts, stops and kills
each loop, several at once on different Claude accounts, and shows each one's output as it comes.

It needs:

- **Python 3.11 or newer.** Nothing else from PyPI; `pytest` is for the tests only.
- **git**, on `PATH`.
- **Claude Code** (`claude`), on `PATH` or named in the settings, logged in to each account a loop
  will use.

It runs on Windows, macOS and Linux. It is early work, at 0.1: it has run real loops on Windows
only, and its settings, page and commands may still change from one release to the next.

## Building and starting

To use AgentLoop, install a release with one line - `curl -LsSf
https://github.com/AxelStrem/AgentLoop/releases/latest/download/install.sh | sh` on macOS and Linux,
`irm https://github.com/AxelStrem/AgentLoop/releases/latest/download/install.ps1 | iex` on Windows:
[docs/install.md](docs/install.md). Each release is also a container image, `ghcr.io/axelstrem/agentloop`,
which the same page covers under "Running in a container". What follows is for a clone, to work on
AgentLoop itself.

A loop runs from a build, never from the clone, so that editing the code cannot change a loop
that is running. From a clone:

```sh
python build.py <path>
```

This makes a runnable copy at `<path>` and records `<path>` as the settings' `install`. Later builds
go beside the earlier ones, under `<path>/versions/`, and `<path>/current` names the one to run.
A loop already running keeps running from the build it started with. Start the app with the
launcher the build makes:

| | |
| --- | --- |
| Windows | `<path>\agentloop.cmd serve --open` |
| macOS, Linux | `<path>/agentloop serve --open` |

`serve` prints the page's address, `http://127.0.0.1:8765/?token=...`, and `--open` opens it in
the browser. The page only answers on this machine, and only to a browser that has the token
(`docs/install.md` says how to reach it from another machine, as a container needs). A
loop started from the page is a process of its own: it keeps working when the app is closed, and
the app finds it again when it next starts.

## The page

- **Loops** - each loop on this machine, with its state, the item it is on, and its output. A loop
  is a project, a worker, and where that project's repositories are on this PC. **Start**; **stop**,
  which ends the loop once the item at work is done; **kill**, which ends the session at work now.
  If something is missing before a loop can start (a repository's folder, a key file), the page says
  what.
- **This machine** - the workers (the Claude accounts, and the Overseer server each reaches the
  trackers on, its account and its key file), and the machine's settings.

The same, from a terminal:

```sh
agentloop start <loop>     # as the page's start
agentloop stop <loop>
agentloop kill <loop>
agentloop status [<loop>]
agentloop run <loop>       # one loop in this terminal, as run-agent-loop.ps1
agentloop run <loop> --dry [--item <handle>]   # stop where the first session would begin: no session run
agentloop review <project> [<run>] [--all]   # what a run did and cost, as review-run.py
agentloop --help           # every command; <command> --help for its options
```

## Being handed a project

A new PC needs no settings file made by hand: the page writes the settings as you add each
thing below, and `serve` starts with none.

A project comes from its tracker's address. The tracker is a document on an
[Overseer](https://github.com/AxelStrem/Overseer) server - anyone's, not only the owner's - and the
project's definition is a document beside it: what the project is, its repositories, the systems
it runs on, its settings and its tools. It holds nothing of anyone's PC: no paths, no accounts, no
keys. AgentLoop needs nothing of Overseer on your PC: `tracker.py` reads and
writes the tracker through the server, and reads the project template the tracker inherits from
the server too. So the server must be recent enough to hand that template out. If it is not, or
if its template is a version this AgentLoop does not know, a session's `tracker.py` stops with one
line naming both versions and saying which side to update.

1. **An account on the server.** The server's owner invites you, or you run a server of your own.
2. **An account for the agents.** Sessions comment and move items under their own name, not
   yours: an account of their own, invited like yours, keeps their edits apart and lets you share
   only some documents with it. Signed in as that account, make a key on the page's "Keys", in the
   account drawer (`POST /account/keys`). The key is shown once: save it in a file of its own,
   such as `~/.overseer/agents.key`, readable only by you. If the agents act as you instead,
   make the key on your own account.
3. **Share the tracker** with the agents' account, to edit, and the trackers beside it that its
   sessions may read or file into. If someone hands you a project, they share it with your
   accounts.
4. **Get the code.** Clone the repositories the project names: **Add from a tracker** (step 6)
   shows each of them, with a line saying what it is, and offers **Clone here** for each one whose
   address the project gives.
5. **Add a worker:** on **This machine**, or `agentloop accounts list`, which also lists the
   Claude config folders it found that are not added yet. `agentloop accounts add <folder>` adds one
   (`default` for `~/.claude`), and `agentloop accounts login <worker>` prints the command that logs
   it in. Give the worker the server's address and the agents' key: paste it into **Paste a key**,
   and AgentLoop checks it signs in and keeps it in a file only you can read - or give the path of
   a key file of your own. The key itself never goes in the settings. The server and key belong to the worker, so one
   machine can work trackers on two servers through two workers, one for each.
6. **Add a loop and start it.** From a tracker's address: on **Loops**, **Add from a tracker**.
   Give the address as Overseer's page shows it, the key file for that server (the worker's when
   left empty) and the worker, then **Read**. The page shows what the project is and names, and
   asks where each repository and place is on this PC. On a machine with no browser, run
   `agentloop add <address>` instead, with `--key-stdin` to pipe or type the key in. A tracker on another server than the
   worker's is fine: the loop then carries that server and key file itself. Then start it.

Nothing is guessed: no setting defaults to a server, a path or an account. When one a loop needs
is missing - a repository's folder, the worker's server, the key file - the page and
`agentloop run` say which, by name, and the loop does not start.

`agentloop run <loop> --dry` checks a loop without spending a session. It reads the settings, finds
the repositories, reads the tracker and picks the item (or the one `--item` names). It writes the
system prompt and the prompt a session would get to `dry/` in the loop's log folder, then stops.

## What a session may and may not do

Each session works one item, in the repositories its loop names. A guard runs before every tool call
the session makes, and refuses, in any permission mode:

- **git commit and push.** The loop commits each session's changes itself, one commit per
  repository, with only the files that session changed. Nothing is ever pushed: the owner pushes.
- **Reading a `.env` file** (`.env.example` is allowed).
- **Reading the agents' key**, or the variable that says where it is. `tracker.py` reads it, and
  nothing else needs to.

Logs are in the app's data folder (`%LOCALAPPDATA%\agentloop` on Windows,
`~/Library/Application Support/agentloop` on macOS, `~/.local/state/agentloop` on Linux), under
`logs/<project>/`. You get one JSON-lines transcript per session, `attempts.jsonl` (the record of
every session on each item), and each loop's output and state under `loops/<loop>/`. They hold
transcripts of your code, so they stay on this machine.

## Settings

Every setting is in [docs/settings.md](docs/settings.md): what a project's definition and the
machine's `settings.json` hold, where each file lives on each OS, and the run settings a project or
a loop can change. [docs/examples/](docs/examples/) has examples of both.

## Developing

```sh
pip install -e .[test]
python -m pytest -q
```

The tests never run a real Claude session or reach a live server. They use a fake `claude`
(`tests/fake_claude`) that answers with a recorded session, and serve a copy of a tracker on
loopback with `overseer-server`: the build `AGENTLOOP_OVERSEER_SERVER` names, or else the one on
`PATH`. Without one, the tests that need a server are skipped, and `-rs` lists them with the
reason. Build one from a clone of Overseer with
`cargo build --release --features server --bin overseer-server`.

## Licence

MIT, in [LICENSE](LICENSE).
