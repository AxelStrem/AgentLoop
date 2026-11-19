"""The agents' key pasted in, and kept in a file only this person can read (item paste-key): checked
with the server first, written under the config folder's `keys/`, never echoed - from the module,
the page's route and `agentloop add --key-stdin`."""

import io
import os
import stat
import sys

import pytest

from agentloop import cli, config, keys
from conftest import AGENT, AGENT_KEY
from test_definition import closed_port, served
from test_loop import item
from test_loop_from_url import address, machine  # noqa: F401 - a fixture
from test_server import serve  # noqa: F401 - a fixture


def private(path) -> bool:
    """Readable by the current user alone, as this system says it."""
    if sys.platform == "win32":
        import subprocess

        whoami = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True).stdout
        me = whoami.strip().split(",")[-1].strip('"')
        return keys.windows_sids(path) == [me]
    return stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)


def test_a_key_that_signs_in_is_kept_privately(machine, overseer):
    account, path = keys.save(overseer.url, f"  {AGENT_KEY}\n")
    port = overseer.url.rsplit(":", 1)[1]
    assert account == AGENT
    assert path == config.paths().config / "keys" / f"127.0.0.1_{port}-{AGENT}.key"
    assert path.read_text(encoding="utf-8").strip() == AGENT_KEY
    assert private(path) and private(path.parent)


def test_replacing_a_key_overwrites_its_file_and_keeps_it_private(machine, overseer):
    path = keys.folder() / keys.file_name(overseer.url, AGENT)
    path.parent.mkdir(parents=True)
    path.write_text("an old key", encoding="utf-8")
    if sys.platform != "win32":
        path.chmod(0o644)
    assert keys.save(overseer.url, AGENT_KEY)[1] == path
    assert path.read_text(encoding="utf-8").strip() == AGENT_KEY
    assert private(path)


def test_a_refused_key_writes_nothing(machine, overseer):
    with pytest.raises(config.ConfigError) as refused:
        keys.save(overseer.url, "not-the-key")
    assert refused.value.field == "key" and "not-the-key" not in str(refused.value)
    assert not keys.folder().exists()


@pytest.mark.parametrize("text", ["", "   \n", "two\nlines", "has spaces"])
def test_what_is_no_key_is_refused_before_the_server_is_asked(machine, text):
    with pytest.raises(config.ConfigError) as refused:
        keys.save(closed_port(), text)
    assert refused.value.field == "key"


def test_an_unreachable_server_is_said_without_its_address_or_the_key(machine):
    with pytest.raises(keys.Unreachable) as failed:
        keys.save(closed_port(), AGENT_KEY)
    assert "127.0.0.1" not in str(failed.value) and AGENT_KEY not in str(failed.value)
    assert not keys.folder().exists()


def test_a_bare_name_takes_the_workers_server(machine, overseer):
    settings = config.load_settings()
    assert keys.server_for(settings, "a/proj.os", "w") == overseer.url
    assert keys.server_for(settings, "https://o.example.com/doc/a/proj.os", "bare") == "https://o.example.com"
    with pytest.raises(config.ConfigError):
        keys.server_for(settings, "a/proj.os", "bare")


# The page's route

def test_the_route_answers_the_account_and_path_never_the_key(machine, overseer, serve):
    c = serve()
    status, _, raw = c.request("POST", "/api/keys", {"key": AGENT_KEY, "server": overseer.url})
    assert status == 200 and AGENT_KEY not in raw.decode()
    saved = c.json("POST", "/api/keys", {"key": AGENT_KEY, "address": "a/proj.os", "worker": "w"})
    assert saved["account"] == AGENT
    assert config.expand(saved["path"]) == keys.folder() / keys.file_name(overseer.url, AGENT)
    refused = c.json("POST", "/api/keys", {"key": "not-the-key", "server": overseer.url}, expect=400)
    assert refused["field"] == "key" and "not-the-key" not in str(refused)
    assert c.json("POST", "/api/keys", {"key": AGENT_KEY}, expect=400)["field"] == "server"
    assert AGENT_KEY not in config.paths().settings.read_text(encoding="utf-8")


# The command

def test_the_command_takes_the_key_on_standard_input(machine, overseer, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.setattr("sys.stdin", io.StringIO(AGENT_KEY + "\n"))
    tracker = served(overseer, item("alpha"))
    code = cli.main(["add", address(overseer, tracker), "--worker", "bare", "--key-stdin", "--repo", f"code={machine.code}",
                     "--repo", f"docs={machine.docs}", "--place", f"samples={machine.samples}"])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert f"signs in as {AGENT}" in captured.out and AGENT_KEY not in captured.out + captured.err
    kept = config.load_settings().loop("proj").overseer
    assert config.expand(kept["keyFile"]) == keys.folder() / keys.file_name(overseer.url, AGENT)
    assert AGENT_KEY not in config.paths().settings.read_text(encoding="utf-8")


def test_the_command_refuses_both_a_key_file_and_a_pasted_key(machine, overseer, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(AGENT_KEY + "\n"))
    assert cli.main(["add", "a/proj.os", "--worker", "w", "--key-stdin", "--key-file", str(overseer.key)]) == 2
    assert "--key-stdin" in capsys.readouterr().err
    assert not keys.folder().exists()
