# BowerBird

<!-- CANONICAL SOURCE: bower-bird repo at vault/README.md. Edit the repo and run
     `make sync-vault` — don't edit this copy in the vault. -->

Your knowledge base. Capture stuff, the system files it, you learn from it.

This file is for **you** (the human). `CLAUDE.md` is the playbook for Claude Code.

## How to capture

Send to the **Telegram bot** (`@BowerBirdBot`):

| You send | Lands in | What happens |
|---|---|---|
| a bare link | `inbox/` | bot fetches + renders the full text into a readable doc |
| a link the bot can't fetch (X, login-walled) | `to-clip.md` | open it, Web Clipper into `inbox/` |
| link **+ a note**, or `read: <link>` | `brain/sources/` | counts as read → distilled |
| `tool: <link>` | `tools.md` | a plugin/gadget to remember (not knowledge) |
| junk / no link | `_inbox.md` | nothing dropped, with a reason |

Or use the **Obsidian Web Clipper** → saves straight to `inbox/`.

## The lifecycle (how a read flows)

```
inbox/  →  trinkets/  →  brain/bowers/
(bot drops   (you read it    (filed into
 readable     & moved it)     concept notes)
 docs)
```

**Moving a doc `inbox/ → trinkets/` is the "I read it" signal.** Only items in
`trinkets/` get distilled — nothing is distilled straight out of `inbox/`. That's
what stops the system summarizing things you haven't read.

## Where things live

```
inbox/            reading room — bot drops docs; you READ + annotate them
trinkets/         docs you've read (moved out of inbox); waiting to be filed
brain/            the knowledge
  bowers/<topic>/   distilled ideas — one claim per note
  sources/          one note per thing you read
  _topic_index.md   map of the bowers
tools.md          tools & gadgets shelf (plugins, repos) — not knowledge
to-clip.md        links the bot couldn't fetch — clip these manually
archive/          full clip originals, kept cold (leave alone)
digests/          dated synthesis notes Claude writes for you
forage.md         gaps the system spotted — thin spots to read into
_inbox.md         couldn't be auto-processed
```

## Reading a doc — leave marks

While reading a doc in `inbox/`, mark it up, then move it to `trinkets/`. The
drain lifts these marks into the source note:

- `==highlight==` — the interesting bit (these grow into `brain/` concept notes)
- `#dig` on a line — want to learn more
- `> ? question` — your open question
- `[text](url)` links — leads for further reading

## The point

Capture is cheap and automatic. The **digest** (`make digest`, run by you) does
the thinking — connections across notes, gaps, questions. Read it, leave
feedback at the bottom, and it steers the next one.
