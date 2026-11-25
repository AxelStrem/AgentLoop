#!/bin/sh
# Makes sure claude is installed in the user's home, in the /data volume (item docker-claude-runtime).
# The image carries no claude: on the first start - or whenever it is missing - this installs it with
# Anthropic's native installer into ~/.local (~/.local/bin/claude, its versions in
# ~/.local/share/claude), and claude updates itself there from then on. A claude that is there and
# runs is left as it is. Run by the entrypoint as the user, never as root.
#
# The installer is downloaded to a file and run from there rather than piped to a shell, so that a
# download cut short is never half run; it checks the binary it fetches against Anthropic's
# manifest itself. Each try that fails is retried after a longer wait, and when every one has failed
# this says so and ends with 1: the entrypoint starts serve anyway, sessions report that claude is
# not found, and the next start tries again.
#
#   CLAUDE_INSTALL_WAITS   the seconds to wait before each retry; "5 20" when unset

set -u

say() { printf 'agentloop-entrypoint: %s\n' "$*"; }
warn() { printf 'agentloop-entrypoint: %s\n' "$*" >&2; }

installer_url=https://claude.ai/install.sh
claude="$HOME/.local/bin/claude"

if [ -x "$claude" ] && version="$("$claude" --version 2>/dev/null)"; then
    say "claude $version in $HOME/.local, updating itself there"
    exit 0
fi

say "claude is not in $HOME/.local: installing it (about 200 MB, once)"
mkdir -p "$HOME" || exit 1
work="$(mktemp -d "$HOME/.claude-install.XXXXXX")" || { warn "could not make a folder to install claude from"; exit 1; }
trap 'rm -rf "$work"' EXIT

try() {
    curl -fsSL --retry 2 -o "$work/install.sh" "$installer_url" \
        || { warn "claude's installer could not be downloaded (curl ended with $?)"; return 1; }
    bash "$work/install.sh" \
        || { warn "claude's installer failed (it ended with $?)"; return 1; }
    [ -x "$claude" ] \
        || { warn "claude's installer ended, but left no claude in $HOME/.local/bin"; return 1; }
}

waits="${CLAUDE_INSTALL_WAITS-5 20}"
if try; then
    installed=1
else
    installed=0
    for wait in $waits; do
        say "trying again in $wait s"
        sleep "$wait"
        if try; then
            installed=1
            break
        fi
    done
fi

if [ "$installed" = 1 ] && version="$("$claude" --version 2>/dev/null)"; then
    say "claude $version installed in $HOME/.local"
    exit 0
fi
warn "claude could not be installed: sessions fail with \"claude not found\" until it is. The next start tries again."
exit 1
