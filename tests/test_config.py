import json
import subprocess
from pathlib import Path

import pytest

from agentloop import config
from agentloop.config import ConfigError

EXAMPLES = Path(__file__).resolve().parent / "fixtures" / "examples"


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_the_examples_load():
    settings = config.load_settings(EXAMPLES / "settings.json")
    assert [w.name for w in settings.workers] == ["account1", "account2"]
    assert settings.loop("orchard").worker == "account2"
    assert settings.defaults == {"askEveryMinutes": 60}
    assert (settings.loop("quill").project, settings.loop("quill").tracker) == ("quill", "alice/projects/quill.os")
    assert settings.loop("orchard").tracker == "alice/projects/orchard.os"


def _project(name="p", repos=("code",), places=(), systems=None, settings=None) -> config.Project:
    """A project as its definition gives it."""
    return config.Project(name=name, tracker=config.Tracker(f"alice/projects/{name}.os"), repos=list(repos),
                          settings=settings or {}, raw={}, systems=systems, places=list(places))


def test_settings_layer_over_each_other():
    orchard = {"routes": {"low": {"model": "opus", "effort": "medium"}}, "buildInTriageUpTo": 1}
    settings = config.load_settings(EXAMPLES / "settings.json")
    result = config.effective_settings(settings.defaults, orchard, {"buildInTriageUpTo": 3, "routes": {"high": {"model": "sonnet", "effort": "max"}}})
    assert result["askEveryMinutes"] == 60
    assert result["buildInTriageUpTo"] == 3
    # Routes merge one by one, as the script merges a project's Routes over its own.
    assert result["routes"]["low"] == {"model": "opus", "effort": "medium"}
    assert result["routes"]["high"] == {"model": "sonnet", "effort": "max"}
    assert result["routes"]["trivial"] == config.DEFAULTS["routes"]["trivial"]
    assert result["repeatUpTo"] == 2
    # The defaults are not changed by a layer.
    assert config.DEFAULTS["routes"]["low"] == {"model": "sonnet", "effort": "high"}


def test_a_missing_settings_file_is_an_empty_one(tmp_path):
    settings = config.load_settings(tmp_path / "settings.json")
    assert settings.workers == [] and settings.loops == []
    assert "bundles" not in config.empty_settings()


def _example_settings():
    return json.loads((EXAMPLES / "settings.json").read_text(encoding="utf-8"))


def test_an_overseer_library_from_older_settings_is_let_be(tmp_path):
    """Settings written when tracker.py read the template from a library on the machine still load:
    the template comes from the server now, and what they named means nothing."""
    raw = _example_settings()
    raw["overseer"] = {"bin": "x", "library": "y"}
    config.load_settings(_write(tmp_path / "settings.json", raw))


