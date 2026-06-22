# CLAUDE.md

Operational guidance for working in this repo. For the project overview —
what it is, the no-server architecture, the capture model, and the roadmap — see
[`README.md`](README.md). This file is the rules and the map.

## Capture model (spec)

One Telegram bot, two lanes, told apart by how a message is sent:

- **bare link → to-read:** append to the reading list as `- [ ]` + title +
  one-line "what is it" (metadata, not a summary). No distillation.
- **link + a note, or `read:` prefix → learned:** create a clipping in
  `Clippings/` + propose `[[backlinks]]` into the evergreen layer.

## Invariants (must always hold)

Load-bearing. Don't regress them.

- **Research profile only.** Never gets repo-write, shell-exec, push, or merge
  permissions — that belongs to the separate `coding` profile.
- **Proposes, never asserts.** Creates clippings and *suggests* `[[links]]`;
  never edits raw/source notes without explicit per-session approval.
- **Never distill an article that hasn't been read.** The reading queue holds
  unread items (metadata only); the evergreen layer only ever gets what was
  actually read.
- **Output is pointers, not summaries.** It gives things to read and why they
  connect — it never replaces the reading.
- **Quiet by default.** Stay silent when there's nothing worth surfacing.
- **Idempotent.** An item already processed is never double-processed
  (Telegram offset + url dedup in `state.py`).
- **Session-budget aware.** Scope work to the remaining model/session allowance;
  downscale the model, prefer tasks that finish in the current session.

## Vault write boundary

The vault is **not** in this repo — it lives in iCloud (Obsidian), reachable via
the gitignored `notes/` symlink. At runtime the agent may write **only**:

- `Clippings/` — new clipping files
- `g/learning/reading-list.md` — the reading queue

Backlinks into the evergreen layer (`g/learning/`) are **proposed inside the
clipping**, never written into those raw notes. `ingest._assert_writable`
enforces this in code — keep it that way.

## Model / provider

First-party **Anthropic API**, model **`claude-haiku-4-5`** for all lanes —
cheap (capture + one daily digest), token budget is not a concern here. The
learned-lane synthesis can be bumped to `claude-sonnet-4-6` later if clippings
read thin; per-lane model is a one-line config change. Structured output uses
`messages.parse` with a pydantic model (`llm.ClippingPlan`).

## Repo layout

```
src/bower_bird/
  config.py    env + vault paths + model (frozen Config)
  state.py     telegram offset + url dedup (idempotency)
  telegram.py  getUpdates drain + send receipt
  router.py    two-lane classification (bare link vs link+note / read:)
  fetch.py     page metadata (title/description/excerpt)
  llm.py       Anthropic calls: describe_link, synthesize_clipping (pydantic)
  ingest.py    vault writes (reading-list / Clippings), guarded
  app.py       drain orchestration (one pass)
  __main__.py  `python -m bower_bird`
tests/         router unit tests (pure logic, no network)
notes/         symlink into the Obsidian vault (gitignored)
```

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
