import re

from bower_bird.schema import Lane, Parsed

_URL_RE = re.compile(r"https?://\S+")
_TOOL_PREFIX_RE = re.compile(r"^\s*tool\s*:", re.IGNORECASE)


def strip_trailing_punct(url: str) -> str:
    """Strip punctuation a sentence leaves stuck to the end of a URL."""
    return url.rstrip(").,;]>”\"'")


def parse(text: str) -> Parsed:
    """Route a Telegram send by its shape.

    A ``tool:`` prefix with a link goes to the tools shelf and overrides
    everything; without a link it parks as no_link. Any other link, bare or
    with a note, is shelved, and the note rides along as a seed thought. Text
    with no link is pasted prose and becomes its own source node.
    """
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
