"""Fetch a URL and extract lightweight page metadata or a readable body.

For the to-read lane we fetch + render a readable doc into inbox/ — the human
can then read + annotate the .md directly. For the learned lane the user has
already read it, so we also pull a slice of body text to ground the clipping.

We must NEVER distill an article that hasn't been read (INVARIANTS). The inbox
doc is a rendered copy, not a summary — the full text is there for the human.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict

_UA = "Mozilla/5.0 (compatible; bower-bird/0.1; +https://github.com/)"
_MAX_BODY_CHARS = 6000
# Cap for rendered inbox docs — enough for a typical article without going huge.
_MAX_RENDER_CHARS = 40_000

# Domains we KNOW httpx can't render — JS-/login-walled, so a plain GET yields
# useless metadata (see daily notes 2026-06-24). These skip fetch entirely and
# route to the clip queue: open in a browser + Web Clipper instead.
_NEEDS_BROWSER = ("x.com", "twitter.com", "t.co")


def needs_clipping(url: str) -> bool:
    """True if this URL must be captured via the browser Web Clipper, not httpx."""
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in _NEEDS_BROWSER)


class PageMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    title: str
    description: str
    body_excerpt: str  # only used by the learned lane

    @property
    def is_thin(self) -> bool:
        """Fetch came back empty — no real title and no description. The page is
        likely JS-/login-walled; route to the clip queue instead of writing a
        broken inbox doc."""
        return not self.description and (not self.title or self.title == self.url)


def _meta_content(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find(
            "meta", attrs={"name": name}
        )
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _render_paragraphs(soup: BeautifulSoup) -> str:
    """Extract readable paragraph-structured text from a parsed page.

    Strips boilerplate (script/style/nav/footer/header), then walks top-level
    block elements to emit newline-separated paragraphs — much more readable
    than collapsing all whitespace into one line.

    Returns plain text; not markdown-formatted (no headings, no bullets) so
    it's safe to embed in a markdown doc without escaping.
    """
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # Try to find a main content region; fall back to body.
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(id="content")
        or soup.find(id="main")
        or soup.find(class_="content")
        or soup.body
        or soup
    )

    _BLOCK = {
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "blockquote",
        "pre",
        "div",
        "section",
    }

    paragraphs: list[str] = []
    seen: set[str] = set()
    for el in main.find_all(_BLOCK):  # type: ignore[union-attr]
        # Skip deeply nested blocks — we want top-level chunks, not duplicates.
        if any(p.name in _BLOCK for p in el.parents if p is not main):
            continue
        chunk = " ".join(el.get_text(" ", strip=True).split())
        if chunk and chunk not in seen:
            seen.add(chunk)
            paragraphs.append(chunk)

    return "\n\n".join(paragraphs)


def fetch(url: str, timeout: float) -> PageMeta:
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": _UA},
            timeout=timeout,
            follow_redirects=True,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        # Non-fatal: an unreachable link still gets captured, just thinner.
        return PageMeta(url=url, title=url, description="", body_excerpt="")

    title = _meta_content(soup, "og:title", "twitter:title")
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        title = url

    description = _meta_content(
        soup, "og:description", "twitter:description", "description"
    )

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    body_text = " ".join(soup.get_text(" ", strip=True).split())

    return PageMeta(
        url=url,
        title=title,
        description=description,
        body_excerpt=body_text[:_MAX_BODY_CHARS],
    )


def fetch_rendered(url: str, timeout: float) -> tuple[PageMeta, str]:
    """Fetch a URL and return both PageMeta and a readable paragraph body.

    Used by the inbox-fill path: the bot renders a readable doc into inbox/
    so the human can read + annotate it there. Returns (meta, body_text) where
    body_text is paragraph-structured plain text (empty on fetch failure).

    On failure the meta is thin (title = url, no description) — callers should
    check `meta.is_thin` to decide whether to route to the clip queue instead.
    """
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": _UA},
            timeout=timeout,
            follow_redirects=True,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return PageMeta(url=url, title=url, description="", body_excerpt=""), ""

    title = _meta_content(soup, "og:title", "twitter:title")
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        title = url

    description = _meta_content(
        soup, "og:description", "twitter:description", "description"
    )

    # Paragraph body for the readable doc (richer structure than body_excerpt).
    body = _render_paragraphs(soup)

    # Also derive body_excerpt for the PageMeta (shared with other callers).
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    flat = " ".join(soup.get_text(" ", strip=True).split())

    meta = PageMeta(
        url=url,
        title=title,
        description=description,
        body_excerpt=flat[:_MAX_BODY_CHARS],
    )
    return meta, body[:_MAX_RENDER_CHARS]
