import httpx
from pydantic import BaseModel, ConfigDict, Field

API_BASE = "https://api.telegram.org/bot{token}/{method}"


class Update(BaseModel):
    """One Telegram message the bot received."""

    model_config = ConfigDict(frozen=True)

    update_id: int
    chat_id: int
    text: str
    author: str = Field(
        default="",
        description="Sender first name, else username; the paste lane's author.",
    )


def api_url(token: str, method: str) -> str:
    """Return the Bot API URL for ``method``."""
    return API_BASE.format(token=token, method=method)


def get_updates(token: str, offset: int, limit: int, timeout: float) -> list[Update]:
    """Fetch pending updates from `offset` onward.

    Acknowledging happens implicitly: the next call passes
    `offset = max(update_id) + 1`, which tells Telegram to drop everything
    below it.
    """
    resp = httpx.get(
        api_url(token, "getUpdates"),
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
        frm = msg.get("from") or {}
        author = frm.get("first_name") or frm.get("username") or ""
        updates.append(
            Update(
                update_id=item["update_id"],
                chat_id=msg["chat"]["id"],
                text=text,
                author=author,
            )
        )
    return updates


def send_message(token: str, chat_id: int, text: str, timeout: float = 15.0) -> None:
    """Send a receipt back through the bot."""
    resp = httpx.post(
        api_url(token, "sendMessage"),
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
