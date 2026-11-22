"""On GitHub Actions, failed tests are written as `::error` annotations (tests/conftest.py), which
anyone can read through the API where the job's log needs admin rights."""

import os
import subprocess
import sys
from pathlib import Path

from conftest import github_error

TESTS = Path(__file__).resolve().parent


def test_the_annotation_escapes_what_the_runner_wants_escaped():
    line = github_error("tests/test_x.py::test_a[1,2]", "50% done\r\nE   assert 1 == 2")
    assert line == "::error title=tests/test_x.py%3A%3Atest_a[1%2C2]::50%25 done%0D%0AE   assert 1 == 2"


def _run(tmp_path: Path, *args: str, actions: bool) -> str:
    """Pytest on a file with one passing and one failing test, with this suite's conftest loaded
    as a plugin, as CI would run it. The folder has an ini file of its own, so that it is the
    run's root: given `-c os.devnull`, pytest walked a whole drive to collect one file."""
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_some.py").write_text(
        "def test_passes():\n    pass\n\n"
        "def test_fails():\n    value = 1\n    assert value == 2, 'the value is wrong'\n",
        encoding="utf-8",
    )
    env = dict(os.environ, PYTHONPATH=str(TESTS))
    env.pop("GITHUB_ACTIONS", None)
    if actions:
        env["GITHUB_ACTIONS"] = "true"
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "conftest", "-p", "no:cacheprovider",
         "-c", str(tmp_path / "pytest.ini"), *args, str(tmp_path / "test_some.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 1, done.stdout + done.stderr
    return done.stdout


def _errors(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("::error")]


def test_a_failed_test_is_annotated_on_github_actions(tmp_path):
    errors = _errors(_run(tmp_path, actions=True))
    assert len(errors) == 1, errors
    assert errors[0].startswith("::error title=test_some.py%3A%3Atest_fails::")
    assert "the value is wrong" in errors[0]
    assert "%0A" in errors[0]


def test_under_xdist_too(tmp_path):
    errors = _errors(_run(tmp_path, "-n", "2", actions=True))
    assert len(errors) == 1, errors
    assert "test_fails" in errors[0] and "the value is wrong" in errors[0]


def test_nothing_is_annotated_elsewhere(tmp_path):
    assert _errors(_run(tmp_path, actions=False)) == []
