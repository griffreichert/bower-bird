<p align="center">
  <img src="assets/logo-transparent.png" alt="bower-bird" width="320">
</p>

<h1 align="center">bower-bird</h1>

<p align="center"><em>Hatch a bowerbird to collect shiny bits of knowledge as you learn.</em></p>

---

A quiet knowledge-collation loop. Send a link to a Telegram bot from your phone;
your laptop drains it when awake, decides whether it's something to read later or
something you've already read, and files it into your notes vault — with proposed
links you accept or ignore.

The name fits the job: a bowerbird gathers found objects and curates them into a
bower. Same here — found links, curated into a linked knowledge vault.

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
| a link **+ a note**, or `read:` prefix | **learned** | a clipping is created with your note, plus **proposed** `[[backlinks]]` into your evergreen notes |

Your one-line "why" is the highest-value input: it turns *note + source* into a
linked evergreen note — proposed, for you to accept.

## How it works (no server)

Telegram's own servers hold the queue (~24h) until drained — no webhook, no
public host needed.

```
phone ──link──▶ Telegram bot ──(queued)──▶ laptop drains via getUpdates
                                              │
                                   route ┌────┴────┐ write
                                  to-read│         │learned
                                         ▼         ▼
                             reading-list.md   Clippings/ + proposed [[links]]
                                              │
                                         receipt back through the bot
```

Capture is instant; processing waits until the laptop is awake — fine for a
reading queue. Drain daily (or also copy links to Telegram **Saved Messages** as
a backstop) so nothing ages out of the 24h window.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11.

```bash
uv sync                       # create .venv, install deps
cp .env.example .env          # fill in TELEGRAM_BOT_TOKEN + ANTHROPIC_API_KEY
uv run python -m bower_bird    # drain the queue once
```

`TELEGRAM_BOT_TOKEN` comes from [@BotFather](https://t.me/BotFather);
`ANTHROPIC_API_KEY` from the [Anthropic Console](https://console.anthropic.com/).
The vault path auto-resolves from the `notes/` symlink, or set
`BOWER_VAULT_PATH`. See `.env.example` for all options.

Run it on a daily `cron`/`launchd` schedule, or by hand whenever you want to
process what's piled up.

## Roadmap

This is a learning project — building a harness-engineered agent loop, both to
learn harness engineering and to speed up side projects. Plain Python first, then
layer on the agent framework:

1. **Base** — prove the loop runs, calls a tool, reads the env.
2. **Identity** — research-operator: concise, proposes not asserts.
3. **Memory** — durable facts only: vault conventions, reading interests.
4. **Telegram** — drain via `getUpdates`. ✅ *(plain Python)*
5. **Skill: link → clipping note** — the heart of the project. ✅ *(plain Python)*
6. **One quiet cron** — daily digest; weekly reading-list groom. Silent when
   there's nothing to surface.
7. **Profile split** — formalise so the `coding` profile can start.

**Status:** early. Plain-Python ingestion works end-to-end (Telegram drain →
two-lane router → vault writes). Steps 1–3, 6–7 are next.

## Contributing

Architecture rules, invariants, the vault write boundary, the code map, and the
dev workflow live in [`CLAUDE.md`](CLAUDE.md).
