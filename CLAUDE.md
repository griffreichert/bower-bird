# CLAUDE.md

Operational guidance for working in this repo. For the project overview —
what it is, the no-server architecture, the capture model, and the roadmap — see
[`README.md`](README.md). This file is the rules and the map.

## Capture model (spec)

**Antilibrary (2026-07-13, #12/#13/#22): every send is shelved into `brain/`
immediately — no read gate, no reading rooms, no read keyword.** Recall
(`peck`), not reading, is the learning event. The layer split is Zettelkasten
(decided 2026-07-16): `brain/sources/` are literature notes — one source, full
text inlined below the fold (uniform rule, no size exception), detailed key
ideas, accumulating reader highlights; `brain/bowers/` are permanent notes —
atomic concepts, multi-source, where the thinking lives. Pipeline: ingest →
read → learn. Two capture entry points, both
landing in the owned `BowerBird/` folder:

- **Telegram bot** (Tier-1 Haiku pull):
  - **any link (bare, or with a note) → shelve:** fetch/render the body and
    write a source node in `brain/sources/` right away — frontmatter
    (id/title/source/author/created/tags), claim-shaped `## Key ideas`, the
    note (if any) verbatim under `## Seed thoughts`, `## Links` into the
    concept graph, and the full rendered body inlined under `## Body` (the
    node is canonical; `archive/` is a recycle bin).
  - **X/Twitter link → per-tweet source node:** tweet text resolves via a
    proxy chain (fxtwitter → syndication CDN, `resolve.py` — no browser, no
    paid API) and shelves like any other link — one node per tweet, thin key
    ideas welcome (`peck d` is the triage). No digest, no `tweets/` reading
    room, no default-discard. Native X Articles (`x.com/i/article/…` behind
    the tweet) shelve with the full article body pulled from fxtwitter's
    `article` payload; the syndication fallback carries no article (rare
    miss → media-only → to-clip).
  - **bare PDF link (`.pdf` path or arXiv `/pdf/`) → shelve via Sonnet:**
    download → `pypdf` text extraction → shelve. This is the ONE lane whose
    `synthesize_clipping` runs on `LLMSettings.paper_model` (Sonnet) — Haiku
    thins out on dense multi-page papers. No extractable text (scanned) or
    fetch failure → `to-clip.md`.
  - **pasted prose (no link) → source node:** the sender is the author, the
    text is the immutable `## Body`. Your own thinking is a source too.
  - **link the bot can't read or resolve → to-clip (residue):** JS-/login-
    walled pages (known domains skip fetch; others caught by a thin fetch),
    plus the rare tweet the proxy chain misses, go to `to-clip.md` as a
    `- [ ]` checklist. Open in a browser, Web Clipper into `inbox/`, then the
    clip lane shelves it. `bb drain` retries the queue's unchecked X links
    through the resolver.
  - **`tool:` prefix + link → tools shelf:** append to `tools.md` (title +
    your note + one-line). A keep-for-later shelf of plugins/repos/tools —
    pure recall, NOT knowledge, never enters `brain/`. `tool:` with no link →
    `_inbox.md`. (Distinct from a tool *named inside a source* — that becomes
    a `brain/tools/` leaf node; see Entity leaf nodes below.)
  - **genuinely unprocessable (empty / media-only / junk) → `_inbox.md`**
    with a reason. Nothing dropped. This is the only thing `_inbox.md` is for.
- **Obsidian Web Clipper → `inbox/`:** a transient drop target only — the
  Tier-1 pull consumes it within one tick (reads the clean clip body, skips
  `fetch`), writes the source node, moves the original to `archive/`. Never a
  queue; nothing waits in `inbox/` for a human step.

**Distillation is one-shot, at ingest.** `synthesize_clipping` runs once per
capture; marks added later never re-distill — `weave` owns distillation-layer
refresh. Key ideas are 3–6 standalone testable claims, graded by signal:
clipper `==highlights==` > Telegram seed thought > body on its own terms;
archetype-adaptive (rich essay → several claims; single-claim tweet → one thin
one; link-list → few/none, outbound links harvested). Concept notes get bare
stubs + `[[links]]` only at ingest — earned synthesis substance is weave's job,
never ingest's.

**Entity leaf nodes (`schema.EntityRef`, `ingest.create_leaf_note`).** A
source often names things worth their own node: **tools** (repos/libraries) →
`brain/tools/`, **people** (authors/creators) → `brain/people/`. Haiku extracts
these into `ClippingPlan.tools/people`, grounded on URLs pulled from the body
(`marks.extract_urls`). Each is an **unquizzed leaf** — linked to concepts +
backlinked from the source, but never quizzed or enrolled in review.

**Reader marks (in a clip body, `marks.py`).** Optional signals the reader
leaves in a clip before it's shelved: `==highlight==` (top of the signal
ladder for key-idea extraction → `## Highlights`), `#dig` on a line (learn
more → `## Dig deeper`), `> ? question` (my open question → `## Open
questions`), and outbound `[text](url)` links (→ `## Further reading`).

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
- **Everything sent is shelved.** No reading gate — every send becomes a brain
  node immediately (antilibrary model, 2026-07-13). Recall (peck + the LLM
  judge), not reading, is the learning event.
