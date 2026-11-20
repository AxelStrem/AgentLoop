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
import xml.etree.ElementTree as ET
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


# The container image (item docker-image): built and tried by the release workflow alone, since
# a test must not need Docker. Here only what can be read or run without it.

ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"
DOCKERFILE = ROOT / "docker" / "Dockerfile"


def test_the_entrypoint_has_lf_line_ends_and_parses() -> None:
    assert b"\r\n" not in ENTRYPOINT.read_bytes()
    sh = _sh()
    if sh is None:
        pytest.skip("no sh")
    done = subprocess.run([sh, "-n", str(ENTRYPOINT)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


def test_the_entrypoint_runs_the_command_as_it_is_when_not_root() -> None:
    sh = _sh()
    if sh is None:
        pytest.skip("no sh")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("run as root: the entrypoint would make users")
    done = subprocess.run([sh, str(ENTRYPOINT), "echo", "the command ran"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "the command ran"


def test_the_image_runs_serve_behind_tini_and_restarts_the_loops() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["tini", "--", "/usr/local/bin/agentloop-entrypoint"]' in text
    assert 'CMD ["agentloop", "serve", "--restart-loops"]' in text
    assert "AGENTLOOP_HOME=/data" in text and "AGENTLOOP_HOST=0.0.0.0" in text
    assert "COPY docker/entrypoint.sh /usr/local/bin/agentloop-entrypoint" in text


def test_the_release_waits_for_the_image() -> None:
    text = RELEASE.read_text(encoding="utf-8")
    assert re.search(r"^  image:\n    needs: \[build, try\]", text, re.M)
    assert "needs: [build, try, image]" in text
    assert "if: github.ref_type == 'tag'\n        uses: docker/build-push-action" in text


# The Unraid template (item unraid-template).
TEMPLATE = ROOT / "docker" / "unraid" / "agentloop.xml"


def _template_configs() -> dict[str, "ET.Element"]:
    root = ET.parse(TEMPLATE).getroot()
    return {c.get("Target"): c for c in root.findall("Config")}


def test_the_unraid_template_names_the_image_the_port_and_the_volume() -> None:
    root = ET.parse(TEMPLATE).getroot()
    assert root.findtext("Repository") == "ghcr.io/axelstrem/agentloop:latest"
    assert "[PORT:8765]" in (root.findtext("WebUI") or "")
    configs = _template_configs()
    assert configs["8765"].get("Type") == "Port"
    assert configs["/data"].get("Type") == "Path"


def test_the_unraid_template_has_a_described_field_for_each_variable_the_entrypoint_documents() -> None:
    header = ENTRYPOINT.read_text(encoding="utf-8").split("set -eu")[0]
    documented = set(re.findall(r"^#   ([A-Z_]+)(?:, ([A-Z_]+))?\s{2,}", header, re.M))
    names = {n for pair in documented for n in pair if n}
    assert {"PUID", "PGID", "EXTRA_PACKAGES", "GIT_TOKEN", "GIT_USER_EMAIL"} <= names
    names |= {"AGENTLOOP_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"}
    configs = _template_configs()
    for name in names:
        config = configs.get(name)
        assert config is not None, name
        assert config.get("Type") == "Variable", name
        assert (config.get("Description") or "").strip(), name
    for secret in ("AGENTLOOP_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "GIT_TOKEN"):
        assert configs[secret].get("Mask") == "true", secret


def test_the_documents_links_to_other_documents_resolve() -> None:
    for page in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        text = page.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)#\s]+\.md|[^)\s]+\.(?:md|xml))(#[^)\s]*)?\)", text):
            link = target[0]
            if link.startswith("http"):
                continue
            assert (page.parent / link).is_file(), f"{page.name} links to {link}"
    # An anchor into the container page names a heading it has.
    container = (ROOT / "docs" / "container.md").read_text(encoding="utf-8")
    headings = {re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-") for h in re.findall(r"^#+ (.+)$", container, re.M)}
    for page in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        for anchor in re.findall(r"container\.md#([a-z0-9-]+)", page.read_text(encoding="utf-8")):
            assert anchor in headings, f"{page.name} links to container.md#{anchor}"
