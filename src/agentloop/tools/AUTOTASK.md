# Autotask

How an agent with no prior context works one item of a project's tracker in one session. You will
be pointed at a tracker document - a document on the Overseer server such as
`alice/projects/game.os`, which has no file on this machine and is reached only through
`tracker.py` - and at the project's brief, and given one of its items to
work. This file is the workflow, the same for
every project; the project's description - from its tracker - says what the project is and what
it holds to, and the brief where its parts are, its own rules, and how to check work on it. Where
this file and the brief differ, the brief wins. The repositories hold the rest.

## The session

1. **Read the item.** `tracker.py show <handle>` gives it in full: its fields; the items around it -
   its parent, what it waits on, what waits on it, its parts and its siblings, closed ones too,
   each with its stage and title; and its conversation, oldest first, which carries the history
   and the decisions already made. `show` any of those you need to know more about - a closed one
   too, to see how it was done. That is all you need of the tracker: the comment at its top is for
   people working on the project, and this file, the description and the brief say everything it
   does. A project's tags can say something too - about the part of the project an item tagged so
   is in - and `tracker.py tag <tag>` prints it.
2. **Look before you touch.** Run `git status` in each repository you expect to change - when the
   loop runs you, its prompt gives each repository's `git status --short` as the session began, so
   there is no need to. Uncommitted changes there are the owner's work in progress: leave them exactly as they are -
   never revert, reformat or "tidy" them, and never `git checkout` a file to undo something.
3. **Do the work** the stage asks for, and check it as the brief says.
4. **Record it in the tracker** with `tracker.py move <handle> <stage> --note <text>` - the stage,
   and a comment saying what you did or found, in one step; at `ready`, its complexity and points
   too - or with `tracker.py ask`, when something needs a person. Then `tracker.py check`, and
   report (see "Reporting").

**When the loop runs you** (`run-agent-loop.ps1`), its prompt names the item: work that one and no
other. It gives you this file, the project's description and its brief in your system prompt, so
there is no need to read any of them; and in the prompt, the item as `tracker.py show` prints it
and what the project says about the tags it carries - no need to ask for those either. A session
on a `filed` or `planning` item is a triage: it ends once the item is moved, without building
anything. The loop chooses who builds it from the complexity you give it,
and may resume your session to have you build it while what you read is still in context. A
session on an item flagged `discuss` ends once your thoughts are asked, and nothing is built after
it. After each session the loop commits what changed.

**Stopping the loop.** Only when nothing on this project can be worked - not by you, and not by
the session that would come after you: its repositories cannot be read or written; the tracker
does not parse, and not because of an edit of yours (`tracker.py check`); the tools the brief
depends on - the build, the tests, the server - do not run at all; or the machine is not the one
the brief describes. Then end your reply with the line `STOP_LOOP: <what is wrong, in a
sentence>` in place of the COMMIT line, and change nothing more. The loop stops, commits nothing
of your session, and shows the owner your reason. A problem with the item at hand is never a
reason: ask about it (`tracker.py ask`), and the loop goes on to the next item.

**Going on to a related item.** When you finish, another item may be one you could work straight
away with what you have read: it is beside yours - a sibling, a part, what yours holds up - and
reaches the same code or the same question. Then you may add a line `NEXT: <its handle>` after
the COMMIT line. The loop gives it to you next, in this same session, if the queue would offer it
now - not waiting, not held for a person, nothing flagged `first` ahead of it - and the session
has not grown too long; otherwise it goes on as usual. Only for such an item: the loop picks well
enough for the rest, and a session that goes on carries everything it has read so far into every
step of the next item.

## Tools

`{tools}/tracker.py` reads and edits the tracker:

```bash
{python} {tools}/tracker.py show <handle>
```

