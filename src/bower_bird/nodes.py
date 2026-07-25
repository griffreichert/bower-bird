"""Graph-node writes: source notes (sources/), leaf notes (people/tools), and
the concept-graph plumbing that backs them — `[[links]]`, the `_index.md`
catalog, the `_log.md` activity log.

Every write here goes through `ingest.assert_writable` (the boundary guard
stays in `ingest.py`; this module never bypasses it).
"""

import re
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

from bower_bird.config import Config
from bower_bird.ingest import assert_writable, safe_filename, today_iso, yaml_scalar
from bower_bird.marks import Marks
from bower_bird.schema import ClippingPlan, EntityRef, PageMeta

_LINKS_HEADING = "## Links"  # concept↔concept relations (sibling ideas)
_SOURCES_HEADING = "## Sources"  # source/leaf backlinks — click to read origin


def find_concept_path(config: Config, title: str) -> Path:
    """Resolve a concept note's real path.

    If a note with this title already exists anywhere under brain/bowers/, return
    its actual (possibly nested) path so we link into it rather than creating a
    flat duplicate. Otherwise return the flat path at the bowers root, where new,
    unfiled concepts land until Tier-2 files them into a bower.
    """
    safe = safe_filename(title)
    if config.notes_dir.is_dir():
        for path in config.notes_dir.rglob(f"{safe}.md"):
            return path
    return config.notes_dir / f"{safe}.md"


_CONCEPT_STUB = """\
---
title: "{title}"
created: {today}
bower: generated
tags:
  - concept
---
# {title}
"""


def append_link(
    config: Config, path: Path, target_title: str, heading: str = _LINKS_HEADING
) -> None:
    """Append `- [[target]]` under `heading`. Strictly additive: never edits
    existing text. When the note doesn't exist yet, seed it with a well-formed
    concept-note header first (so auto-created notes aren't bare stubs).
    Idempotent — a link already present under ANY heading is not duplicated.

    `heading` divides the two kinds of link a node carries: `## Links` for
    sibling concepts, `## Sources` for the source/leaf notes you click to read
    the origin. A given target is placed once, in the heading passed here."""
    assert_writable(config, path)
    line = f"- [[{target_title}]]"
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = _CONCEPT_STUB.format(title=path.stem, today=today_iso())

    if line in text:
        return

    if heading in text:
        out = text.replace(f"{heading}\n", f"{heading}\n{line}\n", 1)
    else:
        sep = "" if text == "" or text.endswith("\n") else "\n"
        out = f"{text}{sep}\n{heading}\n{line}\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(out, encoding="utf-8")


# --------------------------------------------------------------------------- #
# graph catalog (_index.md) + activity log (_log.md)
# --------------------------------------------------------------------------- #

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
    graph grows, instead of rglob-ing and opening every page to read its title."""
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
    capture-time line never clobbers a richer one weave has already written."""
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


