# CLAUDE.md

Operational guidance for working in this repo. For the project overview —
what it is, the no-server architecture, the capture model, and the roadmap — see
[`README.md`](README.md). This file is the rules and the map.

## Capture model (spec)

Two capture entry points, both landing in the owned `BowerBird/` folder:

- **Telegram bot** (Tier-1 Haiku pull):
  - **bare link → to-read:** append to `reading-list.md` as `- [ ]` + title +
    one-line "what is it" (metadata, not a summary). No distillation.
  - **bare X/Twitter link → tweet doc:** tweet text resolves via a proxy
    chain (fxtwitter → syndication CDN, `resolve.py` — no browser, no paid
    API) into one rendered doc per tweet in `tweets/` (its own reading room,
    apart from the article `inbox/`). Move a doc to `trinkets/` to keep it
    (marks optional at tweet scale); unmoved docs age out via the let-go
    sweep — default is discard.
  - **bare link the bot can't read or resolve → to-clip (residue):** other
    JS-/login-walled pages (known domains skip fetch; others caught by a thin
    fetch), plus the rare tweet the proxy chain misses, go to `to-clip.md` as
    a `- [ ]` checklist. Open in a browser, Web Clipper into `inbox/`, then
    the clip lane reads + files it. `bb drain` retries the queue's unchecked
    X links through the resolver.
  - **link + a note, or the word `read` (any case, before/after the link) →
    learned:** create a source note in `sources/` + assert `[[links]]` into
    `notes/`. The bare `read` marker fits the share-sheet flow (link first,
    then type). X links resolve through the same proxy chain first — a tweet
    read on X itself goes straight to the graph, skipping the digest queue
    (the read already happened out there).
  - **`tool:` prefix + link → tools shelf:** append to `tools.md` (title +
    your note + one-line). A keep-for-later shelf of plugins/repos/tools —
    pure recall, NOT knowledge, never enters `brain/`. `tool:` with no link →
    `_inbox.md`. (Distinct from a tool *named inside a read source* — that
    becomes a `brain/tools/` leaf node; see Entity leaf nodes below.)
  - **unprocessable (no link / junk) → `_inbox.md`** with a reason. Nothing
    dropped.
- **Obsidian Web Clipper → `inbox/`:** the Tier-1 pull reads the clean clip
  body (skips `fetch`), writes a `sources/` note + links, moves the original to
  `archive/`. A clip counts as *read* → processed immediately.

**Entity leaf nodes (`llm.EntityRef`, `ingest.create_leaf_note`).** A read
source often names things worth their own node: **tools** (repos/libraries) →
`brain/tools/`, **people** (authors/creators) → `brain/people/`. Haiku extracts
these into `ClippingPlan.tools/people`, grounded on URLs pulled from the body
(`marks.extract_urls`). Each is an **unquizzed leaf** — linked to concepts +
backlinked from the source, but never minted as a bower and never seeded into
the review store (no Feynman payload, no `peck`).

**Reader marks (in a clip body, `marks.py`).** The source note is thin —
provenance + the reader's marks, **not the article body** (the full clip stays
cold in `archive/`). Four optional marks the reader leaves while reading:
`==highlight==` (the durable unit — interesting/supports my knowledge → `##
Highlights`, Tier-2 grows into nest concepts), `#dig` on a line (learn more → `##
Dig deeper`), `> ? question` (my open question → `## Open questions`), and
outbound `[text](url)` links (→ `## Further reading`, unread leads). Highlights
anchor Haiku's concept/backlink proposal.

## Invariants (must always hold)

Load-bearing. Don't regress them. Full text:
[`notes/INVARIANTS.md`](notes/INVARIANTS.md).

- **Research profile only.** Never gets push or merge permissions — that
  belongs to the separate `coding` profile.
- **Owns `BowerBird/`; touches nothing outside it.** Every runtime write lands
  under the owned folder.
- **Additive-autonomous.** Inside `BowerBird/` it creates notes and asserts
  `[[links]]` without asking, but **never rewrites or deletes human-authored
  text** — only appends.