Its commands are `show`, `move`, `note` (a comment), `ask` (a question for a person), `after`,
`file` (a new item), `tag` (what a tag says) and `check` (does it still parse); the rest are the
loop script's. Give a
move its comment with `--note`, saying what the move did. `note` adds a comment to any open item,
whatever its stage or flags: the one thing you may add to an item waiting on a person or on its
parts, when there is something its reader should know. Any text - a comment, a question - can be
`@<path>`, read from that file (see "This machine").
It writes each change to the server as it is made, and refuses what the rules below forbid - touching an item waiting on a
person, finishing or cancelling one, moving one flagged `discuss` to `ready`, touching History, an
unknown tag or handle - and moving an item to `ready` while its complexity is unassigned:
`move <handle> ready --complexity medium` sets both. `--doc` names the tracker; the loop sets `OVERSEER_TRACKER` to its
project's, so that commands without `--doc` work on it. `tracker.py <command> --help` gives a
command's options. The brief names any tools of the project's own.

## Stage rules

- **ready** - Everything is decided: implement it, then move it to `testing`.
- **planning** - Its plan is being worked out. Investigate, finish the plan, and move it to `ready`
  once nothing is left to decide.
- **filed** - Nothing done yet. If nothing is left to decide, move it straight to `ready`, with the
  plan in the move's comment. If it has to wait for another item first, add that item's handle to its
  `after` list and leave it `filed`. Its own parts it waits on already (see "Waiting").
- **testing, later** - Waiting on a person. Never process, edit or move these; a comment is all
  you may add. An item flagged `trusted` does not wait: the loop finishes it as soon as it reaches
  `testing`, so its testing comment is the last word on it.
- **parts** - Nothing is left on the item but its open parts, and it moves to `testing` by itself
  once the last of them is finished or cancelled. Never process, edit or move these; a comment is
  all you may add. `tracker.py` refuses `parts` for an item with no open parts.
- **finished, cancelled** - Only a person finishes, cancels or drops an item. Never pick
  `finished` or `cancelled`, never remove an item from the list, never touch History.

An item you leave with open parts - after splitting it into sub-items filed with `--parent`, say -
goes one of two ways:
- to `parts`, when nothing is left to do on the item itself: it goes to `testing` once its parts
  are done, and no agent works it again. Give it no complexity.
- to `planning` or `ready`, when something is still to do on it once its parts are done - putting
  them together, say: it waits on them, and is offered again once the last of them closes. The
  move's comment says what is left on it.

Whenever something needs a person's decision - at any of the stages you work, a build included -
ask: `tracker.py ask <handle> <question>` adds the question as a comment, moves the item to
`planning` and flags it `asked`, in one step. The item waits there until the person answers: with a
comment of their own, taking the flag off, or with clicks on the controls of your question (see
"Controls in a comment"), when the loop takes the flag off for them.

**Flags** are the same in every project, beside its own tags, and say how an item is to be worked:

- `manual` - for a person to work. Leave it alone, but for a comment.
- `auto` - filed by an agent of its own accord. Filing what you find along the way is welcome (see
  "The tracker"); in a loop run `tracker.py file` flags it `auto` by itself.
- `first` - goes before everything else, whatever its stage.
- `asked` - a question in its conversation waits on a person. Leave it alone, but for a comment.
  The person takes it off, or the loop does once every control in the question is decided.
- `discuss` - only your thoughts are wanted. Investigate, then give your analysis and a
  recommendation with `tracker.py ask`. Never move it to `ready`.
- `trusted` - set by a person, who trusts it to be done right. Work it like any other; once you
  move it to `testing`, the loop finishes it after your session, with no person reading it first,
  so the testing comment is its record. Anything that needs a person is asked before, not left there.

You set no flag but `auto` and `asked`, and take none off - `trusted` least of all; `tracker.py`
refuses the rest.

What each move's comment must say:

- to `testing`: what changed, what was checked, and how the person can confirm it works - what to
  open, what to do, what to expect - the steps to confirm as a checklist, `- [ ] ...`, one a line.
- with `ask`: what you found, then the question(s), stated so they can be answered in a line each -
  or with a click, as a selector or a checkbox (see "Controls in a comment").
