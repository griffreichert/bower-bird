"""Fetch a URL and extract lightweight page metadata or a readable body.

For the to-read lane we fetch + render a readable doc into inbox/ — the human
can then read + annotate the .md directly. For the learned lane the user has
already read it, so we also pull a slice of body text to ground the clipping.

We must NEVER distill an article that hasn't been read (INVARIANTS). The inbox
doc is a rendered copy, not a summary — the full text is there for the human.
"""

from __future__ import annotations

import ipaddress
import socket
from io import BytesIO
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify
from pydantic import BaseModel, ConfigDict
from pypdf import PdfReader

_UA = "Mozilla/5.0 (compatible; bower-bird/0.1; +https://github.com/)"
_MAX_BODY_CHARS = 6000
# Cap for rendered inbox docs — enough for a typical article without going huge.
_MAX_RENDER_CHARS = 40_000

# Domains we KNOW httpx can't render — JS-/login-walled, so a plain GET yields
# useless metadata (see daily notes 2026-06-24). These skip fetch entirely and
# route to the clip queue: open in a browser + Web Clipper instead.
_NEEDS_BROWSER = ("x.com", "twitter.com", "t.co")

# PDF lane caps: keep downloads and Haiku grounding bounded.
_MAX_PDF_BYTES = 20_000_000
_MAX_PDF_PAGES = 50


def is_pdf_url(url: str) -> bool:
    """True if this URL points at a PDF we should text-extract, not render.

    ponytail: URL shape only (.pdf path or arXiv /pdf/) — a PDF served from an
    extensionless URL falls through to the HTML path; add a Content-Type sniff
    if that ever bites.
    """
    parts = urlparse(url)
    return parts.path.lower().endswith(".pdf") or (
        parts.netloc.lower().endswith("arxiv.org") and parts.path.startswith("/pdf/")
    )


def needs_clipping(url: str) -> bool:
    """True if this URL must be captured via the browser Web Clipper, not httpx."""
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in _NEEDS_BROWSER)


_MAX_REDIRECTS = 5


def _is_public_host(host: str) -> bool:
    """True only if every IP `host` resolves to is publicly routable.

    SSRF guard: URLs come from untrusted Telegram messages, so a fetch must not
    be steerable at localhost, LAN devices, or cloud-metadata (169.254.169.254).
    Rejects loopback/private/link-local/reserved/multicast ranges.
    """
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def _safe_get(url: str, timeout: float) -> httpx.Response:
    """GET with an SSRF guard, following redirects manually so every hop is
    re-validated (a public URL can 30x-redirect into internal space).

    Raises ValueError on a non-http scheme or a non-public host at any hop.

    ponytail: small TOCTOU/DNS-rebind residual — getaddrinfo here and httpx's
    own resolution are separate lookups. Acceptable for a personal laptop tool;
    upgrade path is pinning the validated IP into the connection.
    """
    with httpx.Client(
        headers={"User-Agent": _UA}, timeout=timeout, follow_redirects=False
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                raise ValueError(f"blocked non-http(s) scheme: {parsed.scheme}")
            if not _is_public_host(parsed.hostname or ""):
                raise ValueError(f"blocked non-public host: {parsed.hostname}")
            resp = client.get(url)
            if resp.is_redirect and resp.has_redirect_location:
                url = str(resp.next_request.url)
                continue
            return resp
    raise ValueError("too many redirects")


class PageMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    title: str
    description: str
    body_excerpt: str  # only used by the learned lane
    author: str = ""  # byline, when the page/clip exposes one

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


def _render_markdown(soup: BeautifulSoup) -> str:
    """Render a readable markdown body from a parsed page.

    Strips boilerplate (script/style/nav/footer/header/aside), picks the main
    content region, then converts it to markdown (headings/lists/links intact)
    for the human to read + annotate in the inbox doc.
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

    md = markdownify(str(main), heading_style="ATX")
    # Collapse the runs of blank lines markdownify leaves behind.
    return "\n\n".join(chunk.strip() for chunk in md.split("\n\n") if chunk.strip())


def fetch(url: str, timeout: float) -> PageMeta:
    try:
        resp = _safe_get(url, timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        # Non-fatal: an unreachable or blocked link still gets captured, thinner.
        return PageMeta(url=url, title=url, description="", body_excerpt="")

    title = _meta_content(soup, "og:title", "twitter:title")
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        title = url

    description = _meta_content(
        soup, "og:description", "twitter:description", "description"
    )
    author = _meta_content(soup, "article:author", "author", "twitter:creator")

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    body_text = " ".join(soup.get_text(" ", strip=True).split())

    return PageMeta(
        url=url,
        title=title,
        description=description,
        body_excerpt=body_text[:_MAX_BODY_CHARS],
        author=author,
    )


def fetch_pdf(url: str, timeout: float) -> PageMeta:
    """Download a PDF and extract its text for the learned lane.

    A sent PDF counts as read (INVARIANTS, 2026-07-11), so unlike HTML it never
    lands in the to-read court — the caller synthesizes straight into brain/.
    Returns thin meta (empty body_excerpt) on any failure, including a scanned
    PDF with no extractable text; the caller routes those to the clip queue.
    """
    try:
        resp = _safe_get(url, timeout)
        resp.raise_for_status()
        if len(resp.content) > _MAX_PDF_BYTES:
            raise ValueError(f"pdf too large: {len(resp.content)} bytes")
        reader = PdfReader(BytesIO(resp.content))
        text = "\n".join(
            page.extract_text() or "" for page in reader.pages[:_MAX_PDF_PAGES]
        ).strip()
    except Exception:
        # Non-fatal, same contract as fetch(): thin meta, caller decides.
        return PageMeta(url=url, title=url, description="", body_excerpt="")

    info = reader.metadata
    title = (info.title or "").strip() if info else ""
    if not title:
        # Fall back to the first real line of text, then the URL filename.
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        title = first[:80] or urlparse(url).path.rsplit("/", 1)[-1] or url
    author = (info.author or "").strip() if info else ""

    return PageMeta(
        url=url,
        title=title,
        description="",
        body_excerpt=text[:_MAX_BODY_CHARS],
        author=author,
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
        resp = _safe_get(url, timeout)
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

    # Markdown body for the readable doc (richer structure than body_excerpt).
    body = _render_markdown(soup)

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
