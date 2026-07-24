# BowerBird

<!-- CANONICAL SOURCE: bower-bird repo at vault/README.md. Edit the repo and run
     `make sync-vault` — don't edit this copy in the vault. -->

Your knowledge base. Capture stuff, the system shelves it, you learn from it
by recall (`peck`), not by reading queues.

This file is for **you** (the human). `CLAUDE.md` is the playbook for Claude Code.

## How to capture

Send to the **Telegram bot** (`@BowerBirdBot`):

| You send | Lands in | What happens |
|---|---|---|
| any link (bare, or with a note) | `brain/sources/` | shelved immediately: key ideas distilled, your note kept as a seed thought, full body inlined |
| an X/Twitter link | `brain/sources/` | tweet text resolved (no browser needed) into its own node |
| a PDF link | `brain/sources/` | downloaded, text-extracted, shelved (Sonnet does the distilling) |
| pasted prose (no link) | `brain/sources/` | your own thinking becomes a node — you're the author |
| a link the bot can't fetch (login-walled) | `to-clip.md` | open it, Web Clipper into `inbox/`, it shelves on the next pass |
| `tool: <link>` | `tools.md` | a plugin/gadget to remember (not knowledge) |
| junk / empty | `_inbox.md` | nothing dropped, with a reason |

Or use the **Obsidian Web Clipper** → saves into `inbox/`, shelved within a tick.

## The lifecycle

```
send → brain/sources/ node (immediately) → peck quizzes you on it later
```

**Everything sent is shelved** — there is no to-read pile and no read signal.
The antilibrary is the point: capture freely; recall (`peck`, the spaced-rep
quiz) is where the learning happens. A node that never earns recall gets
retired from quizzing with a keystroke (`d`), not guilt.

## Where things live

```
inbox/            Web Clipper drop target — emptied automatically, never a queue
brain/            the knowledge
  bowers/<topic>/   concept notes — one claim per note
  sources/          one node per thing you shelved (key ideas + your seed
                    thoughts + the full text, all on the node)
  _topic_index.md   map of the bowers
tools.md          tools & gadgets shelf (plugins, repos) — not knowledge
to-clip.md        links the bot couldn't fetch — clip these manually
archive/          recycle bin for processed clip originals (auto-GC'd; the
                  node keeps the body, so nothing here is unique)
digests/          dated synthesis notes Claude writes for you
_inbox.md         couldn't be auto-processed
_review.json      peck's spaced-rep state (don't edit)
```

## Marks — optional signals in a clip

When you do read something in the browser and clip it, mark it up first; the
marks land on the node and steer the distillation:

- `==highlight==` — the interesting bit (top signal for key ideas; these grow into `brain/` concept notes)
- `#dig` on a line — want to learn more
- `> ? question` — your open question
- `[text](url)` links — leads for further reading

And a note sent alongside any Telegram link is kept verbatim on the node under
`## Seed thoughts` — your one-line "why" is the highest-value input.

## The point

Capture is cheap and automatic. **peck** (spaced-rep recall with an LLM judge)
is the learning event, and the **digest** (`make digest`, run by you) does the
thinking — connections across notes, gaps, questions. Read it, leave feedback
at the bottom, and it steers the next one.