- to `parts`: which parts it waits on, each as `<#handle>`, and that nothing else is left on it.
- to `ready`: why nothing is left to decide, the plan, and - if your estimate of its points
  differs from what it was filed with - what changed it.

**Complexity.** Every item moved to `ready` is given a complexity - how much thinking building it
takes, given the plan in its conversation, where points say how much work. It is for choosing a model to
implement the item, or for the owner to judge whether to take it on themselves:

- `trivial` - straightforward or boilerplate, however much of it there is.
- `low` - one part of the project, a little care.
- `medium` - a few parts, or one that needs real thought.
- `high` - a good understanding of several parts of the project, and real analysis.

Rate what building it will take, not how short the plan reads: a plan that says 'find out',
'probably' or 'check whether' has thinking still to come. Between two, pick the higher - a rating
too low sends the item to a smaller model. The brief gives items already built, for scale.

It stays `unassigned` until then; `tracker.py move --complexity` sets it, and refuses a move to
`ready` without one.

**Points.** An item's points - 1 the smallest thing worth writing down, 8 most of a day - say how
much work it is, and are only a guess when it is filed, before anyone has looked at the code. When
you move an item to `ready`, estimate them again from your plan and give them with the move,
`--points <n>`. They are what the item is worth in the task history once it is finished, so say in
the comment why they changed.

## Execution constraints

1. **Scope:** the code you change is for the item you were given, and no other. The tracker
   around it you may tend: when what you found changes another item's plan - a sibling's, its
   parent's - say so in a comment on it; when you file an item yours now waits on and its siblings
   do too, add it to their `after` lists as well. Never build another item's change, beyond what
   yours cannot be finished without.
2. **Waiting:** an item is waiting while any handle in its `after` list is still an open item
   (an entry of Items) - its own project's, or another's named `<project>:<handle>`. A handle found only in History, or nowhere, does not hold it back.
   An item also waits, without naming them, while any of its parts - the items whose `parent` it
   is - is open. So never add a part to its parent's `after` list, nor a parent to its part's -
   `tracker.py` refuses both: filing parts with `--parent` is all a split needs.

## Other projects

The trackers in one folder are open to each other, and the prompt names the ones beside yours.
An item of another project is named `<project>:<handle>` - `engine:physics`, the project
being its tracker's name without `.os`:

- **Read it:** `tracker.py show engine:physics`, with what is around it there.
- **Wait on it:** `tracker.py after <handle> engine:physics`, or `--after` when filing. Yours
  is held while that item is open, as by one of your own.
- **File into it:** work that belongs to the other project - the engine a game needs fixed, say -
  is filed there, `tracker.py file <handle> <title> --into engine --note @<path>`, and
  `--ask` too if it needs a person first. It begins with the item you were on, and is written
  there at once. Make yours wait on it if it cannot go on without it.

Nothing else in another project's tracker is yours to change, and no code of another project's
either. In a comment, `<#handle>` names an item of your own project only; another project's is
written `<project>:<handle>`.

## The tracker

A project document in Overseer's own language, on the server. `Items` is the open list, `History` the closed
one, `Stages`, `Labels` and `Flags` the vocabularies. An open item looks like this:

```
        - {
            - added = "2026-10-05T10:30:07.935277+00:00"
            - handle = "stages"
            - title = "Project items move through stages, and finishing is one of them"
            - stage = "ready"
            - parent = "fix"
            - after = "enums, cachekey"
            - labels = "ui, dsl"
            - flags = "first"
            - points = 5
            - complexity = "medium"
            div Talk {
                list Comments {
                    - {
                        - at = "2026-10-05T10:30:07.935277+00:00"
                        - author = "owner"
                        - body = "what the owner asked for, in markdown"
                    }
                }
            }
            div Agents {
                list Work {
                    - {
                        - at = "2026-10-06T08:12:40.118502+00:00"
                        - kind = "triage"
                        - model = "claude-opus-5-5 (medium)"
                        - moved = "filed > ready"
                        - points = 5
                        - cost = 0.61
                    }
                }
            }
        }
```

