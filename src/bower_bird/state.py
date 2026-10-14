import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import BaseModel, Field

from bower_bird.resolve import parse_tweet_id

# parse_tweet_id validates the URL but does not expose the handle.
_TWEET_HANDLE_RE = re.compile(r"^/([^/]+)/status/\d+")

_TRACKING_PARAMS = {"s", "t", "si", "fbclid", "gclid", "ref"}


def content_hash(text: str) -> str:
    """Stable hash of a clip's content, for idempotent inbox processing."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_tracking_param(key: str) -> bool:
    """Return True for ``utm_*`` and known share tracking query keys."""
    key = key.lower()
    return key.startswith("utm_") or key in _TRACKING_PARAMS


def canonicalize_url(url: str) -> str:
    """Return one canonical URL per real resource.

    Dedup and clearing then ignore which share link variant was sent. Tweet
    URLs collapse to the tweet-id form (id is the identity; two
    different-handle forms of the same status don't occur in practice, the
    handle lives in the path). Everything else: lowercase scheme+host, strip
    `www.`, strip the fragment, drop known tracking params while keeping the
    rest (arXiv-style params can be load-bearing), and drop a trailing slash
    on non-root paths.
    """
    tweet_id = parse_tweet_id(url)
    if tweet_id is not None:
        parsed = urlparse(url)
        match = _TWEET_HANDLE_RE.match(parsed.path)
        handle = match.group(1).lower() if match else ""
        return f"https://x.com/{handle}/status/{tweet_id}"

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parsed.port:
        host = f"{host}:{parsed.port}"
    path = parsed.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    kept_params = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not is_tracking_param(k)
    ]
    query = urlencode(kept_params)
    return urlunparse((parsed.scheme.lower(), host, path, "", query, ""))


class State(BaseModel):
    """Persist Telegram progress and processed item identities."""

    path: Path
    telegram_offset: int = 0
    processed_urls: set[str] = Field(default_factory=set)
    processed_hashes: set[str] = Field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> "State":
        """Load state from ``path``, or start empty when the file is missing.

        URLs are canonicalized on load so older history keeps deduping.
        """
        if not path.exists():
            return cls(path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            telegram_offset=int(raw.get("telegram_offset", 0)),
            processed_urls={canonicalize_url(u) for u in raw.get("processed_urls", [])},
            processed_hashes=set(raw.get("processed_hashes", [])),
        )

    def save(self) -> None:
        """Write state atomically through a temp file and rename."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "telegram_offset": self.telegram_offset,
            "processed_urls": sorted(self.processed_urls),
            "processed_hashes": sorted(self.processed_hashes),
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def seen_url(self, url: str) -> bool:
        """Return True when the canonical form of ``url`` was processed."""
        return canonicalize_url(url) in self.processed_urls

    def mark_url(self, url: str) -> None:
        """Record the canonical form of ``url`` as processed."""
        self.processed_urls.add(canonicalize_url(url))

    def seen_hash(self, digest: str) -> bool:
        """Return True when a clip with this content hash was processed."""
        return digest in self.processed_hashes

    def mark_hash(self, digest: str) -> None:
        """Record a clip content hash as processed."""
        self.processed_hashes.add(digest)
