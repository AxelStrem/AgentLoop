"""What a loop run did and where it stumbled, read from its logs.

    python review-run.py overseer                    the latest run
    python review-run.py overseer 20261008-213004    a run, by when it started
    python review-run.py overseer --all              every run in the logs

    --logs <folder>   the project's logs, where they are not logs/<project> beside this file:
                      the app keeps them in its data folder
    --tracker <doc>   the tracker, a document on the server OVERSEER_URL names; without it,
                      where each item is now is left out

For each session: the item, the kind of session, the model, turns, minutes and cost, and where the
item went. Then each item: its sessions, its cost in all and by model, the tokens and the lines of
code they took, and where the item stands now in the tracker - sent back, if it went to testing
and is at ready or planning again. Then every tool call that failed, and the trouble a shell reports even when its command
went through - a command not found, a syntax error, a path a Windows program could not find, git
run outside a repository - grouped by kind, each with the session it was in, so a pattern across
sessions shows as one.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

LOGS = Path(__file__).resolve().parent / 'logs'
NAME = re.compile(r'loop-(\d{8}-\d{6})-(\d+)(?:-(\w+))?\.jsonl$')
# Trouble a shell reports, most telling first: the first that matches names it.
TROUBLE = [
    ('tracker refused', re.compile(r'waiting on (a person|the owner) - leave it|is flagged discuss|goes to ready with its complexity|is for a person to pick')),
    ('git outside a repository', re.compile(r'not a git repository')),
    ('command not found', re.compile(r'command not found|is not recognized as the name of a cmdlet')),
    ('shell syntax error', re.compile(r'syntax error|unexpected EOF|ParserError|unexpected token')),
    ('path not found', re.compile(r'No such file or directory|FileNotFoundError|cannot find (the )?path|Could not find')),
    ('bad arguments', re.compile(r'unrecognized arguments|invalid choice|the following arguments are required')),
    ('edit did not match', re.compile(r'String to replace not found|Found \d+ matches of the string')),
    ('timed out', re.compile(r'timed out|Command running in background')),
]


def kind_of(text):
    return next((name for name, pattern in TROUBLE if pattern.search(text)), None)


def text_of(content):
    if isinstance(content, str):
        return content
    return ' '.join(part.get('text', '') for part in content or [] if isinstance(part, dict))


def one_line(text, width):
    text = ' '.join(str(text).split())
    return text if len(text) <= width else text[:width - 3] + '...'


def read_session(path):
    session = {'model': '?', 'id': None, 'turns': 0, 'ms': 0, 'cost': 0.0, 'calls': 0, 'failed': [], 'trouble': []}
    asked = {}
    for line in path.open(encoding='utf-8'):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get('type')
        if kind == 'system' and event.get('subtype') == 'init':
            session['model'] = event.get('model', '?')
            session['id'] = event.get('session_id')
        elif kind == 'assistant':
            for block in event['message'].get('content', []):
                if block.get('type') == 'tool_use':
                    session['calls'] += 1
                    given = block.get('input', {})
                    what = given.get('command') or given.get('file_path') or given.get('pattern') or json.dumps(given)
                    asked[block['id']] = (block['name'], what)
        elif kind == 'user':
            content = event['message'].get('content')
            for block in content if isinstance(content, list) else []:
                if block.get('type') != 'tool_result':
                    continue
                tool, what = asked.get(block.get('tool_use_id'), ('?', ''))
                said = text_of(block.get('content'))
                trouble = kind_of(said)
                if block.get('is_error'):
                    session['failed'].append((trouble or 'other', tool, what, said))
                elif trouble and tool in ('Bash', 'PowerShell'):
                    session['trouble'].append((trouble, tool, what, said))
        elif kind == 'result':
            # A log holds a result for each time the session stopped - a background task's end
            # wakes it again, a usage limit stops it - and each says what the whole session has
            # used so far, the triage a build was resumed from included. The most any says is where
            # this log's part ends; main takes off what the session's earlier part had used.
            session['turns'] += event.get('num_turns', 0)
            session['ms'] += event.get('duration_ms', 0)
            session['cumulative'] = max(session.get('cumulative', 0.0), event.get('total_cost_usd') or 0.0)
    return session


def now_in_tracker(project, doc=None):
    """Where each item stands now, by handle: its stage if open, closed and how if not."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import tracker
    # The app names the tracker on the server, or none: it keeps no tracker in a file, as the
    # script's .psd1 named one.
    if not doc:
        return {}
    # Opened as tracker.py opens a --doc. What it refuses with may name the server's address, so it
    # is not repeated.
    try:
        t = tracker.open_tracker(doc)
    except SystemExit:
        print('the tracker could not be read: where each item is now is left out', file=sys.stderr)
        return {}
    found = {f.get('handle'): f['stage'] + (f' [{f["flags"]}]' if f['flags'] else '') for _, _, f in t.items()}
    # Its closed items, those History keeps in part files among them.
    for *_, f in t.closed():
        found.setdefault(f.get('handle'), f.get('status', 'finished'))
    return found


