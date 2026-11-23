"""Transcribes one recording with faster-whisper, and prints `{"language", "text"}` as one line of
JSON (item voice-engines).

It runs in local Whisper's own Python environment, which `agentloop voice setup` makes, never in
the app's: `agentloop.voice` starts it as a subprocess. With `--download-only` it only downloads
the model into the models folder, as setup's last step. A failure ends it with exit code 1 and the
exception's type and message as the last line on stderr.

The CUDA libraries pip installs (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) are in
`site-packages/nvidia/*/bin` on Windows, where nothing looks for them: they are added to the DLL
search path, and to PATH, before CTranslate2 is imported. On Linux the loader reads
LD_LIBRARY_PATH only when a process starts, so the app sets it for this one.
"""

import argparse
import json
import os
import sys
import sysconfig
from pathlib import Path


def add_cuda_dlls() -> None:
    if sys.platform != "win32":
        return
    site = Path(sysconfig.get_path("purelib"))
    for folder in sorted((site / "nvidia").glob("*/bin")):
        os.add_dll_directory(str(folder))
        os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")


def resolve(device: str, compute_type: str | None) -> tuple[str, str]:
    """The device, `auto` made `cuda` when CTranslate2 sees a GPU and `cpu` otherwise, and the
    compute type: float16 on a GPU, int8 on the CPU, unless one was given."""
    if device == "auto":
        import ctranslate2

        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    return device, compute_type or ("float16" if device == "cuda" else "int8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe one recording with faster-whisper.")
    parser.add_argument("file", nargs="?", help="the recording")
    parser.add_argument("--model", default="large-v3", help="a Whisper model's name, or a converted model's folder")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--compute-type", help="float16, int8_float16, int8, float32 ...")
    parser.add_argument("--language", help="the language's code; detected when left out")
    parser.add_argument("--models-dir", required=True, help="where models are downloaded to and kept")
    parser.add_argument("--download-only", action="store_true", help="download the model, and transcribe nothing")
    args = parser.parse_args()
    if not args.download_only and not args.file:
        parser.error("the recording is needed")
    add_cuda_dlls()
    if args.download_only:
        from faster_whisper import download_model

        download_model(args.model, cache_dir=args.models_dir)
        print(json.dumps({"downloaded": args.model}))
        return 0
    from faster_whisper import WhisperModel

    device, compute_type = resolve(args.device, args.compute_type)
    model = WhisperModel(args.model, device=device, compute_type=compute_type, download_root=args.models_dir)
    # The voice activity filter drops the silences, where Whisper is known to make words up.
    segments, info = model.transcribe(args.file, language=args.language, beam_size=5, vad_filter=True)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    print(json.dumps({"language": info.language, "text": text, "device": device, "computeType": compute_type},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # It runs with -I, which ignores PYTHONIOENCODING; the text is in any language, and Windows'
    # console code page would refuse most of them.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 - every failure is reported the same way
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
