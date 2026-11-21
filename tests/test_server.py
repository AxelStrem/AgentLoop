"""The page's server, asked over HTTP on a free port: who may reach it, the settings read and
changed, the accounts, and a loop started, its output followed, stopped and killed with the fake
claude."""

import http.client
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from agentloop import accounts, config, runner, server
from agentloop import loop as loop_mod

from test_runner import Machine, move, write

KEY = "the-agents-secret-key-0123456789"


class Client:
    def __init__(self, s: server.Server):
        self.s = s

    def request(self, method: str, path: str, body=None, headers: dict | None = None, token: bool = True):
        connection = http.client.HTTPConnection("127.0.0.1", self.s.port, timeout=30)
        sent = {"Host": f"127.0.0.1:{self.s.port}"}
        if token:
            sent["Cookie"] = f"{self.s.cookie}={self.s.token}"
        if body is not None:
            sent["Content-Type"] = "application/json"
        sent.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, body=data, headers=sent)
        answer = connection.getresponse()
        raw = answer.read()
        connection.close()
        return answer.status, answer, raw

    def json(self, method: str, path: str, body=None, expect: int = 200, **more):
        status, _, raw = self.request(method, path, body, **more)
        data = json.loads(raw)
        assert status == expect, data
        return data


@pytest.fixture
def serve():
    started: list[server.Server] = []

    def make(the_runner: runner.Runner | None = None, wide: bool = False, **more) -> Client:
        s = server.Server(0, the_runner=the_runner, **more)
        s.wrong_wait = 0.01
        # A server listening widely, as a container's does, but bound to loopback: a test binding
        # every interface would open a port on the network, and ask Windows' firewall about it.
        s.wide = s.wide or wide
        threading.Thread(target=s.serve_forever, daemon=True).start()
        started.append(s)
        return Client(s)

    yield make
    for s in started:
        s.close()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A machine with no loops: a bundle, and a worker whose Overseer key file holds a key."""
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    # accounts.found looks in the home folder.
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    (tmp_path / "user" / ".claude-account7").mkdir(parents=True)
    key = tmp_path / "agent.key"
    key.write_text(KEY, encoding="utf-8")
    bundle = tmp_path / "home" / "config" / "bundles" / "proj"
    bundle.mkdir(parents=True)
    config.save_json(bundle / "project.json", {"format": 1, "name": "proj", "tracker": {"server": "alice/projects/proj.os"},
                                               "repos": ["code"], "settings": {"repeatUpTo": 3}})
    account = tmp_path / "account"
    account.mkdir()
    config.save_json(config.paths().settings, {
        "format": 1, "bundles": [str(bundle)],
        "workers": [{"name": "w", "claude": str(account), "overseer": {"server": "http://127.0.0.1:1", "keyFile": str(key)}}],
        "loops": [], "defaults": {},
    })
    return tmp_path


def loop_entry(name: str = "one", **more) -> dict:
    return {"name": name, "project": "proj", "worker": "w", "repos": {"code": "~/code"}, "settings": {}, **more}


# Who may reach it

def test_the_token_is_traded_for_a_cookie(home, serve):
    c = serve()
    status, answer, _ = c.request("GET", f"/?token={c.s.token}", token=False)
    assert status == 303 and answer.getheader("Location") == "/"
    cookie = answer.getheader("Set-Cookie")
    assert cookie.startswith(f"{c.s.cookie}={c.s.token};") and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    status, answer, page = c.request("GET", "/", token=False, headers={"Cookie": cookie.split(";")[0]})
    assert status == 200 and answer.getheader("Content-Type").startswith("text/html") and b"/page.js" in page
    # The bearer header does as well as the cookie.
    assert c.request("GET", "/api/loops", token=False, headers={"Authorization": f"Bearer {c.s.token}"})[0] == 200


@pytest.mark.parametrize("path", ["/api/loops", "/api/settings", "/page.js"])
def test_no_token_or_a_wrong_one_is_refused(home, serve, path):
    c = serve()
    assert c.request("GET", path, token=False)[0] == 401
    assert c.request("GET", path, token=False, headers={"Cookie": f"{c.s.cookie}=nope"})[0] == 401
    assert c.request("GET", path, token=False, headers={"Authorization": "Bearer nope"})[0] == 401
    assert c.request("GET", f"/?token=nope", token=False)[0] == 401


@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_the_page_without_a_token_goes_to_the_sign_in_form(home, serve, path):
    c = serve()
    for cookie in ({}, {"Cookie": f"{c.s.cookie}=a-token-since-changed"}):
        status, answer, _ = c.request("GET", path, token=False, headers=cookie)
        assert status == 303 and answer.getheader("Location") == "/signin.html"
    # The form and what it needs are served without the token; nothing else is.
    for name in server.PUBLIC:
        status, _, body = c.request("GET", name, token=False)
        assert status == 200 and body == (server.PAGE / name.lstrip("/")).read_bytes()
    assert b"/api/signin" in (server.PAGE / "signin.js").read_bytes()


def test_the_sign_in_form_trades_the_token_for_a_cookie(home, serve):
    c = serve()
    status, answer, _ = c.request("POST", "/api/signin", body={"token": c.s.token}, token=False)
    cookie = answer.getheader("Set-Cookie")
    assert status == 200 and cookie.startswith(f"{c.s.cookie}={c.s.token};") and "Secure" not in cookie
    assert c.request("GET", "/api/loops", token=False, headers={"Cookie": cookie.split(";")[0]})[0] == 200
    for wrong in ({"token": "nope"}, {"token": 5}, {}):
        status, answer, _ = c.request("POST", "/api/signin", body=wrong, token=False)
        assert status == 401 and answer.getheader("Set-Cookie") is None
    # Sent as a form, from another site's page, it is refused before the token is looked at.
    assert c.request("POST", "/api/signin", token=False, headers={"Content-Type": "application/x-www-form-urlencoded"})[0] == 415


def test_the_cookie_is_secure_behind_an_https_proxy(home, serve):
    c = serve()
    status, answer, _ = c.request("POST", "/api/signin", body={"token": c.s.token}, token=False,
                                  headers={"X-Forwarded-Proto": "https"})
    assert status == 200 and answer.getheader("Set-Cookie").endswith("; Secure")
    status, answer, _ = c.request("GET", f"/?token={c.s.token}", token=False, headers={"X-Forwarded-Proto": "https"})
    assert status == 303 and answer.getheader("Set-Cookie").endswith("; Secure")
    status, answer, _ = c.request("GET", f"/?token={c.s.token}", token=False, headers={"X-Forwarded-Proto": "http"})
    assert "Secure" not in answer.getheader("Set-Cookie")


def test_wrong_tokens_are_answered_one_at_a_time_and_slowly(home, serve):
    c = serve()
    c.s.wrong_wait = 0.3
    answers: list[int] = []
    tries = [threading.Thread(target=lambda: answers.append(c.request("GET", "/api/loops", token=False,
                                                                        headers={"Authorization": "Bearer nope"})[0]))
             for _ in range(3)]
    began = time.monotonic()
    for t in tries:
        t.start()
    for t in tries:
        t.join()
    assert answers == [401, 401, 401] and time.monotonic() - began >= 0.85
    # No token at all, a first visit, is not made to wait.
    began = time.monotonic()
    assert c.request("GET", "/api/loops", token=False)[0] == 401 and time.monotonic() - began < 0.25


def test_another_host_or_origin_is_refused(home, serve):
    c = serve()
    # DNS rebinding: a name of the attacker's, pointed at 127.0.0.1.
    assert c.request("GET", "/api/loops", headers={"Host": f"evil.example:{c.s.port}"})[0] == 403
    assert c.request("GET", "/api/loops", headers={"Origin": "http://evil.example"})[0] == 403
    assert c.request("GET", "/api/loops", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert c.request("GET", "/api/loops", headers={"Origin": f"http://localhost:{c.s.port}", "Host": f"localhost:{c.s.port}"})[0] == 200
    # On loopback, a page over HTTPS is not this server's.
    assert c.request("GET", "/api/loops", headers={"Origin": f"https://127.0.0.1:{c.s.port}"})[0] == 403
    # A change sent as a form is refused, and no CORS header is ever sent.
    status, answer, _ = c.request("POST", "/api/loops", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert status == 415 and answer.getheader("Access-Control-Allow-Origin") is None


def test_listening_widely_any_host_is_let_in_with_the_token(home, serve):
    c = serve(wide=True)
    lan = {"Host": f"192.168.1.20:{c.s.port}"}
    assert c.request("GET", "/api/loops", headers=lan)[0] == 200
    # A rebound name is let past the Host check, but its page never holds the cookie.
    assert c.request("GET", "/api/loops", token=False, headers={"Host": f"evil.example:{c.s.port}"})[0] == 401
    # The page's own origin is the name the browser asked for, or the one a proxy passes on.
    assert c.request("GET", "/api/loops", headers={**lan, "Origin": f"http://192.168.1.20:{c.s.port}"})[0] == 200
    assert c.request("GET", "/api/loops", headers={"Host": "agentloop:8765", "Origin": "https://loop.example.org",
                                                   "X-Forwarded-Host": "loop.example.org"})[0] == 200
    assert c.request("GET", "/api/loops", headers={**lan, "Origin": "http://evil.example"})[0] == 403
    assert c.request("GET", "/api/loops", headers={**lan, "Origin": "null"})[0] == 403
    assert c.request("GET", "/api/loops", headers={**lan, "Sec-Fetch-Site": "cross-site"})[0] == 403


def test_listening_widely_the_names_listed_are_the_only_ones_let_in(home, serve):
    c = serve(wide=True, names=["loop.example.org", "nas.local:9000"])
    for host in ("loop.example.org", f"loop.example.org:{c.s.port}", "nas.local:9000", "LOOP.example.org"):
        assert c.request("GET", "/api/loops", headers={"Host": host})[0] == 200, host
    for host in (f"192.168.1.20:{c.s.port}", f"nas.local:{c.s.port}", f"evil.example:{c.s.port}"):
        assert c.request("GET", "/api/loops", headers={"Host": host})[0] == 403, host
    assert c.request("GET", "/api/loops", headers={"Host": "loop.example.org", "Origin": "https://loop.example.org"})[0] == 200
    assert c.request("GET", "/api/loops", headers={"Host": "loop.example.org", "Origin": "https://evil.example"})[0] == 403


def test_the_log_leaves_out_the_token(home):
    lines: list[str] = []
    s = server.Server(0, log=lines.append)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        Client(s).request("GET", f"/?token={s.token}", token=False)
        Client(s).request("POST", "/api/signin", body={"token": s.token}, token=False)
    finally:
        s.close()
    assert lines == ["GET / 303", "POST /api/signin 200"]


def test_the_address_and_names_come_from_the_flag_the_variable_or_the_settings(home, monkeypatch):
    monkeypatch.delenv("AGENTLOOP_HOST", raising=False)
    settings = config.load_settings()
    assert server.address(settings) == ("127.0.0.1", server.DEFAULT_PORT) and server.address(settings, 0) == ("127.0.0.1", 0)
    settings.raw["server"] = {"host": "0.0.0.0", "port": 9000}
    assert server.address(settings) == ("0.0.0.0", 9000)
    monkeypatch.setenv("AGENTLOOP_HOST", "[::]")
    assert server.address(settings) == ("::", 9000)
    assert server.address(settings, 8000, "192.168.1.20") == ("192.168.1.20", 8000)
    settings.raw["server"] = {"host": 5}
    monkeypatch.delenv("AGENTLOOP_HOST")
    with pytest.raises(config.ConfigError, match="server.host"):
        server.address(settings)
    assert server.names(settings) is None
    settings.raw["server"] = {"names": ["Loop.example.org "]}
    assert server.names(settings) == ["loop.example.org"]
    settings.raw["server"] = {"names": "loop.example.org"}
    with pytest.raises(config.ConfigError, match="server.names"):
        server.names(settings)


def test_a_token_the_person_sets_is_long_enough_and_never_printed(home, monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTLOOP_TOKEN", raising=False)
    settings = config.load_settings()
    assert server.set_token(settings) is None
    held = tmp_path / "page.token"
    held.write_text("a-token-from-a-file-0123\n", encoding="utf-8")
    settings.raw["server"] = {"tokenFile": str(held)}
    assert server.set_token(settings) == "a-token-from-a-file-0123"
    # The variable goes before the file.
    monkeypatch.setenv("AGENTLOOP_TOKEN", "a-token-from-the-container")
    assert server.set_token(settings) == "a-token-from-the-container"
    for weak in ("short", "a token with spaces in it", 'a-token-with-"quotes"-0123', "a-token;with-a-semicolon"):
        monkeypatch.setenv("AGENTLOOP_TOKEN", weak)
        with pytest.raises(config.ConfigError, match="AGENTLOOP_TOKEN") as raised:
            server.set_token(settings)
        assert weak not in str(raised.value)
    monkeypatch.delenv("AGENTLOOP_TOKEN")
    settings.raw["server"] = {"tokenFile": str(tmp_path / "none.token")}
    with pytest.raises(config.ConfigError, match="server.tokenFile"):
        server.set_token(settings)
    s = server.Server(0, token="a-token-from-the-container")
    try:
        assert s.token_set and s.url == f"http://127.0.0.1:{s.port}/" and "container" not in s.url
    finally:
        s.server_close()
    s = server.Server(0)
    try:
        assert s.url == f"http://127.0.0.1:{s.port}/?token={s.token}"
    finally:
        s.server_close()


def test_serve_listens_where_it_is_told_with_the_token_set(home, monkeypatch, capsys):
    monkeypatch.setenv("AGENTLOOP_TOKEN", "a-token-from-the-container")
    monkeypatch.setenv("AGENTLOOP_HOST", "127.0.0.1")
    made: list[server.Server] = []

    def interrupt(self, *args, **kwargs):
        made.append(self)
        raise KeyboardInterrupt

    monkeypatch.setattr(server.Server, "serve_forever", interrupt)
    assert server.serve(0) == 0
    out = capsys.readouterr()
    assert made[0].token == "a-token-from-the-container" and not made[0].wide
    assert "sign in with the token you set" in out.out and "a-token-from-the-container" not in out.out + out.err


def test_serve_ends_on_sigterm_as_on_ctrl_c(home, monkeypatch):
    import signal

    monkeypatch.setenv("AGENTLOOP_HOST", "127.0.0.1")
    before = signal.getsignal(signal.SIGTERM)

    def terminated(self, *args, **kwargs):
        # Only once serve has a handler of its own: SIGTERM's own would end the test run.
        assert signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, signal.SIG_IGN, None, before)
        signal.raise_signal(signal.SIGTERM)

    monkeypatch.setattr(server.Server, "serve_forever", terminated)
    assert server.serve(0) == 0
    assert signal.getsignal(signal.SIGTERM) == before


def write_said(name: str, state: str | None, kill: bool = False) -> None:
    """A loop's folder as its last run left it: what it said in status.json, and a kill file."""
    folder = loop_mod.loop_dir("proj", name)
    folder.mkdir(parents=True, exist_ok=True)
    if state is not None:
        loop_mod.write_json(folder / "status.json", {"state": state, "item": "alpha"})
    if kill:
        (folder / "kill").touch()


