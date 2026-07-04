"""Vault writes — the only side effects that touch the vault.

Hard boundary (per INVARIANTS): bower-bird owns the `BowerBird/` folder
(`config.vault_path`) and writes *nowhere else*. `_assert_writable` enforces
that in code so a future change can't quietly break it.

Additive-autonomous: bower-bird creates its own notes (sources/, new concept
notes in brain/bowers/) and asserts `[[links]]` freely, but it **never rewrites
or deletes an existing note**. Every write here is either a brand-new file or a
strictly additive append (`_append_link`) — so nothing a human authored is ever
clobbered.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

from bower_bird.config import Config
from bower_bird.fetch import PageMeta
from bower_bird.llm import ClippingPlan, EntityRef, FeynmanConcept
from bower_bird.marks import Marks
from bower_bird.review import ReviewStore

_INVALID_FILENAME = re.compile(r'[/:\\?%*|"<>]')
_SKIP_NOTE_STEMS = {"_index", "_archive", "_template", "__init__"}
_LINKS_HEADING = "## Links"


def _assert_writable(config: Config, path: Path) -> None:
    """Refuse any write outside the owned `BowerBird/` folder."""
    resolved = path.resolve()
    root = config.vault_path.resolve()
    if resolved != root and root not in resolved.parents:
        raise PermissionError(
            f"Refusing to write outside the owned folder: {resolved}. "
            f"Only {root} and its contents are writable at runtime."
        )


def _safe_filename(title: str) -> str:
    cleaned = _INVALID_FILENAME.sub("-", title).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or "untitled")[:120]


def _today() -> str:
    return datetime.now().date().isoformat()


def list_concept_notes(config: Config) -> list[str]:
    """Concept-note titles across all bowers — candidates for backlinks.

    Recurses brain/bowers/ since concepts live in topical subfolders.
    """
    if not config.notes_dir.is_dir():
        return []
    titles: list[str] = []
    for path in sorted(config.notes_dir.rglob("*.md")):
        if path.stem in _SKIP_NOTE_STEMS:
            continue
        titles.append(path.stem)
    return titles


def find_concept_path(config: Config, title: str) -> Path:
    """Resolve a concept note's real path.

    If a note with this title already exists anywhere under brain/bowers/, return
    its actual (possibly nested) path so we link into it rather than creating a
    flat duplicate. Otherwise return the flat path at the bowers root, where new,
    unfiled concepts land until Tier-2 files them into a bower.
    """
    safe = _safe_filename(title)
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


def _append_link(config: Config, path: Path, target_title: str) -> None:
    """Append `- [[target]]` under a `## Links` heading. Strictly additive:
    never edits existing text. When the note doesn't exist yet, seed it with a
    well-formed concept-note header first (so auto-created notes aren't bare
    stubs). Idempotent — a link already present is not duplicated."""
    _assert_writable(config, path)
    line = f"- [[{target_title}]]"
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = _CONCEPT_STUB.format(title=path.stem, today=_today())

    if line in text:
        return

    if _LINKS_HEADING in text:
        out = text.replace(f"{_LINKS_HEADING}\n", f"{_LINKS_HEADING}\n{line}\n", 1)
    else:
        sep = "" if text == "" or text.endswith("\n") else "\n"
        out = f"{text}{sep}\n{_LINKS_HEADING}\n{line}\n"

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

    This is the ONLY link-candidate source `build` reads — a compact one-line-
    per-page catalog — so per-item cost stays bounded as the graph grows (vs.
    `list_concept_notes`, which reads every page title, O(N))."""
    path = config.index_path
    return path.read_text(encoding="utf-8") if path.exists() else ""


def upsert_index_line(config: Config, title: str, category: str, oneline: str) -> None:
    """Add or replace this page's catalog line, keyed on `[[title]]`. Idempotent.

    Incremental: touches only this one line (O(1)), never rescans the graph —
    `weave` owns periodic full repair. Seeds the header on first write."""
    path = config.index_path
    _assert_writable(config, path)
    line = f"- [[{title}]] · {category} · {oneline}".rstrip(" ·")

    text = path.read_text(encoding="utf-8") if path.exists() else _INDEX_HEADER
    # Match an existing line for this exact title (start-anchored so a title that
    # is a prefix of another can't be clobbered).
    existing = re.compile(rf"^- \[\[{re.escape(title)}\]\](?: ·.*)?$", re.MULTILINE)
    match = existing.search(text)
    if match:
        if match.group(0) == line:
            return  # already current — no write
        text = existing.sub(lambda _m: line, text, count=1)
    else:
        sep = "" if text.endswith("\n") else "\n"
        text = f"{text}{sep}{line}\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_log(config: Config, message: str) -> None:
    """Append a timestamped line to `_log.md`. Seeds the header on first write."""
    path = config.log_path
    _assert_writable(config, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_LOG_HEADER, encoding="utf-8")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"- `{stamp}` {message}\n")