def by_item(items, now, attempts):
    """Each item's sessions in the run, what they cost and took, and where the item is now."""
    if not items:
        return
    print('  by item:')
    for handle, sessions in items.items():
        cost = sum(s['cost'] for _, s, _ in sessions)
        models = defaultdict(float)
        for _, s, a in sessions:
            models[a.get('model', s['model'])] += s['cost']
        last = sessions[-1][2]
        points = str(last.get('points', '?')) + (f' (was {last["points_was"]})' if 'points_was' in last else '')
        # Recorded since the loop kept them; earlier attempts have none.
        tokens = [a.get('tokens') for _, _, a in sessions if a.get('tokens')]
        commits = [c for _, _, a in sessions for c in a.get('commits', []) if not c.get('tracker')]
        lines = sum(c.get('insertions', 0) + c.get('deletions', 0) for c in commits)
        recorded = any('commits' in a for _, _, a in sessions)
        state = now.get(handle, '?')
        # Built, sent to testing, and now back where it is planned or built again: a failed attempt.
        tested = any(a.get('to') == 'testing' for (_, _), a in attempts.items() if a.get('handle') == handle)
        if tested and state.split(' ')[0] in ('ready', 'planning'):
            state += ' - sent back'
        print(f'    {handle:16} {points:>10} pts {last.get("complexity", "?"):10} '
              f'{", ".join(k + " " + a.get("model", "?") for k, _, a in sessions):34} ${cost:>5.2f}'
              f' ({", ".join(f"{m} ${c:.2f}" for m, c in models.items())})')
        detail = []
        if tokens:
            detail.append('tokens ' + ', '.join(f'{k.replace("cache_", "")} {sum(t.get(k, 0) for t in tokens) / 1000:.0f}k'
                                                for k in ('cache_read', 'cache_write', 'output')))
        if recorded:
            detail.append(f'{lines} lines of code in {len(commits)} commits' + (f', ${cost / lines:.3f} a line' if lines else ''))
        detail.append(f'now {state}')
        print(f'    {"":16} ' + '; '.join(detail))
    print()


def options(argv):
    """The words given, and the values of --logs and --tracker, None for one not given."""
    args, given = [], {'--logs': None, '--tracker': None}
    words = iter(argv)
    for word in words:
        if word in given:
            given[word] = next(words, None)
            if given[word] is None:
                sys.exit(f'{word} wants a value')
        elif not word.startswith('--'):
            args.append(word)
    return args, given['--logs'], given['--tracker']


def main():
    args, logs, doc = options(sys.argv[1:])
    if not args:
        sys.exit(__doc__)
    sys.stdout.reconfigure(encoding='utf-8')
    folder = Path(logs) if logs else LOGS / args[0]
    runs = defaultdict(list)
    for path in folder.glob('loop-*.jsonl'):
        m = NAME.search(path.name)
        if m:
            runs[m.group(1)].append((int(m.group(2)), m.group(3) or '?', path))
    if not runs:
        sys.exit(f'no logs in {folder}')
    wanted = sorted(runs) if '--all' in sys.argv else [args[1]] if len(args) > 1 else [max(runs)]
    attempts = {}
    for line in (folder / 'attempts.jsonl').open(encoding='utf-8') if (folder / 'attempts.jsonl').exists() else []:
        try:
            a = json.loads(line)
        except ValueError:
            continue
        attempts[(a.get('session'), a.get('kind'))] = a

    failed, trouble = defaultdict(list), defaultdict(list)
    for stamp in wanted:
        if stamp not in runs:
            sys.exit(f'no run {stamp}; the runs are {", ".join(sorted(runs))}')
        print(f'run {stamp}\n')
        total = 0.0
        items = defaultdict(list)
        spent = {}
        for iteration, kind, path in sorted(runs[stamp], key=lambda r: (r[0], r[2].stat().st_mtime)):
            s = read_session(path)
            # This part's own share: a build resumed from its triage reports the triage too.
            s['cost'] = max(0.0, s.get('cumulative', 0.0) - spent.get(s['id'], 0.0))
            spent[s['id']] = max(spent.get(s['id'], 0.0), s.get('cumulative', 0.0))
            total += s['cost']
            # The build a triage goes on to is recorded as a build, in the same session.
            a = attempts.get((s['id'], 'build' if kind == 'continue' else kind), {})
            moved = f'{a.get("from", "?")} -> {a.get("to", "?")}' if a else ''
            print(f'  {iteration:>3} {kind:9} {a.get("handle", "?"):16} {s["model"]:26} {s["turns"]:>4} turns '
                  f'{s["ms"] / 60000:>5.1f} min ${s["cost"]:>5.2f}  {moved}'
                  f'{"  " + str(len(s["failed"])) + " failed" if s["failed"] else ""}')
            if a:
                items[a['handle']].append((kind, s, a))
            where = f'{stamp} #{iteration} {kind}'
            for t, tool, what, said in s['failed']:
                failed[t].append((where, tool, what, said))
            for t, tool, what, said in s['trouble']:
                trouble[t].append((where, tool, what, said))
        print(f'\n  ${total:.2f} in all\n')
        by_item(items, now_in_tracker(args[0], doc), attempts)

    for title, found in (('Tool calls that failed', failed), ('Shell trouble in calls that went through', trouble)):
        if not found:
            continue
        print(f'{title}, by kind:')
        for t, cases in sorted(found.items(), key=lambda kv: -len(kv[1])):
            print(f'\n  {t} - {len(cases)}')
            for where, tool, what, said in cases:
                print(f'    {where:34} {tool:10} {one_line(what, 90)}')
                pattern = dict(TROUBLE).get(t)
                hit = pattern.search(said) if pattern else None
                at = max(0, hit.start() - 60) if hit else 0
                print(f'    {"":34} -> {one_line(said[at:], 120)}')
        print()


if __name__ == '__main__':
    main()