@pytest.mark.parametrize("change, says", [
    (lambda s: s.update(format=2), "settings.json: format: must be 1"),
    (lambda s: s["loops"][0].update(worker="account3"), 'settings.json: loops[0].worker: no worker named "account3"'),
    (lambda s: s["loops"][1]["settings"].update(maxIteration=3), "settings.json: loops[1].settings.maxIteration: no such setting"),
    (lambda s: s["defaults"].update(routes={"low": {"model": "opus"}}), "settings.json: defaults.routes.low.effort: missing"),
    (lambda s: s["defaults"].update(routes={"huge": {"model": "opus", "effort": "high"}}), "defaults.routes.huge: no such route"),
    (lambda s: s["defaults"].update(chainUpTo=True), "defaults.chainUpTo: a whole number"),
    (lambda s: s["defaults"].update(trial={"until": "soon", "complexity": "medium", "upTo": 3, "route": {"model": "a", "effort": "b"}}),
     "defaults.trial.until: a date"),
    (lambda s: s["defaults"].update(autoStart=1), "defaults.autoStart: true or false"),
    (lambda s: s["defaults"].update(autoStartMinutes=0), "defaults.autoStartMinutes: a number above 0"),
    (lambda s: s["workers"].append(dict(s["workers"][0])), 'workers[2].name: a second worker named "account1"'),
    (lambda s: s["loops"][0]["repos"].update({"Bad Name": "x"}), "loops[0].repos.Bad Name: not a repository name"),
    (lambda s: s["loops"][0].update(places=["x"]), "loops[0].places: an object from place names to folders"),
    (lambda s: s["loops"][0].update(places={"bro": ""}), "loops[0].places.bro: the folder on this PC"),
    (lambda s: s["workers"][0].update(manager="Ap 31"), "workers[0].manager: null, or an Overseer username"),
    (lambda s: s["workers"][0].update(manager=7), "workers[0].manager: null, or an Overseer username"),
    (lambda s: s["workers"][0].update(manager=""), "workers[0].manager: null, or an Overseer username"),
    (lambda s: s["workers"][0].update(signIn={"token": "sk-ant-oat01-xyz"}), "workers[0].signIn: null for the folder's login"),
    (lambda s: s["workers"][0].update(signIn={"password": "X"}), "workers[0].signIn: null for the folder's login"),
    (lambda s: s["workers"][0].update(signIn={"token": "A", "apiKey": "B"}), "workers[0].signIn: null for the folder's login"),
    (lambda s: s["workers"][0].update(signIn="TOKEN"), "workers[0].signIn: null for the folder's login"),
    (lambda s: s["workers"][0].update(limits=0.8), "workers[0].limits: null, or each window's share"),
    (lambda s: s["workers"][0].update(limits={"five_hour": 80}), "workers[0].limits.five_hour: a share from 0 to 1"),
    (lambda s: s["workers"][0].update(limits={"five_hour": True}), "workers[0].limits.five_hour: a share from 0 to 1"),
    (lambda s: s["workers"][0].update(limits={"Five Hour": 0.5}), "workers[0].limits: 'Five Hour' is not a window's name"),
    (lambda s: s["workers"][0].update(limits={"overage": 0.5}), "workers[0].limits.overage: 0 for none, or null"),
])
def test_wrong_settings_are_said_plainly(tmp_path, change, says):
    raw = _example_settings()
    change(raw)
    path = _write(tmp_path / "settings.json", raw)
    with pytest.raises(ConfigError) as error:
        config.load_settings(path)
    assert says in str(error.value)


