"""The speech-to-text engines and local Whisper's setup (item voice-engines).

No real model, network, GPU or pip install: the whisper engine runs the real run script in a real
virtual environment, made without pip, whose `faster_whisper` and `ctranslate2` are small fakes;
the openai engine posts to a fake endpoint on loopback; setup is tried as a dry run only.
"""

import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from agentloop import cli, voice

FAKE_FASTER_WHISPER = '''
import os
from pathlib import Path


class _Info:
    language = "ru"


class _Segment:
    def __init__(self, text):
        self.text = text


class WhisperModel:
    def __init__(self, model, device, compute_type, download_root):
        self.args = (model, device, compute_type, download_root)

    def transcribe(self, file, language=None, beam_size=5, vad_filter=False):
        # As faster-whisper 1.2.1's decode_audio opens every recording (item metadata-errors).
        import av

        with av.open(file, mode="r", metadata_errors="ignore"):
            pass
        said = Path(file).read_text(encoding="utf-8")
        if said == "FAIL":
            raise RuntimeError("the recording could not be decoded")
        if said == "ARGS":
            said = "|".join(map(str, self.args))
        if said == "LIBS":
            said = os.environ.get("PATH" if os.name == "nt" else "LD_LIBRARY_PATH", "")
        info = _Info()
        info.language = language or "ru"
        return iter([_Segment(" " + part + " ") for part in said.split("|")]), info


def download_model(model, cache_dir=None):
    Path(cache_dir, model).mkdir(parents=True, exist_ok=True)
    return str(Path(cache_dir, model))
'''

# PyAV as it is now: `open` no longer takes `metadata_errors` (item metadata-errors).
FAKE_AV = '''
import contextlib


def open(file, mode="r", **options):
    if "metadata_errors" in options:
        raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
    return contextlib.nullcontext(file)
'''

FAKE_CTRANSLATE2 = '''
import os


def get_cuda_device_count():
    return int(os.environ.get("FAKE_CUDA_DEVICES", "0"))
'''


@pytest.fixture(scope="module")
def whisper_home(tmp_path_factory):
    """An app home whose local Whisper environment is set up, with the fakes in its site-packages."""
    home = tmp_path_factory.mktemp("home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("AGENTLOOP_HOME", str(home))
        venv = voice.venv_dir()
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, timeout=300)
        python = voice.venv_python(venv)
        assert python.is_file()
        site = Path(subprocess.run([str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                                   capture_output=True, text=True, check=True).stdout.strip())
        (site / "faster_whisper").mkdir()
        (site / "faster_whisper" / "__init__.py").write_text(FAKE_FASTER_WHISPER, encoding="utf-8")
        (site / "ctranslate2.py").write_text(FAKE_CTRANSLATE2, encoding="utf-8")
        (site / "av.py").write_text(FAKE_AV, encoding="utf-8")
        yield home, site


@pytest.fixture
def home(whisper_home, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(whisper_home[0]))
    monkeypatch.delenv("FAKE_CUDA_DEVICES", raising=False)
    return whisper_home


def _recording(tmp_path, said: str, name: str = "memo.webm") -> Path:
    path = tmp_path / name
    path.write_text(said, encoding="utf-8")
    return path


def test_whisper_transcribes_through_its_own_environment(home, tmp_path):
    got = voice.transcribe(_recording(tmp_path, "Сделай|кнопку синей"), {"engine": "whisper"})
    assert got == {"language": "ru", "text": "Сделай кнопку синей"}


def test_whisper_passes_its_settings(home, tmp_path):
    got = voice.transcribe(_recording(tmp_path, "ARGS"),
                           {"engine": "whisper", "model": "small", "device": "cpu", "computeType": "float32",
                            "language": "en"})
    model, device, compute, root = got["text"].split(" ")
    assert (model, device, compute) == ("small", "cpu", "float32")
    assert Path(root) == voice.models_dir()
    assert got["language"] == "en"


@pytest.mark.parametrize("gpus, device, compute", [("0", "cpu", "int8"), ("1", "cuda", "float16")])
def test_whisper_picks_the_device_and_its_compute_type(home, tmp_path, monkeypatch, gpus, device, compute):
    monkeypatch.setenv("FAKE_CUDA_DEVICES", gpus)
    got = voice.transcribe(_recording(tmp_path, "ARGS"), {"engine": "whisper"})
    model, used, compute_type, _ = got["text"].split(" ")
    assert (model, used, compute_type) == ("large-v3", device, compute)


def test_whisper_finds_the_cuda_libraries(home, tmp_path):
    _, site = home
    libs = site / "nvidia" / "cublas" / ("bin" if sys.platform == "win32" else "lib")
    libs.mkdir(parents=True)
    try:
        got = voice.transcribe(_recording(tmp_path, "LIBS"), {"engine": "whisper"})
    finally:
        libs.rmdir()
    assert str(libs) in got["text"]


def test_a_whisper_failure_names_the_exception(home, tmp_path):
    with pytest.raises(voice.VoiceError, match=r"^whisper: RuntimeError: the recording could not be decoded$"):
        voice.transcribe(_recording(tmp_path, "FAIL"), {"engine": "whisper"})


def test_whisper_not_set_up_says_how(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "empty"))
    with pytest.raises(voice.VoiceError, match="agentloop voice setup"):
        voice.transcribe(_recording(tmp_path, "x"), {"engine": "whisper"})


