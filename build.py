"""`python build.py <path>`: the same as `agentloop build --to <path>`, from a clone that has
not been installed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from agentloop.cli import main  # noqa: E402

args = sys.argv[1:]
if args and not args[0].startswith("-"):
    args = ["--to", *args]
sys.exit(main(["build", *args]))
