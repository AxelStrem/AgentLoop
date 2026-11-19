"""`agentloop serve`: the local web server behind the page.

It listens on loopback only. Any program on the PC can still open a loopback port, and any page in
the browser can send it requests, so every request must carry a token printed with the address:
`/?token=...` trades it for a cookie, and the page's requests carry that. A request whose Host is
not this server's (DNS rebinding) or whose Origin is another site's is refused, a change must be
sent as JSON (which a form on another site cannot send without a preflight, never answered here),
and no CORS header is ever sent.

The routes answer JSON. A setting's secret is never served, only the paths in the settings: the key
file is a path there, and no file is read but the page's own.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from agentloop import accounts, config, definition, runner, session, usage
from agentloop import loop as loop_mod

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PAGE = Path(__file__).resolve().parent / "page"
# A request body larger than any settings file.
MAX_BODY = 1 << 20
CHANGES = ("POST", "PUT", "DELETE")
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class Refused(Exception):
    """An answer other than 200, with the error said to the page."""

    def __init__(self, status: int, message: str, **more: Any):
        super().__init__(message)
        self.status = status
        self.more = more


class _Answered(Refused):
    """The answer is sent already: nothing more is sent."""

    def __init__(self) -> None:
        super().__init__(0, "")


def version(path: Path) -> str:
    """The file's version, which a change must name: the hash of its bytes, or "" for none."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""


def address(settings: config.Settings, port: int | None = None) -> int:
    """The port to listen on: `port`, else the settings' `server.port`, else the default. A
    `server.host` other than loopback is refused: the page is for this PC alone."""
    server = settings.raw.get("server") or {}
    host = server.get("host")
    if host is not None and host not in ("127.0.0.1", "localhost"):
        raise config.ConfigError(settings.path.name, "server.host", "the page listens on 127.0.0.1 only")
    if port is not None:
        return port
    return int(server.get("port") or DEFAULT_PORT)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = DEFAULT_PORT, token: str | None = None, the_runner: runner.Runner | None = None,
                 log: Callable[[str], None] | None = None):
        self.token = token or secrets.token_urlsafe(32)
        self.runner = the_runner or runner.Runner()
        self.log = log
        # The runner keeps the processes it started in a dict, so what changes a loop is done one
        # at a time for each loop, while a status, which only reads, waits for none of it: a kill
        # holds the lock for as long as the loop takes to end, and the whole page reads the status
        # of every loop. A change to the settings reads, checks and writes them under one lock.
        self.loop_locks: dict[str, threading.Lock] = {}
        self.locks_lock = threading.Lock()
        self.settings_lock = threading.Lock()
        super().__init__((HOST, port), Handler)

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/?token={self.token}"

    @property
    def cookie(self) -> str:
        # Cookies are per host, not per port: two servers on one PC each keep their own.
        return f"agentloop-{self.port}"

    def hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def origins(self) -> set[str]:
        return {f"http://{h}" for h in self.hosts()}

    def loop_lock(self, name: str) -> threading.Lock:
        with self.locks_lock:
            return self.loop_locks.setdefault(name, threading.Lock())

    def close(self) -> None:
        """Ends the server. The loops go on: each is a process of its own."""
        self.shutdown()
        self.server_close()


# Routes: (method, pattern, handler's name). A pattern's groups are the handler's arguments.
ROUTES = [
    ("GET", r"/api/loops", "loops"),
    ("POST", r"/api/loops", "add_loop"),
    ("POST", r"/api/loops/from-tracker", "add_from_tracker"),
    ("POST", r"/api/definition", "read_definition"),
    ("GET", r"/api/loops/([^/]+)", "get_loop"),
    ("PUT", r"/api/loops/([^/]+)", "put_loop"),
    ("DELETE", r"/api/loops/([^/]+)", "delete_loop"),
    ("POST", r"/api/loops/([^/]+)/start", "start"),
    ("POST", r"/api/loops/([^/]+)/stop", "stop"),
    ("POST", r"/api/loops/([^/]+)/kill", "kill"),
    ("POST", r"/api/loops/([^/]+)/auto-start", "auto_start"),
    ("POST", r"/api/loops/([^/]+)/accept-definition", "accept_definition"),
    ("POST", r"/api/output", "output"),
    ("GET", r"/api/settings", "get_settings"),
    ("PUT", r"/api/settings", "put_settings"),
    ("GET", r"/api/bundles", "bundles"),
    ("GET", r"/api/bundles/([^/]+)", "get_bundle"),
    ("PUT", r"/api/bundles/([^/]+)", "put_bundle"),
    ("GET", r"/api/accounts", "get_accounts"),
    ("POST", r"/api/accounts", "add_account"),
    ("POST", r"/api/accounts/default", "default_account"),
    ("GET", r"/api/usage", "get_usage"),
]