def test_the_loops_cut_off_mid_run_are_started_again(home, monkeypatch, capsys):
    path = config.paths().settings
    raw = json.loads(path.read_text(encoding="utf-8"))
    names = ["working", "waiting", "idle", "stopped", "failed", "killed", "unsaid", "refused"]
    raw["loops"] = [loop_entry(name) for name in names]
    config.save_json(path, raw)
    # Died saying it was working, waiting out a limit or idle: cut off.
    write_said("working", "running")
    write_said("waiting", "waiting")
    write_said("idle", "idle")
    write_said("refused", "running")
    # Stopped, ended with an error, killed, or never said anything: left as they are.
    write_said("stopped", "stopped")
    write_said("failed", "failed")
    write_said("killed", "running", kill=True)
    write_said("unsaid", None)
    the_runner = runner.Runner()
    assert the_runner.cut_off() == ["working", "waiting", "idle", "refused"]

    started = []

    def start(self, name, setup=False):
        if name == "refused":
            raise runner.RunnerError("refused cannot start: the script is working proj.")
        started.append(name)

    monkeypatch.setattr(runner.Runner, "start", start)
    assert server.restart_cut_off(the_runner) == ["working", "waiting", "idle"]
    assert started == ["working", "waiting", "idle"]
    out = capsys.readouterr()
    assert "Started working again" in out.out and "refused was cut off, and could not be started again" in out.err

    # serve does so only when asked.
    started.clear()
    monkeypatch.setenv("AGENTLOOP_HOST", "127.0.0.1")
    monkeypatch.setattr(server.Server, "serve_forever", lambda self, *a, **k: None)
    assert server.serve(0) == 0 and started == []
    assert server.serve(0, restart_loops=True) == 0 and started == ["working", "waiting", "idle"]


