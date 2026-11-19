"""Tries an installed agentloop, as the release workflow does on each system after installing the
wheel (item package): `agentloop --version` prints the version expected, and `agentloop serve`
starts and answers its page.

    python .github/try-install.py <version>

It runs the `agentloop` found on PATH, with AGENTLOOP_HOME in a folder of its own, so that the
settings it starts with are none and nothing of the machine's is read or written. The address serve
prints carries the page's token, so it is never printed here.
"""

from __future__ import annotations

import http.cookiejar
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request

WAIT = 60


def fail(message: str) -> int:
    print(f"try-install: {message}", file=sys.stderr)
    return 1


def end(process: subprocess.Popen) -> None:
    # On Windows the console script is a launcher that runs Python as its child, so the tree is
    # ended; on macOS and Linux serve was started as the leader of a process group of its own.
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
    else:
        import signal

        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(15)
    except subprocess.TimeoutExpired:
        process.kill()


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        return fail("usage: try-install.py <version>")
    expected = f"agentloop {argv[0]}"
    command = shutil.which("agentloop")
    if command is None:
        return fail("agentloop is not on PATH")

    done = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=60)
    line = done.stdout.strip()
    if done.returncode != 0 or not line.startswith(expected + " "):
        return fail(f"agentloop --version printed {line!r} (exit {done.returncode}), not {expected} ...")
    print(f"try-install: --version: {line}")

    with tempfile.TemporaryDirectory() as home:
        env = {**os.environ, "AGENTLOOP_HOME": home}
        group = {} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen([command, "serve", "--port", "0"], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, env=env, **group)
        try:
            # The first line is the address; it is read on a thread so that a serve that never
            # prints it is given up on rather than waited for.
            got: list[str] = []
            reader = threading.Thread(target=lambda: got.append(process.stdout.readline()), daemon=True)
            reader.start()
            reader.join(WAIT)
            found = re.search(r"http://127\.0\.0\.1:\d+/\?token=\S+", got[0] if got else "")
            if found is None:
                return fail(f"agentloop serve printed no address in {WAIT} s (exit {process.poll()})")
            # The token is traded for a cookie and a redirect to the page without it.
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            try:
                with opener.open(found.group(0), timeout=30) as response:
                    status, body = response.status, response.read()
            except OSError as error:
                return fail(f"the page did not answer ({type(error).__name__})")
            if status != 200 or b"<html" not in body.lower():
                return fail(f"the page answered {status}, without the page")
            print(f"try-install: serve answered its page ({len(body)} bytes)")
        finally:
            end(process)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
