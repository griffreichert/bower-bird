<p align="center">
  <img src="assets/logo-transparent.png" alt="bower-bird" width="320">
</p>

<h1 align="center">bower-bird</h1>

<p align="center"><em>Hatch a bowerbird to collect shiny bits of knowledge as you learn.</em></p>

<!-- demo: uncomment once assets/demo.gif is recorded (see scripts/demo-record.sh) -->
<!--
<p align="center">
  <img src="assets/demo.gif" alt="bower-bird demo: capture → note lands → peck (LLM-as-judge)" width="720">
</p>
-->

---

A quiet antilibrary. Send a link (or paste prose, or forward a tweet) to a
Telegram bot from your phone; your laptop pulls it when awake and shelves it
straight into your notes vault as a linked knowledge node — no queue to
triage, no read gate first.

The name fits the job: a bowerbird gathers found objects and curates them into
a bower. Same here — found links, curated into a linked knowledge vault.

## What it demonstrates

A production-shaped **agent harness**, built plain-Python-first so the moving
parts stay legible:

- **Agent loop / lifecycle** — capture → gather → peck. Cron-driven, idempotent,
  resumable from durable state; no orchestration framework hiding the control
  flow.
- **LLM-authored evals, LLM-as-judge grading** — the `peck` loop generates a
  fresh quiz question at recall time (depth scales with how well you know the
  node), grades your typed answer **strong / weak / wrong** via an LLM judge
  with a one-line rationale — you accept or override — and the grade drives a
  Leitner ladder. A real eval loop, not self-assessment.
- **Cost-tiered model routing** — per-item work runs on **Haiku** (cheap,
  cron-safe, structured output via `messages.parse`); whole-graph synthesis runs
  on a **larger model** through Claude Code. Cost shape decides the tier, not
  convenience.
- **Production thinking** — the invariants below are enforced in code: additive
  writes only, human text never clobbered, idempotent dedup, a hard vault-folder
  boundary, and least blast radius on anything unattended.

## What it is (and isn't)

- **Is:** the **`research` profile** — an antilibrary. Every send is shelved
  immediately into a linked knowledge graph; recall, not reading, is the
  learning event.
- **Isn't:** a coding agent. That's a separate `coding` profile, with its own
  repo, memory, permissions, and credentials. The two share only the Telegram
  front door. The split exists because the blast radius differs: coding needs
  repo-write, shell, and a real prompt-injection surface; research needs none of
  that.

It **proposes, never asserts**: it drafts source nodes and suggests
`[[backlinks]]` — you decide what to keep. It never invents claims from a
source it didn't fetch, and it stays quiet when there's nothing to surface.

## What it does

One bot, told apart by **what** you send — every lane shelves into
`brain/sources/` as a full-text literature note (frontmatter, key ideas, the
rendered body inlined below the fold). Nothing waits in a reading queue.