def test_a_server_on_ipv6_loopback(home):
    try:
        s = server.Server(0, host="::1")
    except OSError:
        pytest.skip("no IPv6 on this machine")
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        assert not s.wide and s.url.startswith(f"http://[::1]:{s.port}/?token=")
        connection = http.client.HTTPConnection("::1", s.port, timeout=30)
        connection.request("GET", "/api/loops", headers={"Host": f"[::1]:{s.port}", "Authorization": f"Bearer {s.token}"})
        assert connection.getresponse().status == 200
        connection.close()
    finally:
        s.close()


# Settings, bundles and loops

def test_the_page_and_its_files_are_served(home, serve):
    c = serve()
    for name, kind in (("page.js", "text/javascript"), ("page.css", "text/css")):
        status, answer, body = c.request("GET", f"/{name}")
        assert status == 200 and answer.getheader("Content-Type").startswith(kind) and body == (server.PAGE / name).read_bytes()
    page = (server.PAGE / "index.html").read_text(encoding="utf-8")
    assert '<script src="/page.js"' in page and '<link rel="stylesheet" href="/page.css">' in page


def test_the_page_draws_text_never_markup():
    # Every text from the server - a session's output, a setting, an error - goes in as text.
    script = (server.PAGE / "page.js").read_text(encoding="utf-8")
    for markup in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert markup not in script


