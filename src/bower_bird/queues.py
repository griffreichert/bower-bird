"""Flat-file shelf/queue appends: the clip queue (`to-clip.md`), the tools
shelf (`tools.md`), and the catch-all Telegram inbox (`_inbox.md`).

Unlike `nodes.py` these never touch the concept graph — each is a single
append-only markdown file. Every write here goes through
`ingest.assert_writable` (the boundary guard stays in `ingest.py`).
"""

from datetime import datetime

from bower_bird.config import Config
from bower_bird.ingest import assert_writable, today_iso

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
    assert_writable(config, path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(_TO_CLIP_HEADER.format(today=today_iso()), encoding="utf-8")

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
    assert_writable(config, path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(_TOOLS_HEADER.format(today=today_iso()), encoding="utf-8")

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
    assert_writable(config, path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(
            _TELEGRAM_INBOX_HEADER.format(today=today_iso()), encoding="utf-8"
        )

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    flat = " ".join(text.split())
    entry = f"- `{stamp}` ({reason}) {flat}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(entry)
