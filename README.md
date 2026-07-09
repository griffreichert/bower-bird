<p align="center">
  <img src="assets/logo-transparent.png" alt="bower-bird" width="320">
</p>

<h1 align="center">bower-bird</h1>

<p align="center"><em>Hatch a bowerbird to collect shiny bits of knowledge as you learn.</em></p>

---

A quiet knowledge-collation loop. Send a link to a Telegram bot from your phone;
your laptop pulls it when awake, decides whether it's something to read later or
something you've already read, and files it into your notes vault — with proposed
links you accept or ignore.

The name fits the job: a bowerbird gathers found objects and curates them into a
bower. Same here — found links, curated into a linked knowledge vault.

## What it demonstrates

A production-shaped **agent harness**, built plain-Python-first so the moving
parts stay legible:

- **Agent loop / lifecycle** — capture → court → peck. Cron-driven, idempotent,
  resumable from durable state; no orchestration framework hiding the control
  flow.
- **LLM-authored eval payloads** — at ingest, Haiku mints a Feynman-style quiz
  (question + model answer) for each load-bearing concept via structured output.
  The `peck` loop then runs active recall against it, grading **STRONG / WEAK /
  WRONG** on a Leitner ladder. *(Auto-grading the recall — LLM-as-judge — is the
  in-progress step; today the grade is self-scored against the model answer.)*
- **Cost-tiered model routing** — per-item work runs on **Haiku** (cheap,
  cron-safe, structured output via `messages.parse`); whole-graph synthesis runs
  on a **larger model** through Claude Code. Cost shape decides the tier, not
  convenience.
- **Production thinking** — the invariants below are enforced in code: additive
  writes only, human text never clobbered, idempotent dedup, a hard vault-folder
  boundary, and least blast radius on anything unattended.

## What it is (and isn't)

- **Is:** the **`research` profile** — a quiet capture loop. Send a link →
  captured → either queued to read or filed as a clipping with proposed
  `[[backlinks]]` → a quiet daily digest.
- **Isn't:** a coding agent. That's a separate `coding` profile, with its own
  repo, memory, permissions, and credentials. The two share only the Telegram
  front door. The split exists because the blast radius differs: coding needs
  repo-write, shell, and a real prompt-injection surface; research needs none of
  that.

It **proposes, never asserts**: it drafts clippings and suggests links — you
decide what to keep. It never summarises something you haven't read, and it stays
quiet when there's nothing to surface.

## What it does

One bot, told apart by **how** you send:

| You send | Lane | What happens |
| --- | --- | --- |
| a bare link | **to-read** | appended to your reading list with a one-line "what is it" (metadata, not a summary) |
| an X/Twitter link | **tweet digest** | tweet text resolved (fxtwitter → syndication fallback) into a batched daily digest doc in `tweets/` — read it like a newsletter, mark what's worth keeping; **only marked tweets enter the graph**, the rest are let go. Already read it on X? Add a note or just the word `read` and it skips the queue |
| a link the bot can't read or resolve (login-walled) | **to-clip** | queued for a browser Web Clipper, then read + filed |
| a link **+ a note**, or the word `read` anywhere | **learned** | a clipping is created with your note, plus **proposed** `[[backlinks]]` into your evergreen notes |
| `tool:` **+ link** | **tools shelf** | appended to a keep-for-later shelf — pure recall, never enters the knowledge graph |

Your one-line "why" is the highest-value input: it turns *note + source* into a
linked evergreen note — proposed, for you to accept.

## How it works (no server)

Telegram's own servers hold the queue (~24h) until pulled — no webhook, no
public host needed.

```
phone ──link──▶ Telegram bot ──(queued)──▶ laptop pulls via getUpdates
                                              │
                                   route ┌────┴────┐ write
                                  to-read│         │learned
                                         ▼         ▼
                             reading-list.md   sources/ + proposed [[links]]
                                              │
                                         receipt back through the bot
```

Capture is instant; processing waits until the laptop is awake — fine for a
reading queue. Pull daily (or also copy links to Telegram **Saved Messages** as
a backstop) so nothing ages out of the 24h window.

## Design principles

Load-bearing invariants, enforced in code — not aspirations. Full text in
[`notes/INVARIANTS.md`](notes/INVARIANTS.md).

- **Enhance, never replace.** Your own reading, highlights, and writing are never
  erased or summarised away. The curated distillation may grow; your thinking is
  never clobbered — writes are additive only.
- **Never distill the unread.** Only what you've actually read enters the graph.
  Unread items get a pointer (title + one line), never a summary. The fluency
  illusion — mistaking reading for understanding — is the enemy.
- **Containment.** Owns one vault folder, touches nothing outside it; the folder
  boundary is asserted in `ingest._assert_writable`.
- **Least blast radius.** Unattended work is least-privilege and non-destructive;
  high-stakes or untrusted-web actions are gated behind a deliberate human step.
- **Nothing is lost.** Nothing is hard-deleted or dropped silently — processed
  items are archived, unprocessable ones logged with a reason.
- **Quiet by default; idempotent.** Silent when there's nothing to surface; an
  already-processed item is never processed twice (Telegram offset + URL dedup +
  clip content-hash).

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11.

```bash
uv sync                       # create .venv, install deps
cp .env.example .env          # fill in TELEGRAM_BOT_TOKEN + ANTHROPIC_API_KEY
uv run python -m bower_bird    # pull the queue once
```

`TELEGRAM_BOT_TOKEN` comes from [@BotFather](https://t.me/BotFather);
`ANTHROPIC_API_KEY` from the [Anthropic Console](https://console.anthropic.com/).
The vault path auto-resolves from the `notes/` symlink, or set
`BOWER_VAULT_PATH`. See `.env.example` for all options.

Run it on a daily `cron`/`launchd` schedule, or by hand whenever you want to
process what's piled up.

## Status

Plain-Python capture works end-to-end: Telegram pull → four-lane router →
guarded vault writes (`sources/` notes + proposed `[[links]]`, entity leaf nodes
for tools/people), Web Clipper inbox, daily digest.

The **grow loop** — `peck`, spaced-rep review over due bowers — runs end-to-end
with self-scored recall. Next: an **LLM-as-judge** grade so recall is scored
automatically, then formalise the `research`/`coding` profile split.

## Contributing

Architecture rules, invariants, the vault write boundary, the code map, and the
dev workflow live in [`CLAUDE.md`](CLAUDE.md).