def test_a_loop_says_what_it_is_missing(home, serve):
    c = serve()
    code = home / "code"
    code.mkdir()
    subprocess.run(["git", "init", "-q", str(code)], check=True)
    placed = c.json("POST", "/api/loops", {"loop": loop_entry("placed", repos={"code": str(code)})})
    assert placed["missing"] == []
    unplaced = c.json("POST", "/api/loops", {"loop": loop_entry("unplaced", repos={})})
    assert unplaced["missing"] == [{"field": "repos.code", "message": "proj uses the repository code, and this loop does not say where it is"}]
    nowhere = c.json("POST", "/api/loops", {"loop": loop_entry("nowhere", repos={"code": str(home / "gone")})})
    assert nowhere["missing"] == [{"field": "repos.code", "message": "no such folder"}]
    # A worker without the key file, which a tracker on a server needs; the key itself is never read.
    raw = c.json("GET", "/api/settings")
    raw["settings"]["workers"].append({"name": "keyless", "claude": None, "overseer": None})
    c.json("PUT", "/api/settings", {"version": raw["version"], "settings": raw["settings"]})
    keyless = c.json("POST", "/api/loops", {"loop": loop_entry("keyless", worker="keyless", repos={"code": str(code)})})
    assert [m["field"] for m in keyless["missing"]] == ["worker"]
    # Judged as the loop judges it: the first of them is what a start is refused for.
    machine = config.load_machine()
    with pytest.raises(config.ConfigError) as refused:
        config.place_repos(machine.settings.loop("unplaced"), machine.bundles["proj"])
    assert refused.value.field == "repos.code"


