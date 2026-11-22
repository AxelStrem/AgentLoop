import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

import pytest

FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude" / "fake_claude.py"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Tests marked `slow` go first. pytest-xdist hands tests to its workers in this order, so a
    slow one starts at once and runs beside the rest, instead of starting late and keeping the
    whole run waiting for it."""
    items.sort(key=lambda test: test.get_closest_marker("slow") is None)

class FakeClaude:
    """The fake `claude` on PATH, and the record of what it was called with."""

    def __init__(self, bin_dir: Path, log: Path, monkeypatch: pytest.MonkeyPatch):
        self.bin = bin_dir
        self.log = log
        self._monkeypatch = monkeypatch

    def scenario(self, *names: str) -> None:
        """The session to play, or several: one per call, the last one again after that."""
        self._monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", ",".join(names))

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_launcher(bin_dir: Path) -> None:
    """A file named `claude` that runs fake_claude.py, as the real one is found: through PATH.
    Windows runs a .cmd; macOS and Linux an executable script. The POSIX one is untried here -
    this machine is Windows."""
    if os.name == "nt":
        launcher = bin_dir / "claude.cmd"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{FAKE_CLAUDE}" %*\r\nexit /b %errorlevel%\r\n')
    else:
        launcher = bin_dir / "claude"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLAUDE}" "$@"\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)



@pytest.fixture(scope="session", autouse=True)
def _git_without_fsmonitor():
    """Every git the tests start, directly or through the code under test, runs with fsmonitor off.
    With core.fsmonitor on (a system gitconfig can set it) the first git command in each throwaway
    repository starts a daemon that outlives the repository's deletion. Set through the environment
    because git reads GIT_CONFIG_COUNT on every OS and every git of 2.31 or newer."""
    saved = {k: os.environ.get(k) for k in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0")}
    os.environ.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.fsmonitor", GIT_CONFIG_VALUE_0="false")
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

@pytest.fixture(scope="session", autouse=True)
def _mutexes_of_our_own():
    """The script's Windows mutexes, named for this process: test processes running at once (pytest
    -n), and the loops they start, which inherit it, never find a project held by each other - every
    test's project is `proj` - nor by a real loop on this PC (config.script_mutex)."""
    saved = os.environ.get("AGENTLOOP_MUTEX_TAG")
    os.environ["AGENTLOOP_MUTEX_TAG"] = f"test-{os.getpid()}"
    yield
    if saved is None:
        os.environ.pop("AGENTLOOP_MUTEX_TAG", None)
    else:
        os.environ["AGENTLOOP_MUTEX_TAG"] = saved


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeClaude:
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    _write_launcher(bin_dir)
    log = tmp_path / "fake-claude-calls.jsonl"
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.delenv("FAKE_CLAUDE_SCENARIO", raising=False)
    monkeypatch.delenv("FAKE_CLAUDE_ENV", raising=False)
    # What the fake records is what the test sets: an agent running these tests has its own.
    for name in list(os.environ):
        if (name.startswith(("AGENT_", "OVERSEER_")) or name == "CLAUDE_CONFIG_DIR"):
            monkeypatch.delenv(name)
    return FakeClaude(bin_dir, log, monkeypatch)


# A tracker on a server: every tracker is one (item simplify), so the tests serve theirs.

# The overseer-server the tests serve their trackers with: the build AGENTLOOP_OVERSEER_SERVER
# names, or else the one on PATH. Without either, the tests that need a server skip, saying so -
# no machine's own path is assumed. It hands tracker.py the project template from the library it
# runs with, so the server must be one that names a document's bases (overseer:library-source).
def _overseer_server() -> Path | None:
    named = os.environ.get("AGENTLOOP_OVERSEER_SERVER")
    if named:
        return Path(named)
    found = shutil.which("overseer-server")
    return Path(found) if found else None


OVERSEER_SERVER = _overseer_server()
TRACKER_PY = Path(__file__).resolve().parents[1] / "src" / "agentloop" / "tools" / "tracker.py"
# The account the tests' agents act as, and its key: made up here, for a server of the tests' own.
AGENT = "agent"
AGENT_KEY = "agentloop-tests-key"
# The other accounts the tests' server keeps, as (username, display name): names with markup and
# spaces, and one that begins another, for the mentions of item assign-bot.
PEOPLE = [("someone", "**Some** One <icon=bot>"), ("zombo", "Zombo"), ("zombobot", "<color=red | Zombo Bot>"),
          ("ap31", "Dmitrii")]


