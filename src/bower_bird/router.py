"""Router: tell the lanes apart by *how* the message was sent.

| Send                          | Lane     |
| ------------------------------ | -------- |
| `tool:` + link                 | tool     |
| any link (bare, or +note)      | shelve   |
| no link, real text             | paste    |
| no link, nothing usable        | no_link  |

Antilibrary model (2026-07-13): every capture is shelved immediately, so
there's no read-status fork left to parse for. A link — bare or with a note —
is always the `shelve` lane; the note (if any) rides along as a seed thought
for distillation. Text with no link at all is pasted prose and becomes its own
source node (the `paste` lane). An explicit `tool:` prefix overrides
everything — it's a keep-for-later shelf item, not knowledge.
"""

import re

from bower_bird.schema import Lane, Parsed

_URL_RE = re.compile(r"https?://\S+")
_TOOL_PREFIX_RE = re.compile(r"^\s*tool\s*:", re.IGNORECASE)


def strip_trailing_punct(url: str) -> str:
    return url.rstrip(").,;]>”\"'")


def parse(text: str) -> Parsed:
    # `tool:` is an explicit override — a shelf item, never knowledge. A tool
    # note without a link isn't useful to collect, so it falls back to NO_LINK.
    if _TOOL_PREFIX_RE.match(text):
        body = _TOOL_PREFIX_RE.sub("", text, count=1)
        match = _URL_RE.search(body)
        if not match:
            return Parsed(lane=Lane.NO_LINK, url=None, note=text.strip())
        url = strip_trailing_punct(match.group(0))
        note = (body[: match.start()] + body[match.end() :]).strip()
        return Parsed(lane=Lane.TOOL, url=url, note=note)

    match = _URL_RE.search(text)
    if match:
        url = strip_trailing_punct(match.group(0))
        note = (text[: match.start()] + text[match.end() :]).strip()
        return Parsed(lane=Lane.SHELVE, url=url, note=note)

    stripped = text.strip()
    if stripped:
        return Parsed(lane=Lane.PASTE, url=None, note=stripped)
    return Parsed(lane=Lane.NO_LINK, url=None, note="")
