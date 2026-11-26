"""Settings: the examples load, layers merge, a save keeps what it does not know and refuses what
would not load, and a wrong setting is refused naming where it is. A refusal is checked for the
place it names, not for its wording."""
import json
from pathlib import Path

import pytest

from agentloop import config
from agentloop.config import ConfigError

EXAMPLES = Path(__file__).resolve().parent / "fixtures" / "examples"
# The settings docs/settings.md shows, which stay ones that load.
DOCS_EXAMPLES = Path(__file__).resolve().parents[1] / "docs" / "examples"


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _example_settings():
    return json.loads((EXAMPLES / "settings.json").read_text(encoding="utf-8"))


def test_the_examples_load():
    settings = config.load_settings(EXAMPLES / "settings.json")
    assert [w.name for w in settings.workers] == ["account1", "account2"]
    assert settings.loop("orchard").worker == "account2"
    assert settings.loop("orchard").tracker == "alice/projects/orchard.os"
    docs = config.load_settings(DOCS_EXAMPLES / "settings.json")
    assert docs.loop("quill-2").places == {"press": "C:/Program Files/Press"}


def test_a_missing_settings_file_is_an_empty_one(tmp_path):
    settings = config.load_settings(tmp_path / "settings.json")
    assert settings.workers == [] and settings.loops == []


def test_settings_layer_over_each_other():
    result = config.effective_settings({"askEveryMinutes": 60},
                                       {"routes": {"low": {"model": "opus", "effort": "medium"}}},
                                       {"routes": {"high": {"model": "sonnet", "effort": "max"}}})
    assert result["askEveryMinutes"] == 60
    assert result["routes"]["low"] == {"model": "opus", "effort": "medium"}
    assert result["routes"]["high"] == {"model": "sonnet", "effort": "max"}
    assert result["routes"]["trivial"] == config.DEFAULTS["routes"]["trivial"]


@pytest.mark.parametrize("change, where", [
    (lambda s: s.update(format=2), "format"),
    (lambda s: s["loops"][0].update(worker="account3"), "loops[0].worker"),
    (lambda s: s["loops"][1]["settings"].update(maxIteration=3), "loops[1].settings.maxIteration"),
    (lambda s: s["defaults"].update(routes={"low": {"model": "opus"}}), "defaults.routes.low.effort"),
    (lambda s: s["workers"].append(dict(s["workers"][0])), "workers[2].name"),
    (lambda s: s["workers"][0].update(signIn={"token": "sk-ant-oat01-xyz"}), "workers[0].signIn"),
    (lambda s: s["workers"][0].update(limits={"five_hour": 80}), "workers[0].limits.five_hour"),
    (lambda s: s["defaults"].update(leftSessions=0), "defaults.leftSessions"),
    (lambda s: s["defaults"].update(leftHours=-1), "defaults.leftHours"),
    (lambda s: s["loops"][1]["settings"].update(transcribeOnly="yes"), "loops[1].settings.transcribeOnly"),
])
def test_wrong_settings_are_refused_where_they_are(tmp_path, change, where):
    raw = _example_settings()
    change(raw)
    with pytest.raises(ConfigError) as error:
        config.load_settings(_write(tmp_path / "settings.json", raw))
    assert f"settings.json: {where}:" in str(error.value)


def test_a_wrong_token_is_never_said(tmp_path):
    raw = _example_settings()
    raw["workers"][0]["signIn"] = {"token": "sk-ant-oat01-xyz"}
    with pytest.raises(ConfigError) as error:
        config.load_settings(_write(tmp_path / "settings.json", raw))
    assert "sk-ant" not in str(error.value)


def test_not_json_names_the_line(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{\n  "format": 1,\n  oops\n}', encoding="utf-8")
    with pytest.raises(ConfigError, match=r"^settings.json: line 3:"):
        config.load_settings(path)


@pytest.mark.parametrize("changes, where", [
    ({"repos": []}, "repos"),
    ({"repos": ["X"]}, "repos[0]"),
    ({"systems": ["windows", "beos"]}, "systems"),
    # Whether a loop only transcribes is each machine's to say, never a project's.
    ({"settings": {"transcribeOnly": True}}, "settings.transcribeOnly"),
])
def test_wrong_projects_are_refused_where_they_are(changes, where):
    with pytest.raises(ConfigError) as error:
        config.check_project({"repos": ["x"], "settings": {}, **changes}, "x definition")
    assert f"{where}:" in str(error.value)


def test_save_keeps_unknown_keys_and_order(tmp_path):
    raw = {"zeta": 1, **_example_settings(), "laterKey": {"kept": True}}
    path = tmp_path / "settings.json"
    config.save_settings(raw, path)
    again = json.loads(path.read_text(encoding="utf-8"))
    assert list(again) == list(raw)
    assert again["laterKey"] == {"kept": True}
    # Written in place, through no file left beside it.
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_save_refuses_settings_that_would_not_load(tmp_path):
    path = tmp_path / "settings.json"
    config.save_settings(_example_settings(), path)
    before = path.read_bytes()
    raw = _example_settings()
    raw["loops"][0]["worker"] = "nobody"
    with pytest.raises(ConfigError):
        config.save_settings(raw, path)
    assert path.read_bytes() == before


def test_paths_under_agentloop_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path))
    found = config.paths()
    assert (found.config, found.data) == (tmp_path / "config", tmp_path / "data")
    assert found.settings == tmp_path / "config" / "settings.json"