def worker_usage(worker: config.Worker) -> dict | None:
    """The worker's last usage as the page shows it: when it was reported, what, and its line."""
    last = accounts.last_usage(worker.name)
    if not last:
        return None
    return {"at": last[0], "rate": last[1], "line": usage.usage_line(last[1], usage.account_name(worker.claude))}


class Handler(BaseHTTPRequestHandler):
    server: Server
    server_version = "agentloop"
    sys_version = ""

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    # The log names the path, never the query: the token is in it.
    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        if self.server.log:
            self.server.log(f"{self.command} {urlsplit(self.path).path} {int(code) if str(code).isdigit() else code}")

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _handle(self, method: str) -> None:
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        try:
            self._admit(method, path, parse_qs(parts.query))
            if path.startswith("/api/"):
                self._route(method, path)
            elif method == "GET":
                self._static(path)
            else:
                raise Refused(HTTPStatus.METHOD_NOT_ALLOWED, "Not allowed here.")
        except _Answered:
            pass
        except Refused as refused:
            self._json({"error": str(refused), **refused.more}, refused.status)
        except definition.Refusals as refused:
            # Each field wrong at once, for the form to mark them all (item loop-from-url).
            self._json({"error": "\n".join(str(p) for p in refused.problems), "where": refused.where, "field": refused.field,
                        "problems": [{"field": p.field, "message": p.message} for p in refused.problems]},
                       HTTPStatus.BAD_REQUEST)
        except config.ConfigError as error:
            self._json({"error": str(error), "where": error.where, "field": error.field}, HTTPStatus.BAD_REQUEST)
        except definition.Unreachable as error:
            # Its text names the exception's type only, never the address.
            self._json({"error": f"The tracker's server: {error}."}, HTTPStatus.BAD_GATEWAY)
        except runner.RunnerError as error:
            self._json({"error": str(error)}, HTTPStatus.CONFLICT)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as error:
            # The type alone: an exception's text can name a path or a value of the settings.
            self._json({"error": f"The server failed ({type(error).__name__})."}, HTTPStatus.INTERNAL_SERVER_ERROR)

    # Who may reach it

    def _admit(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        if self.headers.get("Host", "") not in self.server.hosts():
            raise Refused(HTTPStatus.FORBIDDEN, "Not this server's address.")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.origins():
            raise Refused(HTTPStatus.FORBIDDEN, "Requests from another site are refused.")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise Refused(HTTPStatus.FORBIDDEN, "Requests from another site are refused.")
        given = query.get("token", [None])[0]
        if method == "GET" and path == "/" and given is not None and self._same(given):
            # The token for a cookie, and the address without it, so that it stays out of the
            # browser's history and the address bar.
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Set-Cookie", f"{self.server.cookie}={self.server.token}; HttpOnly; SameSite=Strict; Path=/")
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self._common_headers()
            self.end_headers()
            raise _Answered()
        if not self._same(self._token()):
            raise Refused(HTTPStatus.UNAUTHORIZED, "Open the address agentloop serve printed, with its token.")
        if method in CHANGES and self.headers.get_content_type() != "application/json":
            raise Refused(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "A change is sent as application/json.")

    def _token(self) -> str | None:
        bearer = self.headers.get("Authorization", "")
        if bearer.startswith("Bearer "):
            return bearer[len("Bearer "):]
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        morsel = cookies.get(self.server.cookie)
        return morsel.value if morsel else None

    def _same(self, token: str | None) -> bool:
        return token is not None and hmac.compare_digest(token.encode(), self.server.token.encode())

    # Answers

    def _common_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def _send(self, body: bytes, content_type: str, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._common_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        self._send(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise Refused(HTTPStatus.BAD_REQUEST, "No length to the request.") from None
        if length > MAX_BODY:
            raise Refused(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "The request is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise Refused(HTTPStatus.BAD_REQUEST, "The request is not JSON.") from None
        if not isinstance(data, dict):
            raise Refused(HTTPStatus.BAD_REQUEST, "The request is a JSON object.")
        return data

    def _static(self, path: str) -> None:
        name = "index.html" if path == "/" else path.lstrip("/")
        target = (PAGE / name).resolve()
        if not target.is_relative_to(PAGE) or not target.is_file():
            raise Refused(HTTPStatus.NOT_FOUND, "No such page.")
        self._send(target.read_bytes(), TYPES.get(target.suffix.lower(), "application/octet-stream"))

    def _route(self, method: str, path: str) -> None:
        allowed = False
        for verb, pattern, name in ROUTES:
            match = re.fullmatch(pattern, path)
            if match:
                if verb == method:
                    getattr(self, f"route_{name}")(*match.groups())
                    return
                allowed = True
        if allowed:
            raise Refused(HTTPStatus.METHOD_NOT_ALLOWED, f"{method} is not allowed on {path}.")
        raise Refused(HTTPStatus.NOT_FOUND, f"No such route: {path}.")

    # The settings, changed under the server's lock and only from the version the page read

    def _change_settings(self, change: Callable[[dict], None], expected: str | None = None) -> config.Settings:
        path = config.paths().settings
        with self.server.settings_lock:
            if expected is not None and expected != version(path):
                raise Refused(HTTPStatus.CONFLICT, "The settings were changed since they were read; read them again.",
                              version=version(path))
            settings = config.load_settings(path)
            raw = json.loads(json.dumps(settings.raw))
            change(raw)
            # A loop names a bundle the settings list: load_machine's check, on settings not yet
            # written.
            checked = config.parse_settings(raw, path)
            names = {config.load_bundle(folder).name for folder in checked.bundles}
            for index, found in enumerate(checked.loops):
                if found.tracker is None and found.project not in names:
                    raise config.ConfigError(path.name, f"loops[{index}].project", f'no bundle named "{found.project}" in bundles')
            return config.save_settings(raw, path)

    # Loops

    def _loop_dict(self, machine: config.Machine, found: config.Loop) -> dict:
        # A loop on a tracker shows the definition it last read: the page never waits on the
        # server, and the loop reads it again when it starts (item doc-read).
        folder = loop_mod.loop_dir(found.project, found.name)
        loaded = definition.project_for(machine, found, folder, read=False)
        bundle = loaded.project
        status = self.server.runner.status(found.name)
        # A change to the definition, to show: waiting to be accepted, or used at once and not yet
        # dismissed (item doc-change). What it places is asked for before it is accepted.
        changed = definition.change(folder, found.tracker) if found.tracker else None
        waiting = definition.pending(folder, found.tracker) if found.tracker and changed and changed["pending"] else None
        placing = definition.project(found.tracker, waiting["definition"]) if waiting else bundle
        return {
            "name": found.name,
            "project": found.project,
            "worker": found.worker,
            "like": found.like,
            "repos": found.repos,
            "places": found.places,
            "settings": found.settings,
            "effective": config.loop_settings(machine, found, bundle),
            "tracker": {"server": found.tracker} if found.tracker else dataclasses.asdict(bundle.tracker),
            # The host of the server the loop reaches its tracker on, when it is its own and not
            # its worker's (item loop-from-url); null for the worker's.
            "server": urlsplit(str(found.overseer["server"])).netloc or str(found.overseer["server"]) if found.overseer else None,
            # "tracker" for a project read from its tracker, "bundle" for one on a local bundle.
            "source": "tracker" if found.tracker else "bundle",
            "definitionRead": loaded.read_at,
            "about": bundle.about if bundle else {},
            # What a loop on a tracker places, as its definition last read names them; null
            # before it has read one.
            "definition": {"repos": placing.repos, "places": placing.places} if found.tracker and placing else None,
            "definitionChange": changed,
            "acceptDefinition": config.accept_mode(machine, found),
            "status": status.as_dict(),
            "missing": self._missing(machine, found, placing),
        }

    def _missing(self, machine: config.Machine, found: config.Loop, bundle: config.Project | None) -> list[dict]:
        """What keeps the loop from starting, each with its field, judged as the loop judges it
        (config.place_repos), for the page to ask for before it starts it. A loop on a tracker
        that has not read its definition yet is judged on its worker alone."""
        problems: list[config.ConfigError] = []
        if bundle is not None:
            try:
                config.check_system(bundle)
            except config.ConfigError as error:
                problems.append(error)
        worker = machine.settings.worker(found.worker)
        overseer = config.overseer_for(machine.settings, found)
        if worker is None:
            problems.append(config.ConfigError(f'loop "{found.name}"', "worker", f"no worker named {found.worker}"))
        elif found.overseer and not config.expand(found.overseer["keyFile"]).is_file():
            problems.append(config.ConfigError(f'loop "{found.name}"', "overseer.keyFile",
                                               f"{found.project}'s tracker is on a server, and the loop's key file is not there"))
        elif not (overseer and config.expand(overseer["keyFile"]).is_file()):
            # As loop.Run refuses it; the key file is looked for, never read.
            problems.append(config.ConfigError(f'loop "{found.name}"', "worker",
                                               f"{found.project}'s tracker is on a server, and {worker.name} names no file holding the agents' key"))
        if bundle is None:
            return [{"field": p.field, "message": p.message} for p in problems]
        try:
            problems += config.missing(found, bundle, runner.remembered_top)
        except config.ConfigError as error:
            problems.append(error)
        return [{"field": p.field, "message": p.message} for p in problems]

    def _machine_loop(self, name: str) -> tuple[config.Machine, config.Loop]:
        machine = config.load_machine()
        found = machine.settings.loop(name)
        if found is None:
            raise Refused(HTTPStatus.NOT_FOUND, f"No loop named {name}.")
        return machine, found

    def route_loops(self) -> None:
        machine = config.load_machine()
        self._json({"version": version(machine.settings.path),
                    "loops": [self._loop_dict(machine, found) for found in machine.settings.loops]})

    def route_get_loop(self, name: str) -> None:
        machine, found = self._machine_loop(name)
        self._json(self._loop_dict(machine, found))

    def route_add_loop(self) -> None:
        body = self._body()
        new = body.get("loop")
        if not isinstance(new, dict):
            raise Refused(HTTPStatus.BAD_REQUEST, 'Send {"loop": {...}}.')
        self._change_settings(lambda raw: raw.setdefault("loops", []).append(new), body.get("version"))
        self.route_get_loop(new["name"])

    def _tracker_form(self) -> dict:
        body = self._body()
        for key in ("address", "worker"):
            if not isinstance(body.get(key), str):
                raise config.ConfigError(definition.NEW, key, "missing")
        if body.get("keyFile") is not None and not isinstance(body["keyFile"], str):
            raise config.ConfigError(definition.NEW, "keyFile", "the key file's path")
        return body

    def route_read_definition(self) -> None:
        """The definition a tracker's address leads to, for the form adding a loop from it to show
        and ask for its paths (item loop-from-url). Nothing is written."""
        body = self._tracker_form()
        self._json(definition.preview(config.load_settings(), body["address"], body.get("keyFile"), body["worker"]))

    def route_add_from_tracker(self) -> None:
        """A loop on a tracker added from its address, checked whole before it is saved (item
        loop-from-url)."""
        body = self._tracker_form()
        for key in ("repos", "places", "settings"):
            if body.get(key) is not None and not isinstance(body[key], dict):
                raise config.ConfigError(definition.NEW, key, "an object")
        expected = body.get("version")

        def save(new: dict) -> None:
            self._change_settings(lambda raw: raw.setdefault("loops", []).append(new), expected)

        new = definition.add(config.load_settings(), name=body.get("name") or "", address=body["address"],
                             key_file=body.get("keyFile"), worker=body["worker"], repos=body.get("repos") or {},
                             places=body.get("places") or {}, run_settings=body.get("settings") or None, save=save)
        self.route_get_loop(new["name"])

    def route_put_loop(self, name: str) -> None:
        body = self._body()
        new = body.get("loop")
        if not isinstance(new, dict):
            raise Refused(HTTPStatus.BAD_REQUEST, 'Send {"loop": {...}}.')

        def change(raw: dict) -> None:
            loops = raw.get("loops") or []
            index = next((i for i, l in enumerate(loops) if isinstance(l, dict) and l.get("name") == name), None)
            if index is None:
                raise Refused(HTTPStatus.NOT_FOUND, f"No loop named {name}.")
            if new.get("name", name) != name:
                self._refuse_copied(name, "renamed")
                with self.server.loop_lock(name):
                    if self.server.runner.status(name).alive:
                        raise Refused(HTTPStatus.CONFLICT, f"{name} is running; it is renamed once it has stopped.")
            loops[index] = new

        self._change_settings(change, body.get("version"))
        self.route_get_loop(new.get("name", name))

    def route_delete_loop(self, name: str) -> None:
        body = self._body()

        def change(raw: dict) -> None:
            loops = raw.get("loops") or []
            if not any(isinstance(l, dict) and l.get("name") == name for l in loops):
                raise Refused(HTTPStatus.NOT_FOUND, f"No loop named {name}.")
            self._refuse_copied(name, "deleted")
            with self.server.loop_lock(name):
                if self.server.runner.status(name).alive:
                    raise Refused(HTTPStatus.CONFLICT, f"{name} is running; it is deleted once it has stopped.")
            raw["loops"] = [l for l in loops if not (isinstance(l, dict) and l.get("name") == name)]

        self._change_settings(change, body.get("version"))
        self._json({"deleted": name})

    def _refuse_copied(self, name: str, done: str) -> None:
        # The copies would name a loop that is not there: said here, with their names, rather than
        # as the settings' own refusal of each copy's `like`.
        copies = config.load_settings().copies(name)
        if copies:
            raise Refused(HTTPStatus.CONFLICT, f"{name} cannot be {done}: {', '.join(copies)} "
                          f"{'is a copy' if len(copies) == 1 else 'are copies'} of it. Delete them first, or make them loops of their own.")

    def route_start(self, name: str) -> None:
        self._body()
        with self.server.loop_lock(name):
            self._json(self.server.runner.start(name).as_dict())

    def route_stop(self, name: str) -> None:
        self._body()
        with self.server.loop_lock(name):
            self._json(self.server.runner.stop(name).as_dict())

    def route_kill(self, name: str) -> None:
        self._body()
        with self.server.loop_lock(name):
            killed = self.server.runner.kill(name)
        self._json(dataclasses.asdict(killed))

    def route_auto_start(self, name: str) -> None:
        """The page's toggle: one setting of one loop, read by the loop itself if it is running
        (`Run._auto_start`), so with no version to match and no stop asked of it."""
        on = self._body().get("on")
        if not isinstance(on, bool):
            raise Refused(HTTPStatus.BAD_REQUEST, 'Send {"on": true} or {"on": false}.')

        def change(raw: dict) -> None:
            found = next((l for l in raw.get("loops") or [] if isinstance(l, dict) and l.get("name") == name), None)
            if found is None:
                raise Refused(HTTPStatus.NOT_FOUND, f"No loop named {name}.")
            found["settings"] = {**(found.get("settings") or {}), "autoStart": on}

        self._change_settings(change)
        self.route_get_loop(name)

    def route_accept_definition(self, name: str) -> None:
        """The page's Accept, or Dismiss: as `agentloop accept` does. A loop that is running sees
        the files and goes on, so nothing is asked of it here."""
        self._body()
        _, found = self._machine_loop(name)
        if found.tracker is None:
            raise Refused(HTTPStatus.BAD_REQUEST, f"{name} runs on a local bundle, not on a definition.")
        definition.accept(loop_mod.loop_dir(found.project, found.name), found.tracker)
        self.route_get_loop(name)

    def route_output(self) -> None:
        """What the loops print and how they stand, for all the page's panes in one short request:
        a stream for each would hold a connection for as long as its pane is open, and a browser
        has six for a host, all tabs together, so that six panes left it none to send anything
        else on, a reload included (item slow-ui). `{"loops": {name: id or null}}` is answered
        with, for each loop still there, the whole lines since `id`, the id to go on from, its
        status, and `reset` when a new run's output begins."""
        asked = self._body().get("loops")
        if not isinstance(asked, dict):
            raise Refused(HTTPStatus.BAD_REQUEST, 'Send {"loops": {name: id or null}}.')
        loops = {}
        for name, last in asked.items():
            try:
                loops[name] = self._output(name, last)
            except runner.RunnerError:
                # Deleted while its pane was open: the other panes still want theirs.
                continue
        self._json({"loops": loops})

    def _output(self, name: str, last: Any) -> dict:
        path = runner.output_path(name)
        # An id is `<file>:<offset>`: a browser coming back after a new run began is given the new
        # run's output from the top, however long it is by then.
        file_id: int | None = None
        offset = 0
        said = re.fullmatch(r"(\d+):(\d+)", last) if isinstance(last, str) else None
        if said:
            file_id, offset = int(said.group(1)), int(said.group(2))
        reset = False
        identity, text, offset_after = read_output(path, offset)
        if (file_id is not None and identity is not None and identity != file_id) or offset_after < offset:
            # Another file: a new run moved the last one's output aside.
            reset = True
            identity, text, offset_after = read_output(path, 0)
        if identity is not None:
            file_id = identity
        return {"text": text, "reset": reset, "id": None if file_id is None else f"{file_id}:{offset_after}",
                "status": self.server.runner.status(name).as_dict()}

    # Settings and bundles

    def route_get_settings(self) -> None:
        path = config.paths().settings
        with self.server.settings_lock:
            settings = config.load_settings(path)
            # The app's own run settings too, beneath every layer: the page says where a value is from.
            self._json({"version": version(path), "settings": settings.raw, "appDefaults": config.DEFAULTS})

    def route_put_settings(self) -> None:
        body = self._body()
        new = body.get("settings")
        if not isinstance(new, dict) or not isinstance(body.get("version"), str):
            raise Refused(HTTPStatus.BAD_REQUEST, 'Send {"version": ..., "settings": {...}}.')

        def change(raw: dict) -> None:
            raw.clear()
            raw.update(new)

        settings = self._change_settings(change, body["version"])
        self._json({"version": version(settings.path), "settings": settings.raw})

    def _bundle_folder(self, name: str) -> Path:
        for folder in config.load_settings().bundles:
            path = config.expand(folder)
            if path.name == name:
                return path
        raise Refused(HTTPStatus.NOT_FOUND, f"No bundle named {name}.")

    def route_bundles(self) -> None:
        machine = config.load_machine()
        self._json({"bundles": [{"name": b.name, "folder": str(b.folder), "tracker": dataclasses.asdict(b.tracker),
                                 "repos": b.repo_names(), "places": b.places, "systems": b.systems}
                                for b in machine.bundles.values()]})

    def route_get_bundle(self, name: str) -> None:
        folder = self._bundle_folder(name)
        bundle = config.load_bundle(folder)
        self._json({"version": version(folder / "project.json"), "bundle": bundle.raw})

    def route_put_bundle(self, name: str) -> None:
        body = self._body()
        new = body.get("bundle")
        if not isinstance(new, dict) or not isinstance(body.get("version"), str):
            raise Refused(HTTPStatus.BAD_REQUEST, 'Send {"version": ..., "bundle": {...}}.')
        folder = self._bundle_folder(name)
        with self.server.settings_lock:
            if body["version"] != version(folder / "project.json"):
                raise Refused(HTTPStatus.CONFLICT, "The bundle was changed since it was read; read it again.",
                              version=version(folder / "project.json"))
            bundle = config.save_bundle(folder, new)
        self._json({"version": version(folder / "project.json"), "bundle": bundle.raw})

    # Accounts

    def route_get_accounts(self) -> None:
        settings = config.load_settings()
        try:
            claude = session.find_claude(settings.claude)
        except session.SessionError:
            claude = None

        def login(worker: config.Worker) -> dict:
            if claude is None:
                return dataclasses.asdict(accounts.Login("unknown", "claude", detail="claude was not found"))
            return dataclasses.asdict(accounts.status(claude, worker.claude))

        # claude answers in a second or two for each account, so all are asked at once.
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(settings.workers))) as pool:
            logins = list(pool.map(login, settings.workers))
        default = settings.default_worker
        workers = []
        for worker, said in zip(settings.workers, logins):
            workers.append({
                "name": worker.name,
                "claude": worker.claude,
                "overseer": worker.overseer,
                "manager": worker.manager,
                "default": default is not None and worker.name == default.name,
                "login": {**said, "line": accounts.Login(**said).line()},
                "loginCommand": accounts.login_command(worker.claude),
                "usage": worker_usage(worker),
            })
        found = [{"name": f.name, "claude": f.claude} for f in accounts.found(settings.workers)]
        self._json({"version": version(settings.path), "workers": workers, "found": found})

    def route_get_usage(self) -> None:
        """Each worker's last usage alone, read from its file and never from claude, so that the page
        can ask for it as often as it polls."""
        settings = config.load_settings()
        self._json({"workers": {worker.name: worker_usage(worker) for worker in settings.workers}})

    def route_add_account(self) -> None:
        body = self._body()
        claude = body.get("claude")
        if claude is not None and not isinstance(claude, str):
            raise Refused(HTTPStatus.BAD_REQUEST, 'Send {"claude": folder or null, "name": ...}.')
        name = body.get("name") or accounts.suggest_name(claude)
        if not isinstance(name, str):
            raise Refused(HTTPStatus.BAD_REQUEST, "A worker's name is text.")
        with self.server.settings_lock:
            accounts.add_worker(name, claude)
        self._json({"added": name})

    def route_default_account(self) -> None:
        body = self._body()
        name = body.get("worker")
        if not isinstance(name, str):
            raise Refused(HTTPStatus.BAD_REQUEST, 'Send {"worker": name}.')
        with self.server.settings_lock:
            accounts.set_default(name)
        self._json({"default": name})


def read_output(path: Path, offset: int) -> tuple[int | None, str, int]:
    """The output file's identity, what it holds from `offset` on up to its last whole line, and
    the offset after that, as `Runner.output` reads it; but from the one file the identity is of,
    so that a new run, which moves the last run's file aside, is told from more of the same."""
    try:
        with open(path, "rb") as f:
            identity = os.fstat(f.fileno()).st_ino or None
            size = f.seek(0, os.SEEK_END)
            if offset > size:
                return identity, "", 0
            f.seek(offset)
            data = f.read()
    except FileNotFoundError:
        return None, "", offset
    whole = data.rfind(b"\n") + 1
    return identity, data[:whole].decode("utf-8", errors="replace"), offset + whole


def serve(port: int | None = None, open_browser: bool = False) -> int:
    """Serves the page until Ctrl-C; the loops it started go on."""
    settings = config.load_settings()
    try:
        server = Server(address(settings, port), log=lambda line: print(line, file=sys.stderr, flush=True))
    except OSError as error:
        print(f"agentloop serve: the port could not be opened ({type(error).__name__}); is another server on it?", file=sys.stderr)
        return 1
    print(f"Serving the page at {server.url}", flush=True)
    if open_browser:
        import webbrowser

        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
