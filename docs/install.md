# Installing AgentLoop

AgentLoop is a Python package with no dependencies. Each release on GitHub carries a wheel, which
serves Windows, macOS and Linux alike, and an install script for each kind of system.

## What it needs

- **git**, on `PATH`.
- **Claude Code** (`claude`), installed and logged in to each account a loop will use.
  `agentloop accounts list` shows the accounts it finds, and `agentloop accounts login <worker>`
  prints the command that logs one in.

The install script checks for both and says how to get either one that is missing. It installs
neither.

## Installing

On macOS or Linux, in a terminal:

```sh
curl -LsSf https://github.com/AxelStrem/AgentLoop/releases/latest/download/install.sh | sh
```

On Windows, in PowerShell:

```powershell
irm https://github.com/AxelStrem/AgentLoop/releases/latest/download/install.ps1 | iex
```

The script installs [uv](https://docs.astral.sh/uv/) if it is missing. uv brings its own Python, so
you need neither Python nor pipx. The script then installs the release's wheel as a uv tool, puts
`agentloop` on `PATH` for new terminals, and starts `agentloop serve --open`. Ctrl+C stops the page.
The script installs nothing else, and it never asks for a key or reads one. For a release other than
the latest, use `releases/download/v<version>/` in place of `releases/latest/download/`.

While the repository is private, these addresses need access to it.

### By hand

With uv, or with [pipx](https://pipx.pypa.io) and Python 3.11 or newer, take the wheel's address from
the release you want on the repository's Releases page:

```sh
uv tool install https://github.com/AxelStrem/AgentLoop/releases/download/v0.1.0/agentloop-0.1.0-py3-none-any.whl
pipx install https://github.com/AxelStrem/AgentLoop/releases/download/v0.1.0/agentloop-0.1.0-py3-none-any.whl
agentloop --version
```

`pipx install git+https://github.com/AxelStrem/AgentLoop@v0.1.0` installs the same tag from the
source.

## The first loop

The install script starts the page. After that, start it with:

```sh
agentloop serve --open
```

This prints the page's address and opens it. The page writes the settings as you add things to it.
You start with none. Then:

1. Get an account on the tracker's Overseer server, and a key for the agents, made on its page.
2. Add a worker: a Claude account, with the server's address. Paste the agents' key into **Paste a
   key** and **Save key**: AgentLoop checks that it signs in, keeps it in a file only you can read,
   and fills in the key file's path. A key file you made yourself works as well.
3. Add a loop from the tracker's address: on **Loops** use **Add from a tracker**, or run
   `agentloop add <address>`. It shows what the project names and asks where each of its
   repositories is cloned on this PC. A repository the definition gives an address can be cloned
   there instead: **Clone here** on the page, or `--clone` (asked on a terminal). `agentloop add --key-stdin` takes the agents' key on standard
   input, unseen on a terminal, and keeps it as **Paste a key** does.
4. Start it.

The README's "Being handed a project" says each step in full. `agentloop run <loop> --dry` checks a
loop without spending a session.

## Upgrading

Run the install line again: it installs the latest release over the one you have, and says so. By
hand, `uv tool install --force` (or `pipx install --force`) with the new release's wheel does the
same.

A loop runs from the installed package, and its sessions read the package's tools on every step.
An upgrade replaces those files under a loop that is running. Either stop the loops first, or run
them from a build that upgrades do not touch:

```sh
agentloop build --to <path>
```

That makes a copy at `<path>/versions/<id>`, with a launcher (`<path>/agentloop.cmd` on Windows,
`<path>/agentloop` on macOS and Linux) that runs the one built last. Start the app with the launcher.
After an upgrade, run the installed `agentloop build` again to add the new version beside the old
one. The first build recorded `<path>` in the settings, so it needs no `--to` now. Use the
`agentloop` the install put on `PATH`, not the launcher, which would copy the build it runs. A loop
already running keeps the version it started with.

`agentloop --version` says what is running: `installed` for the installed package, the commit and
the time for a build, and `from source` for a clone.