def test_not_json_names_the_line(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{\n  "format": 1,\n  oops\n}', encoding="utf-8")
    with pytest.raises(ConfigError, match=r"^settings.json: line 3: not JSON"):
        config.load_settings(path)


@pytest.mark.parametrize("changes, says", [
    ({"repos": []}, "repos: names no repository for the sessions to run in"),
    ({"repos": ["x", "x"]}, "repos: names a repository twice"),
    ({"repos": ["X"]}, "repos[0]"),
    ({"settings": {"routes": []}}, "settings.routes"),
    ({"systems": ["windows", "beos"]}, "systems: absent for every system, or a list of windows, macos, linux"),
    ({"systems": []}, "systems: absent for every system"),
    ({"repos": ["tools"]}, 'repos: "tools" is a name the app fills in itself'),
    ({"places": ["x"]}, '"x" is a repository'),
    ({"places": ["python"]}, 'or one the app fills in itself'),
    ({"places": ["Tool"]}, "places[0]: a place's name"),
    ({"places": ["tool", "tool"]}, "places: names a place twice"),
])
def test_wrong_projects_are_said_plainly(changes, says):
    with pytest.raises(ConfigError) as error:
        config.check_project({"repos": ["x"], "settings": {}, **changes}, "x definition")
    assert says in str(error.value)


def test_local_bundles_are_refused_and_an_empty_list_is_dropped(tmp_path):
    """Bundles are gone (item drop-bundle-source): a loop that names one, or settings that list one,
    would not run, and are said; the empty list settings written before held loads, and saving the
    settings again leaves it out."""
    raw = _example_settings()
    raw["loops"][0] = {"name": "quill", "project": "quill", "worker": "account1", "repos": {}}
    with pytest.raises(ConfigError, match=r"loops\[0\]\.project: names a local bundle, and bundles are gone"):
        config.load_settings(_write(tmp_path / "a.json", raw))
    raw = _example_settings()
    raw["bundles"] = ["~/src/bundles/quill"]
    with pytest.raises(ConfigError, match=r"bundles: local bundles are gone"):
        config.load_settings(_write(tmp_path / "b.json", raw))
    raw["bundles"] = []
    path = _write(tmp_path / "settings.json", raw)
    machine = config.load_machine(path)
    assert config.loop_settings(machine, machine.settings.loops[0])["repeatUpTo"] == 2
    config.save_settings(raw, path)
    assert "bundles" not in json.loads(path.read_text(encoding="utf-8"))


def test_save_keeps_unknown_keys_and_order(tmp_path):
    raw = _example_settings()
    raw["laterKey"] = {"kept": True}
    raw = {"zeta": 1, **raw}
    path = tmp_path / "settings.json"
    config.save_settings(raw, path)
    again = json.loads(path.read_text(encoding="utf-8"))
    assert list(again) == list(raw)
    assert again["laterKey"] == {"kept": True}
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_a_workers_sign_in_loads(tmp_path):
    raw = _example_settings()
    raw["workers"][0]["signIn"] = {"token": "CLAUDE_TOKEN_ONE"}
    raw["workers"][1]["signIn"] = {"apiKey": "MY_KEY"}
    one, two = config.load_settings(_write(tmp_path / "a.json", raw)).workers
    assert (one.sign_in_kind, one.sign_in_variable) == ("token", "CLAUDE_TOKEN_ONE")
    assert (two.sign_in_kind, two.sign_in_variable) == ("apiKey", "MY_KEY")
    del raw["workers"][0]["signIn"]
    raw["workers"][1]["signIn"] = None
    assert [w.sign_in_kind for w in config.load_settings(_write(tmp_path / "b.json", raw)).workers] == [None, None]


def test_a_workers_limits_load(tmp_path):
    raw = _example_settings()
    raw["workers"][0]["limits"] = {"five_hour": 0.8, "seven_day": 1, "seven_day_sonnet": None, "overage": 0}
    raw["workers"][1]["limits"] = {"five_hour": None}
    one, two = config.load_settings(_write(tmp_path / "a.json", raw)).workers
    assert one.limits == {"five_hour": 0.8, "seven_day": 1.0, "overage": 0.0}
    assert two.limits is None


def test_a_workers_manager_loads_and_is_saved(tmp_path):
    raw = _example_settings()
    assert [w.manager for w in config.load_settings(_write(tmp_path / "a.json", raw)).workers] == [None, None]
    raw["workers"][0]["manager"] = "alice"
    raw["workers"][1]["manager"] = None
    path = tmp_path / "settings.json"
    config.save_settings(raw, path)
    assert [w.manager for w in config.load_settings(path).workers] == ["alice", None]
    assert json.loads(path.read_text(encoding="utf-8"))["workers"][0]["manager"] == "alice"


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


def test_paths_without_agentloop_home(monkeypatch):
    monkeypatch.delenv("AGENTLOOP_HOME", raising=False)
    found = config.paths()
    assert found.config.name == "agentloop" and found.data.name == "agentloop"


def test_home_folder_is_written_as_tilde():
    assert config.home_relative(Path.home() / ".claude-account1") == "~/.claude-account1"
    assert config.expand("~/.claude-account1") == Path.home() / ".claude-account1"
    assert config.home_relative("D:\\Source\\Repos\\X") == "D:/Source/Repos/X"


# Repositories, placed: real repositories, made here.

def _git_repo(folder: Path) -> Path:
    folder.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(folder)], check=True)
    return folder


def _loop(repos):
    return config.Loop("l", "p", "w", {name: str(folder).replace("\\", "/") for name, folder in repos.items()}, {})


def test_place_repos_merges_one_repository_named_twice(tmp_path):
    code = _git_repo(tmp_path / "code")
    docs = _git_repo(tmp_path / "docs")
    (code / "web").mkdir()
    project = _project(repos=["code", "web", "docs"])
    placed = config.place_repos(_loop({"code": code, "web": code / "web", "docs": docs}), project)
    # code and web are one repository.
    assert placed.tops == [code.resolve(), docs.resolve()]
    assert placed.folders["web"] == code / "web"
    assert placed.tracker_name == "alice/projects/p.os"


