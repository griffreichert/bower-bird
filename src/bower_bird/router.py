"""Two-lane router: tell the lanes apart by *how* the message was sent.

| Send                         | Lane     |
| ---------------------------- | -------- |
| bare link                    | to-read  |
| link + my note, or `read:`   | learned  |

The user's one-line "why" is the highest-value input, so anything beyond the
bare URL (or the explicit `read:` prefix) routes to the learned lane.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

_URL_RE = re.compile(r"https?://\S+")
_READ_PREFIX_RE = re.compile(r"^\s*read\s*:", re.IGNORECASE)


class Lane(StrEnum):
    TO_READ = "to_read"
    LEARNED = "learned"
    NO_LINK = "no_link"  # nothing to capture; skipped quietly


class Parsed(BaseModel):
    model_config = ConfigDict(frozen=True)

    lane: Lane
    url: str | None
    note: str  # the user's surrounding note ("why"), empty for bare links


def _strip_trailing_punct(url: str) -> str:
    return url.rstrip(").,;]>”\"'")


def parse(text: str) -> Parsed:
    explicit_read = bool(_READ_PREFIX_RE.match(text))
    body = _READ_PREFIX_RE.sub("", text, count=1)

    match = _URL_RE.search(body)
    if not match:
        return Parsed(lane=Lane.NO_LINK, url=None, note=text.strip())

    url = _strip_trailing_punct(match.group(0))
    note = (body[: match.start()] + body[match.end() :]).strip()

    if explicit_read or note:
        return Parsed(lane=Lane.LEARNED, url=url, note=note)
    return Parsed(lane=Lane.TO_READ, url=url, note="")
