"""Router: tell the lanes apart by *how* the message was sent.

| Send                          | Lane     |
| ----------------------------- | -------- |
| `tool:` + link                | tool     |
| bare link                     | to-read  |
| link + my note, or `read`     | learned  |

The user's one-line "why" is the highest-value input, so anything beyond the
bare URL routes to the learned lane. The word `read` alone (any case, before
or after the link — the share-sheet flow is link first, then type) is an
explicit I-read-this marker, not a note. An explicit `tool:` prefix overrides
everything — it's a keep-for-later shelf item, not knowledge.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

_URL_RE = re.compile(r"https?://\S+")
_READ_PREFIX_RE = re.compile(r"^\s*read\s*:", re.IGNORECASE)
_TOOL_PREFIX_RE = re.compile(r"^\s*tool\s*:", re.IGNORECASE)
# The whole note is just the word "read" (punctuation/whitespace around it OK):
# an explicit I-read-this marker, not a note worth keeping.
_READ_TOKEN_RE = re.compile(r"^\W*read\W*$", re.IGNORECASE)


class Lane(StrEnum):
    TO_READ = "to_read"
    LEARNED = "learned"
    TOOL = "tool"  # keep-for-later shelf (a plugin/repo/tool), not knowledge
    NO_LINK = "no_link"  # nothing to capture; skipped quietly


class Parsed(BaseModel):
    model_config = ConfigDict(frozen=True)

    lane: Lane
    url: str | None
    note: str  # the user's surrounding note ("why"), empty for bare links


def _strip_trailing_punct(url: str) -> str:
    return url.rstrip(").,;]>”\"'")


def parse(text: str) -> Parsed:
    # `tool:` is an explicit override — a shelf item, never knowledge. A tool
    # note without a link isn't useful to collect, so it falls back to NO_LINK.
    if _TOOL_PREFIX_RE.match(text):
        body = _TOOL_PREFIX_RE.sub("", text, count=1)
        match = _URL_RE.search(body)
        if not match:
            return Parsed(lane=Lane.NO_LINK, url=None, note=text.strip())
        url = _strip_trailing_punct(match.group(0))
        note = (body[: match.start()] + body[match.end() :]).strip()
        return Parsed(lane=Lane.TOOL, url=url, note=note)

    explicit_read = bool(_READ_PREFIX_RE.match(text))
    body = _READ_PREFIX_RE.sub("", text, count=1)

    match = _URL_RE.search(body)
    if not match:
        return Parsed(lane=Lane.NO_LINK, url=None, note=text.strip())

    url = _strip_trailing_punct(match.group(0))
    note = (body[: match.start()] + body[match.end() :]).strip()

    if _READ_TOKEN_RE.match(note):
        return Parsed(lane=Lane.LEARNED, url=url, note="")
    if explicit_read or note:
        return Parsed(lane=Lane.LEARNED, url=url, note=note)
    return Parsed(lane=Lane.TO_READ, url=url, note="")
