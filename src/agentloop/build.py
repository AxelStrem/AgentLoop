"""Making a runnable copy of the app at another path, and saying which build a running app is.

The script has no build: it runs from AgenticLoop, so an edit there changes every loop running.
The app's builds go beside each other and are never overwritten, because a loop that runs for
days is still reading the files it started from (guard.py runs again on every tool call):

    <to>/agentloop.cmd         the Windows launcher
    <to>/agentloop             the sh launcher, for macOS and Linux
    <to>/current               one line: the name of the version to run
    <to>/versions/<id>/__main__.py
    <to>/versions/<id>/agentloop/   the package, with its tools and its page inside it
    <to>/versions/<id>/agentloop/_build.json

`<id>` is `<UTC yyyymmdd-HHMMSS>-<short commit>`, then `-dirty` if the tree had changes. A launcher
reads `current` and runs `python versions/<id>`; Python runs a folder's `__main__.py`.

The sh launcher, its execute bit and the symlink handling are untried here: this machine is
Windows. Only the text of the sh launcher is tested (item other-os tries it on macOS and Linux).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agentloop import __version__, config

BUILD_FILE = "_build.json"
CURRENT = "current"
VERSIONS = "versions"


class BuildError(Exception):
    """Something that stops a build, said so the owner can act on it."""


@dataclass
class Built:
    to: Path
    id: str
    launcher: Path
    previous: str | None
    kept: list[str]


def package_dir() -> Path:
    return Path(__file__).resolve().parent


# Which build is running

def read_build(package: Path | None = None) -> dict | None:
    """The record a build wrote into its copy; None when running from the source tree."""
    path = (package or package_dir()) / BUILD_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def describe(package: Path | None = None) -> str:
    """The line `agentloop version` prints."""
    record = read_build(package)
    if record is None:
        # No build record: the source tree, or a package pip or pipx installed from a wheel, which
        # has no repository of its own to name a commit from (item package).
        commit, _ = source_state(package or package_dir())
        return f"agentloop {__version__} ({'from source' if commit else 'installed'})"
    commit = record.get("commit")
    parts = [commit[:7] if commit else "no commit"]
    if record.get("clean") is not None:
        parts.append("clean" if record["clean"] else "dirty")
    built = record.get("built")
    if built:
        try:
            built = datetime.fromisoformat(built).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except ValueError:
            pass
        parts.append(f"built {built}")
    return f"agentloop {record.get('version', __version__)} ({', '.join(parts)})"


# The source tree

def _git(args: list[str], cwd: Path) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def source_state(package: Path) -> tuple[str | None, bool | None]:
    """The commit the source repository is at, and whether its tree is clean; None, None with no
    git or no repository of its own (the package's folder is `<repository>/src/agentloop`)."""
    repo = package.parent.parent
    top = _git(["rev-parse", "--show-toplevel"], package)
    if top is None or not top.strip() or not os.path.samefile(top.strip(), repo):
        return None, None
    commit = _git(["rev-parse", "HEAD"], repo)
    status = _git(["status", "--porcelain"], repo)
    return (commit.strip() if commit else None), (status.strip() == "" if status is not None else None)


# Launchers

def cmd_launcher(python: str) -> str:
    """Windows: reads `current`, then runs the version. `exit /b` is on the last line so that
    cmd, which reads a batch file as it runs it, never goes on into a rewritten file."""
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        'set /p AGENTLOOP_VERSION=<"%~dp0current"\r\n'
        f'"{python}" "%~dp0versions\\%AGENTLOOP_VERSION%" %*\r\n'
        "exit /b %errorlevel%\r\n"
    )


def sh_launcher(python: str) -> str:
    """macOS and Linux. A symlink to the launcher (in ~/bin, say) is followed to the folder it
    lives in."""
    return (
        "#!/bin/sh\n"
        'self=$0\n'
        'while [ -L "$self" ]; do\n'
        '  link=$(readlink "$self")\n'
        '  case $link in\n'
        '    /*) self=$link ;;\n'
        '    *) self=$(dirname "$self")/$link ;;\n'
        '  esac\n'
        'done\n'
        'here=$(cd "$(dirname "$self")" && pwd)\n'
        'version=$(head -n 1 "$here/current")\n'
        f'exec {shlex.quote(python)} "$here/versions/$version" "$@"\n'
    )


