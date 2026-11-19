"""Read and edit a project's feature list - the Features tab of its tracker - as tracker.py does its
items.

    python features.py list [<standing>] [--json]   the handles in each standing - proposed, wanted,
                                                    accepted, discarded - each with its title
    python features.py show <HANDLE> [--json]       one feature in full: its fields, what it needs,
                                                    excludes and goes with, the features that name
                                                    it so, and its conversation
    python features.py add <HANDLE> <title> [--standing s] [--description t] [--pros t] [--cons t]
                           [--requires A,B] [--incompatible A,B] [--synergies A,B] [--note t]
    python features.py set <HANDLE> <field> <value> title, standing, description, pros, cons - or
                                                    requires, incompatible, synergies as a whole
                                                    list, A,B; '' empties a field
    python features.py link <HANDLE> <relation> <OTHER>...    add to requires, incompatible or synergies
    python features.py unlink <HANDLE> <relation> <OTHER>...  take out of one
    python features.py note <HANDLE> <text>         add a comment to its conversation

A feature's handle is in capitals - SELF-APPLY - so it is never taken for an item's. Text names a
feature as <#HANDLE>, which the tab draws as its chip, as items are named in theirs. Excluding and
going with are both ways: a link or unlink of incompatible or synergies writes both features;
requires is one way - A needs B says nothing of B.

Any text can be @<path>, read from that file, as with tracker.py. Every command takes --doc, the
tracker on the server, with the same default as tracker.py, and each edit is sent as it is made.
Comments are signed as tracker.py signs them.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tracker
from tracker import FIELD, indent_of, quoted, value_of

# The order a feature's fields are in: the template's. The texts are in its Detail fold, its
# comments in its Talk fold, Detail first.
ORDER = ['added', 'handle', 'standing', 'title', 'requires', 'incompatible', 'synergies', 'touched_at']
DETAIL = ['description', 'pros', 'cons']
RELATIONS = {'requires': 'needs', 'incompatible': 'excludes', 'synergies': 'goes with'}
BOTH_WAYS = {'incompatible', 'synergies'}
DEFAULT_STANDING = 'proposed'
HANDLE = re.compile(r'[A-Z0-9][A-Z0-9-]*')


class Features:
    def __init__(self, path):
        self.t = tracker.open_tracker(path)
        # A tracker with no features yet writes no `- Features` line, but the template it inherits
        # declares the list, so it is there, empty, and the first add writes it.
        if self.t.header('Features') is None and not (
                self.t.base() and self.t.base_tracker().header('Features') is not None):
            sys.exit(f'no feature list in {self.t.path.name}: it has no list Features')

    def all(self):
        """Every feature, as (first line, last line, fields), its Detail texts among the fields."""
        found = []
        if self.t.header('Features') is None:
            return found
        for top, bottom, f in self.t.entries('Features'):
            f.setdefault('standing', DEFAULT_STANDING)
            detail = self.t.block(top, bottom, 'div Detail')
            if detail and detail[0] != detail[1]:
                own = indent_of(self.t.lines[detail[0]]) + '    '
                for line in self.t.lines[detail[0] + 1:detail[1]]:
                    m = FIELD.match(line)
                    if m and m.group(1) == own:
                        f[m.group(2)] = value_of(m.group(3))
            found.append((top, bottom, f))
        return found

    def handles(self):
        return [f.get('handle') for _, _, f in self.all()]

    def one(self, handle):
        hits = [e for e in self.all() if e[2].get('handle') == handle]
        if not hits:
            sys.exit(f'no feature {handle}' + (' - a feature\'s handle is in capitals' if handle != handle.upper() else ''))
        return hits[0]

    def standings(self):
        return self.t.vocabulary('Standings')

    def on_server(self, top, field, value):
        """One field of a feature written on the server, and the tracker read again: a command can
        write a field twice - a relation's old links out, then the new ones in - and the second
        must start from the first."""
        self.t.server.set(self.t.name, f'{self.t.address_at(top)}/{field}',
                          tracker.typed(field.split('/')[-1], value))
        self.t.reload()

    def set_own(self, handle, name, value):
        """Write one of a feature's own fields; None gives it the template's own value."""
        top, _, _ = self.one(handle)
        self.on_server(top, name, value)

    def set_detail(self, handle, name, value):
        """Write one of the texts in a feature's Detail fold, which the template gives every one."""
        top, _, _ = self.one(handle)
        self.on_server(top, f'Detail/{name}', value)

    def set_field(self, handle, name, text):
        """Any field by name, an empty text taking it out - the standing too, at its default."""
        if name in DETAIL:
            self.set_detail(handle, name, quoted(text) if text else None)
        elif name == 'standing':
            self.set_own(handle, name, None if text in ('', DEFAULT_STANDING) else quoted(text))
        else:
            self.set_own(handle, name, quoted(text) if text else None)