- **Never distill an article that hasn't been read.** The reading queue holds
  unread items (metadata only); `sources/`/`notes/` only get what was read. A
  Web Clipper save counts as read; a bare link does not.
- **Output is pointers, not summaries** for unread items.
- **Quiet by default.** Stay silent when there's nothing worth surfacing.
- **Idempotent.** Already-processed items are never double-processed — Telegram
  offset + url dedup + clip content-hash, all in `state.py` (in the repo, so it
  survives archiving).
- **Session-budget aware.** Scope work to the remaining model/session allowance.

## Vault write boundary

The vault is **not** in this repo — it lives in iCloud (Obsidian). bower-bird
owns one folder there, `BowerBird/` (`config.vault_path`), and writes **nowhere
else**. Writable at runtime: `inbox/`, `tweets/`, `brain/sources/`,
`brain/bowers/`, `brain/people/`, `brain/tools/`, `archive/` (incl. `unread/`),
`reading-list.md`, `to-clip.md`, `tools.md`, `let-go.md`, `_inbox.md`,
`digests/`.

Writes are additive: new files, or `_append_link` appends under `## Links` —
existing notes are never rewritten. `ingest._assert_writable` enforces the
folder boundary in code — keep it that way. (The `notes/` symlink in the repo
points at the project's *planning* notes, a different folder from the runtime
`BowerBird/`.)

## Model / provider — two tiers

Synthesis is split by cost shape (decided 2026-06-24):

- **Tier 1 — per-item (this Python app):** first-party **Anthropic API**,
  **`claude-haiku-4-5`**. The cron pull reading inbox/Telegram and writing
  baseline `sources/` notes + links. Cheap, automatable. Structured output uses
  `messages.parse` with a pydantic model (`llm.ClippingPlan`).
- **Tier 2 — whole-graph (Claude Code, not this app):** the deep `weave` pass —
  cross-corpus synthesis + lint — runs on the **Max subscription** via Claude
  Code, driven by `BowerBird/CLAUDE.md`. No API tokens; you-triggered (`make
  weave`), never crond (subscription-in-cron is ToS-gray).

Keep the Python path Haiku-only; do **not** route whole-graph synthesis through
the API. Per-lane Tier-1 model is a one-line config change if a lane reads thin.

## Repo layout

```
src/bower_bird/
  config.py    env + owned-folder (BowerBird/) paths + model (frozen Config)
  state.py     telegram offset + url + clip-hash dedup (idempotency)
  telegram.py  getUpdates pull + send receipt
  router.py    lane classification (tool: / bare link / link+note / read:)
  marks.py     reader marks pulled from a clip body (highlight/dig/question/links)
  fetch.py     page metadata (title/description/excerpt)
  llm.py       Anthropic calls: describe_link, synthesize_clipping (pydantic)
  ingest.py    owned-folder writes (sources/notes/reading-list/to-clip/tools/_inbox), guarded
  inbox.py     clipper inbox scan: clip -> source note + links -> archive
  app.py       pull orchestration (Telegram queue + clipper inbox)
  __main__.py  `python -m bower_bird`
tests/         router + ingest/inbox unit tests (pure logic, no network)
scripts/       launchd install/uninstall, weave.sh (Claude Code Tier-2)
notes/         symlink into the Obsidian vault — planning notes (gitignored)
```

The Tier-2 synthesis playbook lives at `BowerBird/CLAUDE.md` inside the vault
(loaded by Claude Code when run there), not in this repo.

Data schemas are **pydantic models**; `Config`/`State` stay plain (plumbing, not
contracts).

## Dev workflow

- **Env:** `uv` (Python ≥ 3.11, pinned 3.13). `uv sync` builds `.venv` and
  installs deps + the `dev` group.
- **Lint/format:** `uv run ruff check .` / `uv run ruff format .`.
- **Hooks:** `uv run pre-commit install && uv run pre-commit install --hook-type
  commit-msg`. Local hooks run ruff (check `--fix`, format) and commitizen.
- **Commits:** Conventional Commits, enforced by commitizen (`cz check` on
  commit-msg). Bump with `uv run cz bump`.
- **Tests:** `uv run python tests/test_router.py`.
