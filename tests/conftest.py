"""What every test shares: git without fsmonitor, an environment without the machine's own AgentLoop
settings, this package for the Pythons they start, and failures as GitHub annotations."""
import os
from pathlib import Path

import pytest


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


# What the container image and a loop's session put in the environment, and the code under test
# reads: AGENTLOOP_OVERSEER_SERVER alone is kept, since the tests read it on purpose.
_LEAKED = ("AGENTLOOP_HOST", "AGENTLOOP_TOKEN", "AGENTLOOP_HOME", "AGENTLOOP_CODE", "AGENTLOOP_BREW",
           "AGENTLOOP_TRACKER", "AGENTLOOP_PYTHON")


@pytest.fixture(scope="session", autouse=True)
def _environment_of_its_own():
    """Every test starts without the settings of the machine it runs on. The suite runs inside the
    container, whose AGENTLOOP_HOST would have a test's page listen on every address, and inside a
    loop's session, whose OVERSEER_* and AGENT_* name a live tracker. A test that wants one of
    these sets it itself."""
    saved = {k: v for k, v in os.environ.items()
             if k in _LEAKED or k.startswith(("OVERSEER_", "AGENT_"))}
    for k in saved:
        del os.environ[k]
    yield
    os.environ.update(saved)


@pytest.fixture(scope="session", autouse=True)
def _children_import_this_package():
    """A Python a test starts (`python -m agentloop`) imports the package the tests import. Without
    an install, pytest's `pythonpath` puts `src` on its own path only, and not on a child's."""
    import agentloop

    saved = os.environ.get("PYTHONPATH")
    here = str(Path(agentloop.__file__).resolve().parent.parent)
    os.environ["PYTHONPATH"] = os.pathsep.join([here, saved] if saved else [here])
    yield
    if saved is None:
        os.environ.pop("PYTHONPATH", None)
    else:
        os.environ["PYTHONPATH"] = saved


def github_error(title: str, message: str) -> str:
    """A GitHub Actions `::error` workflow command, escaped as the runner wants: `%`, CR and LF in
    the message, and `,` and `:` too in the title, which is a property."""

    def escape(text: str) -> str:
        return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")

    prop = escape(title).replace(":", "%3A").replace(",", "%2C")
    return f"::error title={prop}::{escape(message)}"


def failure_message(report: pytest.TestReport, lines: int = 15) -> str:
    """What a failed test's annotation says: the line it failed on and why, then the last lines
    of its traceback, where the `E` lines are."""
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    head = f"{crash.path}:{crash.lineno}: {crash.message}" if crash is not None else ""
    tail = "\n".join(report.longreprtext.rstrip().splitlines()[-lines:])
    return f"{head}\n\n{tail}" if head else tail


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """On GitHub Actions, each failed or errored test becomes an annotation of the job. A job's
    log can only be downloaded with admin rights on the repository, but its annotations can be
    read by anyone through the public API (`check-runs/<job>/annotations`), so an agent can find
    out which test failed. GitHub shows at most ten errors a step; the log's own summary (-rfE)
    lists the rest. Under xdist this runs on the controller, which has every worker's reports."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    stats = terminalreporter.stats
    for report in stats.get("failed", []) + stats.get("error", []):
        if getattr(report, "nodeid", None):
            terminalreporter.write_line(github_error(report.nodeid, failure_message(report)))
