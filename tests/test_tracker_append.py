"""`tracker.py append` (item bench-record): a record appended to a list the project's own document
declares - here, as Brocessor's benchmarks are, a tab of the tracker's own mounting a document of
records - and refused for every list tracker.py governs."""

import json

from test_loop import item, tracker_text

# The records' document, as `brocessor.bench.os` is made: its templates hidden, its list keyed by
# when a run finished, and each record keeping a list of its own.
BENCH = "\n".join([
    "div bench (mutable=true) {",
    "    div (hidden=true) {",
    "        div BenchPatch {",
    '            string name = ""',
    "            float score = 0.0",
    "            bool scored = true",
    "        }",
    "        div BenchRun {",
    '            timestamp at = "2026-01-01T00:00:00Z"',
    '            string machine = ""',
    "            float score = 0.0",
    "            int runs = 0",
    "            bool dirty = false",
    '            list Patches (entry=<BenchPatch>, key="name") { }',
    "        }",
    "    }",
    '    list Benchmarks (entry=<BenchRun>, key="at") { }',
    "}",
    "",
])

LIST = "Benchmarks/bench/bench/Benchmarks"

RECORD = {
    "at": {"Timestamp": "2026-11-24T10:00:00Z"},
    "machine": "box",
    "score": 42.5,
    "runs": 3,
    "dirty": True,
    "Patches": [{"name": "pad", "score": 40.25, "scored": True}, {"name": "lead", "score": 2.0, "scored": False}],
}


def own_tracker(overseer, tab: bool = True):
    """A tracker with its own Benchmarks tab, and the records' document beside it."""
    folder = overseer.folder()
    overseer.document(BENCH, project="proj.bench", folder=folder)
    text = tracker_text(item("lone")).rstrip("\n")
    if tab:
        text = text[:-1] + "\n".join([
            '    tab Benchmarks (label="Benchmarks") {',
            '        mount bench (source="proj.bench.os/bench", lazy=false, mutable=true) { }',
            "    }",
            "}",
        ])
    return overseer.document(text + "\n", folder=folder), f"{folder}/proj.bench.os"


def test_a_record_lands_in_the_mounted_document_with_its_types(overseer, tmp_path):
    doc, bench = own_tracker(overseer)
    before = overseer.text(doc)
    record = tmp_path / "record.json"
    record.write_text(json.dumps(RECORD), encoding="utf-8")
    said = overseer.tracker(doc, "append", LIST, f"@{record}")
    assert said.stdout.strip() == f"project/{LIST}/[2026-11-24T10:00:00Z]"
    text = overseer.text(bench)
    for line in ('- at = "2026-11-24T10:00:00Z"', '- machine = "box"', "- score = 42.5", "- runs = 3",
                 "- dirty = true", '- name = "pad"', "- score = 40.25", '- name = "lead"', "- scored = false"):
        assert line in text, line
    # The patches are the record's, not entries of the list beside it.
    assert text.index("- runs = 3") < text.index('- name = "pad"')
    # Nothing of it is in the tracker's own text, which every session reads.
    assert overseer.text(doc) == before


def test_records_are_appended_one_after_another_and_inline(overseer):
    doc, bench = own_tracker(overseer)
    overseer.tracker(doc, "append", f"/project/{LIST}", json.dumps(RECORD))
    second = {"at": {"Timestamp": "2026-11-25T10:00:00Z"}, "machine": "other", "score": 7.0}
    said = overseer.tracker(doc, "append", LIST, json.dumps(second))
    assert said.stdout.strip().endswith("[2026-11-25T10:00:00Z]")
    text = overseer.text(bench)
    assert '- machine = "box"' in text and '- machine = "other"' in text
    # 7.0 is a float in JSON, and stays one: a whole number would be refused by a float field.
    assert "- score = 7.0" in text or "- score = 7" in text


def test_the_lists_tracker_py_governs_are_refused(overseer):
    doc, _ = own_tracker(overseer)
    before = overseer.text(doc)
    for address in ("Items", "project/History", "Features", "Discussion", "Labels", "Bench",
                    "Items/[lone]/Talk/Comments", "Benchmarks/Items"):
        said = overseer.tracker(doc, "append", address, json.dumps({"handle": "sneak"}), check=False)
        assert said.returncode and "governs" in said.stderr, address
    assert overseer.text(doc) == before


def test_a_list_the_document_does_not_declare_is_refused(overseer):
    doc, _ = own_tracker(overseer, tab=False)
    said = overseer.tracker(doc, "append", LIST, json.dumps(RECORD), check=False)
    assert said.returncode and "declares of its own" in said.stderr
    # A field the document fills in for the template is not a part of its own.
    said = overseer.tracker(doc, "append", "description", json.dumps({}), check=False)
    assert said.returncode and "declares of its own" in said.stderr


def test_what_is_not_a_record_is_refused(overseer):
    doc, bench = own_tracker(overseer)
    before = overseer.text(bench)
    for given in ("[1, 2]", "not json", json.dumps({"machine": {"a": 1, "b": 2}})):
        said = overseer.tracker(doc, "append", LIST, given, check=False)
        assert said.returncode and "append:" in said.stderr, given
    assert overseer.text(bench) == before


def test_a_hand_run_reaches_the_tracker_through_its_loop(overseer, tmp_path):
    """`agentloop tracker <loop> ...`: tracker.py with the loop's server, key and tracker, for a
    project's script run by hand, outside a session."""
    import os
    import subprocess
    import sys

    from agentloop import config

    doc, bench = own_tracker(overseer)
    home = tmp_path / "home"
    config.save_json(home / "config" / "settings.json", {
        "format": 1,
        "workers": [{"name": "w", "claude": str(tmp_path), "overseer": overseer.worker}],
        "loops": [{"name": "proj", "tracker": doc, "worker": "w", "repos": {"code": str(tmp_path)}}],
        "defaults": {},
    })
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_", "OVERSEER_", "AGENTIC_"))}
    done = subprocess.run([sys.executable, "-m", "agentloop", "tracker", "proj", "append", LIST, json.dumps(RECORD)],
                          capture_output=True, text=True, encoding="utf-8", env={**clean, "AGENTLOOP_HOME": str(home)})
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().endswith("[2026-11-24T10:00:00Z]")
    assert '- machine = "box"' in overseer.text(bench)
    done = subprocess.run([sys.executable, "-m", "agentloop", "tracker", "nope", "check"],
                          capture_output=True, text=True, encoding="utf-8", env={**clean, "AGENTLOOP_HOME": str(home)})
    assert done.returncode == 2 and "nope" in done.stderr



def test_a_whole_number_is_taken_by_a_float_field(overseer):
    # PowerShell's ConvertTo-Json writes a whole double as `7`: sent as an Integer, it still lands.
    doc, bench = own_tracker(overseer)
    overseer.tracker(doc, "append", LIST, json.dumps({"at": {"Timestamp": "2026-11-26T10:00:00Z"}, "score": 7}))
    assert "- score = 7" in overseer.text(bench)
