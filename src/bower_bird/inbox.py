import shutil
from datetime import datetime
from pathlib import Path

from bower_bird import catalog, ingest, nodes
from bower_bird.config import Config
from bower_bird.llm import synthesize_clipping
from bower_bird.marks import (
    extract_marks,
    extract_urls,
    pick_source_url,
    strip_directives,
)
from bower_bird.schema import PageMeta
from bower_bird.state import State, content_hash

_BODY_LIMIT = 6000


def is_processable(path: Path) -> bool:
    """Skip dotfiles and iCloud/Obsidian sync-conflict copies."""
    name = path.name
    if name.startswith("."):
        return False
    lowered = name.lower()
    return not ("conflicted copy" in lowered or ".sync-conflict" in lowered)


def parse_clip(text: str) -> tuple[dict[str, str], str]:
    """Split a clip into a flat frontmatter dict and its markdown body.

    Minimal on purpose — we only read title/source/description; no YAML dep.
    """
    fm: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end].strip("\n")
            body = text[end + 4 :].lstrip("\n")
            for line in block.splitlines():
                if ":" in line and not line.startswith((" ", "-", "\t")):
                    key, _, value = line.partition(":")
                    fm[key.strip()] = value.strip().strip("\"'")
    return fm, body


def clip_to_meta(
    path: Path, fm: dict[str, str], body: str, fallback_url: str = ""
) -> PageMeta:
    """Build page metadata from a clip's frontmatter and body."""
    return PageMeta(
        url=fm.get("source") or fm.get("url") or fallback_url or "",
        title=fm.get("title") or path.stem,
        description=fm.get("description", ""),
        body_excerpt=body[:_BODY_LIMIT],
        author=fm.get("author", ""),
    )


def archive_clip(config: Config, path: Path, new_stem: str | None = None) -> None:
    """Move a processed clip into archive/, renamed to ``new_stem`` if given.

    The content is untouched. A name collision gets a timestamp suffix.
    """
    config.archive_dir.mkdir(parents=True, exist_ok=True)
    stem = ingest.safe_filename(new_stem) if new_stem else path.stem
    dest = config.archive_dir / f"{stem}{path.suffix}"
    if dest.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = config.archive_dir / f"{stem}.{stamp}{path.suffix}"
    ingest.assert_writable(config, dest)
    shutil.move(str(path), str(dest))


def file_clip(
    config: Config, meta: PageMeta, body: str
) -> tuple[Path | None, str, str]:
    """Synthesize one clip body into a source note, leaf notes and catalog lines.

    Returns ``(note_path, display_title, log_detail)``.
    """
    body_urls = extract_urls(body)
    if not meta.url:
        meta = meta.model_copy(update={"url": pick_source_url(body_urls)})

    # Marks come from the raw body; everything stored uses the cleaned copy.
    marks = extract_marks(body, self_url=meta.url)
    clean = strip_directives(body)
    meta = meta.model_copy(update={"body_excerpt": clean[:_BODY_LIMIT]})
    query = f"{meta.title} {meta.body_excerpt}"
    candidates = catalog.candidate_index(config, config.llm, query)
    plan = synthesize_clipping(
        meta,
        "",
        candidates,
        config.llm.model,
        config.llm,
        highlights=marks.highlights,
        body_urls=body_urls,
        person_anchors=marks.person_anchors,
    )
    # Belt for the prompt gate: no #person tag → never mint a person leaf,
    # whatever the model returned.
    if not marks.person_anchors:
        plan.people = []
    note_path = nodes.create_source_note(
        config, meta, plan, marks=marks, full_body=clean
    )

    # One concise title names the node, its backlinks and the archived copy.
    display_title = (plan.concise_title or meta.title).strip() or meta.title
    source_title = ingest.safe_filename(display_title)

    # File named entities (tools, people) as their own leaf nodes.
    entity_ids = nodes.file_entities(config, plan, source_title)

    # Concept stub lines are insert only, so weave's richer text survives.
    if note_path is not None:
        catalog.upsert_index_line(config, source_title, plan.category, plan.description)
        for topic in plan.topics:
            catalog.upsert_index_line(
                config, topic, plan.category, "", insert_only=True
            )
        catalog.append_log(
            config,
            f"build sources/{note_path.name} "
            f"[{plan.category or 'uncategorized'}] "
            f"links={len(plan.topics)} entities={len(entity_ids)}",
        )

    links = ", ".join(plan.topics) or "none"
    marked = (
        f"{len(marks.highlights)}h/{len(marks.dig)}d/"
        f"{len(marks.questions)}q/{len(marks.further_links)}l"
    )
    if marks.promote:
        marked += "/promote"
    elif marks.frozen:
        marked += "/frozen"
    entities = ("; entities: " + ", ".join(entity_ids)) if entity_ids else ""
    detail = f"(links: {links}; marks: {marked}{entities})"
    return note_path, display_title, detail


def process_inbox(config: Config, state: State) -> list[str]:
    """Process every clip dropped in inbox/ once. Returns one log line per clip.

    inbox/ is the Web Clipper's transient drop target — nothing waits here for
    a human read-and-move step; whatever's present gets shelved this tick.
    """
    if not config.inbox_dir.is_dir():
        return []

    log: list[str] = []
    for path in sorted(config.inbox_dir.glob("*.md")):
        if not is_processable(path):
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.append(f"skip {path.name}: unreadable ({exc})")
            continue

        digest = content_hash(raw)
        if state.seen_hash(digest):
            archive_clip(config, path)  # already processed; just clear the inbox
            log.append(f"dup {path.name}: already processed, archived")
            continue

        fm, body = parse_clip(raw)
        meta = clip_to_meta(
            path, fm, body, fallback_url=pick_source_url(extract_urls(body))
        )
        # A re-clip of the same source misses the content hash; check its URL.
        if meta.url and state.seen_url(meta.url):
            archive_clip(config, path, new_stem=meta.title)
            log.append(f"dup {path.name}: url already processed, archived")
            continue

        try:
            note_path, display_title, detail = file_clip(config, meta, body)
        except Exception as exc:
            log.append(f"error {path.name}: {exc}")
            continue

        state.mark_hash(digest)
        if meta.url:
            state.mark_url(meta.url)
        state.save()
        archive_clip(config, path, new_stem=display_title)

        if note_path is None:
            log.append(f"{path.name}: source note already existed; archived")
        else:
            log.append(f"{path.name} -> sources/{note_path.name} {detail}")

    return log
