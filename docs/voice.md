# Voice messages: speech to text

Overseer's page can record a voice message into a comment. Claude can't take audio, so a loop
first turns the recording into text with a speech-to-text engine. Then a short Claude call cleans
that text up into English instructions, and the result is written under the recording. This page
covers the engines and how to set them up. Which engine a loop uses comes from the `transcribe`
setting, whose keys are listed below.

There are two engines:

| engine    | where it runs              | needs                                    | the recording goes to |
|-----------|----------------------------|------------------------------------------|-----------------------|
| `whisper` | this machine, GPU or CPU   | `agentloop voice setup`, once            | nowhere               |
| `openai`  | OpenAI's servers           | an OpenAI API key in a key file          | OpenAI                |

**Privacy:** with `openai`, every voice message is sent to OpenAI. With `whisper`, nothing leaves
the machine except the model's download, made once during setup.

## Local Whisper

Whisper runs through [faster-whisper](https://github.com/SYSTRAN/faster-whisper) in a Python
environment of its own, under the app's data folder in `voice/venv`. Its models are kept in
`voice/models`. The app's own environment stays standard-library-only, so CTranslate2, PyAV and
the CUDA libraries go into that environment and nowhere else. Removing the `voice` folder removes
all of it.

The data folder is `%LOCALAPPDATA%\agentloop` on Windows, `~/Library/Application Support/agentloop`
on macOS, `~/.local/state/agentloop` on Linux, and `$AGENTLOOP_HOME/data` when that is set.

### Setting it up

```
agentloop voice setup                  # large-v3, on the GPU where there is one
agentloop voice setup --model large-v3-turbo
agentloop voice setup --cpu            # no CUDA libraries
agentloop voice setup --dry-run        # print the commands, run none
```

Setup does four things, printing each command before it runs it:

1. Creates the environment with the Python the app runs on (`python -m venv`).
2. Upgrades pip in it.
3. Installs `faster-whisper`. On **Windows and Linux** with an NVIDIA driver (`nvidia-smi` on
   `PATH`), it also installs `nvidia-cublas-cu12` and `nvidia-cudnn-cu12`, the CUDA 12 and cuDNN 9
   libraries CTranslate2 needs. A separate CUDA toolkit is not needed, only the NVIDIA driver.
4. Downloads the model into `voice/models`. `large-v3` is about 3 GB.

You can run setup again at any time: it upgrades the packages and fetches only a model it does
not have yet.

**On each system:**

- **Windows:** use an NVIDIA driver recent enough for CUDA 12. The CUDA libraries are found in
  the environment's `site-packages/nvidia/*/bin`, with nothing to add to `PATH` by hand.
- **Linux:** the same, from `site-packages/nvidia/*/lib`. The app sets `LD_LIBRARY_PATH` for the
  transcription process. On a machine without an NVIDIA GPU, setup leaves the CUDA libraries out
  by itself.
- **macOS:** there is no CUDA, so Whisper runs on the CPU with `int8`. Apple Silicon handles
  `small` or `medium` comfortably on short memos. `large-v3` works too, only slower.

### The models

| model            | parameters | GPU memory (float16) | notes                                         |
|------------------|-----------:|---------------------:|-----------------------------------------------|
| `large-v3`       | 1.55 B     | about 4-5 GB         | the largest and most accurate; the default    |
| `large-v3-turbo` | 0.8 B      | about 2-3 GB         | several times faster, a little less accurate outside English |
| `medium`         | 0.77 B     | about 2-3 GB         |                                               |
| `small`          | 0.24 B     | about 1 GB           | fine on a CPU                                 |

On an RTX 3090 (24 GB), `large-v3` in float16 fits with plenty of room to spare, and a short
memo takes seconds.

### Its settings

The `transcribe` setting holds `engine` plus the engine's own keys:

```json
"transcribe": {"engine": "whisper", "model": "large-v3", "device": "cuda"}
```

- `model`: a Whisper model's name, as above, or the folder of a converted CTranslate2 model.
  Default `large-v3`.
- `device`: `cuda`, `cpu` or `auto` (the GPU when CTranslate2 sees one). Default `auto`.
- `computeType`: default `float16` on `cuda` and `int8` on `cpu`. `int8_float16` halves the GPU
  memory again.
- `language`: the language's code, such as `ru`. When left out, Whisper detects it from the first
  seconds.

## OpenAI

```json
"transcribe": {"engine": "openai", "model": "gpt-4o-transcribe", "keyFile": "~/keys/openai.key"}
```

- `keyFile`: the path of a file that holds the API key and nothing else. The setting holds the
  path, never the key. The key is read only to be sent to OpenAI, and is never printed or logged.
  An error names only the exception type and the HTTP status.
- `model`: `gpt-4o-transcribe` (default), `gpt-4o-mini-transcribe` or `whisper-1`.
- `language`: as above. It is optional, but it helps with short messages.

OpenAI takes recordings of up to 25 MB in flac, mp3, mp4, m4a, ogg, wav or webm. The
`gpt-4o-*` models do not say which language they heard. `whisper-1` does, by its name
(`russian`).

## Trying it

```
agentloop voice try memo.webm
agentloop voice try memo.webm --engine openai --key-file ~/keys/openai.key --language ru
```

This prints the language (`(not said)` when the engine does not report it) and then the text.
The flags take the place of the setting's keys: `--model`, `--device`, `--compute-type`,
`--language` and `--key-file`.
