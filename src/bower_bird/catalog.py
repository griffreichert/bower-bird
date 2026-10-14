import re
from collections import Counter
from datetime import datetime
from math import log1p

from bower_bird.config import Config, LLMSettings
from bower_bird.ingest import assert_writable
from bower_bird.recall import tokenize

_INDEX_HEADER = """\
---
title: Index
bower: generated
tags:
  - index
---
# Index

The graph catalog — one line per brain/ page. `build` upserts this incrementally
and reads it as its link-candidate lookup; `weave` repairs it. Format:
`- [[title]] · category · one-liner`.

## Pages
"""

_LOG_HEADER = """\
---
title: Log
bower: generated
tags:
  - log
---
# Log

Append-only activity log — one line per build. `weave` reads it to run
incrementally (touch only what changed since the last pass).

"""


def read_index(config: Config) -> str:
    """Return the raw `_index.md` catalog, or '' if it doesn't exist yet.

    This is the ONLY link-candidate source `build` reads — a single compact
    catalog file (one line per page) — so per-item cost stays bounded as the
    graph grows, instead of rglob-ing and opening every page to read its title.
    """
    path = config.index_path
    return path.read_text(encoding="utf-8") if path.exists() else ""


def upsert_index_line(
    config: Config, title: str, category: str, oneline: str, insert_only: bool = False
) -> None:
    """Add or replace this page's catalog line, keyed on `[[title]]`. Idempotent.

    Incremental: touches only this one line (O(1)), never rescans the graph —
    `weave` owns periodic full repair. Seeds the header on first write.

    `insert_only=True` inserts a line only when the title is absent and leaves an
    existing line untouched — used for concept stubs at capture, so a thin
    capture-time line never clobbers a richer one weave has already written.
    """
    path = config.index_path
    assert_writable(config, path)
    line = f"- [[{title}]] · {category} · {oneline}".rstrip(" ·")

    text = path.read_text(encoding="utf-8") if path.exists() else _INDEX_HEADER
    # Match an existing line for this exact title (start-anchored so a title that
    # is a prefix of another can't be clobbered).
    existing = re.compile(rf"^- \[\[{re.escape(title)}\]\](?: ·.*)?$", re.MULTILINE)
    match = existing.search(text)
    if match:
        if insert_only or match.group(0) == line:
            return  # present already (insert_only) or unchanged — no write
        text = existing.sub(lambda _m: line, text, count=1)
    else:
        sep = "" if text.endswith("\n") else "\n"
        text = f"{text}{sep}{line}\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


_CANDIDATE_INDEX_LINE_RE = re.compile(r"^- \[\[([^\]]+)\]\].*$", re.MULTILINE)
_CANDIDATE_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_WORD_RE = re.compile(r"[a-z0-9]+")  # term-frequency pass, paired with tokenize()


def inbound_link_counts(config: Config) -> Counter[str]:
    """Count the distinct notes that wikilink to each concept title.

    Sources and the tool and people leaves all count. A concept is any title
    with a note under `brain/bowers/`. This is `candidate_index`'s popularity
    signal.

    Counts leaves deliberately, and that is the ONLY thing that should. A
    concept fed by seven tool leaves is a well-connected hub even with one
    source, but a sources-only count read it as an orphan — `Data extraction`
    scored 1 against 8 real inbound links and fell below the popularity
    cutoff. Synthesis eligibility is the
    opposite question and keeps its own sources-only counter,
    `lint.inbound_source_counts`: INVARIANTS wants two *sources* to converge,
    and tool leaves carry no claims to converge.
    """
    counts: Counter[str] = Counter()
    if not config.notes_dir.is_dir():
        return counts
    concept_stems = {p.stem for p in config.notes_dir.rglob("*.md")}
    feeders = (config.sources_dir, config.tools_dir, config.people_dir)
    for directory in feeders:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            targets = {
                m.group(1).strip() for m in _CANDIDATE_WIKILINK_RE.finditer(text)
            }
            counts.update(targets & concept_stems)
    return counts


def candidate_index(config: Config, llm: LLMSettings, query: str) -> str:
    """Return the link candidates `synthesize_clipping` sees for one source.

    Relevance first, backed by popularity. The whole `_index.md` holds about
    320 titles, and popularity alone buries relevant concepts.

    Popularity is an irrelevance prior: ranking candidates purely by
    feeding-source count buries low-count-but-on-topic concepts below the
    cut. The source "Scaling document processing at
    Harvey" got topics like `RAG and retrieval` / `Scaling laws` /
    `Always-on systems` because every actually-relevant concept (`Data
    extraction`, `Entity extraction`, `AI infrastructure`, `Legal AI tools`)
    sat at feeding-source count 1, below the top-60 popularity cutoff (score
    2) then in force — the model never saw them.

    Selection is two-part:
    - relevance: score each concept title by how much of it the source covers
      (fraction of the title's OWN tokens present) times how *hard* the source
      leans on those tokens (mean log term frequency). Coverage alone
      saturates — a token appearing once anywhere in a 15k-char body scores
      the same as one appearing fifty times, so every two-word title matched
      perfectly and ties fell back to popularity (about 8 titles tied at 1.0
      per source). Term frequency is what separates
      `Data extraction` from `Writing systems for AI` on the Harvey source.
      Sort by (score, inbound-link count) descending; take
      `llm.topic_relevance_limit`.
    - popularity: the top `llm.topic_candidate_limit` concepts by
      inbound-link count — the always-present backbone.

    Final order: relevance hits first (the titles most worth reusing for
    THIS source), then popularity titles not already included. No dupes.

    Also with every concept as a candidate, the "prefer an existing title"
    instruction doesn't survive the list length and Haiku coins new ones
    instead — 221 singleton topics on the live vault. Capping keeps the
    preferred targets few enough that Haiku actually reuses them.
    """
    full_text = read_index(config)
    lines_by_title = {
        m.group(1): m.group(0) for m in _CANDIDATE_INDEX_LINE_RE.finditer(full_text)
    }
    counts = inbound_link_counts(config)
    query_tokens = tokenize(query)
    query_tf = Counter(w for w in _WORD_RE.findall(query.lower()) if w in query_tokens)

    scored = []
    for title in counts:
        title_tokens = tokenize(title)
        overlap = query_tokens & title_tokens
        if not overlap:
            continue
        coverage = len(overlap) / len(title_tokens)
        weight = sum(log1p(query_tf[w]) for w in overlap) / len(overlap)
        scored.append((title, coverage * weight))
    scored.sort(key=lambda pair: (pair[1], counts[pair[0]]), reverse=True)
    relevant = [title for title, _score in scored[: llm.topic_relevance_limit]]

    popular = [t for t, _count in counts.most_common(llm.topic_candidate_limit)]

    seen: set[str] = set()
    ordered = []
    for title in relevant + popular:
        if title not in seen:
            seen.add(title)
            ordered.append(title)

    lines = [lines_by_title.get(title, f"- [[{title}]]") for title in ordered]
    return "\n".join(lines)


def append_log(config: Config, message: str) -> None:
    """Append a timestamped line to `_log.md`. Seeds the header on first write."""
    path = config.log_path
    assert_writable(config, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_LOG_HEADER, encoding="utf-8")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"- `{stamp}` {message}\n")
