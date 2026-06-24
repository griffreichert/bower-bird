"""Vault writes — the only side effects that touch the vault.

Hard boundary (per INVARIANTS): bower-bird owns the `BowerBird/` folder
(`config.vault_path`) and writes *nowhere else*. `_assert_writable` enforces
that in code so a future change can't quietly break it.

Additive-autonomous: bower-bird creates its own notes (sources/, new concept
notes) and asserts `[[links]]` freely, but it **never rewrites or deletes an
existing note**. Every write here is either a brand-new file or a strictly
additive append (`_append_link`) — so nothing a human authored is ever
clobbered.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .config import Config
from .fetch import PageMeta
from .llm import ClippingPlan

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
    """Concept-note titles across all nests — candidates for backlinks.

    Recurses brain/nests/ since concepts live in topical subfolders.
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

    If a note with this title already exists anywhere under brain/nests/, return
    its actual (possibly nested) path so we link into it rather than creating a
    flat duplicate. Otherwise return the flat path at the nests root, where new,
    unfiled concepts land until Tier-2 files them into a nest.
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
# to-read lane (bare links — unread, metadata only)
# --------------------------------------------------------------------------- #

_READING_LIST_HEADER = """\
---
title: Reading list
created: {today}
bower: generated
tags:
  - reading-list
---
# Reading list

Unread links captured by bower-bird. Check one off when read, then it can be
promoted to a source note.

#type/reference #domain/personal #status/active

## Queue
"""


def append_to_reading_list(config: Config, url: str, title: str, oneline: str) -> bool:
    """Append a `- [ ]` entry. Returns False if the URL is already listed."""
    path = config.reading_list_path
    _assert_writable(config, path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(_READING_LIST_HEADER.format(today=_today()), encoding="utf-8")

    existing = path.read_text(encoding="utf-8")
    if url in existing:
        return False

    entry = f"- [ ] [{title}]({url}) — {oneline}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(entry)
    return True


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

_SOURCE_TEMPLATE = """\
---
title: "{title}"
source: "{url}"
created: {today}
description: "{description}"
bower: generated
tags:
  - source
---
# {title}

## Links
{backlinks}

{connection}

## Captured
{captured}
"""


def create_source_note(
    config: Config, meta: PageMeta, captured: str, plan: ClippingPlan
) -> Path | None:
    """Write a source note into sources/ and assert links into the graph.

    Returns the source-note path, or None if a note for this source already
    exists (filename-level guard; primary dedup is in state).
    """
    config.sources_dir.mkdir(parents=True, exist_ok=True)
    source_title = _safe_filename(meta.title)
    path = config.sources_dir / f"{source_title}.md"
    _assert_writable(config, path)
    if path.exists():
        return None

    targets = list(plan.proposed_backlinks)
    if plan.proposed_note_title:
        targets.append(plan.proposed_note_title)

    backlinks = (
        "\n".join(f"- [[{t}]]" for t in targets)
        if targets
        else "- _(no connections yet)_"
    )
    connection = f"**Why these connect:** {plan.connection}" if plan.connection else ""

    body = _SOURCE_TEMPLATE.format(
        title=meta.title.replace('"', "'"),
        url=meta.url,
        today=_today(),
        description=plan.description.replace('"', "'"),
        backlinks=backlinks,
        connection=connection,
        captured=captured.strip() or "_(no note or excerpt captured)_",
    )
    path.write_text(body, encoding="utf-8")

    # Assert reciprocal links into the concept graph (additive only). Link into
    # an existing nested concept where it lives; new ones land flat at the nests
    # root for Tier-2 to file.
    for target in targets:
        _append_link(config, find_concept_path(config, target), source_title)

    return path
