"""The install scripts (item installer): each parses in its own shell, carries the version
placeholder the release workflow writes over, and stops with a sentence when it names no release
and no wheel is given.

Installing for real is the release workflow's job, on all three systems: it would download uv and
change the machine's tools, which a test must not.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "install" / "install.sh"
PS1 = ROOT / "install" / "install.ps1"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"


def _sh() -> str | None:
    # On Windows a bare `sh` may be WSL's launcher or nothing; Git's sh is the one wanted.
    found = shutil.which("sh")
    if found is None and os.name == "nt":
        git = shutil.which("git")
        if git is not None:
            candidate = Path(git).resolve().parent.parent / "bin" / "sh.exe"
            found = str(candidate) if candidate.is_file() else None
    return found


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.parametrize("script", [SH, PS1], ids=["sh", "ps1"])
def test_the_version_placeholder_is_where_the_workflow_writes_it(script: Path) -> None:
    text = script.read_text(encoding="utf-8")
    # One assignment of the placeholder, which sed replaces; the release's wheel is named from it.
    assert len(re.findall(r"@VERSION@", text)) == 1
    assert "releases/download/v$" in text
    assert "s/@VERSION@/" in RELEASE.read_text(encoding="utf-8")


def test_the_shell_script_has_lf_line_ends() -> None:
    assert b"\r\n" not in SH.read_bytes()


def test_the_shell_script_parses() -> None:
    sh = _sh()
    if sh is None:
        pytest.skip("no sh")
    done = subprocess.run([sh, "-n", str(SH)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


def test_the_shell_script_stops_without_a_release_or_a_wheel(tmp_path: Path) -> None:
    sh = _sh()
    if sh is None:
        pytest.skip("no sh")
    env = {k: v for k, v in os.environ.items() if k != "AGENTLOOP_WHEEL"}
    env["HOME"] = str(tmp_path)
    done = subprocess.run([sh, str(SH)], capture_output=True, text=True, timeout=60, env=env)
    assert done.returncode == 1
    assert "names no release" in done.stderr


def test_the_powershell_script_parses() -> None:
    shell = _powershell()
    if shell is None:
        pytest.skip("no PowerShell")
    command = (
        "$errors = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile($env:SCRIPT_TO_PARSE, [ref]$null, [ref]$errors); "
        "if ($errors) { $errors | ForEach-Object { [Console]::Error.WriteLine($_.Message) }; exit 1 }"
    )
    done = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True,
                          text=True, timeout=120, env={**os.environ, "SCRIPT_TO_PARSE": str(PS1)})
    assert done.returncode == 0, done.stderr


def test_the_powershell_script_stops_without_a_release_or_a_wheel() -> None:
    shell = _powershell()
    if shell is None:
        pytest.skip("no PowerShell")
    env = {k: v for k, v in os.environ.items() if k != "AGENTLOOP_WHEEL"}
    done = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(PS1)],
                          capture_output=True, text=True, timeout=120, env=env)
    assert done.returncode != 0
    assert "names no release" in done.stdout + done.stderr
