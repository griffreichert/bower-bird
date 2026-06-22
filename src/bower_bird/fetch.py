"""Fetch a URL and extract lightweight page metadata.

For the to-read lane we only need *metadata* (title + what-is-it), never a
summary of the content — we must never distill an article that hasn't been
read. For the learned lane the user has already read it, so we also pull a
slice of body text to ground the clipping.
"""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict

_UA = "Mozilla/5.0 (compatible; bower-bird/0.1; +https://github.com/)"
_MAX_BODY_CHARS = 6000


class PageMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    title: str
    description: str
    body_excerpt: str  # only used by the learned lane


def _meta_content(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find(
            "meta", attrs={"name": name}
        )
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


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
