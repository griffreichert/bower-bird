"""Durable state: Telegram offset + processed-item dedup.

Idempotency is a hard invariant: an item already processed is never
double-processed. Two layers:

- `telegram_offset` — passed to getUpdates so Telegram never redelivers an
  update we've already acknowledged.
- `processed_urls` — guards against the same link being re-sent by hand and
  re-ingested.
- `processed_hashes` — content hashes of clipper files already processed.
  Lives here (in the repo), not the vault, so dedup survives the original clip
  being moved to `archive/`.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bower_bird.resolve import parse_tweet_id

# resolve.py only imports from fetch.py, so state -> resolve is cycle-free.
# parse_tweet_id validates host + status path but doesn't expose the handle,
# so the handle capture is redone here rather than reaching into resolve's
# private regex.
_TWEET_HANDLE_RE = re.compile(r"^/([^/]+)/status/\d+")

_TRACKING_PARAMS = {"s", "t", "si", "fbclid", "gclid", "ref"}


def content_hash(text: str) -> str:
    """Stable hash of a clip's content, for idempotent inbox processing."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_tracking_param(key: str) -> bool:
    key = key.lower()
    return key.startswith("utm_") or key in _TRACKING_PARAMS


def canonicalize_url(url: str) -> str:
    """One canonical form per real resource, so dedup and clearing don't
    care which share-link variant was sent.

    Tweet URLs collapse to the tweet-id form (id is the identity; two
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


@dataclass
class State:
    path: Path
    telegram_offset: int = 0
    processed_urls: set[str] = field(default_factory=set)
    processed_hashes: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists():
            return cls(path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            telegram_offset=int(raw.get("telegram_offset", 0)),
            # canonicalize on load so history from before this change keeps
            # deduping; canonicalizing an already-canonical URL is a no-op
            processed_urls={canonicalize_url(u) for u in raw.get("processed_urls", [])},
            processed_hashes=set(raw.get("processed_hashes", [])),
        )

    def save(self) -> None:
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
        return canonicalize_url(url) in self.processed_urls

    def mark_url(self, url: str) -> None:
        self.processed_urls.add(canonicalize_url(url))

    def seen_hash(self, digest: str) -> bool:
        return digest in self.processed_hashes

    def mark_hash(self, digest: str) -> None:
        self.processed_hashes.add(digest)
