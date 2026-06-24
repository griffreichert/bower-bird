"""Clipper inbox: process Obsidian Web Clipper drops in `BowerBird/inbox/`.

A Web Clipper save is a deliberate "I read / care about this" signal (per
INVARIANTS), so clips are processed into the graph immediately. The clipper
already wrote clean markdown — so we skip `fetch.py` entirely and synthesise
from the clip body, which sidesteps the scraper's failure on JS-heavy /
login-walled pages (e.g. X).

Per clip: parse frontmatter + body, dedup by content hash (state lives in the
repo, so it survives the move), synthesise a source note + asserted links, then
move the original clip to `archive/` (non-destructive).
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from . import ingest
from .config import Config
from .fetch import PageMeta
from .llm import synthesize_clipping
from .marks import extract_marks
from .state import State, content_hash

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


def _clip_to_meta(path: Path, fm: dict[str, str], body: str) -> PageMeta:
    return PageMeta(
        url=fm.get("source") or fm.get("url") or "",
        title=fm.get("title") or path.stem,
        description=fm.get("description", ""),
        body_excerpt=body[:_BODY_LIMIT],
    )


def _archive(config: Config, path: Path) -> None:
    config.archive_dir.mkdir(parents=True, exist_ok=True)
    dest = config.archive_dir / path.name
    if dest.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = config.archive_dir / f"{path.stem}.{stamp}{path.suffix}"
    ingest._assert_writable(config, dest)
    shutil.move(str(path), str(dest))


def process_inbox(config: Config, state: State) -> list[str]:
    """Process every clip in the inbox once. Returns one log line per clip."""
    if not config.inbox_dir.is_dir():
        return []

    log: list[str] = []
    for path in sorted(config.inbox_dir.glob("*.md")):
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

        try:
            fm, body = _parse_clip(raw)
            meta = _clip_to_meta(path, fm, body)
            marks = extract_marks(body, self_url=meta.url)
            candidates = ingest.list_concept_notes(config)
            plan = synthesize_clipping(
                meta, "", candidates, model=config.model, highlights=marks.highlights
            )
            note_path = ingest.create_source_note(config, meta, plan, marks=marks)
        except Exception as exc:  # noqa: BLE001 — one bad clip must not stall the rest
            log.append(f"error {path.name}: {exc}")
            continue

        state.mark_hash(digest)
        if meta.url:
            state.mark_url(meta.url)
        state.save()
        _archive(config, path)

        if note_path is None:
            log.append(f"{path.name}: source note already existed; archived")
        else:
            links = ", ".join(plan.topics) or "none"
            marked = (
                f"{len(marks.highlights)}h/{len(marks.dig)}d/"
                f"{len(marks.questions)}q/{len(marks.further_links)}l"
            )
            log.append(
                f"{path.name} -> sources/{note_path.name} "
                f"(links: {links}; marks: {marked})"
            )

    return log