def split(text):
    return tracker.split(text)


def checked_handle(handle):
    if not HANDLE.fullmatch(handle or ''):
        sys.exit(f'{handle!r} is not a feature handle: capitals, digits and -, as SELF-APPLY')
    return handle


def checked_others(fs, handle, others):
    known = set(fs.handles())
    for o in others:
        checked_handle(o)
        if o == handle:
            sys.exit('a feature is not related to itself')
        if o not in known:
            sys.exit(f'no feature {o}')
    return others


def relate(fs, handle, relation, others, add):
    """Add others to a relation, or take them out; both features for one that goes both ways."""
    if relation not in RELATIONS:
        sys.exit(f'no relation {relation}; the relations are {", ".join(RELATIONS)}')

    def change(h, names):
        _, _, f = fs.one(h)
        now = split(f.get(relation))
        new = now + [n for n in names if n not in now] if add else [n for n in now if n not in names]
        if new != now:
            fs.set_own(h, relation, quoted(', '.join(new)) if new else None)

    change(handle, others)
    if relation in BOTH_WAYS:
        for o in others:
            change(o, [handle])


def with_titles(fs, text):
    """A text with each feature it names followed by its title - <#SELF-APPLY> (its title) - and
    items named so by theirs, as tracker.py shows them; code is left as it is."""
    titles = {f.get('handle'): f.get('title', '') for _, _, f in fs.all()}

    def titled(m):
        h = m.group(1)
        if titles.get(h):
            return f'{m.group(0)} ({titles[h]})'
        found = tracker.stage_and_title(fs.t, h) if not HANDLE.fullmatch(h) else None
        return f'{m.group(0)} ({found[1]})' if found else m.group(0)
    parts = re.split(r'(```.*?```|`[^`\n]*`)', text, flags=re.S)
    return ''.join(p if i % 2 else tracker.MENTION.sub(titled, p) for i, p in enumerate(parts))


def cmd_list(fs, args):
    standings = fs.standings()
    if args.standing and args.standing not in standings:
        sys.exit(f'no standing {args.standing}; the standings are {", ".join(standings)}')
    by = {s: [] for s in standings}
    for _, _, f in fs.all():
        by.setdefault(f['standing'], []).append(f)
    wanted = [args.standing] if args.standing else list(by)
    if args.json:
        print(json.dumps({s: [{'handle': f.get('handle'), 'title': f.get('title', '')} for f in by[s]] for s in wanted}))
        return
    for s in wanted:
        print(f'{s} ({len(by[s])})')
        for f in by[s]:
            print(f'  {f.get("handle", ""):20} {f.get("title", "")}')


def cmd_show(fs, args):
    top, bottom, f = fs.one(args.handle)
    said = fs.t.nested(top, bottom, tracker.TALK)
    others = fs.all()
    named_by = {r: [g.get('handle') for _, _, g in others if args.handle in split(g.get(r))] for r in RELATIONS}
    if args.json:
        print(json.dumps(dict(f, named_by=named_by, comments=said)))
        return
    titles = {g.get('handle'): g.get('title', '') for _, _, g in others}
    for name in ['handle', 'title', 'standing']:
        print(f'{name:12} {f.get(name, "")}')
    for r, word in RELATIONS.items():
        mine = split(f.get(r))
        print(f'{word:12} ' + ('; '.join(f'{h} ({titles.get(h, "?")})' for h in mine) if mine else '-'))
    needed = [h for h in named_by['requires']]
    print(f'{"needed by":12} ' + ('; '.join(f'{h} ({titles.get(h, "?")})' for h in needed) if needed else '-'))
    for r in BOTH_WAYS:
        # Both ways by rule; one written by hand on one side only shows here.
        lopsided = [h for h in named_by[r] if h not in split(f.get(r))]
        if lopsided:
            print(f'{"":12} also named {RELATIONS[r]} by {", ".join(lopsided)}, not written here')
    for name in DETAIL:
        print(f'\n## {name}\n\n{with_titles(fs, f[name]) if f.get(name) else "(nothing written)"}')
    print(f'\nconversation, oldest first: {len(said) or "nothing said yet"}')
    for c in said:
        print(f'\n--- {c.get("at", "")[:16].replace("T", " ")}  {c.get("author", "")}\n{with_titles(fs, c.get("body", ""))}')