class Overseer:
    """A copy of overseer-server on loopback, serving a folder of the tests' own, which keeps one
    account - an admin, `agent` - with a key in `key`. Each test's trackers go in a folder of their
    own on it, so the projects beside one are only those the test made."""

    def __init__(self, url: str, root: Path, key: Path):
        self.url = url
        self.root = root
        self.key = key
        self._made = 0

    @property
    def worker(self) -> dict:
        """A worker's `overseer`: this server, and the key file."""
        return {"server": self.url, "keyFile": str(self.key)}

    @property
    def env(self) -> dict[str, str]:
        return {"OVERSEER_URL": self.url, "OVERSEER_KEY_FILE": str(self.key)}

    def folder(self) -> str:
        """A folder on the server no test has used, under the account's own."""
        self._made += 1
        return f"{AGENT}/t{os.getpid()}-{self._made}"

    def document(self, text: str, project: str = "proj", folder: str | None = None) -> str:
        """A tracker written to the server's folder, by the name the server knows it by."""
        name = f"{folder or self.folder()}/{project}.os"
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return name

    def text(self, name: str) -> str:
        return (self.root / name).read_text(encoding="utf-8")

    def tracker(self, name: str, *args: str, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
        """tracker.py on a document of this server, as the agent account."""
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith(("AGENT_", "OVERSEER_")) and k != "AGENTIC_LOOP"}
        done = subprocess.run([sys.executable, str(TRACKER_PY), "--doc", name, *args], capture_output=True, text=True,
                              encoding="utf-8", env={**clean, **self.env, **(env or {})})
        if check and done.returncode:
            raise AssertionError(done.stderr)
        return done


@pytest.fixture(scope="session")
def overseer_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Overseer]:
    """One server for the whole run: starting one takes a moment, and each test's trackers are in a
    folder of their own on it. Skipped where no overseer-server is built."""
    if OVERSEER_SERVER is None or not OVERSEER_SERVER.is_file():
        pytest.skip("no overseer-server: name a build with AGENTLOOP_OVERSEER_SERVER, or put one on PATH")
    work = tmp_path_factory.mktemp("overseer")
    root = work / "documents"
    root.mkdir()
    accounts = work / "accounts.json"
    # Never signed in with: only the key is used, so the password's hash only has to read as one.
    password = "$argon2id$v=19$m=19456,t=2,p=1$c2FsdHNhbHQ$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaGhhc2g"
    accounts.write_text(json.dumps({"accounts": [{
        "username": AGENT, "name": "Agent", "admin": True, "sessions": [], "password": password,
        "keys": [{"name": "tests", "hash": hashlib.sha256(AGENT_KEY.encode()).hexdigest(), "made": "2026-10-01T00:00:00Z"}],
    }] + [
        # Others the directory names, for mentions to name (item assign-bot): no key reaches them.
        {"username": username, "name": name, "admin": False, "sessions": [], "password": password, "keys": []}
        for username, name in PEOPLE
    ], "invites": []}), encoding="utf-8")
    key = work / "key"
    key.write_text(AGENT_KEY, encoding="utf-8")
    # A copy runs, not the build's own: Windows locks a running executable.
    binary = work / OVERSEER_SERVER.name
    shutil.copy(OVERSEER_SERVER, binary)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([str(binary), "--root", str(root), "--port", str(port), "--accounts", str(accounts)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        # Up once it answers at all: /documents refuses a caller with no key, and a refusal is an
        # answer. Only a connection that fails is tried again.
        for _ in range(150):
            try:
                urllib.request.urlopen(f"{url}/documents", timeout=5).read()
                break
            except urllib.error.HTTPError:
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail(f"{OVERSEER_SERVER.name} did not answer on {url} within 30 seconds")
        _hands_out_templates(url)
        yield Overseer(url, root, key)
    finally:
        server.terminate()
        server.wait(timeout=10)


def _hands_out_templates(url: str) -> None:
    """Skip, saying why, where the server is older than the one that names a document's bases:
    tracker.py could read no project template from it, and every test would fail the same way."""
    probe = urllib.request.Request(f"{url}/v1/source?document=library:projects/project_template.os",
                                   headers={"Authorization": f"Bearer {AGENT_KEY}"})
    try:
        answer = json.loads(urllib.request.urlopen(probe, timeout=10).read().decode("utf-8"))
    except urllib.error.HTTPError:
        answer = {}
    if "template_version" not in answer.get("text", ""):
        pytest.skip(f"{OVERSEER_SERVER.name} hands out no project template: build a newer Overseer")


@pytest.fixture
def overseer(overseer_server: Overseer) -> Overseer:
    return overseer_server