# --------------------------------------------------------------------------- #
# clip queue (links httpx can't read — open in a browser + Web Clipper)
# --------------------------------------------------------------------------- #

_TO_CLIP_HEADER = """\
---
title: To clip
created: {today}
bower: generated
tags:
  - to-clip
---
# To clip

Links bower-bird can't read on its own (X/Twitter, JS- or login-walled pages).
Open each in a browser and save it with the Obsidian Web Clipper into `inbox/`;
it'll be read + filed on the next pass. Check one off once you've clipped it.

## Queue
"""


def append_to_clip_queue(config: Config, url: str, title: str = "") -> bool:
    """Append a `- [ ]` clip-me entry. Returns False if the URL is already listed."""
    path = config.to_clip_path
    _assert_writable(config, path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(_TO_CLIP_HEADER.format(today=_today()), encoding="utf-8")

    existing = path.read_text(encoding="utf-8")
    if url in existing:
        return False

    label = title.strip()
    entry = f"- [ ] [{label}]({url})\n" if label and label != url else f"- [ ] {url}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(entry)
    return True


# --------------------------------------------------------------------------- #
# tools shelf (plugins/repos/tools to keep for later — not knowledge)
# --------------------------------------------------------------------------- #

_TOOLS_HEADER = """\
---
title: Tools
created: {today}
bower: generated
tags:
  - tools
---
# Tools

A shelf of plugins, repos, and tools worth remembering — collected via the
`tool:` Telegram prefix. Not knowledge (never enters brain/); this is "what was
that thing I saw" recall. Search by your own words.

## Shelf
"""


def append_to_tools(
    config: Config, url: str, title: str, oneline: str, note: str = ""
) -> bool:
    """Append a tool entry. Returns False if the URL is already shelved.

    `note` is the user's own words ("claude loop plugin") — kept first so recall
    search hits their phrasing, not just the scraped title.
    """
    path = config.tools_path
    _assert_writable(config, path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(_TOOLS_HEADER.format(today=_today()), encoding="utf-8")

    existing = path.read_text(encoding="utf-8")
    if url in existing:
        return False

    label = title.strip() or url
    desc = " · ".join(p for p in (note.strip(), oneline.strip()) if p)
    entry = f"- [{label}]({url})" + (f" — {desc}\n" if desc else "\n")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(entry)
    return True


# --------------------------------------------------------------------------- #
# inbox fill (bot-rendered readable docs for the to-read lane)
# --------------------------------------------------------------------------- #

_INBOX_DOC_TEMPLATE = """\
---
title: "{title}"
source: "{url}"
created: {today}
bower: bot-rendered
tags:
  - to-read
---
# {title}

> Source: [{url}]({url})

{body}
"""


def write_inbox_doc(config: Config, meta: PageMeta, body: str) -> Path | None:
    """Write a rendered readable doc into inbox/.

    The bot fills inbox/ with fetch-rendered .md files so the human can read
    + annotate directly in Obsidian. Returns the path written, or None if a
    doc for this URL already exists (filename-level guard; primary dedup is
    url dedup in state).

    `body` is the paragraph-structured body text from fetch.fetch_rendered().
    The doc is NOT a summary — it is the full fetched text rendered for reading.
    """
    config.inbox_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(meta.title)
    path = config.inbox_dir / f"{safe}.md"
    _assert_writable(config, path)

    if path.exists():
        return None

    body_block = (
        body.strip()
        if body.strip()
        else "_Unable to extract body text — open in browser to read._"
    )
    content = _INBOX_DOC_TEMPLATE.format(
        title=meta.title.replace('"', "'"),
        url=meta.url,
        today=_today(),
        body=body_block,
    )
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# catch-all inbox (Telegram messages we couldn't process)
# --------------------------------------------------------------------------- #

_TELEGRAM_INBOX_HEADER = """\
---
title: Inbox
created: {today}
bower: generated
tags:
  - inbox
---
# Inbox

Telegram messages bower-bird couldn't process (no link, or a fetch failure).
Nothing is dropped — triage these by hand.

"""


def append_to_telegram_inbox(config: Config, text: str, reason: str) -> None:
    """Park an unprocessable Telegram message with a timestamp + reason."""
    path = config.telegram_inbox_path
    _assert_writable(config, path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(_TELEGRAM_INBOX_HEADER.format(today=_today()), encoding="utf-8")

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    flat = " ".join(text.split())
    entry = f"- `{stamp}` ({reason}) {flat}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(entry)


# --------------------------------------------------------------------------- #
# learned lane (read items — source note in sources/, links into notes/)
# --------------------------------------------------------------------------- #

_SOURCE_FRONTMATTER = """\
---
title: "{title}"
source: "{url}"
created: {today}
description: "{description}"
bower: generated
tags:
{tags}
---
# {title}
"""


def _source_tags(marks: Marks) -> str:
    """`source`, plus `dig` when the reader flagged something to go deeper on."""
    tags = ["source"]
    if marks.dig or marks.questions:
        tags.append("dig")
    return "\n".join(f"  - {t}" for t in tags)


def create_source_note(
    config: Config,
    meta: PageMeta,
    plan: ClippingPlan,
    *,
    note: str = "",
    marks: Marks | None = None,
) -> Path | None:
    """Write a (thin) source note into sources/ and assert links into the graph.

    The note is provenance + the reader's marks — NOT the article body (the full
    clip stays in archive/ cold). `note` is the human's why-it-matters line (the
    Telegram learned lane); `marks` are the highlights/dig/questions/links pulled
    from a clip. Returns the source-note path, or None if one already exists
    (filename-level guard; primary dedup is in state).
    """
    marks = marks or Marks()
    config.sources_dir.mkdir(parents=True, exist_ok=True)
    # Prefer the model's fluff-free title for the graph node; fall back to the
    # raw page title if the model gave nothing.
    display_title = (plan.concise_title or meta.title).strip() or meta.title
    source_title = _safe_filename(display_title)
    path = config.sources_dir / f"{source_title}.md"
    _assert_writable(config, path)
    if path.exists():
        return None

    targets = list(plan.topics)

    backlinks = (
        "\n".join(f"- [[{t}]]" for t in targets)
        if targets
        else "- _(no connections yet)_"
    )

    parts = [
        _SOURCE_FRONTMATTER.format(
            title=display_title.replace('"', "'"),
            url=meta.url,
            today=_today(),
            description=plan.description.replace('"', "'"),
            tags=_source_tags(marks),
        ),
        f"## Links\n{backlinks}\n",
    ]
    if plan.key_ideas:
        body = "\n".join(f"- {idea}" for idea in plan.key_ideas)
        parts.append(f"## Key ideas\n{body}\n")
    if marks.highlights:
        body = "\n".join(f"> {h}" for h in marks.highlights)
        parts.append(f"## Highlights\n{body}\n")
    if note.strip():
        parts.append(f"## Note\n{note.strip()}\n")
    if marks.dig:
        body = "\n".join(f"- {d}" for d in marks.dig)
        parts.append(f"## Dig deeper\n{body}\n")
    if marks.questions:
        body = "\n".join(f"- {q}" for q in marks.questions)
        parts.append(f"## Open questions\n{body}\n")
    if marks.further_links:
        body = "\n".join(f"- [{a}]({u})" for a, u in marks.further_links)
        parts.append(f"## Further reading\n{body}\n")

    path.write_text("\n".join(parts), encoding="utf-8")

    # Assert reciprocal links into the concept graph (additive only). Link into
    # an existing bower concept where it lives; new ones land flat at the bowers
    # root for Tier-2 to file.
    for target in targets:
        _append_link(config, find_concept_path(config, target), source_title)

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
    payload and is not quizzed. Additive: a brand-new file, or `_append_link`
    appends to an existing one. `kind` is the tag + folder ('person' | 'tool').
    """
    folder = config.people_dir if kind == "person" else config.tools_dir
    folder.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(entity.name)
    path = folder / f"{safe}.md"
    _assert_writable(config, path)

    if not path.exists():
        body = _LEAF_FRONTMATTER.format(
            title=entity.name.replace('"', "'"),
            url=entity.url,
            today=_today(),
            kind=kind,
        )
        if entity.note.strip():
            body = f"{body}\n{entity.note.strip()}\n"
        path.write_text(body, encoding="utf-8")

    # Link into the concept graph (reciprocal, additive) + back to the source.
    for topic in entity.topics:
        _append_link(config, path, topic)
        _append_link(config, find_concept_path(config, topic), safe)
    _append_link(config, path, source_title)
    return path


def file_entities(config: Config, plan: ClippingPlan, source_title: str) -> list[str]:
    """File every tool/person leaf in a plan, backlinked from the source note.

    Returns short ids like 'tool:roboflow-supervision' for the run log.
    """
    filed: list[str] = []
    source_path = config.sources_dir / f"{_safe_filename(source_title)}.md"
    for kind, entities in (("tool", plan.tools), ("person", plan.people)):
        for entity in entities:
            if not entity.name.strip():
                continue
            leaf = create_leaf_note(config, kind, entity, source_title)
            # Surface the leaf on the source note's Links (additive, idempotent).
            if source_path.exists():
                _append_link(config, source_path, leaf.stem)
            filed.append(f"{kind}:{leaf.stem}")
    return filed


# --------------------------------------------------------------------------- #
# bower minting (gather: trinkets/ → brain/bowers/ with Feynman payload)
# --------------------------------------------------------------------------- #

_ID_RE = re.compile(r"^id:\s*(.+)$", re.MULTILINE)
_FM_CLOSE = re.compile(r"^---\s*$", re.MULTILINE)
_CONCEPT_SECTION_RE = re.compile(
    r"<!-- bower:concept -->\n.*?<!-- /bower:concept -->", re.DOTALL
)

_BOWER_FRONTMATTER = """\
---
title: "{title}"
id: {bower_id}
created: {today}
bower: generated
tags:
  - concept
---
# {title}
"""

_CONCEPT_SECTION = """\
<!-- bower:concept -->
## Concept

**Definition:** {definition}

**Why it matters:** {why}

**Test question:** {test_question}

**Model answer:** {model_answer}
<!-- /bower:concept -->"""


def _extract_bower_id(text: str) -> str | None:
    """Pull the `id:` value from existing frontmatter, or None if absent."""
    m = _ID_RE.search(text)
    return m.group(1).strip() if m else None


def _inject_id_into_frontmatter(text: str, bower_id: str) -> str:
    """Append `id: <bower_id>` as the first line inside the frontmatter block.

    Called only when the file exists but has no `id:` yet (append-once).
    Leaves all other frontmatter + body untouched.
    """
    # Find the opening --- and inject after it.
    if not text.startswith("---"):
        return text  # no frontmatter — leave as-is; id will be added on next full write
    first_newline = text.index("\n")
    return text[: first_newline + 1] + f"id: {bower_id}\n" + text[first_newline + 1 :]


def _write_concept_section(text: str, concept: FeynmanConcept) -> str:
    """Replace the machine-managed concept block, or append it if absent.

    Human-authored prose outside the <!-- bower:concept --> sentinel is
    never touched. The sentinel block is bot-managed (rewrite-by-layer policy).
    """
    block = _CONCEPT_SECTION.format(
        definition=concept.definition,
        why=concept.why,
        test_question=concept.test_question,
        model_answer=concept.model_answer,
    )
    if _CONCEPT_SECTION_RE.search(text):
        return _CONCEPT_SECTION_RE.sub(block, text)
    # No existing block — append (before any trailing newline for tidiness)
    sep = "" if text.endswith("\n") else "\n"
    return f"{text}{sep}\n{block}\n"


def mint_bower(
    config: Config,
    concept: FeynmanConcept,
    source_title: str,
) -> tuple[Path, str]:
    """Create or update a bower note in brain/bowers/ carrying a Feynman payload.

    Additive on human-authored prose — only the sentinel ``<!-- bower:concept
    -->`` block and the Links section are bot-managed. The ``id:`` frontmatter
    key is append-once (immutable after first write).

    Returns ``(path, bower_id)`` — the note path and its stable id.
    """
    path = find_concept_path(config, concept.handle)
    _assert_writable(config, path)

    if path.exists():
        text = path.read_text(encoding="utf-8")
        bower_id = _extract_bower_id(text)
        if bower_id is None:
            # Existing note has no id yet — inject one (append-once).
            bower_id = str(uuid.uuid4())
            text = _inject_id_into_frontmatter(text, bower_id)
        # Update the machine-managed concept block; leave everything else alone.
        text = _write_concept_section(text, concept)
    else:
        bower_id = str(uuid.uuid4())
        text = _BOWER_FRONTMATTER.format(
            title=concept.handle,
            bower_id=bower_id,
            today=_today(),
        )
        text = _write_concept_section(text, concept)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

    # Assert the backlink from this bower to its source (additive, idempotent).
    _append_link(config, path, source_title)

    # Seed the review store for newly minted bowers (idempotent — existing
    # entries are left untouched). Persisted immediately so the vault file
    # stays consistent even when the caller doesn't call save() separately.
    store = ReviewStore.load(config)
    if store.seed(bower_id):
        store.save()

    return path, bower_id
