"""tracker.py on a tracker whose History is kept in part files, a week to each, beside it
(overseer:listparts): the server names them with the tracker's text (overseer:histparts), and
every reader of a closed item reads them too - on the tests' own server."""

import json
import urllib.parse
import urllib.request

import pytest

from test_loop import item, tracker_text



def closed(handle: str, finished_at: str, parent: str | None = None, said: str | None = None) -> str:
    """A closed item as a part file holds it: an entry of the part's own list, one level in."""
    lines = ['    - {', f'        - finished_at = "{finished_at}"', f'        - handle = "{handle}"',
             f'        - title = "The {handle} item"', '        - stage = "finished"', '        - points = 2']
    if parent:
        lines.append(f'        - parent = "{parent}"')
    if said:
        lines += ['        div Talk {', '            list Comments {', '                - {',
                  '                    - at = "2026-10-27T09:00:00+00:00"', '                    - author = "owner"',
                  f'                    - body = "{said}"', '                }', '            }', '        }']
    lines.append('    }')
    return '\n'.join(lines)


def part(*entries: str) -> str:
    return '\n'.join(['list History {', *entries, '}', ''])


def put_part(overseer, doc: str, week: str, text: str) -> None:
    folder = overseer.root / doc.removesuffix('.os')
    path = folder.parent / f'{folder.name}.parts' / 'History' / f'{week}.os'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')


@pytest.fixture
def doc(overseer):
    child = item('child').replace('            - handle = "child"',
                                  '            - handle = "child"\n            - parent = "old"')
    name = overseer.document(tracker_text(child, item('lone')))
    put_part(overseer, name, '2026-W44', part(closed('old', '2026-10-28T10:00:00+00:00', said='Done in week 44.')))
    put_part(overseer, name, '2026-W45', part(closed('sib', '2026-11-03T10:00:00+00:00', parent='old')))
    asked = urllib.request.Request(f'{overseer.url}/v1/source?{urllib.parse.urlencode({"document": name})}',
                                   headers={'Authorization': f'Bearer {overseer.key.read_text(encoding="utf-8")}'})
    with urllib.request.urlopen(asked, timeout=30) as answer:
        if 'parts' not in json.loads(answer.read()):
            pytest.skip("the tests' overseer-server names no parts: it was built before overseer:histparts")
    return name


def test_a_closed_item_in_a_part_is_shown_with_its_conversation(overseer, doc):
    shown = json.loads(overseer.tracker(doc, 'show', 'old', '--json').stdout)
    assert shown['closed'] and shown['finished_at'].startswith('2026-10-28')
    assert [c['body'] for c in shown['comments']] == ['Done in week 44.']
    text = overseer.tracker(doc, 'show', 'sib').stdout
    assert 'closed     - it can be read' in text and 'The sib item' in text


def test_closed_items_in_parts_are_around_an_open_one(overseer, doc):
    rows = {(r['relation'], r['handle'], r['stage']) for r in
            json.loads(overseer.tracker(doc, 'show', 'child', '--json').stdout)['related']}
    assert ('parent', 'old', 'finished') in rows and ('sibling', 'sib', 'finished') in rows


def test_a_handle_closed_in_a_part_is_taken_and_finished(overseer, doc):
    before = overseer.text(doc)
    filed = overseer.tracker(doc, 'file', 'sib', 'Another sib', check=False)
    assert filed.returncode and 'sib' in filed.stderr
    moved = overseer.tracker(doc, 'move', 'old', 'ready', '--complexity', 'low', check=False)
    assert moved.returncode and 'old is finished' in moved.stderr
    assert overseer.text(doc) == before


def test_another_projects_closed_item_in_a_part_is_read(overseer, doc):
    folder = doc.rsplit('/', 1)[0]
    mine = overseer.document(tracker_text(item('here')), project='mine', folder=folder)
    shown = json.loads(overseer.tracker(mine, 'show', 'proj:old', '--json').stdout)
    assert shown['closed'] and shown['handle'] == 'old'