def test_a_loop_on_a_tracker_is_shown_from_the_definition_it_kept(home, serve):
    # The worker's server is a port nothing listens on: the page never asks it (item doc-read).
    c = serve()
    code = home / "code"
    code.mkdir()
    subprocess.run(["git", "init", "-q", str(code)], check=True)
    made = c.json("POST", "/api/loops", {"loop": {"name": "t", "tracker": "alice/projects/remote.os", "worker": "w",
                                                  "repos": {"code": str(code)}}})
    assert (made["project"], made["source"], made["tracker"], made["definition"]) == ("remote", "tracker", {"server": "alice/projects/remote.os"}, None)
    assert made["missing"] == []
    from agentloop import definition, loop as loop_mod
    definition.keep(loop_mod.loop_dir("remote", "t"), "alice/projects/remote.os", {
        "description": "Remote.", "repos": [{"name": "code", "about": "The code"}, {"name": "docs"}], "places": [],
        "systems": None, "settings": {"repeatUpTo": 7}}, read_at="2026-11-16T12:00:00+00:00")
    shown = next(l for l in c.json("GET", "/api/loops")["loops"] if l["name"] == "t")
    assert shown["definition"] == {"repos": ["code", "docs"], "places": []} and shown["about"] == {"code": "The code"}
    assert shown["definitionRead"] == "2026-11-16T12:00:00+00:00" and shown["effective"]["repeatUpTo"] == 7
    assert [m["field"] for m in shown["missing"]] == ["repos.docs"]
    bundled = c.json("POST", "/api/loops", {"loop": loop_entry("b", repos={"code": str(code)})})
    assert bundled["source"] == "bundle" and bundled["definition"] is None


def test_a_machine_set_up_from_nothing(tmp_path, monkeypatch, serve):
    # No settings file, as on a PC the app has just been installed on: everything the page does
    # writes it, and nothing has to be made by hand first.
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    (tmp_path / "user" / ".claude-account1").mkdir(parents=True)
    bundle = tmp_path / "handed" / "proj"
    bundle.mkdir(parents=True)
    config.save_json(bundle / "project.json", {"format": 1, "name": "proj", "tracker": {"server": "alice/projects/proj.os"},
                                               "repos": ["code"], "places": ["tool"], "settings": {}})
    (bundle / "brief.md").write_text("# The brief\n", encoding="utf-8")
    key = tmp_path / "agent.key"
    key.write_text(KEY, encoding="utf-8")
    code = tmp_path / "code"
    code.mkdir()
    subprocess.run(["git", "init", "-q", str(code)], check=True)
    assert not config.paths().settings.exists()

    c = serve()
    assert c.json("GET", "/api/loops")["loops"] == [] and c.json("GET", "/api/bundles")["bundles"] == []
    assert c.json("GET", "/api/accounts")["found"] == [{"name": "account1", "claude": "~/.claude-account1"}]
    assert c.json("POST", "/api/accounts", {"claude": "~/.claude-account1"}) == {"added": "account1"}
    assert config.paths().settings.exists()
    # The bundle's folder, and the worker's server and key file, as This machine changes them.
    read = c.json("GET", "/api/settings")
    raw = read["settings"]
    raw["bundles"] = [str(bundle)]
    raw["workers"][0]["overseer"] = {"server": "http://127.0.0.1:1", "keyFile": str(key)}
    c.json("PUT", "/api/settings", {"version": read["version"], "settings": raw})
    assert [b["name"] for b in c.json("GET", "/api/bundles")["bundles"]] == ["proj"]
    made = c.json("POST", "/api/loops", {"loop": {"name": "proj", "project": "proj", "worker": "account1",
                                                  "repos": {"code": str(code)}, "settings": {}}})
    assert [m["field"] for m in made["missing"]] == ["places.tool"]
    tool = tmp_path / "tool"
    tool.mkdir()
    placed = c.json("PUT", "/api/loops/proj", {"loop": {**{k: made[k] for k in ("name", "project", "worker", "repos", "settings")},
                                                        "places": {"tool": str(tool)}}})
    assert placed["missing"] == []
    assert config.load_settings().loop("proj").places == {"tool": str(tool)}


def test_the_settings_come_with_the_apps_own(home, serve):
    # Beneath every layer, so the page can say where a value is from.
    assert serve().json("GET", "/api/settings")["appDefaults"] == config.DEFAULTS


