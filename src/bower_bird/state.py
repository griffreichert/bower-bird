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
from dataclasses import dataclass, field
from pathlib import Path


def content_hash(text: str) -> str:
    """Stable hash of a clip's content, for idempotent inbox processing."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
            processed_urls=set(raw.get("processed_urls", [])),
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
        return url in self.processed_urls

    def mark_url(self, url: str) -> None:
        self.processed_urls.add(url)

    def seen_hash(self, digest: str) -> bool:
        return digest in self.processed_hashes

    def mark_hash(self, digest: str) -> None:
        self.processed_hashes.add(digest)
