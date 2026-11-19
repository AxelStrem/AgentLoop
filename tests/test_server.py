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

    def make(the_runner: runner.Runner | None = None) -> Client:
        s = server.Server(0, the_runner=the_runner)
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
                                               "repos": ["code"], "settings": {"maxIterations": 3}})
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


@pytest.mark.parametrize("path", ["/", "/index.html", "/api/loops", "/api/settings"])
def test_no_token_or_a_wrong_one_is_refused(home, serve, path):
    c = serve()
    assert c.request("GET", path, token=False)[0] == 401
    assert c.request("GET", path, token=False, headers={"Cookie": f"{c.s.cookie}=nope"})[0] == 401
    assert c.request("GET", f"/?token=nope", token=False)[0] == 401


def test_another_host_or_origin_is_refused(home, serve):
    c = serve()
    # DNS rebinding: a name of the attacker's, pointed at 127.0.0.1.
    assert c.request("GET", "/api/loops", headers={"Host": f"evil.example:{c.s.port}"})[0] == 403
    assert c.request("GET", "/api/loops", headers={"Origin": "http://evil.example"})[0] == 403
    assert c.request("GET", "/api/loops", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert c.request("GET", "/api/loops", headers={"Origin": f"http://localhost:{c.s.port}", "Host": f"localhost:{c.s.port}"})[0] == 200
    # A change sent as a form is refused, and no CORS header is ever sent.
    status, answer, _ = c.request("POST", "/api/loops", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert status == 415 and answer.getheader("Access-Control-Allow-Origin") is None


def test_the_static_files_stay_in_the_page_folder(home, serve):
    c = serve()
    assert c.request("GET", "/../server.py")[0] == 404
    assert c.request("GET", "/%2e%2e/server.py")[0] == 404
    assert c.request("GET", "/nothing.css")[0] == 404
    assert c.json("GET", "/api/nothing", expect=404)["error"] == "No such route: /api/nothing."
    assert c.json("DELETE", "/api/settings", body={}, expect=405)


def test_the_log_leaves_out_the_token(home):
    lines: list[str] = []
    s = server.Server(0, log=lines.append)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        Client(s).request("GET", f"/?token={s.token}", token=False)
    finally:
        s.close()
    assert lines == ["GET / 303"]


def test_the_address_is_loopback_only(home):
    settings = config.load_settings()
    assert server.address(settings) == server.DEFAULT_PORT and server.address(settings, 0) == 0
    settings.raw["server"] = {"host": "0.0.0.0", "port": 9000}
    with pytest.raises(config.ConfigError, match="127.0.0.1 only"):
        server.address(settings)
    settings.raw["server"] = {"host": "127.0.0.1", "port": 9000}
    assert server.address(settings) == 9000


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
        "systems": None, "settings": {"maxIterations": 7}}, read_at="2026-11-16T12:00:00+00:00")
    shown = next(l for l in c.json("GET", "/api/loops")["loops"] if l["name"] == "t")
    assert shown["definition"] == {"repos": ["code", "docs"], "places": []} and shown["about"] == {"code": "The code"}
    assert shown["definitionRead"] == "2026-11-16T12:00:00+00:00" and shown["effective"]["maxIterations"] == 7
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
    # Effective: the bundle's maxIterations, the loop's model.
    assert made["effective"]["maxIterations"] == 3 and made["effective"]["model"] == "claude-sonnet-5-5"

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
    assert made["settings"] == {"chainUpTo": 1} and made["effective"]["maxIterations"] == 3
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
    raw["defaults"] = {"maxIterations": 5}
    done = c.json("PUT", "/api/settings", {"version": read["version"], "settings": raw})
    assert done["settings"]["defaults"] == {"maxIterations": 5} and done["version"] != read["version"]
    # The page's copy is now out of date: the second change is refused, and nothing written.
    raw["defaults"] = {"maxIterations": 6}
    stale = c.json("PUT", "/api/settings", {"version": read["version"], "settings": raw}, expect=409)
    assert stale["version"] == done["version"]
    raw["defaults"] = {"maxIterations": "many"}
    assert c.json("PUT", "/api/settings", {"version": done["version"], "settings": raw}, expect=400)["field"].startswith("defaults")
    assert config.load_settings().defaults == {"maxIterations": 5}


def test_a_bundle_read_and_changed(home, serve):
    c = serve()
    assert [b["name"] for b in c.json("GET", "/api/bundles")["bundles"]] == ["proj"]
    read = c.json("GET", "/api/bundles/proj")
    raw = read["bundle"]
    raw["settings"] = {"maxIterations": 9}
    done = c.json("PUT", "/api/bundles/proj", {"version": read["version"], "bundle": raw})
    assert done["bundle"]["settings"] == {"maxIterations": 9}
    assert c.json("PUT", "/api/bundles/proj", {"version": read["version"], "bundle": raw}, expect=409)
    raw["name"] = "other"
    assert c.json("PUT", "/api/bundles/proj", {"version": done["version"], "bundle": raw}, expect=400)["field"] == "name"
    assert c.json("GET", "/api/bundles/nope", expect=404)


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
        assert "[93m Iteration 1 of" in events.text and "[96m alpha - " in events.text
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
