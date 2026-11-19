"""The `agentloop` command. A subcommand in _STUBS is a stub until its item is built."""

import argparse
import json
import sys
import textwrap
from pathlib import Path

# The subcommand and the item that builds it.
_STUBS: dict[str, str] = {}


class _Version(argparse.Action):
    """argparse's own version action wants the text when the parser is made; this reads the build
    record only when the flag is given."""

    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        from agentloop import build

        print(build.describe())
        parser.exit()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentloop", description="The agent loop that works the project trackers.")
    # The same line as the version subcommand; the flag is what an install guide, or a release's
    # check that the installed command runs, reaches for first (item package).
    parser.add_argument("--version", action=_Version, help="print the version and exit")
    sub = parser.add_subparsers(dest="command", metavar="command")
    serve = sub.add_parser("serve", help="serve the local web page, on 127.0.0.1")
    serve.add_argument("--port", type=int, help="the port; the settings' server.port when left out, else 8765; 0 for any free one")
    serve.add_argument("--open", action="store_true", help="open the page in the browser")
    run = sub.add_parser("run", help="run one loop in the terminal, as run-agent-loop.ps1 does")
    run.add_argument("loop", help="the loop's name, in the settings; import-psd1 names it after its project")
    run.add_argument("--max-iterations", type=int, help="how many items to work, at most (maxIterations)")
    run.add_argument("--model", help="every session on this model, at its default effort (model)")
    run.add_argument("--worker", help="this run on another worker of the settings: another Claude account")
    run.add_argument("--colour", choices=["auto", "always", "never"], default="auto",
                     help="the script's colours, as ANSI codes: auto gives them to a terminal unless NO_COLOR is set")
    run.add_argument("--dry", action="store_true",
                     help="stop where the first session would begin: the item picked, its system prompt and prompt written, no session run")
    run.add_argument("--item", help="with --dry, this item and not the queue's next")
    # Given by the runner: the output is a log file, written in UTF-8 a line at a time.
    run.add_argument("--managed", action="store_true", help=argparse.SUPPRESS)
    start = sub.add_parser("start", help="start a loop as a process of its own, which goes on when this ends")
    start.add_argument("loop")
    stop = sub.add_parser("stop", help="stop a loop once the item at work is done, as stop-agent-loop.ps1 does")
    stop.add_argument("loop")
    kill = sub.add_parser("kill", help="stop a loop now, its session ended, as kill-agent-loop.ps1 does")
    kill.add_argument("loop")
    status = sub.add_parser("status", help="what each loop is doing, or one")
    status.add_argument("loop", nargs="?")
    status.add_argument("--json", action="store_true", help="as JSON")
    show_def = sub.add_parser("show-definition", help="the definition a loop on a tracker runs on, and what changed in it")
    show_def.add_argument("loop")
    accept = sub.add_parser("accept", help="accept a loop's changed definition, or dismiss one already used")
    accept.add_argument("loop")
    build = sub.add_parser("build", help="make a runnable copy of the app at another path")
    build.add_argument("--to", help="where to build; the settings' install field when left out")
    sub.add_parser("version", help="print the version")
    acc = sub.add_parser("accounts", help="this machine's Claude accounts: list them, add one as a worker, log one in")
    acc_sub = acc.add_subparsers(dest="action", metavar="action")
    acc_sub.add_parser("list", help="the workers, whether each is logged in, and the folders found not yet added (the default)")
    add = acc_sub.add_parser("add", help="add a worker on a Claude config folder")
    add.add_argument("folder", help="the config folder, or default for the default account (~/.claude)")
    add.add_argument("--name", help="the worker's name; the one list suggests when left out")
    login = acc_sub.add_parser("login", help="print the command that logs a worker's account in, for this OS's shell")
    login.add_argument("worker")
    default = acc_sub.add_parser("default", help="the worker a new loop starts with")
    default.add_argument("worker")
    review = sub.add_parser("review", help="what a loop run did and cost and where it stumbled, as review-run.py prints it")
    review.add_argument("project", help="the project, by its bundle's name")
    review.add_argument("run", nargs="?", help="the run, by when it started (20261008-213004); the latest when left out")
    review.add_argument("--all", action="store_true", help="every run in the logs")
    review.add_argument("--loop", help="the loop whose repositories and worker find the tracker; the project's first when left out")
    new = sub.add_parser("add", help="add a loop on a tracker from its address, asking where its repositories are")
    new.add_argument("address", help="Overseer's address of the tracker (https://host/doc/alice/projects/x.os), or its name there")
    new.add_argument("--key-file", help="the file holding the agents' key for that server; the worker's when left out")
    new.add_argument("--key-stdin", action="store_true",
                     help="read the agents' key from standard input - asked for unseen on a terminal - and keep it in a "
                          "file only you can read, in the config folder's keys/; never on the command line")
    new.add_argument("--worker", help="the Claude account it runs on; the default worker when left out")
    new.add_argument("--name", help="the loop's name; the project's when left out")
    new.add_argument("--repo", action="append", metavar="NAME=PATH", help="where a repository the project names is; asked for when left out")
    new.add_argument("--place", action="append", metavar="NAME=PATH", help="where a place the project names is; asked for when left out")
    ret = sub.add_parser("retarget", help="move a loop from its bundle to its tracker, keeping this machine's paths")
    ret.add_argument("loop", help="the loop's name, in the settings")
    ret.add_argument("address", nargs="?",
                     help="Overseer's address of the tracker, or its name there; the bundle's tracker.server when left out")
    exp = sub.add_parser("export-definition", help="write a bundle out as its definition document and tools, to put beside its tracker")
    exp.add_argument("bundle", type=Path, help="the bundle's folder, with project.json, brief.md and tools/")
    exp.add_argument("--out", type=Path, required=True, help="an empty folder, laid out as the tracker's folder on the server")
    exp.add_argument("--tracker-description", type=Path, metavar="FILE",
                     help="a file holding the tracker's own description; read from the tracker through the worker's server when left out")
    exp.add_argument("--worker", help="the worker whose server and key read the tracker's description; the default worker when left out")
    imp = sub.add_parser("import-psd1", help="make a bundle, a worker and a loop from one of run-agent-loop.ps1's projects/<name>.psd1")
    imp.add_argument("file", type=Path, help="the .psd1, with its brief <name>.md and tools folder <name>/ beside it")
    imp.add_argument("--replace", action="store_true", help="replace a bundle or loop of the same name")
    imp.add_argument("--to", type=Path, help="the folder the bundle is made in, as <to>/<name>; the settings' bundles folder when left out")
    return parser


