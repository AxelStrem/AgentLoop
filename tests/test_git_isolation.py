import os
import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def test_tests_run_git_with_fsmonitor_off(tmp_path: Path) -> None:
    assert os.environ["GIT_CONFIG_KEY_0"] == "core.fsmonitor"
    assert _git(tmp_path, "init").returncode == 0
    assert _git(tmp_path, "status").returncode == 0
    value = _git(tmp_path, "config", "--get", "core.fsmonitor").stdout.strip()
    assert value == "false"


def test_no_fsmonitor_daemon_is_left_for_a_repository_the_tests_made(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "status")
    # `status` of the daemon exits 0 only when one runs for this worktree; it never starts one.
    # Git without the daemon (older, or a platform lacking it) exits non-zero too, which also passes.
    probe = _git(tmp_path, "-c", "core.fsmonitor=true", "fsmonitor--daemon", "status")
    assert probe.returncode != 0, probe.stdout
