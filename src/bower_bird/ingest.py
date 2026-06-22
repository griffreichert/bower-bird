"""Vault writes — the only side effects that touch the vault.

Hard boundary (per INVARIANTS): runtime vault writes are limited to
`Clippings/` and `reading-list.md`. Backlinks into the evergreen layer
(`g/learning/`) are *proposed inside the clipping*, never written into those
raw notes — the agent proposes, I decide. `_assert_writable` enforces this in
code so a future change can't quietly break it.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .config import Config
from .fetch import PageMeta
from .llm import ClippingPlan

_INVALID_FILENAME = re.compile(r'[/:\\?%*|"<>]')
_SKIP_NOTE_STEMS = {"_index", "_archive", "_template", "__init__"}


def _assert_writable(config: Config, path: Path) -> None:
    resolved = path.resolve()
    allowed = (
        resolved == config.reading_list_path.resolve()
        or config.clippings_dir.resolve() in resolved.parents
    )
    if not allowed:
        raise PermissionError(
            f"Refusing to write outside the research sandbox: {resolved}. "
            "Only Clippings/ and reading-list.md are writable at runtime."
        )


def list_evergreen_notes(config: Config) -> list[str]:
    """Existing evergreen-note titles — candidates for proposed backlinks."""
    if not config.learning_dir.is_dir():
        return []
    titles: list[str] = []
    for path in sorted(config.learning_dir.glob("*.md")):
        if path.stem in _SKIP_NOTE_STEMS:
            continue
        titles.append(path.stem)
    return titles


def _safe_filename(title: str) -> str:
    cleaned = _INVALID_FILENAME.sub("-", title).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or "untitled")[:120]


# --------------------------------------------------------------------------- #
# to-read lane
# --------------------------------------------------------------------------- #

_READING_LIST_HEADER = """\
---
title: Reading list
created: {today}
tags:
  - clippings
---
# Reading list

Links to read, captured by bower-bird. Check one off when read, then promote it
to a clipping in `Clippings/`.

#type/reference #domain/personal #status/active

## Queue
"""


def append_to_reading_list(config: Config, url: str, title: str, oneline: str) -> bool:
    """Append a `- [ ]` entry. Returns False if the URL is already listed."""
    path = config.reading_list_path
    _assert_writable(config, path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(
            _READING_LIST_HEADER.format(today=date.today().isoformat()),
            encoding="utf-8",
        )

    existing = path.read_text(encoding="utf-8")
    if url in existing:
        return False

    entry = f"- [ ] [{title}]({url}) — {oneline}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(entry)
    return True


# --------------------------------------------------------------------------- #
# learned lane
# --------------------------------------------------------------------------- #

_CLIPPING_TEMPLATE = """\
---
title: "{title}"
source: "{url}"
created: {today}
description: "{description}"
tags:
  - clippings
---
# {title}

## My note
{note}

## Proposed links
> _Suggested by bower-bird — accept the ones you want, ignore the rest._

{backlinks}

{proposed_note}

{connection}
"""


def create_clipping(
    config: Config, meta: PageMeta, note: str, plan: ClippingPlan
) -> Path | None:
    """Write a clipping into Clippings/. Returns None if it already exists."""
    config.clippings_dir.mkdir(parents=True, exist_ok=True)
    path = config.clippings_dir / f"{_safe_filename(meta.title)}.md"
    _assert_writable(config, path)
    if path.exists():
        return None

    backlinks = (
        "\n".join(f"- [[{link}]]" for link in plan.proposed_backlinks)
        if plan.proposed_backlinks
        else "- _(no existing notes matched)_"
    )
    proposed_note = (
        f"**Proposed new evergreen note:** [[{plan.proposed_note_title}]]"
        if plan.proposed_note_title
        else ""
    )
    connection = f"**Why these connect:** {plan.connection}" if plan.connection else ""

    body = _CLIPPING_TEMPLATE.format(
        title=meta.title.replace('"', "'"),
        url=meta.url,
        today=date.today().isoformat(),
        description=plan.description.replace('"', "'"),
        note=note or "_(captured via `read:` with no note)_",
        backlinks=backlinks,
        proposed_note=proposed_note,
        connection=connection,
    )
    path.write_text(body, encoding="utf-8")
    return path
