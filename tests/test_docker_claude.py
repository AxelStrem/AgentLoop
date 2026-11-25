"""The image's claude install at start (item docker-claude-runtime): docker/ensure-claude.sh leaves
a claude already in the volume alone, installs a missing one with the installer it downloads,
retries a failed try, and ends with 1 and a clear line when every try failed. The network is a
fake `curl` on PATH, which writes a fake installer or fails, and logs each call."""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                                reason="the image's scripts are POSIX shell, run on Linux")

SCRIPT = Path(__file__).resolve().parent.parent / "docker" / "ensure-claude.sh"

# The installer the fake curl hands out: it puts a claude in ~/.local/bin, as Anthropic's does.
INSTALLER = """#!/bin/bash
mkdir -p "$HOME/.local/bin"
printf '#!/bin/sh\\necho "9.9.9 (Claude Code)"\\n' > "$HOME/.local/bin/claude"
chmod 755 "$HOME/.local/bin/claude"
"""

# Fails as many times as FAKE_CURL_FAILS says, then writes the installer to the -o file.
CURL = """#!/bin/sh
echo "$*" >> "$FAKE_CURL_LOG"
n=$(wc -l < "$FAKE_CURL_LOG")
[ "$n" -gt "${FAKE_CURL_FAILS:-0}" ] || exit 7
while [ $# -gt 0 ]; do
    if [ "$1" = "-o" ]; then out="$2"; fi
    shift
done
cp "$FAKE_INSTALLER" "$out"
"""


def _executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def run(tmp_path: Path, fails: int = 0, waits: str = "0 0") -> subprocess.CompletedProcess:
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    _executable(tools / "curl", CURL)
    installer = tmp_path / "installer.sh"
    installer.write_text(INSTALLER)
    env = {
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "FAKE_CURL_LOG": str(tmp_path / "curl.log"),
        "FAKE_CURL_FAILS": str(fails),
        "FAKE_INSTALLER": str(installer),
        "CLAUDE_INSTALL_WAITS": waits,
    }
    return subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


def curl_calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "curl.log"
    return log.read_text().splitlines() if log.exists() else []


def test_a_missing_claude_is_installed_in_the_home(tmp_path):
    done = run(tmp_path)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "home" / ".local" / "bin" / "claude").is_file()
    assert "claude 9.9.9 (Claude Code) installed" in done.stdout
    [call] = curl_calls(tmp_path)
    assert call.startswith("-fsSL --retry 2 -o ") and call.endswith(" https://claude.ai/install.sh")
    # The folder the installer was downloaded to is gone again.
    assert [p.name for p in (tmp_path / "home").iterdir()] == [".local"]


def test_a_claude_already_in_the_home_is_left_as_it_is(tmp_path):
    assert run(tmp_path).returncode == 0
    (tmp_path / "curl.log").unlink()
    done = run(tmp_path)
    assert done.returncode == 0, done.stderr
    assert "claude 9.9.9 (Claude Code) in" in done.stdout
    assert curl_calls(tmp_path) == []


def test_a_claude_that_does_not_run_is_installed_again(tmp_path):
    broken = tmp_path / "home" / ".local" / "bin" / "claude"
    broken.parent.mkdir(parents=True)
    _executable(broken, "#!/bin/sh\nexit 1\n")
    done = run(tmp_path)
    assert done.returncode == 0, done.stderr
    assert "installed" in done.stdout
    assert len(curl_calls(tmp_path)) == 1


def test_a_failed_try_is_tried_again(tmp_path):
    done = run(tmp_path, fails=2)
    assert done.returncode == 0, done.stderr
    assert len(curl_calls(tmp_path)) == 3
    assert done.stdout.count("trying again") == 2
    assert done.stderr.count("could not be downloaded (curl ended with 7)") == 2


def test_every_try_failing_ends_with_1_and_says_so(tmp_path):
    done = run(tmp_path, fails=99)
    assert done.returncode == 1
    assert len(curl_calls(tmp_path)) == 3
    assert "claude could not be installed" in done.stderr
    assert "next start tries again" in done.stderr
    assert not (tmp_path / "home" / ".local" / "bin" / "claude").exists()


def test_an_installer_that_leaves_no_claude_is_a_failure(tmp_path):
    (tmp_path / "installer.sh").write_text("#!/bin/bash\nexit 0\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    _executable(tools / "curl", CURL.replace('cp "$FAKE_INSTALLER"', 'echo "exit 0" >'))
    env = {
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "FAKE_CURL_LOG": str(tmp_path / "curl.log"),
        "CLAUDE_INSTALL_WAITS": "",
    }
    done = subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 1
    assert "left no claude" in done.stderr
