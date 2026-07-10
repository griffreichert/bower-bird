---
name: fetch
description: Retrieve what the vault already knows about a topic — an agentic query over the knowledge graph that assembles a briefing from brain/ concept notes, their sources, and cross-bower links. Read-only. Use when the user asks "what do I know about X?", "fetch/pull everything on X", or wants a recall briefing on a topic before writing or reading.
---

# fetch

The **deep-query verb** of the graph. A topic goes in; a briefing of everything
the vault holds on it — *and the related ideas it connects to* — comes out. The
point is depth: **traverse the knowledge graph outward** from the topic, don't
just grep-and-list. `peck` tests what you know, `forage` finds what to read next,
and **`fetch` goes deep on a topic the user names.**

Runs on this Claude Code session's model (subscription, no API tokens) — the
retrieval is agentic reasoning over markdown, which is why it lives here and not
in the cheap Haiku path.

## Read-only — the hard line

fetch **writes nothing**. No new notes, no `[[link]]` asserts, no digest. It only
reads. That is what separates it from `weave` (which synthesizes and appends to
the graph). If the user wants a connection you found made permanent, say so and
point them at `weave` — don't assert it here.

The vault is ~30 concept notes + ~30 sources of plain markdown. **Grep + read +
follow wikilinks beats any index or embedding at this scale** — do not build
retrieval infrastructure. The graph is small enough to walk.

## Loop

**First, resolve the vault path** (the owned `BowerBird/` folder lives in iCloud,
not the repo):

```bash
uv run python -c 'from bower_bird.config import load_config; print(load_config().vault_path)' | tail -1
```

All paths below (`brain/`, `_topic_index.md`, …) are relative to that. Topic =
the user's phrase; expand it to obvious synonyms before searching (e.g.
"attention" → attention, KV cache, transformer, self-attention).

1. **Map the topic to bowers.** Read `brain/_topic_index.md` — the table of
   bowers and what lives in each. Pick the bower(s) the topic touches; note
   named notes it lists.

2. **Search the knowledge layer.** Grep `brain/bowers/` and `brain/sources/` for
   the topic + synonyms (titles, body, tags). Collect every hit.

3. **Traverse outward — this is the job.** Read each matched concept note, then
   **follow its `[[links]]` / `## Sources` / `## Links` wikilinks and keep
   walking** into related ideas, hop by hop, as long as each new note is still
   relevant to the topic. Related concepts in *other* bowers are the payoff — a
   note on "attention" that links to one on "inference cost" is exactly the
   connection the user wants surfaced. Stop a branch when it drifts off-topic;
   don't cap at a fixed hop count, cap on relevance. Track visited notes so you
   don't loop.

4. **Ground each claim in its source.** A concept note is a claim; its provenance
   is either a `source:` frontmatter URL or a `[[wikilink]]` to a `brain/sources/`
   note (both flavors exist — handle both). Pull the URL so the user can jump to
   the original in Obsidian.

5. **Rank and assemble.** Order by relevance to the topic. Build the briefing
   below. Keep it scannable — pointers and claims, not essays.

## Output — the briefing (in chat)

- **What you know** — the on-topic concept notes as one-line claims, `[[linked]]`
  by their real note title (so they're clickable in Obsidian), grouped by bower.
- **Sources** — where each claim came from: `source:` URL or source-note title.
- **Connections** — cross-bower `[[a]] ↔ [[b]]` pairs the topic bridges, one-line
  why. Report them; do **not** assert them (that's `weave`).
- **Thin spots** — sub-topics the graph is quiet or silent on. Hand off: "run
  `forage` to find reads that fill this."
- **Arrived, unread** *(only if any)* — on-topic items sitting unread in `inbox/`
  or `tweets/`, found via `pull … (unread)` lines in `brain/_log.md` (title +
  link, one per line). **Pointers only, clearly separated** — this is the
  two-streams rule: never blur read knowledge with auto-pulled arrivals, and
  never distill an unread item into the answer.

If the graph holds nothing on the topic, say so plainly and suggest `forage`.

## Not this skill

- **Quiz me on what I know** → `peck`.
- **What should I read next / where are the gaps** → `forage`.
- **Synthesize new connections into the graph, write a digest** → `weave` (it
  writes; fetch never does).
- **Capture a link/clip** → the `pull` pass (Tier-1).