def test_loops_made_changed_and_deleted(home, serve):
    c = serve()
    assert c.json("GET", "/api/loops")["loops"] == []
    made = c.json("POST", "/api/loops", {"loop": loop_entry(settings={"model": "claude-sonnet-5-5"})})
    assert made["name"] == "one" and made["status"]["state"] == "stopped" and made["tracker"]["server"] == "alice/projects/proj.os"
    # Effective: the bundle's repeatUpTo, the loop's model.
    assert made["effective"]["repeatUpTo"] == 3 and made["effective"]["model"] == "claude-sonnet-5-5"

    bad = c.json("POST", "/api/loops", {"loop": loop_entry("two", worker="nobody")}, expect=400)
    assert bad["field"] == "loops[1].worker"
    bad = c.json("POST", "/api/loops", {"loop": loop_entry("two", project="nothing")}, expect=400)
    assert "no bundle named" in bad["error"]
    assert [l.name for l in config.load_settings().loops] == ["one"]

    changed = c.json("PUT", "/api/loops/one", {"loop": loop_entry(repos={"code": "~/elsewhere"})})
    assert changed["repos"] == {"code": "~/elsewhere"}
    assert c.json("PUT", "/api/loops/nope", {"loop": loop_entry("nope")}, expect=404)
    assert c.json("DELETE", "/api/loops/one", {}) == {"deleted": "one"}
    assert config.load_settings().loops == []


def test_the_auto_start_toggle(home, serve):
    c = serve()
    c.json("POST", "/api/loops", {"loop": loop_entry()})
    assert c.json("GET", "/api/loops/one")["effective"]["autoStart"] is False
    assert c.json("POST", "/api/loops/one/auto-start", {"on": True})["effective"]["autoStart"] is True
    assert config.load_settings().loop("one").settings == {"autoStart": True}
    assert c.json("POST", "/api/loops/one/auto-start", {"on": False})["effective"]["autoStart"] is False
    assert "Send" in c.json("POST", "/api/loops/one/auto-start", {"on": "yes"}, expect=400)["error"]
    c.json("POST", "/api/loops/none/auto-start", {"on": True}, expect=404)


def test_a_copy_of_a_loop_made_and_its_original_kept(home, serve):
    c = serve()
    c.json("POST", "/api/loops", {"loop": loop_entry(settings={"chainUpTo": 1})})
    made = c.json("POST", "/api/loops", {"loop": {"name": "two", "like": "one", "repos": {"code": "~/code-2"}}})
    assert (made["like"], made["project"], made["worker"], made["repos"]) == ("one", "proj", "w", {"code": "~/code-2"})
    assert made["settings"] == {"chainUpTo": 1} and made["effective"]["repeatUpTo"] == 3
    # The file keeps only what the copy gives itself.
    assert config.load_settings().raw["loops"][1] == {"name": "two", "like": "one", "repos": {"code": "~/code-2"}}

    # The original can be neither deleted nor renamed while the copy names it.
    refused = c.json("DELETE", "/api/loops/one", {}, expect=409)
    assert refused["error"].startswith("one cannot be deleted: two is a copy of it.")
    refused = c.json("PUT", "/api/loops/one", {"loop": loop_entry("uno")}, expect=409)
    assert refused["error"].startswith("one cannot be renamed: two is a copy of it.")
    # Changed in place it can be, and the copy follows.
    c.json("PUT", "/api/loops/one", {"loop": loop_entry(settings={"chainUpTo": 2})})
    assert c.json("GET", "/api/loops/two")["settings"] == {"chainUpTo": 2}
    assert c.json("DELETE", "/api/loops/two", {}) == {"deleted": "two"}
    assert c.json("DELETE", "/api/loops/one", {}) == {"deleted": "one"}


def test_the_settings_change_only_from_the_version_read(home, serve):
    c = serve()
    read = c.json("GET", "/api/settings")
    raw = read["settings"]
    raw["defaults"] = {"repeatUpTo": 5}
    done = c.json("PUT", "/api/settings", {"version": read["version"], "settings": raw})
    assert done["settings"]["defaults"] == {"repeatUpTo": 5} and done["version"] != read["version"]
    # The page's copy is now out of date: the second change is refused, and nothing written.
    raw["defaults"] = {"repeatUpTo": 6}
    stale = c.json("PUT", "/api/settings", {"version": read["version"], "settings": raw}, expect=409)
    assert stale["version"] == done["version"]
    raw["defaults"] = {"repeatUpTo": "many"}
    assert c.json("PUT", "/api/settings", {"version": done["version"], "settings": raw}, expect=400)["field"].startswith("defaults")
    assert config.load_settings().defaults == {"repeatUpTo": 5}


def test_a_bundle_read_and_changed(home, serve):
    c = serve()
    assert [b["name"] for b in c.json("GET", "/api/bundles")["bundles"]] == ["proj"]
    read = c.json("GET", "/api/bundles/proj")
    raw = read["bundle"]
    raw["settings"] = {"repeatUpTo": 9}
    done = c.json("PUT", "/api/bundles/proj", {"version": read["version"], "bundle": raw})
    assert done["bundle"]["settings"] == {"repeatUpTo": 9}
    assert c.json("PUT", "/api/bundles/proj", {"version": read["version"], "bundle": raw}, expect=409)
    raw["name"] = "other"
    assert c.json("PUT", "/api/bundles/proj", {"version": done["version"], "bundle": raw}, expect=400)["field"] == "name"
    assert c.json("GET", "/api/bundles/nope", expect=404)


