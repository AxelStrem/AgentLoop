# Installing AgentLoop

AgentLoop is a Python package with no dependencies. Each release on GitHub carries a wheel,
installed with [pipx](https://pipx.pypa.io), which gives the `agentloop` command its own
environment. The same wheel serves Windows, macOS and Linux.

## What it needs

- **Python 3.11 or newer**, and **pipx** (`python -m pip install --user pipx`, then
  `python -m pipx ensurepath` and a new terminal).
- **git**, on `PATH`.
- **Claude Code** (`claude`), installed and logged in to each account a loop will use.
  `agentloop accounts list` shows the accounts it finds, and `agentloop accounts login <worker>`
  prints the command that logs one in.

## Installing

Take the wheel's address from the release you want, on the repository's Releases page, and:

```sh
pipx install https://github.com/AxelStrem/AgentLoop/releases/download/v0.1.0/agentloop-0.1.0-py3-none-any.whl
agentloop --version
```

`pipx install git+https://github.com/AxelStrem/AgentLoop@v0.1.0` installs the same tag from the
source. While the repository is private, either one needs access to it.

## The first loop

```sh
agentloop serve --open
```

This prints the page's address and opens it. The page writes the settings as you add things to it.
You start with none. Then:

1. Get an account on the tracker's Overseer server, and one for the agents with a key in a file of
   its own.
2. Add a worker: a Claude account, with the server's address and the key file's path.
3. Add a loop. Given a tracker's address, on **Loops** use **Add from a tracker**, or run
   `agentloop add <address>`: it shows what the project names and asks where each of its
   repositories is cloned on this PC. Given a bundle, first add its folder on **This machine**,
   then **Add from a bundle**.
4. Start it.

The README's "Being handed a project" says each step in full. `agentloop run <loop> --dry` checks a
loop without spending a session.

## Upgrading

```sh
pipx upgrade agentloop
```

A loop runs from the installed package, and its sessions read the package's tools on every step.
An upgrade replaces those files under a loop that is running. Either stop the loops first, or run
them from a build that upgrades do not touch:

```sh
agentloop build --to <path>
```

That makes a copy at `<path>/versions/<id>`, with a launcher (`<path>/agentloop.cmd` on Windows,
`<path>/agentloop` on macOS and Linux) that runs the one built last. Start the app with the launcher.
After an upgrade, run pipx's `agentloop build` again to add the new version beside the old one. The
first build recorded `<path>` in the settings, so it needs no `--to` now. Use the `agentloop` pipx
installed, not the launcher, which would copy the build it runs. A loop already running keeps the
version it started with.

`agentloop --version` says what is running: `installed` for the pipx package, the commit and the
time for a build, and `from source` for a clone.
