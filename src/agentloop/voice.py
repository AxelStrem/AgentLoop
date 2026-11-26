"""Speech to text for voice messages (item voice-engines): two engines, and local Whisper's setup.

`transcribe(file, settings)` turns one recording into `{"language": ..., "text": ...}` in the
language it was spoken in. `settings` is the `transcribe` setting's dict - `engine` and that
engine's own keys:

- `whisper`, local: `faster-whisper` in a Python environment of its own, `voice/venv` under the
  app's data folder, made by `agentloop voice setup`. It runs as a subprocess on the run script
  `tools/whisper-run.py`, which prints one line of JSON. The app's own environment stays
  standard-library-only, and the heavy native wheels (CTranslate2, PyAV, the CUDA libraries) live
  in that environment alone. Keys: `model` (default `large-v3`, or a folder holding a converted
  model), `device` (`auto`, `cuda` or `cpu`), `computeType` (default `float16` on cuda, `int8` on
  cpu) and `language` (a code such as `ru`; detected when left out).
- `openai`: a multipart POST to OpenAI's transcription endpoint over `urllib`. Keys: `model`
  (default `gpt-4o-transcribe`; `whisper-1` also), `keyFile` (the path of a file holding the API
  key - never the key) and `language`. The key is read only to be sent, and never printed or
  logged; an error names the exception type and the HTTP status, never the URL, the key or the
  answer's body (OpenAI's 401 answer quotes part of the key).

`language` in the result is the code Whisper detected or was given (`ru`), the name `whisper-1`
reports (`russian`), or `""` when the engine does not say - `gpt-4o-transcribe` never does.

`agentloop voice setup` makes the environment, pip-installs `faster-whisper` into it - and on
Windows and Linux with an NVIDIA GPU the CUDA 12 libraries (`nvidia-cublas-cu12`,
`nvidia-cudnn-cu12`) too - then downloads the model once into `voice/models`. Those libraries
are found where pip puts them, `site-packages/nvidia/*/bin` on Windows and `.../lib` on Linux:
this module puts them on the child's `PATH` or `LD_LIBRARY_PATH`, which Linux reads only when a
process starts, and the run script adds the Windows ones with `os.add_dll_directory` as well.
macOS has no CUDA: Whisper runs there on the CPU. `agentloop voice try <file>` transcribes one
file, so the owner can check the setup.

What the loop does with a transcript (item voice-loop) is here too: `cleanup_prompt`, the prompt
of the one-turn call that makes it clear English instructions, and `transcript_text`, what is
written under the recording.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from agentloop import config, tools

ENGINES = ("whisper", "openai")
WHISPER_MODEL = "large-v3"
OPENAI_MODEL = "gpt-4o-transcribe"
OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"
# The tests serve a fake endpoint on loopback; the override is honoured for a loopback address
# only, so that nothing can send the key anywhere but OpenAI.
OPENAI_URL_ENV = "AGENTLOOP_OPENAI_URL"
CUDA_PACKAGES = ("nvidia-cublas-cu12", "nvidia-cudnn-cu12")
# A long recording on the CPU, or the first load of a model, takes minutes; past this something
# is wrong.
WHISPER_TIMEOUT = 1800
OPENAI_TIMEOUT = 300


class VoiceError(Exception):
    """A transcription that could not be done, said without paths, URLs or keys."""


def voice_dir() -> Path:
    """Where local Whisper's environment and models are: `voice` in the app's data folder."""
    return config.paths().data / "voice"


def venv_dir() -> Path:
    return voice_dir() / "venv"


def models_dir() -> Path:
    return voice_dir() / "models"


def venv_python(venv: Path, system: str | None = None) -> Path:
    """The Python of a virtual environment, where `venv` puts it on this system."""
    if (system or config.this_system()) == "windows":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def run_script() -> Path:
    """The run script beside the other tools, in the repository or a build alike."""
    return tools.path("whisper-run.py")


def cuda_library_dirs(venv: Path, system: str | None = None) -> list[Path]:
    """The folders of the CUDA libraries pip put into the environment, for the loader to find."""
    system = system or config.this_system()
    if system == "windows":
        sites = [venv / "Lib" / "site-packages"]
        leaf = "bin"
    else:
        sites = sorted((venv / "lib").glob("python*/site-packages"))
        leaf = "lib"
    found: list[Path] = []
    for site in sites:
        found.extend(sorted(p for p in (site / "nvidia").glob(f"*/{leaf}") if p.is_dir()))
    return found


def transcribe(file: Path, settings: dict) -> dict:
    """One recording as `{"language", "text"}`, by the engine `settings["engine"]` names."""
    engine = (settings or {}).get("engine")
    if engine == "whisper":
        return _whisper(Path(file), settings)
    if engine == "openai":
        return _openai(Path(file), settings)
    raise VoiceError(f"no speech-to-text engine named {engine!r}; it is one of {', '.join(ENGINES)}")


def whisper_command(file: Path, settings: dict, python: Path) -> list[str]:
    cmd = [str(python), "-I", str(run_script()), str(file),
           "--model", str(settings.get("model") or WHISPER_MODEL),
           "--device", str(settings.get("device") or "auto"),
           "--models-dir", str(models_dir())]
    if settings.get("computeType"):
        cmd += ["--compute-type", str(settings["computeType"])]
    if settings.get("language"):
        cmd += ["--language", str(settings["language"])]
    return cmd


def _child_env(venv: Path) -> dict[str, str]:
    env = dict(os.environ)
    dirs = [str(d) for d in cuda_library_dirs(venv)]
    if dirs:
        name = "PATH" if config.this_system() == "windows" else "LD_LIBRARY_PATH"
        env[name] = os.pathsep.join(dirs + ([env[name]] if env.get(name) else []))
    # Hugging Face's hub, which faster-whisper downloads models through, reports nothing home.
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    return env


def _whisper(file: Path, settings: dict) -> dict:
    venv = venv_dir()
    python = venv_python(venv)
    if not python.is_file():
        raise VoiceError("whisper: local Whisper is not set up on this machine; run: agentloop voice setup")
    if not file.is_file():
        raise VoiceError("whisper: the recording is not there")
    try:
        done = subprocess.run(whisper_command(file, settings, python), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=_child_env(venv),
                              timeout=WHISPER_TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise VoiceError(f"whisper: took longer than {WHISPER_TIMEOUT} seconds") from None
    except OSError as error:
        raise VoiceError(f"whisper: could not start ({type(error).__name__})") from None
    if done.returncode != 0:
        # The run script's last line on stderr is the exception's type and message: it names no
        # key, and the recording's path is the scratch copy's at most.
        last = (done.stderr.strip().splitlines() or [f"exit code {done.returncode}"])[-1]
        raise VoiceError(f"whisper: {last}")
    return _result(done.stdout, "whisper")


def _result(text: str, engine: str) -> dict:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    try:
        got = json.loads(lines[-1])
    except (IndexError, ValueError):
        raise VoiceError(f"{engine}: the answer was not JSON") from None
    if not isinstance(got, dict) or not isinstance(got.get("text"), str):
        raise VoiceError(f"{engine}: the answer had no text")
    return {"language": str(got.get("language") or ""), "text": got["text"].strip()}


def openai_url() -> str:
    given = os.environ.get(OPENAI_URL_ENV)
    if given and urllib.parse.urlsplit(given).hostname in ("127.0.0.1", "localhost", "::1"):
        return given
    return OPENAI_URL


def _read_key(settings: dict) -> str:
    key_file = settings.get("keyFile")
    if not key_file:
        raise VoiceError("openai: the setting keyFile, the file holding the API key, is not set")
    try:
        key = config.expand(str(key_file)).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise VoiceError(f"openai: the key file could not be read ({type(error).__name__})") from None
    if not key:
        raise VoiceError("openai: the key file is empty")
    return key


def multipart(fields: dict[str, str], file: Path) -> tuple[bytes, str]:
    """A multipart/form-data body with `fields` and the recording as `file`, and its type."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    kind = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    # The name only tells the endpoint the format; quotes and line breaks are kept out of it.
    name = "".join(c for c in file.name if c not in '"\r\n\\') or "recording"
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                 f"Content-Type: {kind}\r\n\r\n".encode() + file.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _openai(file: Path, settings: dict) -> dict:
    model = str(settings.get("model") or OPENAI_MODEL)
    key = _read_key(settings)
    if not file.is_file():
        raise VoiceError("openai: the recording is not there")
    # whisper-1 says the language it heard only in verbose_json; the gpt-4o models answer json alone.
    fields = {"model": model, "response_format": "verbose_json" if model == "whisper-1" else "json"}
    if settings.get("language"):
        fields["language"] = str(settings["language"])
    try:
        body, kind = multipart(fields, file)
    except OSError as error:
        raise VoiceError(f"openai: the recording could not be read ({type(error).__name__})") from None
    request = urllib.request.Request(openai_url(), data=body, method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": kind})
    try:
        with urllib.request.urlopen(request, timeout=OPENAI_TIMEOUT) as answer:
            text = answer.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        raise VoiceError(f"openai: HTTPError {error.code}") from None
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", None)
        what = type(reason).__name__ if isinstance(reason, BaseException) else type(error).__name__
        raise VoiceError(f"openai: {what}") from None
    got = _result(text, "openai")
    if not got["language"] and settings.get("language"):
        got["language"] = str(settings["language"])
    return got


def wants_gpu(cpu: bool, system: str | None = None) -> bool:
    """Whether setup installs the CUDA libraries: on Windows and Linux with an NVIDIA driver."""
    system = system or config.this_system()
    return not cpu and system in ("windows", "linux") and shutil.which("nvidia-smi") is not None


def setup_commands(model: str, gpu: bool, system: str | None = None) -> list[list[str]]:
    """The commands `agentloop voice setup` runs, in order."""
    venv = venv_dir()
    python = venv_python(venv, system)
    cmds = []
    if not python.is_file():
        cmds.append([sys.executable, "-m", "venv", str(venv)])
    cmds.append([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    cmds.append([str(python), "-m", "pip", "install", "--upgrade", "faster-whisper", *(CUDA_PACKAGES if gpu else ())])
    cmds.append([str(python), "-I", str(run_script()), "--download-only",
                 "--model", model, "--models-dir", str(models_dir())])
    return cmds


def setup(model: str = WHISPER_MODEL, cpu: bool = False, dry_run: bool = False, out=print) -> int:
    """`agentloop voice setup`: each command shown, then run unless `dry_run`; the setting to use
    after. A pip install or a download that fails ends it with that command's exit code."""
    gpu = wants_gpu(cpu)
    out(f"local Whisper in {voice_dir()}, model {model}, on the {'GPU (CUDA)' if gpu else 'CPU'}")
    for cmd in setup_commands(model, gpu):
        out("$ " + (subprocess.list2cmdline(cmd) if config.this_system() == "windows" else shlex.join(cmd)))
        if dry_run:
            continue
        env = _child_env(venv_dir()) if cmd[0] != sys.executable else None
        code = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL).returncode
        if code != 0:
            out(f"agentloop voice setup: that command failed with exit code {code}")
            return code
    setting = {"engine": "whisper", "model": model, "device": "cuda" if gpu else "cpu"}
    out("")
    if dry_run:
        out("A dry run: nothing was run. Once it is, the transcribe setting for this machine: " + json.dumps(setting))
        return 0
    out("Done. The transcribe setting for this machine: " + json.dumps(setting))
    out("Try it on a recording: agentloop voice try <file>")
    return 0


# What the loop does with a transcript (item voice-loop): a one-turn Claude call, on the `voice`
# route, makes it clear English instructions, and that is what is written under the recording.

CLEANUP_RULES = """\
You turn a voice message into written instructions for the agents who work the items of a project's tracker. \
A person recorded it in a comment on one of the items, and a speech-to-text engine wrote down what they said, \
in the language they said it in. What you answer is written under the recording, for the agents and the people \
of the project to read in its place.

Write it as clear English instructions, in the speaker's own voice, as they would have written the comment:
- A voice message is wordy. Leave out the filler, the false starts and what has nothing to do with the work; \
say once what was said several times; and put the parts in an order that reads well, what belongs together together.
- Leave out nothing that matters. Every request, decision, detail, condition, doubt and question stays - and so \
do praise, thanks and comments on work already done, which tell the agents what to keep doing.
- Add nothing the speaker did not say: no plan, no suggestion, no answer to their questions, and no guess at what \
they meant where it is unclear - say that it is unclear.
- Keep item handles, people's names, file names, commands and code words as they were said, code words in \
backticks. An item named aloud by its handle is written <#handle>, and a Discussion topic <:handle>.
- Never write an @ before a name: a mention decides who an item is for, and only the speaker may make one.
- The engine may have misheard. Put right a word it plainly got wrong where the project, the item or the people \
below make the right one clear.

Answer with the text alone: no heading, no preamble, and no note on what you did."""


def cleanup_prompt(*, description: str, handle: str, title: str, before: list[dict], comment: str,
                   people: list[dict], heard: dict, author: str) -> str:
    """The prompt of the call that cleans a transcript up: the rules, then what the speaker had in
    front of them - the project, the item and its conversation up to the comment the recording is
    in, and who may be named - then what the engine heard. `before` is the comments before that
    one, as `show --json` gives them; `comment` is that one's own text, the recording and all."""
    parts = [CLEANUP_RULES, "", "## The project", "", description.strip() or "(no description)", "",
             f"## The item: {handle} - {title}", ""]
    if before:
        parts.append("Its conversation before the voice message, oldest first:")
        for c in before:
            parts += ["", f"--- {c.get('at', '')} {c.get('author', '')}", str(c.get("body") or "").strip()]
    else:
        parts.append("Nothing was said on it before the voice message.")
    parts += ["", f"## The comment the recording is in, by {author}", "", comment.strip(), ""]
    if people:
        parts += ["## The people of the project's server (username: display name)", ""]
        parts += [f"- {p.get('username')}: {p.get('name') or p.get('username')}" for p in people]
        parts.append("")
    language = heard.get("language") or "not said"
    parts += [f"## The voice message, as the engine heard it (language: {language})", "", str(heard.get("text") or "").strip(), ""]
    return "\n".join(parts)


def transcript_text(english: str) -> str:
    """What is written under a recording: the English instructions alone. What was said word for
    word is not kept beside them (item voice-tweaks): the cleanup is there to give the session clear
    instructions, and the raw words under them would bring back what it left out."""
    return english.strip()