def _write_text(path: Path, text: str) -> None:
    """Replaces the file whole, and only when it differs: a running launcher is not touched
    needlessly."""
    if path.exists() and path.read_bytes() == text.encode("utf-8"):
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8", newline="") as out:
            out.write(text)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def launcher_name() -> str:
    return "agentloop.cmd" if os.name == "nt" else "agentloop"


# The build

def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def _check_target(to: Path, package: Path) -> None:
    repo = package.parent.parent
    if _inside(to, repo) or _inside(repo, to):
        raise BuildError(f"{to} is in, or holds, the repository at {repo}: the copy goes outside it")
    if to.exists():
        if not to.is_dir():
            raise BuildError(f"{to} is a file")
        if any(to.iterdir()) and not ((to / CURRENT).is_file() or (to / VERSIONS).is_dir()):
            raise BuildError(f"{to} is not empty and is not an agentloop build; give an empty folder or a new one")


def _new_id(versions: Path, commit: str | None, clean: bool | None, now: datetime) -> str:
    base = f"{now.strftime('%Y%m%d-%H%M%S')}-{commit[:7] if commit else 'nogit'}"
    if clean is False:
        base += "-dirty"
    # Two builds in one second must not share a folder.
    name, count = base, 1
    while (versions / name).exists():
        count += 1
        name = f"{base}-{count}"
    return name


def _version_names(versions: Path) -> list[str]:
    if not versions.is_dir():
        return []
    return sorted(p.name for p in versions.iterdir() if p.is_dir() and not p.name.startswith("."))


def build(to: Path, package: Path | None = None, python: str | None = None, now: datetime | None = None) -> Built:
    """Copies the package into a new version under `to`, then points `current` at it. Running
    loops carry on with the files they started from; the next start uses the new build."""
    package = (package or package_dir()).resolve()
    python = python or sys.executable
    now = now or datetime.now(timezone.utc)
    if (package / BUILD_FILE).exists():
        raise BuildError("this is a built copy; build from the source tree, so that a build never copies a build")
    to = to.resolve()
    _check_target(to, package)

    commit, clean = source_state(package)
    versions = to / VERSIONS
    versions.mkdir(parents=True, exist_ok=True)
    version = _new_id(versions, commit, clean, now)
    staging = versions / f".{version}.tmp"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        shutil.copytree(package, staging / "agentloop", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", BUILD_FILE))
        (staging / "__main__.py").write_text(
            "import sys\n\nfrom agentloop.cli import main\n\nsys.exit(main())\n", encoding="utf-8", newline="\n"
        )
        record = {
            "version": __version__,
            "commit": commit,
            "clean": clean,
            "built": now.isoformat(timespec="seconds"),
            "python": python,
        }
        (staging / "agentloop" / BUILD_FILE).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(staging, versions / version)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    previous = None
    try:
        previous = (to / CURRENT).read_text(encoding="utf-8").strip() or None
    except OSError:
        pass

    _write_text(to / "agentloop.cmd", cmd_launcher(python))
    sh = to / "agentloop"
    _write_text(sh, sh_launcher(python))
    sh.chmod(sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    # Last, and in one step: a launcher starting now reads the old name or the new one.
    _write_text(to / CURRENT, version + "\n")

    kept = [name for name in _version_names(versions) if name not in (version, previous)]
    return Built(to, version, to / launcher_name(), previous, kept)


# The command

def run(to_argument: str | None) -> int:
    """`agentloop build [--to <path>]`: `--to`, else the settings' `install`."""
    try:
        settings = config.load_settings()
        raw = settings.raw
        text = to_argument or raw.get("install")
        if not text:
            raise BuildError("no place to build to: give --to <path>, or set install in settings.json")
        built = build(config.expand(text))
        stored = config.home_relative(built.to)
        if raw.get("install") != stored:
            raw = dict(raw)
            raw["install"] = stored
            try:
                config.save_settings(raw, settings.path)
            except config.ConfigError as error:
                print(f"agentloop build: built, but install was not saved: {error}", file=sys.stderr)
                return 2
    except (BuildError, config.ConfigError) as error:
        print(f"agentloop build: {error}", file=sys.stderr)
        return 2
    print(f"built    {built.id}")
    print(f"at       {built.to}")
    print(f"run      {built.launcher}")
    if built.previous:
        print(f"replaced {built.previous} (loops already running keep it)")
    if built.kept:
        print("older versions still kept (item runner will say which are in use, and --prune remove the rest):")
        for name in built.kept:
            print(f"  {name}")
    return 0