def cmd_add(fs, args):
    handle = checked_handle(args.handle)
    if handle in fs.handles():
        sys.exit(f'{handle} is taken')
    standing = args.standing or DEFAULT_STANDING
    if standing not in fs.standings():
        sys.exit(f'no standing {standing}; the standings are {", ".join(fs.standings())}')
    fields = {'added': quoted(tracker.now()), 'handle': quoted(handle), 'title': quoted(tracker.text_of(args.title))}
    if standing != DEFAULT_STANDING:
        fields['standing'] = quoted(standing)
    fs.t.append_entry('Features', dict(sorted(fields.items(), key=lambda kv: ORDER.index(kv[0]))))
    for r in RELATIONS:
        given = split(getattr(args, r))
        if given:
            relate(fs, handle, r, checked_others(fs, handle, given), True)
    for name in DETAIL:
        text = getattr(args, name)
        if text:
            fs.set_detail(handle, name, quoted(tracker.text_of(text)))
    said = ''
    if args.note:
        said = '; ' + note_on(fs, handle, args.note)
    print(f'added {handle} at {standing}{said}')


def cmd_set(fs, args):
    _, _, f = fs.one(args.handle)
    name = args.field
    if name in RELATIONS:
        new = checked_others(fs, args.handle, split(args.value))
        old = split(f.get(name))
        relate(fs, args.handle, name, [h for h in old if h not in new], False)
        relate(fs, args.handle, name, [h for h in new if h not in old], True)
        print(f'{args.handle} {RELATIONS[name]}: {", ".join(new) or "nothing"}')
        return
    if name not in ['title', 'standing'] + DETAIL:
        sys.exit(f'no field {name}; the fields are title, standing, {", ".join(DETAIL)}, {", ".join(RELATIONS)}')
    text = tracker.text_of(args.value) if args.value else ''
    if name == 'standing' and text not in fs.standings():
        sys.exit(f'no standing {text}; the standings are {", ".join(fs.standings())}')
    if name == 'title' and not text:
        sys.exit('a feature keeps a title')
    was = f.get(name, '')
    fs.set_field(args.handle, name, text)
    print(f'{args.handle} {name}: ' + (f'{was} -> {text}' if name == 'standing' else f'{len(text)} characters'))


def cmd_link(fs, args, add=True):
    fs.one(args.handle)
    others = checked_others(fs, args.handle, args.others) if add else args.others
    relate(fs, args.handle, args.relation, others, add)
    _, _, f = fs.one(args.handle)
    both = ' (and each of them back)' if args.relation in BOTH_WAYS else ''
    print(f'{args.handle} {RELATIONS[args.relation]}: {f.get(args.relation) or "nothing"}{both}')


def note_on(fs, handle, text):
    top, bottom, _ = fs.one(handle)
    body = tracker.text_of(text)
    if not body:
        sys.exit('a comment with nothing in it')
    values, signed = tracker.comment(fs.t, body)
    fs.t.add_nested(top, bottom, tracker.TALK, values)
    return f'comment added, signed {signed}, {len(body)} characters'


def cmd_note(fs, args):
    said = note_on(fs, args.handle, args.text)
    print(f'{args.handle}: {said}')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--doc', default=tracker.DEFAULT)
    doc = argparse.ArgumentParser(add_help=False)
    doc.add_argument('--doc', default=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest='command', required=True)
    command = lambda name: sub.add_parser(name, parents=[doc])
    p = command('list'); p.add_argument('standing', nargs='?'); p.add_argument('--json', action='store_true')
    p = command('show'); p.add_argument('handle'); p.add_argument('--json', action='store_true')
    p = command('add'); p.add_argument('handle'); p.add_argument('title'); p.add_argument('--standing')
    for name in DETAIL + list(RELATIONS) + ['note']:
        p.add_argument(f'--{name}')
    p = command('set'); p.add_argument('handle'); p.add_argument('field'); p.add_argument('value')
    for name in ('link', 'unlink'):
        p = command(name); p.add_argument('handle'); p.add_argument('relation'); p.add_argument('others', nargs='+')
    p = command('note'); p.add_argument('handle'); p.add_argument('text')
    args = parser.parse_args()
    run = {'list': cmd_list, 'show': cmd_show, 'add': cmd_add, 'set': cmd_set, 'note': cmd_note,
           'link': cmd_link, 'unlink': lambda fs, a: cmd_link(fs, a, add=False)}[args.command]
    run(Features(args.doc), args)


if __name__ == '__main__':
    main()
