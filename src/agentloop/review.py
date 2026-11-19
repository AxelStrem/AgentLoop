"""What `agentloop review` gives review-run.py: the app's logs of a project, and its tracker.

review-run.py was written for the script, which keeps its logs in `logs\\<project>` beside itself
and names each project's tracker in `projects\\<project>.psd1`. The app keeps the logs in its data
folder, `logs/<project>/` (item loop), and the tracker is the bundle's: a document on the server
a worker reaches. So both are handed to it.

Nothing runs a review but someone asking for one: no loop, session or route of the page starts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentloop import config, loop, session


@dataclass
class Arguments:
    logs: Path
    # The tracker, as tracker.py's --doc takes it; None when it cannot be found here.
    tracker: str | None = None
    # Why there is no tracker, said to whoever asked for the review.
    why_no_tracker: str | None = None
    # The variables tracker.py reads the tracker with: the server's, and its key file.
    env: dict[str, str | None] = field(default_factory=dict)


def arguments(machine: config.Machine, project: str, loop_name: str | None = None) -> Arguments:
    """The logs and tracker of a project, its tracker found through the loop named, or the
    project's first loop: a loop says where its repositories are and which worker reaches the
    server. A project without its logs is refused; one whose tracker cannot be reached is reviewed
    without it, as review-run.py reviews a project its settings say no tracker for."""
    # A project is a bundle's, or one a loop reads from its tracker (item doc-read).
    bundle = machine.bundle(project)
    known = set(machine.bundles) | {l.project for l in machine.settings.loops if l.tracker}
    if project not in known:
        raise loop.LoopError(f"No project named {project}. The projects are: {', '.join(sorted(known)) or 'none'}")
    found = Arguments(logs=config.paths().data / "logs" / project)
    if not found.logs.is_dir():
        raise loop.LoopError(f"No logs of {project} yet: no loop has run on it on this PC.")
    if loop_name:
        chosen = loop.find_loop(machine, loop_name)
        if chosen.project != project:
            raise loop.LoopError(f"The loop {loop_name} works {chosen.project}, not {project}.")
    else:
        chosen = next((l for l in machine.settings.loops if l.project == project), None)
        if chosen is None:
            found.why_no_tracker = f"no loop on {project} says where its tracker is"
            return found
    # As Run reaches it: the loop's server, else the worker's, and the file holding the agents' key.
    overseer = config.overseer_for(machine.settings, chosen)
    key = config.expand(overseer["keyFile"]) if overseer else None
    if key is None or not key.is_file():
        found.why_no_tracker = f"{project}'s tracker is on a server, and worker {chosen.worker} names no file holding the agents' key"
        return found
    found.tracker = chosen.tracker or (bundle.tracker.server if bundle else None)
    if found.tracker is None:
        found.why_no_tracker = f"no bundle named {project} says where its tracker is"
        return found
    found.env.update({"OVERSEER_URL": str(overseer["server"]).rstrip("/"), "OVERSEER_KEY_FILE": str(key.resolve())})
    return found
