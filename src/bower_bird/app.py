"""Drain orchestration: one pass over the Telegram queue.

Capture is instant; processing waits until the laptop is awake. Each
invocation drains whatever has piled up, routes every item, writes to the
vault, and sends a receipt. Run it from a cron/launchd job (e.g. daily) or by
hand. Designed to finish within the current session — no long-poll loop.
"""

from __future__ import annotations

from . import ingest, telegram
from .config import Config, load_config
from .fetch import fetch
from .llm import describe_link, synthesize_clipping
from .router import Lane, parse
from .state import State


def _handle(config: Config, state: State, text: str) -> str:
    parsed = parse(text)

    if parsed.lane is Lane.NO_LINK:
        return "No link found — nothing to capture."

    url = parsed.url
    assert url is not None  # NO_LINK is the only url-less lane

    if state.seen_url(url):
        return f"Already captured earlier: {url}"

    meta = fetch(url, timeout=config.fetch_timeout)

    if parsed.lane is Lane.TO_READ:
        oneline = describe_link(meta, model=config.model)
        added = ingest.append_to_reading_list(config, url, meta.title, oneline)
        state.mark_url(url)
        if not added:
            return f"Already in the reading list: {meta.title}"
        return f"Queued to read: {meta.title}\n— {oneline}"

    # Lane.LEARNED
    candidates = ingest.list_evergreen_notes(config)
    plan = synthesize_clipping(meta, parsed.note, candidates, model=config.model)
    path = ingest.create_clipping(config, meta, parsed.note, plan)
    state.mark_url(url)
    if path is None:
        return f"Already clipped: {meta.title}"

    links = ", ".join(f"[[{n}]]" for n in plan.proposed_backlinks) or "none yet"
    return (
        f"Clipped: {meta.title}\n"
        f"Proposed links: {links}\n"
        "Open the clipping to accept or ignore them."
    )


def drain(config: Config | None = None) -> int:
    """Drain the queue once. Returns the number of updates processed."""
    config = config or load_config()
    state = State.load(config.state_path)

    updates = telegram.get_updates(
        config.telegram_bot_token,
        offset=state.telegram_offset,
        limit=config.drain_limit,
        timeout=config.fetch_timeout,
    )

    processed = 0
    for update in sorted(updates, key=lambda u: u.update_id):
        try:
            receipt = _handle(config, state, update.text)
        except Exception as exc:  # noqa: BLE001 — report, don't crash the drain
            receipt = f"Couldn't process that one: {exc}"

        try:
            telegram.send_message(config.telegram_bot_token, update.chat_id, receipt)
        except Exception:  # noqa: BLE001 — a failed receipt must not stall the queue
            pass

        # Acknowledge the update regardless, so a poison message can't loop.
        state.telegram_offset = update.update_id + 1
        state.save()
        processed += 1

    return processed
