#!/bin/sh
# The AgentLoop image's entry point (item docker-image). Started as root, it makes the user PUID:PGID
# (Unraid's 99:100 when unset), installs EXTRA_PACKAGES, sets git up from the variables below, and
# then runs the command - `agentloop serve --restart-loops` - as that user. Claude Code refuses
# --dangerously-skip-permissions as root, and what sessions write to the mounted clones must belong
# to the person, not to root. Started as another user already (`docker run --user`), it does none of
# this and runs the command as it is.
#
#   PUID, PGID         the user and group to run as; 99 and 100 when unset. Never 0.
#   EXTRA_PACKAGES     Debian packages a project needs beyond the image's, installed at each start
#   GIT_USER_NAME      git's user.name, for the loops' commits
#   GIT_USER_EMAIL     git's user.email
#   GIT_TOKEN          a token for HTTPS remotes, kept in ~/.git-credentials (mode 600)
#   GIT_TOKEN_HOST     the host it is for; github.com when unset
#   GIT_TOKEN_USER     the user name sent with it; x-access-token when unset, which GitHub takes
#   AGENTLOOP_BREW     1 (the image's): Homebrew at its prefix, filled from the image's copy when
#                      empty, and given to the user (item brew-tools)
#
# An SSH key needs nothing here: put it in /data/home/.ssh, with a known_hosts beside it.
# No secret is printed.

set -eu

if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

say() { printf 'agentloop-entrypoint: %s\n' "$*"; }
fail() { printf 'agentloop-entrypoint: %s\n' "$*" >&2; exit 1; }

PUID="${PUID:-99}"
PGID="${PGID:-100}"
case "$PUID$PGID" in
    *[!0-9]*|"") fail "PUID and PGID must be numbers." ;;
esac
[ "$PUID" -ne 0 ] || fail "PUID is 0: Claude Code refuses to run its sessions as root. Give another user's id."
HOME=/data/home
export HOME

# The group and user, by these ids: an existing one is taken as it is (gid 100 is Debian's users).
group="$(getent group "$PGID" | cut -d: -f1 || true)"
if [ -z "$group" ]; then
    group=agentloop
    groupadd -g "$PGID" "$group"
fi
user="$(getent passwd "$PUID" | cut -d: -f1 || true)"
if [ -z "$user" ]; then
    user=agentloop
    useradd -u "$PUID" -g "$PGID" -d "$HOME" -M -s /bin/sh "$user"
fi

if [ -n "${EXTRA_PACKAGES:-}" ]; then
    say "installing $EXTRA_PACKAGES"
    apt-get update -qq || fail "apt-get update failed, so EXTRA_PACKAGES could not be installed."
    # Split on spaces on purpose: one package a word.
    # shellcheck disable=SC2086
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $EXTRA_PACKAGES \
        || fail "EXTRA_PACKAGES could not be installed: see apt-get's message above."
fi

# /data is the app's alone; the clones are mounted elsewhere and never chowned here. A /data last
# used by another user - PUID changed - is given to this one whole, so the app can read its own.
mkdir -p "$HOME"
if [ "$(stat -c %u /data)" != "$PUID" ]; then
    say "/data belongs to another user: giving it to $PUID:$PGID"
    chown -R "$PUID:$PGID" /data
fi
chown "$PUID:$PGID" "$HOME"

# Homebrew's prefix is a volume of its own, shared by every project. It is filled once, and given
# whole to the user when PUID changes, as /data is; brew refuses to run as root.
if [ "${AGENTLOOP_BREW:-}" = "1" ]; then
    prefix="${HOMEBREW_PREFIX:-/home/linuxbrew/.linuxbrew}"
    mkdir -p "$prefix"
    if command -v mountpoint >/dev/null && ! mountpoint -q "$prefix"; then
        say "$prefix is not a volume: what Homebrew installs is lost when the container is recreated"
    fi
    if [ ! -x "$prefix/bin/brew" ]; then
        say "putting Homebrew in $prefix"
        cp -a /opt/homebrew-seed/. "$prefix/"
        chown -R "$PUID:$PGID" "$prefix"
    elif [ "$(stat -c %u "$prefix")" != "$PUID" ]; then
        say "$prefix belongs to another user: giving it to $PUID:$PGID"
        chown -R "$PUID:$PGID" "$prefix"
    fi
fi

as_user() { gosu "$PUID:$PGID" env HOME="$HOME" "$@"; }

[ -z "${GIT_USER_NAME:-}" ] || as_user git config --global user.name "$GIT_USER_NAME"
[ -z "${GIT_USER_EMAIL:-}" ] || as_user git config --global user.email "$GIT_USER_EMAIL"
# The clones are mounted from the host, and may belong to another id than this one.
as_user git config --global --replace-all safe.directory '*'
if [ -n "${GIT_TOKEN:-}" ]; then
    as_user git config --global credential.helper store
    # Written by the user, readable by it alone; the token never reaches a command line.
    as_user sh -c 'umask 077 && printf "https://%s:%s@%s\n" "${GIT_TOKEN_USER:-x-access-token}" "$GIT_TOKEN" "${GIT_TOKEN_HOST:-github.com}" > "$HOME/.git-credentials"'
fi

exec gosu "$PUID:$PGID" env HOME="$HOME" "$@"
