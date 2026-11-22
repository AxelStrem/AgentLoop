"""tracker.py on a tracker that writes no History or Labels line (item empty-history): the project
template declares both lists, so a new project that has closed nothing and tagged nothing still has
them, empty, rather than stopping at `no list History`. On the tests' own server, with the template
it hands out; and on a small fake server for a template that declares neither."""
import json

import pytest

from test_loop import item
from test_tracker_template import TEMPLATE, fake_server, said, template  # noqa: F401 - fake_server is a fixture


def bare_text(*items: str, with_items: bool = True) -> str:
    """A tracker as a new project's may be: its items, and no Labels or History line."""
    return "\n".join([
        "// A test tracker",
        '<projects/project_template.os/project> project (label="Proj") {',
        '    - name = "Proj"',
        '    - description = "A project for tests."',
        *(["    - Items {", *items, "    }"] if with_items else []),
        "}",
        "",
    ])


@pytest.fixture
def bare(overseer):
    doc = overseer.document(bare_text(item("alpha")))
    text = overseer.text(doc)
    assert "History" not in text and "Labels" not in text
    return doc


def test_an_open_item_shows(overseer, bare):
    shown = overseer.tracker(bare, "show", "alpha")
    assert "The alpha item" in shown.stdout


def test_no_such_item_is_said_cleanly(overseer, bare):
    done = overseer.tracker(bare, "show", "nope", check=False)
    assert done.returncode and "Traceback" not in done.stderr
    assert "no item nope" in done.stderr
    done = overseer.tracker(bare, "after", "alpha", "nope", check=False)
    assert done.returncode and "Traceback" not in done.stderr
    assert "no item nope, open or finished" in done.stderr


def test_tags_are_none(overseer, bare):
    assert json.loads(overseer.tracker(bare, "tag", "--json").stdout) == {}
    done = overseer.tracker(bare, "file", "beta", "A tagged one", "--labels", "core", "--note", "x", check=False)
    assert done.returncode and "Traceback" not in done.stderr
    assert "no such tag core" in done.stderr


def test_an_item_is_filed_and_waited_on(overseer, bare):
    overseer.tracker(bare, "file", "beta", "A second one", "--note", "Filed on a bare tracker.")
    overseer.tracker(bare, "after", "alpha", "beta")
    shown = overseer.tracker(bare, "show", "alpha").stdout
    assert "beta" in shown
    assert "parses: the open list has 2 items" in overseer.tracker(bare, "check").stdout
    done = overseer.tracker(bare, "file", "beta", "Again", "--note", "x", check=False)
    assert done.returncode and "beta is taken" in done.stderr


def test_a_tracker_with_no_items_line_checks(overseer):
    doc = overseer.document(bare_text(with_items=False))
    assert "parses: the open list has 0 items" in overseer.tracker(doc, "check").stdout


def test_a_template_without_the_lists_still_says_so(fake_server):
    documents, run = fake_server
    documents[TEMPLATE] = {"text": template("int template_version (hidden=true) = 1")}
    documents["a/proj.os"] = {"text": bare_text(item("alpha")), "bases": [TEMPLATE]}
    assert said(run("tag")) == "no list Labels in proj.os"
    assert said(run("show", "nope")) == "no list History in proj.os"