- A field the entry does not state has the template's value: no `stage` line means `filed`, no
  `after` means waiting on nothing, no `flags` means none, no `points` means 1, no `complexity`
  means unassigned.
- `handle` is the item's key: short, lower case, unique across Items and History. `parent` names
  the larger item this one is part of; `after` names, comma-separated, the items it waits on.
- **An item's conversation** is its `Talk/Comments` list, oldest first: what the owner asked
  for, and what each session did or found, in that order. Comments are added, never edited or
  removed. `tracker.py` dates each one and signs it - the loop signs yours with your model and
  effort, `AGENT_AUTHOR` - so do not start one with a date or a name. A comment is markdown:
  specific, what, where, what was found, in short paragraphs or a list. Any character can go in
  one - `tracker.py` escapes what the document needs - but give one that holds quotes, backticks or
  `$` through a file, `@<path>` (see "This machine"). An item a comment names is written
  `<#handle>`, which the page draws as the item's chip; backticks are for code, and a `<#handle>`
  inside them stays text. Write the handle alone - the title `tracker.py show` puts after it, as
  `<#handle> (Its title)`, is for you to read, not to copy. A topic of the project's Discussion
  tab is written `<:handle>` instead, and a feature of its Features tab `<$HANDLE>`, its handle in
  capitals - each with a mark of its own, never `<#handle>`, which is only an item's - and `show`
  titles both the same way. The `NEXT:` line and the commands' arguments take plain handles. An image in a comment - a screenshot the owner pasted - is kept
  with the tracker, `![](images/<project>/<name>.png)`; `tracker.py show` prints it at a path on
  this machine, a copy it fetched into your scratch folder. Open it
  with your file-reading tool, which shows images: when a comment holds one, it is usually what
  the comment is about. A comment of yours can hold one too. When you took a screenshot while
  working - of the page you changed, of a document's preview, of a scene - and it shows what words
  would not, keep it with `tracker.py attach <file>`, which prints the path to write, and write it
  in the comment as `![what it shows](<that path>)` - never the path `show` prints, which only
  this machine can open - with a line saying what it shows. A result that is plain from the words
  alone needs no picture. A `![transcribed](sounds/...)` with a quote under it is a voice message
  of the comment's author, transcribed by the loop: read the quote as their own words - and still
  never edit a comment yourself.
- **Controls in a comment.** A comment's markdown can hold controls the reader clicks in the page,
  and a click rewrites the comment's text - an agent's comment too, after it was written:
  - a checklist, `- [ ] step` / `- [x] step`, a line each; one line alone is a checkbox;
  - a selector, a list of options of which one is chosen: `- ( ) A` / `- (x) B`;
  - a vote, `- (+) A` / `- (+2) A`, which only counts.

  In a tracker a click signs the line it decides with the clicker's chip at its end:
  `- (x) B <@ap31>`, `- [x] step <@ap31>`. `tracker.py show` prints the comment as it is now, so a
  question you or an earlier session asked may come back already answered in its own text: read
  the decision there, a choice signed by a person being their answer. Use them when you ask:
  - **a selector** to offer a choice: one option a line, each saying what it costs, the one you
    recommend marked so in its words - never chosen beforehand;
  - **a single checkbox** when you only ask the person to agree with a recommendation, saying
    that they tick it to agree or write a comment to disagree;
  - **a checklist** for the steps a testing comment asks the person to confirm.

  When every selector of the question you asked with `ask` has an option chosen and every box in
  it is ticked, each signed by someone other than you, the loop takes the `asked` flag off and
  says so in a comment: then the item comes back with its answer, and no one had to comment. A box
  left unticked, or a selector left unchosen, keeps the item held, so ask only what a click can
  answer that way, and the rest in words. Never tick, choose or vote in a control yourself, in your
  comment or anyone's, and never edit a comment already made.
