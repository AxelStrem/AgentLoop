"""features.py on a tracker that has no features yet (item features-empty): the template declares the
Features list, so a tracker that writes no `- Features` line still has it, empty, and the first
feature can be added to it. On the tests' own server, with the template it hands out; and on a small
fake server for a template that declares no Features list."""
import json
import os
import subprocess
import sys

import pytest

from conftest import TRACKER_PY
from test_loop import item, tracker_text
from test_tracker_template import TEMPLATE, fake_server, template  # noqa: F401 - fake_server is a fixture

FEATURES_PY = TRACKER_PY.parent / "features.py"


def features(overseer, doc: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_", "OVERSEER_")) and k != "AGENTIC_LOOP"}
    done = subprocess.run([sys.executable, str(FEATURES_PY), "--doc", doc, *args], capture_output=True, text=True,
                          encoding="utf-8", env={**clean, **overseer.env})
    if check and done.returncode:
        raise AssertionError(done.stderr)
    return done


@pytest.fixture
def bare(overseer):
    """A tracker writing no Features line, on a server whose template declares the list."""
    doc = overseer.document(tracker_text(item("alpha")))
    assert "Features" not in overseer.text(doc)
    shown = features(overseer, doc, "list", check=False)
    if "no feature list" in shown.stderr:
        pytest.skip("the server's project template has no Features list: build an Overseer with overseer:featurestab")
    return doc


def test_a_tracker_with_no_features_lists_none(overseer, bare):
    listed = json.loads(features(overseer, bare, "list", "--json").stdout)
    assert listed and all(v == [] for v in listed.values())
    assert "proposed" in listed
    shown = features(overseer, bare, "show", "NOPE", check=False)
    assert shown.returncode and "no feature NOPE" in shown.stderr and "Traceback" not in shown.stderr


def test_the_first_feature_writes_the_list(overseer, bare):
    added = features(overseer, bare, "add", "FIRST", "The first one")
    assert "added FIRST at proposed" in added.stdout
    text = overseer.text(bare)
    # The server writes the list inside the project, beside the items, which are untouched.
    assert "\n    list Features {\n" in text or "\n    - Features {\n" in text
    assert 'handle = "FIRST"' in text and 'handle = "alpha"' in text
    listed = json.loads(features(overseer, bare, "list", "--json").stdout)
    assert listed["proposed"] == [{"handle": "FIRST", "title": "The first one"}]

    second = features(overseer, bare, "add", "SECOND", "Needs the first", "--requires", "FIRST",
                      "--note", "Said once.")
    assert "added SECOND" in second.stdout
    shown = json.loads(features(overseer, bare, "show", "SECOND", "--json").stdout)
    assert shown["requires"] == "FIRST"
    assert [c["body"] for c in shown["comments"]] == ["Said once."]
    assert overseer.text(bare).count("Features {") == 1


def test_a_template_without_features_still_says_so(fake_server):
    documents, run = fake_server
    documents[TEMPLATE] = {"text": template("int template_version (hidden=true) = 1")}
    documents["a/proj.os"] = {"text": tracker_text(item("alpha")), "bases": [TEMPLATE]}
    done = run("list", script=FEATURES_PY)
    assert done.returncode and "Traceback" not in done.stderr
    assert done.stderr.strip() == "no feature list in proj.os: it has no list Features"
