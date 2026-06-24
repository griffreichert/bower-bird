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

Pure text logic — no network, no vault writes. The Tier-1 drain lifts these into
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
_DIG = re.compile(r"#dig\b")  # \b so #digest / #digging don't match
_QUESTION = re.compile(r"^>\s*\?\s?(.*)$")
_QUOTE_LINE = re.compile(r"^>\s?(.*)$")


@dataclass
class Marks:
    """The reader's marks pulled from a clip body."""

    highlights: list[str] = field(default_factory=list)
    dig: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    further_links: list[tuple[str, str]] = field(default_factory=list)

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


def extract_marks(body: str, self_url: str = "") -> Marks:
    """Pull every reader mark out of a clip body."""
    return Marks(
        highlights=_extract_highlights(body),
        dig=_extract_dig(body),
        questions=_extract_questions(body),
        further_links=_extract_links(body, self_url),
    )