- **Discussion and Features.** Beside the items a tracker may hold the topics of its Discussion
  tab - anything to talk over that is neither an item nor a feature - and the features of its
  Features tab: what the project could become, each with its standing (proposed, wanted, accepted
  or discarded), its description, pros and cons, and the features it needs, excludes or goes with.
  `tracker.py show <handle>` reads a topic or a feature as it reads an item, and `tracker.py note
  <handle> <text>` adds a comment to its conversation - when the work you are on bears on it, or
  it asks for a view you have. Neither is ever work: the loop never picks one, no stage or flag is
  theirs, and no other command takes their handles.
- **An item's work** is its `Agents/Work` list: one record per loop session on it - when, what
  kind, which model, where it moved the item, its points, and what the session cost, read,
  wrote and changed. The loop writes it after your session; never write or edit one yourself.
- **A follow-up you find** - a separate bug, a later improvement - is worth filing as a new item
  (`tracker.py file`), with a comment (`--note`) saying where it came from; in a loop run it is
  flagged `auto`. One that needs a person's decision before anyone can work it is filed with its
  question, `tracker.py file <handle> <title> --ask @<path>`: at `planning`, flagged `asked`, in
  one step - filed without it, it would be handed to the next session, which could only ask again.
  Do not fix it in the same session unless the item you are on cannot be finished without it.
- **After any edit, `tracker.py check`** - it asks the server for the open list.

The tracker has no file to edit: everything goes through `tracker.py`, and each change is written
on the server as it is made.

## Hard rules

- Never read, print or stage `.env` or `.env.*` (only `.env.example` is tracked), even if it is
  open in an editor. Never log an API key or token; error text shown to anyone names exception
  types, never paths, URLs or tokens.
- Never commit, push, deploy, or run destructive git commands. Leave the work in the working tree:
  when a loop runs you, it commits what the session changed itself, and the owner pushes.
- The brief adds the project's own.

## Code

Write like the surrounding code. Comments say why, in full sentences, where the reason is not
obvious from the code. The brief gives the project's own conventions.

## Reporting

End the session with a short report, findings first:

- the item, and the stage it moved from and to;
- what changed, by file, and what was checked, with results - a failing check is reported as
  failing, with its output;
- for `testing`: how the owner confirms it; for an ask: the question;
- anything filed as a new item.

## This machine

"Where you are", after this file, names the system, its shell, the Python to run the tools with,
and the folder of each repository the brief names. Where this file or the brief names one of
those folders, it has been filled in with this machine's path already. What has gone wrong before,
and how to avoid it:

- **Paths.** Give programs paths with forward slashes, which every program and shell reads, and
  quote one that holds a space. For files of your own, use the scratch folder the loop gives you -
  in the prompt, and in `AGENT_SCRATCH` - never `/tmp` or another shared folder.
- **Quoting.** Inside double quotes the shell runs anything in backticks or `$( )`, and a comment
  that quotes code or a formula holds both: the comment comes out cut short, or the command fails.
  Never pass a comment's text in double quotes. Write it to a file in the scratch folder with your editor
  tool, and give the path with an @: `tracker.py move <handle> testing --note @<path>`.
- **Heredocs** can drop backslashes from generated code. Write files with an editor tool, or keep
  backslashes out of what a heredoc carries.
- **Repositories.** The prompt names the repositories you may change: run git in exactly those
  folders. The folder above a repository is not usually one too.
- **Long commands.** A tool call waits two minutes unless given a longer timeout, and ten at most
  (600000 ms). A command that may run longer - a test suite, a stress run - is started with
  `run_in_background`, its output going to a log in the scratch folder, and waited for with a
  loop that polls the log until it shows the run has ended, in calls of under ten minutes each.
  Never give such a command a timeout of its own: one that runs out kills it near its end, and
  the whole run starts again. A bare `sleep` to wait is refused.
