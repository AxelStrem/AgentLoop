"""The script's colours as ANSI codes, and when they are written."""

import io

from agentloop import colours


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_a_line_in_its_colour():
    assert colours.ansi("Stopping as asked.", "Green") == "\x1b[92mStopping as asked.\x1b[0m"
    assert colours.ansi("  committed", "DarkGreen") == "\x1b[32m  committed\x1b[0m"
    # No colour, or nothing to colour: as it is.
    assert colours.ansi("plain") == "plain" and colours.ansi("", "Red") == ""
    assert colours.plain("plain", "Red") == "plain"
    assert colours.strip(colours.ansi("x", "Cyan")) == "x"


def test_every_colour_the_script_uses_has_a_code():
    for name in ("Red", "Yellow", "Cyan", "DarkGray", "DarkRed", "DarkCyan", "DarkGreen", "DarkYellow", "Green"):
        assert name in colours.CODES


def test_when_they_are_written(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert colours.wanted("always", io.StringIO())
    assert not colours.wanted("never", Terminal())
    assert not colours.wanted("auto", io.StringIO())
    monkeypatch.setenv("NO_COLOR", "1")
    assert not colours.wanted("auto", Terminal())
    assert colours.wanted("always", io.StringIO())