def test_place_repos_says_what_is_missing(tmp_path):
    code = _git_repo(tmp_path / "code")
    plain = tmp_path / "plain"
    plain.mkdir()
    project = _project(repos=["code", "docs"])
    with pytest.raises(ConfigError, match=r'loop "l": repos.docs: p uses the repository docs, and this loop does not say where'):
        config.place_repos(_loop({"code": code}), project)
    with pytest.raises(ConfigError, match=r"repos.docs: is not in a git repository"):
        config.place_repos(_loop({"code": code, "docs": plain}), project)
    with pytest.raises(ConfigError, match=r"repos.docs: no such folder"):
        config.place_repos(_loop({"code": code, "docs": tmp_path / "gone"}), project)


def test_the_examples_say_places_and_the_overseer():
    settings = config.load_settings(EXAMPLES / "settings.json")
    assert not hasattr(settings, "overseer")
    assert settings.loop("quill").places == {"press": "C:/Program Files/Press"}
    assert settings.loop("orchard").places == {}


@pytest.mark.parametrize("platform, system", [("win32", "windows"), ("darwin", "macos"), ("linux", "linux"), ("freebsd14", "linux")])
def test_this_system(monkeypatch, platform, system):
    monkeypatch.setattr(config.sys, "platform", platform)
    assert config.this_system() == system


def test_a_project_runs_only_on_its_systems(tmp_path, monkeypatch):
    windows = _project("w", systems=["windows"])
    anywhere = _project("a")
    monkeypatch.setattr(config.sys, "platform", "darwin")
    with pytest.raises(ConfigError, match=r"^alice/projects/w.os definition: systems: w runs on windows, not on macos$"):
        config.check_system(windows)
    config.check_system(anywhere)
    monkeypatch.setattr(config.sys, "platform", "win32")
    config.check_system(windows)


def test_place_repos_places_the_folders_that_are_not_repositories(tmp_path):
    code = _git_repo(tmp_path / "code")
    tool = tmp_path / "Program Files" / "tool"
    tool.mkdir(parents=True)
    project = _project(places=["tool"])
    loop = _loop({"code": code})
    with pytest.raises(ConfigError, match=r'loop "l": places.tool: p names the folder tool, and this loop does not say where it is'):
        config.place_repos(loop, project)
    loop.places = {"tool": str(tmp_path / "gone")}
    with pytest.raises(ConfigError, match=r"places.tool: no such folder"):
        config.place_repos(loop, project)
    # A place is not a repository, and is never one of the tops to commit in.
    loop.places = {"tool": str(tool).replace("\\", "/")}
    placed = config.place_repos(loop, project)
    assert placed.places == {"tool": tool}
    assert placed.tops == [code.resolve()]


# A copy of a loop

def _with_copy(**copy) -> dict:
    raw = _example_settings()
    raw["loops"].append({"name": "quill-2", "like": "quill", **copy})
    return raw


def test_a_copy_takes_what_it_does_not_give_itself(tmp_path):
    raw = _with_copy(repos={"quill": "D:/Work/Quill-2"})
    raw["loops"][0]["repos"]["extra"] = "D:/Extra"
    settings = config.load_settings(_write(tmp_path / "settings.json", raw))
    copy, original = settings.loop("quill-2"), settings.loop("quill")
    assert (copy.project, copy.worker, copy.like) == ("quill", "account1", "quill")
    assert copy.repos == {"quill": "D:/Work/Quill-2", "extra": "D:/Extra"}
    assert copy.places == original.places and copy.settings == {"chainUpTo": 2}
    assert original.like is None
    assert settings.copies("quill") == ["quill-2"] and settings.copies("orchard") == []