def test_the_accounts_say_how_each_signs_in_and_never_the_secret(home, serve, fake_claude, monkeypatch):
    raw = json.loads(config.paths().settings.read_text(encoding="utf-8"))
    raw["workers"].append({"name": "tok", "claude": None, "overseer": None, "signIn": {"token": "AGENTLOOP_TEST_TOKEN"}})
    raw["workers"].append({"name": "key", "claude": None, "overseer": None, "signIn": {"apiKey": "AGENTLOOP_TEST_KEY"}})
    config.save_json(config.paths().settings, raw)
    monkeypatch.setenv("AGENTLOOP_TEST_TOKEN", "tok-very-secret")
    monkeypatch.delenv("AGENTLOOP_TEST_KEY", raising=False)
    c = serve()
    said = c.json("GET", "/api/accounts")
    assert "tok-very-secret" not in json.dumps(said)
    w, tok, key = said["workers"]
    assert w["signIn"].startswith("signs in by the login in ")
    assert tok["signIn"] == "signs in with a token from AGENTLOOP_TEST_TOKEN"
    assert tok["login"]["state"] == "in" and "setup-token" in tok["loginCommand"]
    assert key["signIn"] == "signs in with an API key, billed per use, from AGENTLOOP_TEST_KEY - AGENTLOOP_TEST_KEY is not set"
    assert key["login"]["line"] == "not signed in: AGENTLOOP_TEST_KEY is not set"
    assert "billed per use" in key["usage"]["line"]
    assert "billed per use" in c.json("GET", "/api/usage")["workers"]["key"]["line"]
    # The page's settings carry the variable's name, which is all the settings hold.
    assert raw["workers"][1]["signIn"] == {"token": "AGENTLOOP_TEST_TOKEN"}


def test_the_accounts(home, serve, fake_claude):
    c = serve()
    (home / "account" / "fake-login").write_text("max", encoding="utf-8")
    said = c.json("GET", "/api/accounts")
    [w] = said["workers"]
    assert w["name"] == "w" and w["default"] and w["login"]["state"] == "in" and w["login"]["line"] == "logged in (claude.ai, max)"
    assert "claude auth login" in w["loginCommand"] and w["usage"] is None
    assert said["found"] == [{"name": "account7", "claude": "~/.claude-account7"}]
    assert c.json("POST", "/api/accounts", {"claude": "~/.claude-account7"}) == {"added": "account7"}
    assert c.json("POST", "/api/accounts", {"claude": "~/.claude-account7", "name": "again"}, expect=400)
    assert c.json("POST", "/api/accounts/default", {"worker": "account7"}) == {"default": "account7"}
    assert config.load_settings().default_worker_name == "account7"


def test_the_usage_alone(home, serve, fake_claude):
    c = serve()
    assert c.json("GET", "/api/usage") == {"workers": {"w": None}}
    rate = {"unifiedWindows": {"five_hour": {"utilization": 0.4}, "seven_day": {"utilization": 0.1}}}
    accounts.save_usage("w", rate, at=1000.0)
    said = c.json("GET", "/api/usage")["workers"]["w"]
    assert said["at"] == 1000.0 and said["rate"] == rate
    assert said["line"].endswith(": 5-hour 40% used; weekly 10% used")
    # The same as the accounts give, read again on each request.
    assert c.json("GET", "/api/accounts")["workers"][0]["usage"] == said
    accounts.save_usage("w", {"unifiedWindows": {"five_hour": {"utilization": 0.5}}}, at=2000.0)
    assert c.json("GET", "/api/usage")["workers"]["w"]["at"] == 2000.0


def test_no_answer_holds_the_key(home, serve, fake_claude):
    c = serve()
    c.json("POST", "/api/loops", {"loop": loop_entry()})
    for path in ("/", "/api/loops", "/api/loops/one", "/api/settings", "/api/bundles", "/api/bundles/proj", "/api/accounts"):
        status, _, raw = c.request("GET", path)
        assert status == 200 and KEY.encode() not in raw, path
    # The key file is named, as a path.
    assert c.json("GET", "/api/accounts")["workers"][0]["overseer"]["keyFile"] == str(home / "agent.key")


# A loop at work

class Events:
    """A loop's output as the page follows it: asked for again and again, from where it got to."""

    def __init__(self, c: Client, name: str, last: str | None = None):
        self.c = c
        self.name = name
        self.last = last
        self.text = ""
        self.statuses: list[dict] = []
        self.resets = 0

    def next(self) -> None:
        answer = self.c.json("POST", "/api/output", {"loops": {self.name: self.last}})["loops"][self.name]
        if answer["reset"]:
            self.text = ""
            self.resets += 1
        self.text += answer["text"]
        self.last = answer["id"]
        self.statuses.append(answer["status"])

    def until(self, test, timeout: float = 90.0) -> None:
        deadline = time.monotonic() + timeout
        self.next()
        while not test(self):
            assert time.monotonic() < deadline, self.text
            time.sleep(0.1)
            self.next()