def test_the_download_step_puts_the_model_in_the_models_folder(home):
    cmd = voice.setup_commands("small", gpu=False)[-1]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert (voice.models_dir() / "small").is_dir()


def _run_script_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("whisper_run", voice.run_script())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_av(monkeypatch, open_):
    import types

    av = types.ModuleType("av")
    av.open = open_
    monkeypatch.setitem(sys.modules, "av", av)
    return av


def test_pyav_without_metadata_errors_is_opened_without_it(monkeypatch):
    calls = []

    def open_(file, mode="r", **options):
        calls.append(options)
        if "metadata_errors" in options:
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        return file

    av = _fake_av(monkeypatch, open_)
    script = _run_script_module()
    script.tolerate_av_metadata_errors()
    script.tolerate_av_metadata_errors()  # a second call wraps nothing twice
    assert av.open("memo.webm", mode="r", metadata_errors="ignore") == "memo.webm"
    assert calls == [{"metadata_errors": "ignore"}, {}]


def test_pyav_that_takes_metadata_errors_gets_it(monkeypatch):
    calls = []
    av = _fake_av(monkeypatch, lambda file, mode="r", **options: calls.append(options) or file)
    _run_script_module().tolerate_av_metadata_errors()
    assert av.open("memo.webm", metadata_errors="ignore") == "memo.webm"
    assert calls == [{"metadata_errors": "ignore"}]


def test_other_type_errors_from_pyav_are_not_swallowed(monkeypatch):
    def open_(file, mode="r", **options):
        raise TypeError("open() got an unexpected keyword argument 'options'")

    av = _fake_av(monkeypatch, open_)
    _run_script_module().tolerate_av_metadata_errors()
    with pytest.raises(TypeError, match="'options'"):
        av.open("memo.webm", options={}, metadata_errors="ignore")
    with pytest.raises(TypeError, match="'options'"):
        av.open("memo.webm", options={})


def test_an_unknown_engine_is_refused(tmp_path):
    with pytest.raises(voice.VoiceError, match="no speech-to-text engine"):
        voice.transcribe(_recording(tmp_path, "x"), {"engine": "off"})


def test_the_linux_cuda_folders_are_found(tmp_path):
    lib = tmp_path / "lib" / "python3.12" / "site-packages" / "nvidia" / "cudnn" / "lib"
    lib.mkdir(parents=True)
    (tmp_path / "lib" / "python3.12" / "site-packages" / "nvidia" / "cudnn" / "bin").mkdir()
    assert voice.cuda_library_dirs(tmp_path, "linux") == [lib]
    assert voice.venv_python(tmp_path, "linux") == tmp_path / "bin" / "python"
    assert voice.venv_python(tmp_path, "windows") == tmp_path / "Scripts" / "python.exe"


class _Endpoint(http.server.BaseHTTPRequestHandler):
    status = 200
    answer = {"text": "Make the button blue."}
    seen: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).seen.append({"auth": self.headers["Authorization"], "type": self.headers["Content-Type"],
                                "body": body, "path": self.path})
        data = json.dumps(self.answer if self.status == 200
                          else {"error": {"message": "Incorrect API key provided: sk-sec***cret"}}).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def endpoint(monkeypatch, tmp_path):
    class Handler(_Endpoint):
        seen = []

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(voice.OPENAI_URL_ENV, f"http://127.0.0.1:{server.server_address[1]}/v1/audio/transcriptions")
    key = tmp_path / "openai.key"
    key.write_text("sk-secret-key\n", encoding="utf-8")
    yield Handler, key
    server.shutdown()
    server.server_close()


def test_openai_posts_the_recording_with_the_key(endpoint, tmp_path):
    handler, key = endpoint
    got = voice.transcribe(_recording(tmp_path, "AUDIO-BYTES"), {"engine": "openai", "keyFile": str(key)})
    assert got == {"language": "", "text": "Make the button blue."}
    [seen] = handler.seen
    assert seen["auth"] == "Bearer sk-secret-key"
    assert seen["type"].startswith("multipart/form-data; boundary=")
    assert seen["path"] == "/v1/audio/transcriptions"
    body = seen["body"]
    assert b'name="model"\r\n\r\ngpt-4o-transcribe\r\n' in body
    assert b'name="response_format"\r\n\r\njson\r\n' in body
    assert b'filename="memo.webm"' in body and b"AUDIO-BYTES" in body
    assert b"sk-secret" not in body


