"""Reader marks: signals the human leaves in a clip while reading it.

A Web Clipper save is already a "I read this" signal; these marks let the reader
say *what mattered* inside it, with near-zero friction in Obsidian. Four marks,
all optional, all extracted from the clip's markdown body:

- ``==highlight==``  — interesting, supports my knowledge → persist verbatim.
  These are the durable unit: brain/ keeps highlights, not whole articles.
- ``#dig`` on a line — want to learn more about this → next-reads.
- ``> ? question``   — a blockquote line starting with ``?`` is my open question
  (plain ``> quotes`` from the article are ignored). Continuation ``>`` lines
  fold into the same question.
- ``[anchor](url)``  — outbound links the article references → leads to capture.

Plus three bare *directive* tags (opt-in, deliberate — like ``#dig``):

- ``#person`` on/near a line — promote the person named there into a
  ``brain/people/`` node. Anchors person-extraction (default OFF), so ordinary
  bylines don't auto-mint. Placed inline (``Jerry Liu ... #person``) or on its
  own line under the mention (``...Steve Jobs was cool`` ⏎ ``#person``).
- ``#promote`` — file the full source body straight into the graph (not a thin
  node), and freeze it.
- ``#frozen`` — mark this node's prose immutable (weave adds links, never
  rewrites). ``#promote`` implies ``#frozen``.

Pure text logic — no network, no vault writes. The Tier-1 pass lifts these into
the source note; Tier-2 grows highlights into nest concepts and routes
dig/questions into the digest's gaps & next-reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HIGHLIGHT = re.compile(r"==(.+?)==")
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
# Embedded media (image CDNs / image file URLs) — leads we don't want to read.
_IMAGE_URL = re.compile(
    r"(pbs\.twimg\.com|\.(?:png|jpe?g|gif|webp|svg)(?:[?#]|$)|[?&]format=(?:jpg|jpeg|png|webp|gif))",
    re.IGNORECASE,
)
_BARE_URL = re.compile(r"https?://[^\s)\]<>]+")
_DIG = re.compile(r"#dig\b")  # \b so #digest / #digging don't match
_PERSON = re.compile(r"#person\b")  # \b so #personal doesn't match
_PROMOTE = re.compile(r"#promote\b")
_FROZEN = re.compile(r"#frozen\b")
_QUESTION = re.compile(r"^>\s*\?\s?(.*)$")
_QUOTE_LINE = re.compile(r"^>\s?(.*)$")
# Directive tokens carry intent, not content — strip them from stored/synth prose
# so `#promote` never lands in a node body or biases the model's extraction.
_DIRECTIVE_TOKEN = re.compile(r"#(?:promote|frozen|person|dig)\b")


def strip_directives(body: str) -> str:
    """Remove directive tags from a body, leaving the reader's prose clean.

    Extract marks from the RAW body first — this runs after, on the copy that
    gets synthesised + stored. Tokens are dropped inline (a highlight line keeps
    its text); blank lines left behind are collapsed.
    """
    out = _DIRECTIVE_TOKEN.sub("", body)
    out = re.sub(r"[ \t]+\n", "\n", out)  # trailing space from a stripped token
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip("\n")


@dataclass
class Marks:
    """The reader's marks pulled from a clip body."""

    highlights: list[str] = field(default_factory=list)
    dig: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    further_links: list[tuple[str, str]] = field(default_factory=list)
    person_anchors: list[str] = field(default_factory=list)  # #person mention text
    promote: bool = False  # #promote — full body into the graph + freeze
    frozen: bool = False  # #frozen — prose immutable (promote implies this)

    @property
    def empty(self) -> bool:
        return not (self.highlights or self.dig or self.questions or self.further_links)


def _unwrap_highlights(text: str) -> str:
    return _HIGHLIGHT.sub(r"\1", text)


def _extract_highlights(body: str) -> list[str]:
    return [h.strip() for h in _HIGHLIGHT.findall(body) if h.strip()]