def test_a_loop_started_followed_stopped_and_killed(tmp_path, monkeypatch, fake_claude, overseer, serve):
    m = Machine(tmp_path, monkeypatch, fake_claude, overseer)
    c = serve(m.runner)
    try:
        listed = c.json("GET", "/api/loops")["loops"]
        assert [(l["name"], l["status"]["state"]) for l in listed] == [("a", "stopped"), ("b", "stopped")]

        fake_claude.scenario(str(m.scene("a", write("a.txt"), {"fake": "sleep", "seconds": 3}, move("alpha"), result="COMMIT: alpha: built")))
        started = c.json("POST", "/api/loops/a/start", {})
        assert started["alive"] and started["name"] == "a"
        assert "already working" in c.json("POST", "/api/loops/a/start", {}, expect=409)["error"]
        assert "is running" in c.json("DELETE", "/api/loops/a", {}, expect=409)["error"]

        events = Events(c, "a")
        events.until(lambda e: any(s["item"] == "alpha" for s in e.statuses))
        assert c.json("POST", "/api/loops/a/stop", {})["state"] == "stopping"
        events.until(lambda e: e.statuses and not e.statuses[-1]["alive"])
        assert events.statuses[-1]["state"] == "stopped"
        events.until(lambda e: "Stopping as asked." in e.text, timeout=10)
        assert f"Starting the agent loop on {m.project['a']}." in events.text
        # In the script's colours, for the page to draw: the iteration yellow, the item cyan.
        assert "[93m Iteration 1..." in events.text and "[96m alpha - " in events.text
        assert events.text == m.runner.output("a").text
        last = events.last

        # Come back with the last id: nothing is sent twice.
        again = Events(c, "a", last)
        again.next()
        assert again.text == ""

        # A new run's output, come back to with the last run's id, begins from the top.
        fake_claude.scenario(str(m.scene("a2", {"fake": "sleep", "seconds": 120})))
        c.json("POST", "/api/loops/a/start", {})
        newer = Events(c, "a", last)
        newer.until(lambda e: e.resets and f"Starting the agent loop on {m.project['a']}." in e.text)
        newer.until(lambda e: any(s["item"] == "beta" for s in e.statuses) and m.running_json("a").get("child"))
        killed = c.json("POST", "/api/loops/a/kill", {})
        assert killed["ended_session"] and killed["item"] == "beta" and "claude --resume" in killed["resume"]
        newer.until(lambda e: not e.statuses[-1]["alive"])
        assert newer.statuses[-1]["killed"]
        assert "No loop is working" in c.json("POST", "/api/loops/a/kill", {}, expect=409)["error"]
    finally:
        for name in ("a", "b"):
            state = m.runner.status(name)
            if state.alive and state.pid:
                runner.end_tree(state.pid)


def test_output_asked_for_several_loops_at_once(tmp_path, monkeypatch, fake_claude, overseer, serve):
    m = Machine(tmp_path, monkeypatch, fake_claude, overseer)
    c = serve(m.runner)

    def ask(**loops):
        return c.json("POST", "/api/output", {"loops": loops})["loops"]

    # No run yet: nothing, and from no id, and a loop that is not there does not spoil the rest.
    first = ask(a=None, b=None, gone=None)
    assert sorted(first) == ["a", "b"]
    assert first["a"]["text"] == "" and first["a"]["id"] is None and first["a"]["status"]["state"] == "stopped"

    path = runner.output_path("a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"one\ntwo\npart")
    answer = ask(a=None)["a"]
    assert answer["text"] == "one\ntwo\n" and not answer["reset"]
    # The rest of a line once it is whole, and nothing twice.
    with open(path, "ab") as f:
        f.write(b"ial\n")
    more = ask(a=answer["id"])["a"]
    assert more["text"] == "partial\n"
    assert ask(a=more["id"])["a"]["text"] == ""
    # A new run moves the file aside, and is begun from the top.
    path.replace(path.with_suffix(".prev.log"))
    path.write_bytes(b"new\n")
    newer = ask(a=more["id"])["a"]
    assert newer["reset"] and newer["text"] == "new\n"

    assert "Send" in c.json("POST", "/api/output", {"loops": 1}, expect=400)["error"]


def test_the_status_waits_for_no_kill(tmp_path, monkeypatch, fake_claude, overseer, serve):
    m = Machine(tmp_path, monkeypatch, fake_claude, overseer)
    c = serve(m.runner)
    # What a kill holds for as long as the loop takes to end.
    with c.s.loop_lock("a"):
        done: list = []

        def read() -> None:
            done.append(c.json("GET", "/api/loops")["loops"])
            done.append(c.json("POST", "/api/output", {"loops": {"a": None}})["loops"])

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        thread.join(20)
        assert len(done) == 2