| You send | Lane | What happens |
| --- | --- | --- |
| a bare link, or a link + a note | **shelve** | body fetched/rendered and shelved into `brain/sources/` — your note (if any) kept verbatim, key ideas drafted, `[[links]]` proposed into the concept graph |
| an X/Twitter link | **tweet node** | tweet text resolved (fxtwitter → syndication fallback) into its own source node — one node per tweet. A native X Article behind the tweet shelves with its full body pulled from fxtwitter's article payload |
| a link-wrapper tweet (a tweet that's just a link) | **routes to target** | the wrapped link is shelved instead of the tweet itself |
| pasted prose, no link | **source node** | you're the author — the pasted text becomes the immutable body |
| a PDF link (`.pdf` path, or arXiv) | **shelve via Sonnet** | downloaded, text-extracted (`pypdf`), shelved — the one lane that runs the paper-sized model instead of Haiku |
| a link the bot can't read/resolve (login-walled, media-only) | **to-clip** | queued in `to-clip.md`; open in a browser, Web Clipper into `inbox/`, and it shelves on the next pull |
| `tool:` **+ link** | **tools shelf** | appended to `tools.md` — pure recall, never enters the knowledge graph |

Every send gets a one-glyph receipt back through the bot so the outcome is
readable from the notification alone.

## How it works (no server)

Telegram's own servers hold the queue (~24h) until pulled — no webhook, no
public host needed.

```
phone ──link/note──▶ Telegram bot ──(queued ~24h)──▶ laptop pulls via getUpdates
                                                        │  (cron, idempotent)
                                             route ┌────┴────┐
                                                    ▼         ▼
                                          shelve (Haiku)   to-clip.md (residue) ┐ Tier 1
                                                    │         │ browser + Web    │ (Haiku, per-item)
                                                    │         │ Clipper → inbox/ ┘
                                                    ▼         ▼
                                          brain/sources/ ◀── gather
                                          (literature notes, full-text)
                                                    │
                                          ┌─────────┴──────────────┐
                                          ▼                        ▼
                                 peck: recall quiz,        weave: whole-graph  ┐ Tier 2
                                 LLM-as-judge grade,       synthesis into      │ (Claude Code,
                                 Leitner scheduling        brain/bowers/       ┘  subscription)
```

Capture is instant; processing waits until the laptop is awake — pull daily
(or also copy links to Telegram **Saved Messages** as a backstop) so nothing
ages out of the 24h window.

## The graph: sources → bowers

A Zettelkasten split, not one flat pile:

- **`brain/sources/`** — literature notes. One per source, full text inlined
  below the fold, plus drafted key ideas and your accumulating highlights. The
  antilibrary itself: everything you've sent, whether or not you've read it
  yet.
- **`brain/bowers/`** — permanent notes. Atomic, multi-source, where the
  thinking actually lives. Built by `weave`, never by ingest.

Pipeline: **ingest** (shelve a source, draft key ideas) → **read** (in
Obsidian, at your leisure — highlight, `#dig`, ask questions in the margins) →
**learn** (`peck` recall against the key ideas; `weave` synthesizes across
sources into `bowers/`).

## Verbs and skills

- **`bb lint`** — read-only structural graph lint: orphans, broken links.
- **`bb peck`** — spaced-repetition recall session over due source nodes;
  Leitner-scheduled, LLM-as-judge graded, teach-first (a never-quizzed node
  shows you its key ideas before it ever quizzes you).
- **`bb drain`** — retries the `to-clip.md` queue's unchecked X links through
  the resolver.
- **`/forage`** (Claude Code skill) — outward web hunt from a gap signal or
  topic; proposes reads, never writes to the vault.
- **`weave`** (Tier-2, `make weave`) — the deep, subscription-side pass:
  whole-graph synthesis into `brain/bowers/` plus lint, driven by the vault's
  own `BowerBird/CLAUDE.md` playbook. You-triggered, never crond.

## Design principles

Load-bearing invariants, enforced in code — not aspirations. Full text in
[`notes/INVARIANTS.md`](notes/INVARIANTS.md).

- **Enhance, never replace.** Your own reading, highlights, and writing are never
  erased or summarised away. The curated distillation may grow; your thinking is
  never clobbered — writes are additive only.
- **Everything is shelved, nothing is gated.** No read gate: every send becomes
  a brain node immediately. Recall (`peck` + the LLM judge), not reading, is
  the learning event.
- **Containment.** Owns one vault folder, touches nothing outside it; the folder
  boundary is asserted in `ingest.assert_writable`.
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

## Obsidian

To view your BowerBird graph nodes easily, use this filter in graph view:
```
path:BowerBird/brain -path:"/_"
```

## Contributing

Architecture rules, the capture model spec, the invariants, the vault write
boundary, the code map, and the dev workflow live in
[`CLAUDE.md`](CLAUDE.md). Built AI-assisted with Claude Code; commit history
keeps the co-author trailers.
