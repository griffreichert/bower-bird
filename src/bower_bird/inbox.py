"""Trinkets gather: process read+annotated clips in `BowerBird/trinkets/`.

The move inbox/ → trinkets/ is the read signal (per INVARIANTS). Clips land in
inbox/ as the to-read reading room; the human reads + annotates them there, then
moves them to trinkets/ — that move authorises graph writes. The bot never
auto-processes inbox/.

The clipper already wrote clean markdown — so we skip `fetch.py` entirely and
synthesise from the clip body, which sidesteps the scraper's failure on
JS-heavy / login-walled pages (e.g. X).

Per clip: parse frontmatter + body, dedup by content hash (state lives in the
repo, so it survives the move), synthesise a source note + asserted links, then
move the original clip to `archive/` (non-destructive).
"""

import shutil
from datetime import datetime
from pathlib import Path

from bower_bird import ingest
from bower_bird.config import Config
from bower_bird.fetch import PageMeta
from bower_bird.llm import synthesize_clipping
from bower_bird.marks import (
    extract_marks,
    extract_urls,
    pick_source_url,
    strip_directives,
)
from bower_bird.state import State, content_hash

_BODY_LIMIT = 6000


def _is_processable(path: Path) -> bool:
    """Skip dotfiles and iCloud/Obsidian sync-conflict copies."""
    name = path.name
    if name.startswith("."):
        return False
    lowered = name.lower()
    return not ("conflicted copy" in lowered or ".sync-conflict" in lowered)


def _parse_clip(text: str) -> tuple[dict[str, str], str]:
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


def _clip_to_meta(
    path: Path, fm: dict[str, str], body: str, fallback_url: str = ""
) -> PageMeta:
    return PageMeta(
        url=fm.get("source") or fm.get("url") or fallback_url or "",
        title=fm.get("title") or path.stem,
        description=fm.get("description", ""),
        body_excerpt=body[:_BODY_LIMIT],
        author=fm.get("author", ""),
    )


def _archive(config: Config, path: Path, new_stem: str | None = None) -> None:
    config.archive_dir.mkdir(parents=True, exist_ok=True)
    # The cold copy can carry the concise (fluff-free) name once we have one;
    # content is untouched, only the filename shortens.
    stem = ingest._safe_filename(new_stem) if new_stem else path.stem
    dest = config.archive_dir / f"{stem}{path.suffix}"
    if dest.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = config.archive_dir / f"{stem}.{stamp}{path.suffix}"
    ingest._assert_writable(config, dest)
    shutil.move(str(path), str(dest))


def process_inbox(config: Config, state: State) -> list[str]:
    """Process every clip in trinkets/ once. Returns one log line per clip.

    Scans trinkets/ (read+annotated items), never inbox/ (the reading room).
    The move inbox/ → trinkets/ is the human read signal.
    """
    if not config.trinkets_dir.is_dir():
        return []

    log: list[str] = []
    for path in sorted(config.trinkets_dir.glob("*.md")):
        if not _is_processable(path):
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.append(f"skip {path.name}: unreadable ({exc})")
            continue

        digest = content_hash(raw)
        if state.seen_hash(digest):
            _archive(config, path)  # already processed; just clear the inbox
            log.append(f"dup {path.name}: already processed, archived")
            continue

        fm, body = _parse_clip(raw)
        body_urls = extract_urls(body)
        meta = _clip_to_meta(path, fm, body, fallback_url=pick_source_url(body_urls))
        # Same source re-clipped gets fresh bytes + a fresh LLM title, so the
        # content hash misses it — guard on the source URL too. Skip when absent,
        # else every url-less clip would collide on "".
        if meta.url and state.seen_url(meta.url):
            _archive(config, path, new_stem=meta.title)
            log.append(f"dup {path.name}: url already processed, archived")
            continue

        try:
            # Marks come from the RAW body (they carry the directive tokens);
            # everything synthesised or stored uses the cleaned copy so `#promote`
            # et al. never leak into the node or bias extraction.
            marks = extract_marks(body, self_url=meta.url)
            clean = strip_directives(body)
            meta = meta.model_copy(update={"body_excerpt": clean[:_BODY_LIMIT]})
            candidates = ingest.read_index(config)
            plan = synthesize_clipping(
                meta,
                "",
                candidates,
                model=config.build_model,
                highlights=marks.highlights,
                body_urls=body_urls,
                person_anchors=marks.person_anchors,
            )
            # Belt for the prompt gate: no #person tag → never mint a person leaf,
            # whatever the model returned.
            if not marks.person_anchors:
                plan.people = []
            note_path = ingest.create_source_note(
                config, meta, plan, marks=marks, full_body=clean
            )
        except Exception as exc:  # noqa: BLE001 — one bad clip must not stall the rest
            log.append(f"error {path.name}: {exc}")
            continue

        # Concise (fluff-free) title threads through the source ref + the archive
        # name so the graph node, its backlinks, and the cold copy all match.
        display_title = (plan.concise_title or meta.title).strip() or meta.title
        source_title = ingest._safe_filename(display_title)

        # Concept pages are left as pure link stubs here — their substance is
        # written by `weave` (AI territory), and any quiz payload lives in
        # `learn/`, never in the concept page. Capture does not mint or quiz.

        # File named entities (tools, people) as their own leaf nodes.
        entity_ids = ingest.file_entities(config, plan, source_title)

        # Catalog the new source in the index (full line) and ensure each linked
        # concept has a stub line (insert-only — never clobber weave's richer
        # text). Log the build. Only for a freshly written source note.
        if note_path is not None:
            ingest.upsert_index_line(
                config, source_title, plan.category, plan.description
            )
            for topic in plan.topics:
                ingest.upsert_index_line(
                    config, topic, plan.category, "", insert_only=True
                )
            ingest.append_log(
                config,
                f"build sources/{note_path.name} "
                f"[{plan.category or 'uncategorized'}] "
                f"links={len(plan.topics)} entities={len(entity_ids)}",
            )

        state.mark_hash(digest)
        if meta.url:
            state.mark_url(meta.url)
        state.save()
        _archive(config, path, new_stem=display_title)

        if note_path is None:
            log.append(f"{path.name}: source note already existed; archived")
        else:
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
            log.append(
                f"{path.name} -> sources/{note_path.name} "
                f"(links: {links}; marks: {marked}{entities})"
            )

    return log