def _export(args: argparse.Namespace) -> int:
    """`agentloop export-definition` (item def-export): the files, then what is left to do."""
    from agentloop import config, export

    server = None
    try:
        # The settings only say which server the curl lines name, unless the description is read
        # from it: a machine with none can still export with --tracker-description.
        try:
            settings = config.load_settings()
        except config.ConfigError:
            if args.tracker_description is None:
                raise
            settings = None
        worker = None
        if settings is not None:
            worker = settings.worker(args.worker) if args.worker else settings.default_worker
            if args.worker and worker is None:
                raise config.ConfigError(settings.path.name, "workers", f'no worker named "{args.worker}"')
        theirs = (worker.overseer if worker else None) or {}
        server = theirs.get("server")
        if args.tracker_description is not None:
            description = args.tracker_description.read_text(encoding="utf-8")
        else:
            if not server or not theirs.get("keyFile"):
                raise config.ConfigError("export-definition", "tracker-description",
                                         "no worker here reaches a server: give the tracker's description with --tracker-description")
            tracker = config.load_bundle(args.bundle).tracker.server
            description = export.tracker_description(server, config.expand(theirs["keyFile"]), tracker)
        report = export.export(args.bundle, args.out, description)
    except config.ConfigError as error:
        print(f"agentloop export-definition: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"agentloop export-definition: {type(error).__name__}: {error.strerror or error}", file=sys.stderr)
        return 2
    print(f"definition  {report.definition}")
    if report.tools:
        print(f"tools       {report.tools_dir} ({len(report.tools)})")
    print()
    for line in report.steps(server):
        print(line)
    return 0


def _import_psd1(args: argparse.Namespace) -> int:
    from agentloop import config, psd1

    try:
        done = psd1.import_psd1(args.file, replace=args.replace, to=args.to)
    except config.ConfigError as error:
        print(f"agentloop import-psd1: {error}", file=sys.stderr)
        return 2
    print(f"bundle  {done.bundle}")
    print(f"worker  {done.worker}" + (" (new)" if done.worker_made else " (already there)"))
    print(f"loop    {done.loop}")
    return 0


def _accounts(args: argparse.Namespace) -> int:
    from agentloop import accounts, config, session

    try:
        settings = config.load_settings()
        if args.action == "add":
            claude = accounts.normalize(args.folder)
            name = args.name or accounts.suggest_name(claude)
            accounts.add_worker(name, claude)
            print(f"worker  {name}  {claude or '(default account)'}")
            return 0
        if args.action == "login":
            worker = settings.worker(args.worker)
            if worker is None:
                raise config.ConfigError(settings.path.name, "workers", f'no worker named "{args.worker}"')
            print(accounts.login_command(worker.claude))
            return 0
        if args.action == "default":
            accounts.set_default(args.worker)
            return 0
        claude = session.find_claude(settings.claude)
        default = settings.default_worker
        for worker in settings.workers:
            mark = "*" if default and worker.name == default.name else " "
            login = accounts.status(claude, worker.claude)
            print(f"{mark} {worker.name:<16} {worker.claude or '(default account)':<28} {login.line()}")
            if login.state != "in":
                print(f"  {'':<16} log in with: {accounts.login_command(worker.claude)}")
        for f in accounts.found(settings.workers):
            print(f"  {f.name:<16} {f.claude or '(default account)':<28} not added: agentloop accounts add {f.claude or 'default'}")
        return 0
    except (config.ConfigError, session.SessionError) as error:
        print(f"agentloop accounts: {error}", file=sys.stderr)
        return 2


def _review(args: argparse.Namespace) -> int:
    """Runs review-run.py on the app's logs of the project, and on its tracker for where each item
    is now: as a subprocess, as the loop runs the tools. Only ever when someone asks for it."""
    import subprocess

    from agentloop import config, loop, review, session, tools

    try:
        machine = config.load_machine()
        given = review.arguments(machine, args.project, args.loop)
    except (config.ConfigError, loop.LoopError) as error:
        print(f"agentloop review: {error}", file=sys.stderr)
        return 2
    if given.why_no_tracker:
        print(f"agentloop review: {given.why_no_tracker}; where each item is now is left out", file=sys.stderr)
    cmd = [sys.executable, str(tools.path("review-run.py")), args.project, *([args.run] if args.run else []),
           *(["--all"] if args.all else []), "--logs", str(given.logs), *(["--tracker", given.tracker] if given.tracker else [])]
    return subprocess.run(cmd, env=session.build_env(given.env)).returncode


def _run(args: argparse.Namespace) -> int:
    from agentloop import colours, config, loop, session

    if args.item and not args.dry:
        print("agentloop run: --item is for --dry; a loop takes its items from the queue", file=sys.stderr)
        return 2

    if args.managed:
        # A log file is written in the locale's encoding otherwise, which on Windows cannot hold
        # every character a session prints; and with \n line ends on every OS, as a session's log is.
        for stream in (sys.stdout, sys.stderr):
            stream.reconfigure(encoding="utf-8", errors="replace", newline="\n", line_buffering=True)
    # Started by the runner, its output goes to the log the page draws in colour.
    choice = "always" if args.managed else args.colour
    paint = colours.ansi if colours.wanted(choice, sys.stdout) else colours.plain
    try:
        machine = config.load_machine()
        found = loop.find_loop(machine, args.loop)
        # Started by the runner, it may meet a page asking whether its lock is free, for
        # the moment that takes: so it tries for a little while before it gives up.
        run = loop.Run(machine, found, out=lambda line: print(line, flush=True), paint=paint, worker=args.worker,
                       overrides={"maxIterations": args.max_iterations, "model": args.model},
                       lock_wait=2.0 if args.managed else 0.0)
        return run.dry(args.item) if args.dry else run.run()
    except (config.ConfigError, loop.LoopError, session.SessionError) as error:
        # Red, as the script writes what keeps it from starting.
        red = colours.ansi if colours.wanted(choice, sys.stderr) else colours.plain
        print(red(f"agentloop run: {error}", "Red"), file=sys.stderr)
        return 2


def _runner(args: argparse.Namespace) -> int:
    import json

    from agentloop import config, runner

    r = runner.Runner()
    try:
        if args.command == "status":
            found = [r.status(args.loop)] if args.loop else r.statuses()
            if args.json:
                print(json.dumps([s.as_dict() for s in found], indent=2))
            else:
                for s in found:
                    print(s.line())
            return 0
        if args.command == "start":
            s = r.start(args.loop)
            print(f"Started {args.loop} on {s.project}; its output goes to {runner.output_path(args.loop)}")
            return 0
        if args.command == "stop":
            r.stop(args.loop)
            print(f"The loop {args.loop} will stop once the item at work is done.")
            return 0
        killed = r.kill(args.loop)
        how = ", its session ended" if killed.ended_session else " - it was between sessions, or waiting"
        print(f"The loop {args.loop} is stopped{how}.")
        if killed.session:
            print(f"The session at work was {killed.item}'s; its changes are uncommitted. To carry it on by hand:")
            print(f"  {killed.resume}")
        return 0
    except (config.ConfigError, runner.RunnerError) as error:
        print(f"agentloop {args.command}: {error}", file=sys.stderr)
        return 1


def _pairs(given: list[str] | None, flag: str) -> dict[str, str]:
    """`--repo name=path` given again and again, as {name: path}."""
    from agentloop import config

    found: dict[str, str] = {}
    for pair in given or []:
        name, sep, path = pair.partition("=")
        if not sep or not name.strip() or not path.strip():
            raise config.ConfigError("the new loop", flag.lstrip("-"), f"{flag} {pair}: give it as name=path")
        found[name.strip()] = path.strip()
    return found


def _interactive() -> bool:
    return sys.stdin.isatty()


def _read_key() -> str:
    """The agents' key from standard input: asked for, unseen, on a terminal; else the first line
    of what is piped in. Never an argument, which other people on the machine can see."""
    if _interactive():
        import getpass

        return getpass.getpass("the agents' key (not shown): ")
    return sys.stdin.readline()


def _add(args: argparse.Namespace) -> int:
    """`add` (item loop-from-url): a loop on a tracker added from its address, as the page's form
    adds one - the definition read and shown, then a path asked for each repository and place it
    names, on a terminal; given as flags, where there is none."""
    from agentloop import config, definition, keys

    def refused(error: config.ConfigError) -> int:
        problems = getattr(error, "problems", None) or [error]
        for problem in problems:
            print(f"agentloop add: {problem.field}: {problem.message}" if problem.field else f"agentloop add: {problem}", file=sys.stderr)
        return 2

    try:
        settings = config.load_settings()
        worker = args.worker or (settings.default_worker.name if settings.default_worker else "")
        repos, places = _pairs(args.repo, "--repo"), _pairs(args.place, "--place")
        if args.key_stdin:
            if args.key_file:
                raise config.ConfigError(keys.WHERE, "key", "--key-stdin or --key-file, not both")
            server = keys.server_for(settings, args.address, worker)
            account, path = keys.save(server, _read_key())
            args.key_file = config.home_relative(path)
            print(f"key     signs in as {account}; kept in {args.key_file}")
        shown = definition.preview(settings, args.address, args.key_file, worker)
    except config.ConfigError as error:
        return refused(error)
    except definition.Unreachable as error:
        print(f"agentloop add: the tracker's server: {error}", file=sys.stderr)
        return 2
    except keys.PrivacyError as error:
        print(f"agentloop add: the key file could not be made private: {error}", file=sys.stderr)
        return 2

    print(f"{shown['tracker']} on {shown['server']}" + ("" if shown["ownServer"] else f", as worker {worker} reaches it"))
    if shown["summary"]["lead"]:
        print("")
        print(textwrap.fill(shown["summary"]["lead"], 100))
    if shown["summary"]["headings"]:
        print("")
        print("Its description: " + "; ".join(shown["summary"]["headings"]))
    print("")
    print(f"systems: {', '.join(shown['systems']) if shown['systems'] else 'every system'}")
    if shown["systemProblem"]:
        print(f"agentloop add: systems: {shown['systemProblem']}", file=sys.stderr)
        return 2
    asked = [("repos", e, repos, "Sessions run here. " if i == 0 else "") for i, e in enumerate(shown["repos"])]
    asked += [("places", e, places, "") for e in shown["places"]]
    for kind, entry, given, more in asked:
        line = f"  {'repository' if kind == 'repos' else 'place':<10} {entry['name']}"
        about = more + (entry.get("about") or "")
        print(line + (f" - {about}" if about else "") + (f": {given[entry['name']]}" if entry["name"] in given else ""))
    print("")

    def ask(kind: str, entry: dict, given: dict[str, str]) -> None:
        what = "a folder inside a git repository" if kind == "repos" else "a folder; never committed"
        answer = input(f"{entry['name']} ({what}): ").strip()
        if answer:
            given[entry["name"]] = answer

    left = [(kind, entry, given) for kind, entry, given, _ in asked if entry["name"] not in given]
    if left and not _interactive():
        flags = " ".join(f"--{'repo' if kind == 'repos' else 'place'} {entry['name']}=<path>" for kind, entry, _ in left)
        print(f"agentloop add: say where each is on this machine: {flags}", file=sys.stderr)
        return 2
    for kind, entry, given in left:
        ask(kind, entry, given)
    name = args.name or shown["project"]
    while True:
        try:
            new = definition.add(settings, name=name, address=args.address, key_file=args.key_file, worker=worker,
                                 repos=repos, places=places)
            break
        except definition.Unreachable as error:
            print(f"agentloop add: the tracker's server: {error}", file=sys.stderr)
            return 2
        except config.ConfigError as error:
            refused(error)
            problems = getattr(error, "problems", None) or [error]
            # A path or the name, asked again on a terminal; anything else is not the person's
            # answer to put right here.
            again = [p.field for p in problems if p.field == "name" or p.field.split(".", 1)[0] in ("repos", "places")]
            if not _interactive() or len(again) != len(problems):
                return 2
            for field in again:
                if field == "name":
                    name = input("name (lower case letters, digits, - and _): ").strip()
                    continue
                kind, _, which = field.partition(".")
                entry = next((e for k, e, _g, _m in asked if k == kind and e["name"] == which), None)
                if entry is None:
                    return 2
                (repos if kind == "repos" else places).pop(which, None)
                ask(kind, entry, repos if kind == "repos" else places)
    print(f"loop    {new['name']}  on {new['tracker']}" + (f" ({shown['server']})" if "overseer" in new else ""))
    print(f"Start it with: agentloop start {new['name']}")
    return 0


def _retarget(args: argparse.Namespace) -> int:
    """`retarget` (item retarget): a loop on a bundle moved to its tracker, as definition.retarget
    does it. It asks nothing, so it runs the same without a terminal."""
    from agentloop import config, definition

    try:
        done = definition.retarget(config.load_machine(), args.loop, args.address)
    except definition.Unreachable as error:
        print(f"agentloop retarget: the tracker's server: {error}", file=sys.stderr)
        return 2
    except config.ConfigError as error:
        for problem in getattr(error, "problems", None) or [error]:
            print(f"agentloop retarget: {problem.field}: {problem.message}" if problem.field else f"agentloop retarget: {problem}",
                  file=sys.stderr)
        return 2
    print(f"loop    {', '.join(done['moved'])}  on {done['tracker']}")
    print("bundle  dropped from bundles" if done["bundleDropped"] else "bundle  kept in bundles: another loop uses it")
    if done["repoint"]:
        print(f"Repoint these, which are in the bundle's folder: {', '.join(done['repoint'])}")
    return 0


def _definition(args: argparse.Namespace) -> int:
    """`show-definition` and `accept` (item doc-change): what a loop on a tracker keeps of its
    definition, read from its folder, never from the server."""
    from agentloop import config, definition, loop

    try:
        machine = config.load_machine()
        found = loop.find_loop(machine, args.loop)
    except (config.ConfigError, loop.LoopError) as error:
        print(f"agentloop {args.command}: {error}", file=sys.stderr)
        return 2
    if found.tracker is None:
        print(f"agentloop {args.command}: {found.name} runs on the local bundle {found.project}, not on a definition", file=sys.stderr)
        return 2
    folder = loop.loop_dir(found.project, found.name)
    if args.command == "accept":
        done = definition.accept(folder, found.tracker)
        print({"accepted": f"The change to {found.project}'s definition is accepted; {found.name} goes on with it.",
               "dismissed": f"The change to {found.project}'s definition, already in use, is no longer shown.",
               None: f"Nothing to accept: {found.name}'s definition has not changed since it was accepted."}[done])
        return 0
    held = definition.kept(folder, found.tracker)
    if held is None:
        print(f"{found.name} has not read {found.tracker}'s definition yet: it does when it starts.")
        return 0
    raw = held["definition"]
    print(f"{found.tracker}, read {held.get('read_at')}; acceptDefinition {config.accept_mode(machine, found)}")
    for part, what in (("repos", "repository"), ("places", "place")):
        for e in raw.get(part) or []:
            print(f"  {what} {e['name']}" + (f" - {e['about']}" if e.get("about") else ""))
    print(f"  systems: {', '.join(raw['systems']) if raw.get('systems') else 'every system'}")
    print(f"  settings: {json.dumps(raw.get('settings') or {})}")
    print("")
    print(raw.get("description") or "")
    shown = definition.change(folder, found.tracker)
    if shown is None:
        print("")
        print("No change since this loop accepted it.")
        return 0
    print("")
    if shown["pending"]:
        print(f"A change read {shown['read_at']} waits to be accepted (agentloop accept {found.name}); no session runs until it is:")
    else:
        print(f"Changed, and in use since {shown['read_at']} (agentloop accept {found.name} stops showing it):")
    for line in definition.diff_lines(shown["diff"]) or ["  (nothing that is shown)"]:
        print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 2
    if args.command == "version":
        from agentloop import build

        print(build.describe())
        return 0
    if args.command == "build":
        from agentloop import build

        return build.run(args.to)
    if args.command == "import-psd1":
        return _import_psd1(args)
    if args.command == "export-definition":
        return _export(args)
    if args.command == "run":
        return _run(args)
    if args.command == "accounts":
        return _accounts(args)
    if args.command == "review":
        return _review(args)
    if args.command == "serve":
        from agentloop import config, server

        try:
            return server.serve(args.port, args.open)
        except config.ConfigError as error:
            print(f"agentloop serve: {error}", file=sys.stderr)
            return 2
    if args.command == "add":
        return _add(args)
    if args.command == "retarget":
        return _retarget(args)
    if args.command in ("show-definition", "accept"):
        return _definition(args)
    if args.command in ("start", "stop", "kill", "status"):
        return _runner(args)
    print(f"agentloop {args.command}: not built yet (item {_STUBS[args.command]})", file=sys.stderr)
    return 2