_CANDIDATE_INDEX_LINE_RE = re.compile(
    r"^- \[\[([^\]]+)\]\](?: · ([^·\n]*))?", re.MULTILINE
)
_CANDIDATE_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def feeding_source_counts(config: Config) -> Counter[str]:
    """Per concept title, how many distinct `brain/sources/*.md` notes
    wikilink to it. A concept is any title with a note under `brain/bowers/`
    — this count is the ranking signal for `candidate_index` (#Step 4b)."""
    counts: Counter[str] = Counter()
    if not config.sources_dir.is_dir() or not config.notes_dir.is_dir():
        return counts
    concept_stems = {p.stem for p in config.notes_dir.rglob("*.md")}
    for path in sorted(config.sources_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        targets = {m.group(1).strip() for m in _CANDIDATE_WIKILINK_RE.finditer(text)}
        counts.update(targets & concept_stems)
    return counts


def candidate_index(config: Config, limit: int) -> str:
    """The link-candidate list `synthesize_clipping` sees: the top `limit`
    concept titles by feeding-source count, not the whole `_index.md`.

    With every concept as a candidate (~320 on the live vault), the "prefer an
    existing title" instruction doesn't survive the list length and Haiku
    coins new ones instead — 221 singleton topics, measured 2026-07-25.
    Capping to the most-fed concepts keeps the preferred targets few enough
    that Haiku actually reuses them.
    """
    full_text = read_index(config)
    if not full_text.strip():
        return full_text
    categories = dict(_CANDIDATE_INDEX_LINE_RE.findall(full_text))
    lines = []
    for title, _count in feeding_source_counts(config).most_common(limit):
        category = (categories.get(title) or "").strip()
        lines.append(f"- [[{title}]] · {category}".rstrip(" ·"))
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


# --------------------------------------------------------------------------- #
# shelve lane (every capture — source note in sources/, links into notes/)
# --------------------------------------------------------------------------- #

_SOURCE_FRONTMATTER = """\
---
id: {source_id}
title: "{title}"
source: "{url}"
{author_line}created: {today}
{category_line}{topics_block}tags:
{tags}
---
# {title}
"""


def build_source_tags(marks: Marks) -> str:
    """`source`, plus `dig` when the reader flagged something to go deeper on,
    plus `frozen` when the reader pinned the node (`#frozen`/`#promote`) — prose
    immutable, links stay open."""
    tags = ["source"]
    if marks.dig or marks.questions:
        tags.append("dig")
    if marks.frozen:
        tags.append("frozen")
    return "\n".join(f"  - {t}" for t in tags)


def create_source_note(
    config: Config,
    meta: PageMeta,
    plan: ClippingPlan,
    *,
    note: str = "",
    marks: Marks | None = None,
    full_body: str = "",
) -> Path | None:
    """Write a source note into sources/ and assert links into the graph (#13
    schema): frontmatter (id/title/source/author/created/tags), then
    Key ideas / Seed thoughts (if any) / Links / marks headings / Body.

    Antilibrary model — every capture is shelved immediately, no read gate.
    `note` is the human's seed thought sent alongside the link (if any);
    `marks` are the highlights/dig/questions/links pulled from a clip.
    `full_body` is the source's full rendered body, inlined verbatim under
    `## Body` — the node is canonical and self-contained; `archive/` is a cold
    recycle bin, not the record of truth. Returns the source-note path, or
    None if one already exists (filename-level guard; primary dedup is state).
    """
    marks = marks or Marks()
    config.sources_dir.mkdir(parents=True, exist_ok=True)
    # Prefer the model's fluff-free title for the graph node; fall back to the
    # raw page title if the model gave nothing.
    # 64-char clamp backstops the schema's max_length when the fallback is
    # the raw page title (e.g. a long article headline).
    display_title = ((plan.concise_title or meta.title).strip() or meta.title)[:64]
    source_title = safe_filename(display_title)
    path = config.sources_dir / f"{source_title}.md"
    assert_writable(config, path)
    if path.exists():
        return None

    targets = list(plan.topics)

    category_line = f"category: {plan.category}\n" if plan.category else ""
    topics_block = (
        "topics:\n" + "\n".join(f"  - {t}" for t in targets) + "\n" if targets else ""
    )

    backlinks = (
        "\n".join(f"- [[{t}]]" for t in targets)
        if targets
        else "- _(no connections yet)_"
    )

    author = yaml_scalar(meta.author or plan.author)
    author_line = f'author: "{author}"\n' if author else ""

    # Every source is a peck card (antilibrary model, #18) — mint a stable id
    # up front so the review store can key on it. `peck` enrols by scanning
    # this id, not by any write here (no ingest-side coupling).
    source_id = str(uuid.uuid4())

    parts = [
        _SOURCE_FRONTMATTER.format(
            source_id=source_id,
            title=yaml_scalar(display_title),
            url=yaml_scalar(meta.url),
            author_line=author_line,
            today=today_iso(),
            category_line=category_line,
            topics_block=topics_block,
            tags=build_source_tags(marks),
        ),
    ]
    if plan.key_ideas:
        body = "\n".join(f"- {idea}" for idea in plan.key_ideas)
        parts.append(f"## Key ideas\n{body}\n")
    if note.strip():
        # Verbatim, human-authored, append-only — never distilled by the model.
        parts.append(f"## Seed thoughts\n- {note.strip()}\n")
    parts.append(f"## Links\n{backlinks}\n")
    if marks.highlights:
        body = "\n".join(f"> {h}" for h in marks.highlights)
        parts.append(f"## Highlights\n{body}\n")
    if marks.dig:
        body = "\n".join(f"- {d}" for d in marks.dig)
        parts.append(f"## Dig deeper\n{body}\n")
    if marks.questions:
        body = "\n".join(f"- {q}" for q in marks.questions)
        parts.append(f"## Open questions\n{body}\n")
    if marks.further_links:
        body = "\n".join(f"- [{a}]({u})" for a, u in marks.further_links)
        parts.append(f"## Further reading\n{body}\n")
    if full_body.strip():
        # Immutable full rendered body, inlined and canonical.
        parts.append(f"## Body\n{full_body.strip()}\n")

    path.write_text("\n".join(parts), encoding="utf-8")

    # Assert reciprocal links into the concept graph (additive only). Link into
    # an existing bower concept where it lives; new ones land flat at the bowers
    # root for Tier-2 to file. This source lands under the concept's `## Sources`
    # (click-to-read-origin), kept apart from its sibling-concept `## Links`.
    for target in targets:
        append_link(
            config, find_concept_path(config, target), source_title, _SOURCES_HEADING
        )

    return path


# --------------------------------------------------------------------------- #
# entity leaf notes (people + tools — unquizzed brain leaves)
# --------------------------------------------------------------------------- #

_LEAF_FRONTMATTER = """\
---
title: "{title}"
source: "{url}"
created: {today}
bower: generated
tags:
  - {kind}
---
# {title}
"""


def create_leaf_note(
    config: Config,
    kind: str,
    entity: EntityRef,
    source_title: str,
) -> Path:
    """File a person/tool as an unquizzed leaf note in brain/{people,tools}/.

    A leaf links into the concept graph like a source does, but is NEVER minted
    as a bower and NEVER seeded into the review store — it carries no Feynman
    payload and is not quizzed. Additive: a brand-new file, or `append_link`
    appends to an existing one. `kind` is the tag + folder ('person' | 'tool').
    """
    folder = config.people_dir if kind == "person" else config.tools_dir
    folder.mkdir(parents=True, exist_ok=True)
    safe = safe_filename(entity.name)
    path = folder / f"{safe}.md"
    assert_writable(config, path)

    if not path.exists():
        body = _LEAF_FRONTMATTER.format(
            title=yaml_scalar(entity.name),
            url=yaml_scalar(entity.url),
            today=today_iso(),
            kind=kind,
        )
        if entity.note.strip():
            body = f"{body}\n{entity.note.strip()}\n"
        path.write_text(body, encoding="utf-8")

    # Link into the concept graph (reciprocal, additive) + back to the source.
    # The leaf lists its concepts under `## Links`; on the concept + source it is
    # itself a reference node, so it lands under their `## Sources`.
    for topic in entity.topics:
        append_link(config, path, topic)
        append_link(config, find_concept_path(config, topic), safe, _SOURCES_HEADING)
    append_link(config, path, source_title, _SOURCES_HEADING)
    return path


def file_entities(config: Config, plan: ClippingPlan, source_title: str) -> list[str]:
    """File every tool/person leaf in a plan, backlinked from the source note.

    Returns short ids like 'tool:roboflow-supervision' for the run log.
    """
    filed: list[str] = []
    source_path = config.sources_dir / f"{safe_filename(source_title)}.md"
    for kind, entities in (("tool", plan.tools), ("person", plan.people)):
        for entity in entities:
            if not entity.name.strip():
                continue
            leaf = create_leaf_note(config, kind, entity, source_title)
            # Surface the leaf on the source note under `## Sources` (a reference
            # node, not a sibling concept). Additive, idempotent.
            if source_path.exists():
                append_link(config, source_path, leaf.stem, _SOURCES_HEADING)
            filed.append(f"{kind}:{leaf.stem}")
    return filed
