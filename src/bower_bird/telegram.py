"""Telegram Bot API: drain the queue with getUpdates, send receipts.

No webhook, no public server. Telegram's own servers hold the queue (~24h)
until we drain it from the laptop. getUpdates with a running offset is the
whole mechanism.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict

API_BASE = "https://api.telegram.org/bot{token}/{method}"


class Update(BaseModel):
    model_config = ConfigDict(frozen=True)

    update_id: int
    chat_id: int
    text: str


def _url(token: str, method: str) -> str:
    return API_BASE.format(token=token, method=method)


def get_updates(token: str, offset: int, limit: int, timeout: float) -> list[Update]:
    """Fetch pending updates from `offset` onward.

    Acknowledging happens implicitly: the next call passes
    `offset = max(update_id) + 1`, which tells Telegram to drop everything
    below it.
    """
    resp = httpx.get(
        _url(token, "getUpdates"),
        params={"offset": offset, "limit": limit, "timeout": 0},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"getUpdates failed: {body}")

    updates: list[Update] = []
    for item in body.get("result", []):
        msg = item.get("message") or item.get("channel_post")
        if not msg:
            continue
        text = msg.get("text") or msg.get("caption")
        if not text:
            continue
        updates.append(
            Update(
                update_id=item["update_id"],
                chat_id=msg["chat"]["id"],
                text=text,
            )
        )
    return updates


def send_message(token: str, chat_id: int, text: str, timeout: float = 15.0) -> None:
    """Send a receipt back through the bot."""
    resp = httpx.post(
        _url(token, "sendMessage"),
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