def _extract_dig(body: str) -> list[str]:
    """Lines tagged ``#dig`` — the line's text is the thread to pull on."""
    out: list[str] = []
    for raw in body.splitlines():
        if not _DIG.search(raw):
            continue
        cleaned = _DIG.sub("", raw)
        cleaned = _unwrap_highlights(cleaned)
        cleaned = cleaned.lstrip("#>-*+ \t")
        cleaned = " ".join(cleaned.split())
        out.append(cleaned or "(this note)")
    return out


def _clean_line(text: str) -> str:
    """Strip a directive tag's line down to its plain mention text."""
    cleaned = _PERSON.sub("", text)
    cleaned = _unwrap_highlights(cleaned)
    cleaned = cleaned.replace("[[", "").replace("]]", "")
    cleaned = cleaned.lstrip("#>-*+ \t")
    return " ".join(cleaned.split())


def _extract_person(body: str) -> list[str]:
    """Mention text for each ``#person`` tag — the anchor Haiku resolves a name
    from. Inline (tag on the mention's line) uses that line; a tag alone on its
    own line reaches back to the nearest preceding non-empty line.
    """
    lines = body.splitlines()
    out: list[str] = []
    for i, raw in enumerate(lines):
        if not _PERSON.search(raw):
            continue
        anchor = _clean_line(raw)
        if not anchor:  # tag stands alone → use the mention above it
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            anchor = _clean_line(lines[j]) if j >= 0 else ""
        if anchor:
            out.append(anchor)
    return out


def _extract_questions(body: str) -> list[str]:
    """Blockquote lines starting ``> ?`` — my own questions, not article quotes.

    A ``> ?`` line opens a question; following plain ``>`` lines extend it; any
    non-blockquote line (or the next ``> ?``) closes it.
    """
    questions: list[str] = []
    current: str | None = None

    def flush() -> None:
        nonlocal current
        if current is not None and current.strip():
            questions.append(current.strip())
        current = None

    for raw in body.splitlines():
        line = raw.rstrip()
        q = _QUESTION.match(line)
        if q:
            flush()
            current = q.group(1)
            continue
        cont = _QUOTE_LINE.match(line)
        if cont and current is not None:
            current = f"{current} {cont.group(1)}".strip()
            continue
        flush()
    flush()
    return questions


def _extract_links(body: str, self_url: str) -> list[tuple[str, str]]:
    """Outbound markdown links, de-duped, excluding the source's own URL."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for anchor, url in _MD_LINK.findall(body):
        if url == self_url or url in seen or _IMAGE_URL.search(url):
            continue
        seen.add(url)
        out.append((anchor.strip() or url, url))
    return out


def extract_urls(body: str) -> list[str]:
    """Every link in a clip body — bare or markdown, de-duped, order-preserving,
    images dropped. Grounds entity (tool/person) extraction and supplies the
    source URL when the clip frontmatter has none (e.g. a Telegram note pasted
    with bare links)."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _BARE_URL.finditer(body):
        url = match.group(0).rstrip(".,);:'\"")
        if url in seen or _IMAGE_URL.search(url):
            continue
        seen.add(url)
        out.append(url)
    return out


def pick_source_url(urls: list[str]) -> str:
    """Choose the most source-like URL from a clip's body links.

    Prefer one with a deep path (a post / article / repo — ≥2 path segments,
    e.g. `x.com/u/status/123`, `github.com/org/repo`) over a bare profile or
    domain root (`x.com/u`). Falls back to the first URL; '' if none.

    # ponytail: 2-segment path heuristic — good enough to skip author profiles;
    # swap for a known-profile-domain list if it picks wrong on real clips.
    """
    if not urls:
        return ""
    for url in urls:
        after = url.split("://", 1)[-1]
        path = after.split("/", 1)[1] if "/" in after else ""
        segments = [s for s in path.split("/") if s]
        if len(segments) >= 2:
            return url
    return urls[0]


def extract_marks(body: str, self_url: str = "") -> Marks:
    """Pull every reader mark out of a clip body."""
    return Marks(
        highlights=_extract_highlights(body),
        dig=_extract_dig(body),
        questions=_extract_questions(body),
        further_links=_extract_links(body, self_url),
        person_anchors=_extract_person(body),
        promote=bool(_PROMOTE.search(body)),
        frozen=bool(_FROZEN.search(body)) or bool(_PROMOTE.search(body)),
    )
