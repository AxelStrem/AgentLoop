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

## What a loop does with them

A comment holding a recording marked for transcription keeps its item back - unheard, whoever it
is for, and flagged `manual` or not - until the recording is transcribed (`tracker.py sounds`
lists those recordings, `next` names their items in `unheard`). On an item flagged `asked`, or at
`testing`, `later` or `parts`, it waits untranscribed until the item is back in the queue. The mark is the "request transcription" box on the recording's
player on the tracker page, which is ticked on a recording made on the page and unticked on an
audio file dropped in; it is written into the comment as `![transcribe](sounds/...)`. A sound
without it is not an instruction, and holds nothing. Once transcribed the mark reads
`![transcribed](...)`. A loop whose `transcribe` setting names an engine transcribes them before it takes an
item, and again while it waits for work with `autoStart` on, once the tracker changes. That
includes an item taken without a pick: the build a triage goes on to in the same session, and the
item a session asks to go on to. Everything waiting is heard first, for the loops that cannot
transcribe, and only then is work taken. When the item a triage left ready was itself given a
voice message meanwhile, that message is heard with the rest, but the item is not built in that
session, which has not read it; the queue offers it again.

1. `tracker.py sounds --fetch` fetches the recordings into the loop's folder.
2. The engine turns each one into text, in the language it was spoken in.
3. A one-turn Claude call with no tools, on the `voice` route (sonnet/medium unless the settings
   say otherwise), is given the project's description, the item and its conversation up to that
   comment, the people the server knows, and the raw text. It writes clear English instructions:
   it leaves out filler and repeats, and puts the parts in order. It keeps everything that matters,
   praise and comments on earlier work included. It adds nothing, keeps handles and names as they
   were said, and writes no @mention.
4. `tracker.py heard` writes that text under the recording, and only that text: what was said
   word for word is not kept, as it would bring back what the cleanup left out.
5. The loop's output says so, and the item gets a work record of kind `voice` with the call's
   model and cost, which shows in Stats.

While its recordings are transcribed the item is claimed, marked `working` as a session's item
is, so that no other loop transcribes or works it meanwhile, and let go of after. That claim does
not make the item this loop's: who it is for stays as it was, and it is not moved or committed. An
item another loop has claimed already is left alone, and transcribed once it is free. Once its
recordings are transcribed, the item is free to whichever loop it is for, this one or another. When something
fails (the engine is not set up, its key is missing, the cleanup call fails), the loop says so
once, in its output and in its row on the page. It leaves that item's recordings for the rest of
the run and goes on with the others. A usage limit leaves them until the limit is waited out.

With `transcribe` off, the default, a loop never transcribes. Unheard items stay held, for a
loop on another machine to transcribe. The page's row for the loop then says how many items wait,
and that transcription is off on it.

### A loop that only transcribes

A loop that transcribes takes the next item once it is done, and that may be the item it has just
transcribed, when no one is in its `for` column. With `transcribeOnly` on, a loop only transcribes:
it never takes an item into work, runs a session or commits. It waits as an idle loop does, looking
every `autoStartMinutes` and as the tracker changes, whatever `autoStart` says, and transcribes what
it finds, on items for any account, until it is stopped. It runs no setup script and takes no seat
on the bench, since it never takes work. It never writes to its repositories, so it may share them
with a working loop of the same project. A loop with `transcribeOnly` on and `transcribe` off
refuses to start.

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