def test_parts_are_read_from_their_copies_until_another_appears(overseer, doc, tmp_path):
    cache = {'OVERSEER_CACHE': str(tmp_path / 'cache')}
    overseer.tracker(doc, 'show', 'old', env=cache)
    copy = next((tmp_path / 'cache').rglob('2026-W44.os.json'))
    held = json.loads(copy.read_text(encoding='utf-8'))
    host = json.loads(next((tmp_path / 'cache').rglob('proj.os.json')).read_text(encoding='utf-8'))
    assert host['parts']['History'][0].endswith('proj.parts/History/2026-W44.os') and '+' in host['tag']
    # A title only the copy has shows that the part was answered 304 and read from its copy.
    copy.write_text(json.dumps({**held, 'text': held['text'].replace('The old item', 'The kept old item')}),
                    encoding='utf-8')
    assert 'The kept old item' in overseer.tracker(doc, 'show', 'old', env=cache).stdout

    # A new week changes the tracker's tag, though its text stands: the copy is not taken for it.
    put_part(overseer, doc, '2026-W46', part(closed('newest', '2026-11-10T10:00:00+00:00')))
    assert 'The newest item' in overseer.tracker(doc, 'show', 'newest', env=cache).stdout


def deeper(entry: str) -> str:
    """The entry with everything inside its braces eight spaces further in, as the server has
    written an entry moved from Items."""
    lines = entry.split('\n')
    return '\n'.join([lines[0], *('        ' + line for line in lines[1:-1]), lines[-1]])


def test_an_entry_indented_deeper_is_read_all_the_same(overseer, doc):
    put_part(overseer, doc, '2026-W46', part(deeper(closed('deep', '2026-11-11T10:00:00+00:00', parent='old',
                                                           said='Its fields sit deeper.'))))
    shown = json.loads(overseer.tracker(doc, 'show', 'deep', '--json').stdout)
    assert shown['closed'] and shown['title'] == 'The deep item'
    assert [c['body'] for c in shown['comments']] == ['Its fields sit deeper.']
    rows = {(r['relation'], r['handle']) for r in json.loads(overseer.tracker(doc, 'show', 'child', '--json').stdout)['related']}
    assert ('sibling', 'deep') in rows


def test_a_week_written_at_the_depth_of_items_is_read(overseer, doc):
    # Every line a step further in, the entry's own brace too: two trackers' 2026-W45.
    whole = '\n'.join('    ' + line for line in closed('far', '2026-11-11T10:00:00+00:00', parent='old',
                                                       said='The whole entry sits deeper.').split('\n'))
    put_part(overseer, doc, '2026-W46', part(whole, whole.replace('far', 'farther')))
    shown = json.loads(overseer.tracker(doc, 'show', 'far', '--json').stdout)
    assert shown['closed'] and [c['body'] for c in shown['comments']] == ['The whole entry sits deeper.']
    assert json.loads(overseer.tracker(doc, 'show', 'farther', '--json').stdout)['title'] == 'The farther item'
    filed = overseer.tracker(doc, 'file', 'far', 'Another far', check=False)
    assert filed.returncode and 'far' in filed.stderr


def test_an_entry_with_every_level_twice_as_deep_is_read(overseer, doc):
    # The brace in its place and all inside it at double the indent: 2026-W46 as it was written.
    lines = closed('wide', '2026-11-11T10:00:00+00:00', said='Each level is wider.').split('\n')
    wide = '\n'.join([lines[0], *(' ' * (len(l) - len(l.lstrip())) + l for l in lines[1:-1]), lines[-1]])
    put_part(overseer, doc, '2026-W46', part(wide))
    shown = json.loads(overseer.tracker(doc, 'show', 'wide', '--json').stdout)
    assert shown['title'] == 'The wide item' and [c['body'] for c in shown['comments']] == ['Each level is wider.']


def test_an_entry_without_a_handle_is_no_ones_parent(overseer, doc):
    put_part(overseer, doc, '2026-W46', part('    - {\n        - finished_at = "2026-11-11T10:00:00+00:00"\n    }'))
    shown = json.loads(overseer.tracker(doc, 'show', 'lone', '--json').stdout)
    assert shown['handle'] == 'lone' and not [r for r in shown['related'] if r['relation'] == 'parent']
    assert 'lone' in overseer.tracker(doc, 'show', 'lone').stdout


def test_a_tracker_without_parts_reads_its_own_history(overseer):
    # The same entry, as a tracker's own History holds it: one level further in.
    own = '\n'.join('    ' + line for line in closed('done', '2026-10-01T00:00:00+00:00').split('\n'))
    doc = overseer.document(tracker_text(item('open')).replace('    - History {\n    }', f'    - History {{\n{own}\n    }}'))
    assert json.loads(overseer.tracker(doc, 'show', 'done', '--json').stdout)['closed']