- **Distill at ingest.** Key ideas are captured onto the node from the source
  body + any human seed thoughts sent alongside it, never invented.
- **Quiet by default.** Stay silent when there's nothing worth surfacing.
- **Idempotent.** Already-processed items are never double-processed — Telegram
  offset + url dedup + clip content-hash, all in `state.py` (in the repo, so it
  survives archiving).
- **Session-budget aware.** Scope work to the remaining model/session allowance.

## Vault write boundary

The vault is **not** in this repo — it lives in iCloud (Obsidian). bower-bird
owns one folder there, `BowerBird/` (`config.vault_path`), and writes **nowhere
else**. Writable at runtime: `inbox/` (clipper drop target), `brain/sources/`,
`brain/bowers/`, `brain/people/`, `brain/tools/`, `archive/`, `to-clip.md`,
`tools.md`, `_inbox.md`, `digests/`.

Writes are additive: new files, or `append_link` appends under `## Links` —
existing notes are never rewritten. `ingest.assert_writable` enforces the
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

Keep the Python path Haiku by default; do **not** route whole-graph synthesis
through the API. Per-lane overrides live on `LLMSettings` — currently just the
PDF lane's `paper_model` (Sonnet), because Haiku thins out on dense papers.

## Repo layout

```
src/bower_bird/
  config.py    env + owned-folder (BowerBird/) paths + LLMSettings (frozen Config)
  schema.py    pydantic data contracts (Lane/Parsed, PageMeta, TweetText,
               ClippingPlan, EntityRef, QuizQuestion, JudgeVerdict, Review,
               ReviewEntry)
  state.py     telegram offset + url + clip-hash dedup (idempotency)
  telegram.py  getUpdates pull + send receipt
  router.py    lane classification (tool: / shelve / paste / no_link)
  marks.py     reader marks pulled from a clip body (highlight/dig/question/links)
  fetch.py     page metadata fetch (title/description/excerpt)
  resolve.py   X/Twitter tweet text resolution via proxy chain
  llm.py       Anthropic calls: describe_link, synthesize_clipping, peck's
               generate_question/judge_answer (pydantic structured output)
  ingest.py    vault-write boundary guard (assert_writable) + bits shared by
               nodes.py/queues.py (safe_filename, yaml_scalar, format_tweet_body)
  nodes.py     graph-node writes: source notes, leaf notes (people/tools),
               [[links]], the _index.md catalog, the _log.md activity log
  queues.py    flat-file shelf/queue appends: to-clip.md, tools.md, _inbox.md
  inbox.py     clipper inbox scan: clip -> source node + links -> archive
  app.py       pull orchestration (Telegram queue + clipper inbox)
  review.py    spaced-rep ReviewStore + peck quiz loop (stateful; ShelfCensus
               stays here too — its from_store() takes a ReviewStore)
  lint.py      read-only structural graph lint (orphans, broken links)
  __main__.py  `python -m bower_bird`
tests/         router + ingest/inbox unit tests (pure logic, no network)
scripts/       launchd install/uninstall, weave.sh, backfill_bodies, pilot_ingest
notes/         symlink into the Obsidian vault — planning notes (gitignored)
```

The Tier-2 synthesis playbook lives at `BowerBird/CLAUDE.md` inside the vault
(loaded by Claude Code when run there), not in this repo.

Data schemas are **pydantic models**; `Config`/`State` stay plain (plumbing, not
contracts).

## Code standards (repo-specific)

General rules live in the maintained skills (purge-slop, review-slop,
pydantic-principles) — don't restate them here. Local conventions:

- **Pydantic data contracts live in `schema.py`.** New/rewritten models land
  there. Stateful classes with behavior (e.g. `ReviewStore`) stay in their
  module; so does a model whose methods would pull a package import into
  `schema.py` and create a cycle (e.g. `ShelfCensus`, bound to `ReviewStore`).
- **`Config()` is the loader.** No wrapper loaders — validation and env
  handling (e.g. the ANTHROPIC_API_KEY launchd export) live inside the class
  via field validators and `model_post_init`.
- **LLM tunables live on `LLMSettings`** in `config.py`: model names (incl.
  per-lane overrides like the PDF lane's Sonnet) and per-call max-token caps.
  No scattered module-level `_MAX_*` constants.
- **Module-level functions are importable** — no `_` prefix outside classes;
  names say what the function does.
- **Structure:** flat package by design at this size; don't add subpackages.
  `ingest.py` split into `ingest.py` (boundary guard + shared bits) /
  `nodes.py` (graph-node writers) / `queues.py` (flat-file shelf/queue
  appenders) in the post-#25 structure pass (#28).

## Dev workflow

- **Env:** `uv` (Python ≥ 3.11, pinned 3.13). `uv sync` builds `.venv` and
  installs deps + the `dev` group.
- **Lint/format:** `uv run ruff check .` / `uv run ruff format .`.
- **Hooks:** `uv run pre-commit install && uv run pre-commit install --hook-type
  commit-msg`. Local hooks run ruff (check `--fix`, format) and commitizen.
- **Commits:** Conventional Commits, enforced by commitizen (`cz check` on
  commit-msg). Bump with `uv run cz bump`.
- **Tests:** `uv run python tests/test_router.py`.
