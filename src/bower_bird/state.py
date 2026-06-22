"""Durable state: Telegram offset + processed-item dedup.

Idempotency is a hard invariant: an item already processed is never
double-processed. Two layers:

- `telegram_offset` — passed to getUpdates so Telegram never redelivers an
  update we've already acknowledged.
- `processed_urls` — guards against the same link being re-sent by hand and
  re-ingested.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class State:
    path: Path
    telegram_offset: int = 0
    processed_urls: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> State:
        if not path.exists():
            return cls(path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            telegram_offset=int(raw.get("telegram_offset", 0)),
            processed_urls=set(raw.get("processed_urls", [])),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "telegram_offset": self.telegram_offset,
            "processed_urls": sorted(self.processed_urls),
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def seen_url(self, url: str) -> bool:
        return url in self.processed_urls

    def mark_url(self, url: str) -> None:
        self.processed_urls.add(url)
