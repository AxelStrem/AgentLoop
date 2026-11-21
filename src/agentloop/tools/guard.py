"""Refuse what the agent loop must never do, whatever permission mode it runs in.

A PreToolUse hook (see loop-settings.json). Reads the tool call as JSON on stdin and exits 2 with a
reason - which blocks the call and tells the agent why - when it would:

  - push or commit with git: the loop commits each session itself, and nothing is pushed;
  - read a .env file (.env.example is allowed): it holds a project's secrets;
  - read the key the agents reach the server with - the file OVERSEER_KEY_FILE names - or reach
    for it through that variable: tracker.py reads it, and nothing else needs it;
  - where AGENTLOOP_BREW is 1, change what Homebrew has installed other than through the project's
    Brewfile, which is the one record of a project's tools (item brew-tools).

Deny rules alone are prefix matches: `git -C <repo> push` or `cat .env` walk past them. This looks
anywhere in a shell command, and at every path a file tool is given.
"""
import json
import re
import sys

GIT = re.compile(r'''\bgit(?:\.exe)?\b(?:\s+(?:-C\s+(?:"[^"]*"|'[^']*'|\S+)|-c\s+\S+|--?[\w-]+(?:=\S+)?))*\s+(push|commit)(?![\w-])''', re.I)
# A .env file by name - not .env.example, not process.env, not $env:, not .envrc.
ENV = re.compile(r'(?<![\w$])\.env(?!\.example\b)(?:\.[\w-]+)?(?![\w-])', re.I)
# `brew bundle install` is allowed: it installs what the Brewfile lists.
BREW = re.compile(r'\bbrew\s+(install|reinstall|upgrade|uninstall|remove|rm|tap|untap|link|unlink)(?![\w-])')


def names_the_key(text):
    """Whether text names the agents' key: its file, by its path or its name, or the variable that
    says where it is - which a shell would turn into the path past any check of the path itself."""
    import os
    text = str(text).replace('\\', '/').lower()
    if 'overseer_key_file' in text:
        return True
    key = os.environ.get('OVERSEER_KEY_FILE', '').replace('\\', '/').lower()
    return bool(key) and (key in text or key.rsplit('/', 1)[-1] in text)


def main():
    try:
        call = json.loads(sys.stdin.buffer.read().decode('utf-8', 'replace'))
    except Exception:
        # Unreadable is refused rather than waved through: a guard that fails open guards nothing.
        refuse('the guard could not read this tool call, so it is refused.')
    tool = call.get('tool_name', '')
    given = call.get('tool_input') or {}
    if tool in ('Bash', 'PowerShell'):
        command = str(given.get('command', ''))
        hit = GIT.search(command)
        if hit:
            refuse(f'git {hit.group(1).lower()} is not for the agent: the loop commits each session itself, and nothing is pushed.')
        if ENV.search(command):
            refuse('.env files are not to be read or touched - they hold secrets.')
        if names_the_key(command):
            refuse('the agents\' key is not to be read: tracker.py reaches the server with it - use tracker.py.')
        import os
        hit = BREW.search(command) if os.environ.get('AGENTLOOP_BREW') == '1' else None
        if hit:
            refuse(f'brew {hit.group(1)} is not for the agent: add the tool to the project\'s Brewfile and run '
                   'brew bundle install --no-upgrade --file=<that Brewfile>.')
    else:
        for key in ('file_path', 'path', 'glob', 'notebook_path', 'pattern'):
            if ENV.search(str(given.get(key, ''))) and key != 'pattern':
                refuse('.env files are not to be read - they hold secrets.')
            if names_the_key(given.get(key, '')):
                refuse('the agents\' key is not to be read: tracker.py reaches the server with it - use tracker.py.')


def refuse(reason):
    print(reason, file=sys.stderr)
    sys.exit(2)


if __name__ == '__main__':
    main()
