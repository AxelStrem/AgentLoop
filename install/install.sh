#!/bin/sh
# Installs AgentLoop on macOS or Linux (item installer), or upgrades it when run again:
#
#     curl -LsSf https://github.com/AxelStrem/AgentLoop/releases/latest/download/install.sh | sh
#
# It installs uv (https://docs.astral.sh/uv/) if it is missing, which brings its own Python, and
# then the release's wheel as a uv tool, and starts `agentloop serve --open`. It checks for git and
# claude and says how to get them, but installs nothing else, and never asks for or reads a key.
#
# The release workflow writes the release's version over the placeholder below. Beside it:
#   AGENTLOOP_WHEEL     a wheel's path or address to install instead of the release's
#   AGENTLOOP_NO_SERVE  set: install, but do not start the page
#   AGENTLOOP_LOGIN_START  yes: start the page at login without asking; no: neither ask nor start it
#   UV_NO_MODIFY_PATH   set: change no shell profile, neither uv's installer nor this script

set -eu

VERSION="@VERSION@"

say() { printf 'agentloop: %s\n' "$*"; }
fail() { printf 'agentloop: %s\n' "$*" >&2; exit 1; }
has() { command -v "$1" >/dev/null 2>&1; }

# uv's installer puts it in one of these, and the shell running this script has not read the new
# PATH. Each is tried in turn rather than listed and split, so that a home with a space works.
try_uv() {
    if [ -x "$1" ]; then
        printf '%s\n' "$1"
        return 0
    fi
    return 1
}

find_uv() {
    if has uv; then
        command -v uv
        return 0
    fi
    if [ -n "${UV_INSTALL_DIR:-}" ]; then
        try_uv "$UV_INSTALL_DIR/bin/uv" && return 0
        try_uv "$UV_INSTALL_DIR/uv" && return 0
    fi
    if [ -n "${XDG_BIN_HOME:-}" ]; then
        try_uv "$XDG_BIN_HOME/uv" && return 0
    fi
    try_uv "$HOME/.local/bin/uv" && return 0
    try_uv "$HOME/.cargo/bin/uv" && return 0
    return 1
}

# Starting the page at login brings back, after a restart, the loops nobody ended (item
# loginstart). Asked once: a login start already there is left as it is, since it may run a build
# rather than this install. Under `curl | sh` the script is standard input, so the answer is read
# from the terminal; with none, nothing is asked.
offer_login_start() {
    found=0
    "$1" login-start status >/dev/null 2>&1 || found=$?
    case $found in
        0) say "the page starts at login already: 'agentloop login-start off' stops that."; return 0 ;;
        1) ;;
        *) return 0 ;;
    esac
    answer="${AGENTLOOP_LOGIN_START:-}"
    if [ -z "$answer" ]; then
        if [ -t 1 ] && (: </dev/tty) 2>/dev/null; then
            printf 'agentloop: Start the page at login, so loops with Auto start come back after a restart? [Y/n] '
            read -r answer </dev/tty || answer=no
            [ -n "$answer" ] || answer=yes
        else
            say "to start the page at login, so that loops with Auto start come back after a restart: agentloop login-start on"
            return 0
        fi
    fi
    case $answer in
        [Yy]|[Yy][Ee][Ss]|1|true|on)
            "$1" login-start on || say "the page could not be set to start at login: see above." ;;
    esac
}

# The script is all functions until its last line, so that a download cut short under `curl | sh`
# runs nothing.
main() {
    wheel="${AGENTLOOP_WHEEL:-}"
    if [ -z "$wheel" ]; then
        case "$VERSION" in
            @*) fail "this script names no release: fetch it from a release, or set AGENTLOOP_WHEEL to a wheel" ;;
        esac
        wheel="https://github.com/AxelStrem/AgentLoop/releases/download/v$VERSION/agentloop-$VERSION-py3-none-any.whl"
    fi

    # What a loop needs beside AgentLoop, which this script does not install.
    if ! has git; then
        say "git is not on PATH: install it from https://git-scm.com/downloads or your package manager."
    fi
    if ! has claude; then
        say "claude (Claude Code) is not on PATH: see https://docs.anthropic.com/en/docs/claude-code/setup,"
        say "then log in to each account a loop will use."
    fi

    uv=$(find_uv || true)
    if [ -z "$uv" ]; then
        say "installing uv, which brings its own Python"
        if has curl; then
            curl -LsSf https://astral.sh/uv/install.sh | sh
        elif has wget; then
            wget -qO- https://astral.sh/uv/install.sh | sh
        else
            fail "neither curl nor wget is on PATH to fetch uv: see https://docs.astral.sh/uv/getting-started/installation/"
        fi
        uv=$(find_uv || true)
        [ -n "$uv" ] || fail "uv was installed, but it is not where its installer puts it: open a new terminal and run this again"
    fi

    if "$uv" tool list 2>/dev/null | grep -q '^agentloop '; then
        say "AgentLoop is installed already: this upgrades it."
        say "A loop that is running reads the installed files on every step. Stop the loops first, or run"
        say "them from a build that upgrades do not touch (agentloop build --to <path>; see docs/install.md)."
    fi

    # --force replaces an earlier install, which is the upgrade: uv tool upgrade keeps a tool installed
    # from an address at the address it came from.
    say "installing $wheel"
    "$uv" tool install --force --python ">=3.11" "$wheel"

    bin=$("$uv" tool dir --bin)
    agentloop="$bin/agentloop"
    [ -x "$agentloop" ] || fail "uv installed AgentLoop, but $agentloop is not there"

    if [ -z "${UV_NO_MODIFY_PATH:-}" ]; then
        "$uv" tool update-shell >/dev/null 2>&1 || true
        say "agentloop is in $bin: a new terminal finds it on PATH."
    else
        say "agentloop is in $bin: add it to PATH to run it by name."
    fi

    "$agentloop" --version

    offer_login_start "$agentloop"

    if [ -n "${AGENTLOOP_NO_SERVE:-}" ]; then
        exit 0
    fi
    say "starting the page: Ctrl+C stops it, and 'agentloop serve --open' starts it again."
    exec "$agentloop" serve --open
}

main "$@"