def test_openai_whisper_1_reports_the_language(endpoint, tmp_path):
    handler, key = endpoint
    handler.answer = {"text": "Сделай кнопку синей", "language": "russian"}
    got = voice.transcribe(_recording(tmp_path, "x"), {"engine": "openai", "model": "whisper-1", "keyFile": str(key)})
    assert got == {"language": "russian", "text": "Сделай кнопку синей"}
    assert b'name="response_format"\r\n\r\nverbose_json\r\n' in handler.seen[0]["body"]


def test_openai_given_a_language_sends_and_reports_it(endpoint, tmp_path):
    handler, key = endpoint
    got = voice.transcribe(_recording(tmp_path, "x"), {"engine": "openai", "language": "ru", "keyFile": str(key)})
    assert got["language"] == "ru"
    assert b'name="language"\r\n\r\nru\r\n' in handler.seen[0]["body"]


def test_an_openai_error_names_the_status_and_never_the_key(endpoint, tmp_path):
    handler, key = endpoint
    handler.status = 401
    with pytest.raises(voice.VoiceError) as caught:
        voice.transcribe(_recording(tmp_path, "x"), {"engine": "openai", "keyFile": str(key)})
    message = str(caught.value)
    assert message == "openai: HTTPError 401"
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_openai_without_a_key_file_says_so(tmp_path):
    with pytest.raises(voice.VoiceError, match="keyFile"):
        voice.transcribe(_recording(tmp_path, "x"), {"engine": "openai"})
    with pytest.raises(voice.VoiceError, match=r"could not be read \(FileNotFoundError\)$"):
        voice.transcribe(_recording(tmp_path, "x"), {"engine": "openai", "keyFile": str(tmp_path / "none.key")})


def test_openai_unreachable_names_only_the_exception(tmp_path, monkeypatch):
    key = tmp_path / "k"
    key.write_text("sk-secret", encoding="utf-8")
    monkeypatch.setenv(voice.OPENAI_URL_ENV, "http://127.0.0.1:9/v1/audio/transcriptions")
    with pytest.raises(voice.VoiceError) as caught:
        voice.transcribe(_recording(tmp_path, "x"), {"engine": "openai", "keyFile": str(key)})
    assert "127.0.0.1" not in str(caught.value) and "sk-secret" not in str(caught.value)


@pytest.mark.parametrize("given", ["https://example.com/steal", "http://10.0.0.1/x", ""])
def test_the_url_override_is_for_loopback_only(monkeypatch, given):
    monkeypatch.setenv(voice.OPENAI_URL_ENV, given)
    assert voice.openai_url() == voice.OPENAI_URL


@pytest.mark.parametrize("system, cpu, smi, gpu", [
    ("windows", False, True, True),
    ("linux", False, True, True),
    ("linux", False, False, False),
    ("windows", True, True, False),
    ("macos", False, True, False),
])
def test_setup_installs_the_cuda_libraries_only_where_they_work(monkeypatch, system, cpu, smi, gpu):
    monkeypatch.setattr(voice.shutil, "which", lambda name: "/usr/bin/nvidia-smi" if smi else None)
    assert voice.wants_gpu(cpu, system) is gpu


@pytest.mark.parametrize("system", ["windows", "linux", "macos"])
def test_setup_commands(tmp_path, monkeypatch, system):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path))
    for gpu in (False, True):
        cmds = voice.setup_commands("large-v3", gpu, system)
        python = str(voice.venv_python(voice.venv_dir(), system))
        assert cmds[0] == [sys.executable, "-m", "venv", str(voice.venv_dir())]
        install = cmds[2]
        assert install[:5] == [python, "-m", "pip", "install", "--upgrade"]
        assert ("nvidia-cudnn-cu12" in install) is gpu and "faster-whisper" in install
        assert cmds[3] == [python, "-I", str(voice.run_script()), "--download-only", "--model", "large-v3",
                           "--models-dir", str(voice.models_dir())]


def test_a_dry_run_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path))
    monkeypatch.setattr(voice.subprocess, "run", lambda *a, **k: pytest.fail("a dry run ran a command"))
    lines: list[str] = []
    assert voice.setup("medium", cpu=True, dry_run=True, out=lines.append) == 0
    assert not voice.voice_dir().exists()
    assert sum(line.startswith("$ ") for line in lines) == 4
    assert lines[-1].endswith('{"engine": "whisper", "model": "medium", "device": "cpu"}')


def test_voice_try_prints_the_language_and_text(endpoint, tmp_path, capsys):
    _, key = endpoint
    code = cli.main(["voice", "try", str(_recording(tmp_path, "x")), "--engine", "openai", "--key-file", str(key)])
    assert code == 0
    assert capsys.readouterr().out == "language  (not said)\nMake the button blue.\n"


def test_voice_try_reports_a_failure(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("AGENTLOOP_HOME", str(tmp_path / "empty"))
    assert cli.main(["voice", "try", str(_recording(tmp_path, "x"))]) == 1
    assert "agentloop voice try: whisper: local Whisper is not set up" in capsys.readouterr().err