def test_a_copys_own_keys_win(tmp_path):
    raw = _with_copy(worker="account2", places={"press": "D:/Other"},
                     settings={"chainUpTo": 1, "routes": {"low": {"model": "opus", "effort": "low"}}})
    raw["loops"][0]["settings"]["routes"] = {"high": {"model": "sonnet", "effort": "max"}}
    copy = config.load_settings(_write(tmp_path / "settings.json", raw)).loop("quill-2")
    assert copy.worker == "account2" and copy.places == {"press": "D:/Other"}
    # Laid over the original's as a layer is: routes one by one, the rest replaced.
    assert copy.settings == {"chainUpTo": 1, "routes": {
        "high": {"model": "sonnet", "effort": "max"}, "low": {"model": "opus", "effort": "low"}}}


def test_a_copy_may_come_before_the_loop_it_is_like(tmp_path):
    raw = _with_copy()
    raw["loops"].insert(0, raw["loops"].pop())
    assert config.load_settings(_write(tmp_path / "settings.json", raw)).loops[0].project == "quill"


@pytest.mark.parametrize("change, says", [
    (lambda s: s["loops"].append({"name": "x", "like": "nothing"}), 'loops[2].like: no other loop named "nothing"'),
    (lambda s: s["loops"].append({"name": "x", "like": "x"}), 'loops[2].like: no other loop named "x"'),
    (lambda s: s["loops"].extend([{"name": "x", "like": "quill"}, {"name": "y", "like": "x"}]),
     'loops[3].like: "x" is itself a copy of "quill"; name that loop'),
    (lambda s: s["loops"].append({"name": "x", "like": "quill", "tracker": "alice/projects/orchard.os"}),
     "loops[2].tracker: a copy works the project of the loop it is like, alice/projects/quill.os"),
    (lambda s: s["loops"].append({"name": "x", "like": "quill", "worker": "nobody"}), 'loops[2].worker: no worker named "nobody"'),
    (lambda s: s["loops"].append({"name": "x", "like": 3}), "loops[2].like: the name of the loop this one is a copy of"),
])
def test_a_wrong_copy_is_said_plainly(tmp_path, change, says):
    raw = _example_settings()
    change(raw)
    with pytest.raises(ConfigError) as error:
        config.load_settings(_write(tmp_path / "settings.json", raw))
    assert says in str(error.value)


def test_saving_a_copy_keeps_only_its_own_keys(tmp_path):
    path = tmp_path / "settings.json"
    raw = _with_copy(repos={"quill": "D:/Work/Quill-2"})
    config.save_settings(raw, path)
    assert json.loads(path.read_text(encoding="utf-8"))["loops"][2] == {
        "name": "quill-2", "like": "quill", "repos": {"quill": "D:/Work/Quill-2"}}
    # A change to the original reaches the copy.
    raw["loops"][0]["settings"] = {"repeatUpTo": 9}
    assert config.save_settings(raw, path).loop("quill-2").settings == {"repeatUpTo": 9}


def test_deleting_a_loop_that_has_copies_would_not_load(tmp_path):
    raw = _with_copy()
    raw["loops"] = raw["loops"][1:]
    with pytest.raises(ConfigError, match='like: no other loop named "quill"'):
        config.save_settings(raw, tmp_path / "settings.json")


def test_a_retired_setting_is_accepted_and_dropped(tmp_path):
    """maxIterations is gone (item no-limits), and stopAtWeeklyUse (item h87x); a file or
    definition that still says either loads, and saving it again leaves it out."""
    path = tmp_path / "settings.json"
    raw = _example_settings()
    raw["defaults"] = {"maxIterations": 5, "chainUpTo": 1, "stopAtWeeklyUse": 0.9}
    raw["loops"][0]["settings"] = {"maxIterations": 9, "stopAtWeeklyUse": 0.5}
    settings = config.save_settings(raw, path)
    assert settings.defaults == {"chainUpTo": 1} and settings.loop("quill").settings == {}
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["defaults"] == {"chainUpTo": 1} and saved["loops"][0]["settings"] == {}
    assert "maxIterations" not in config.DEFAULTS and "stopAtWeeklyUse" not in config.DEFAULTS
