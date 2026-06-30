"""Run orchestration: one pass over both capture lanes.

Capture is instant; processing waits until the laptop is awake. Each
invocation (a) pulls the Telegram queue, and (b) processes any Web Clipper
drops in the inbox, routing every item and writing into the owned `BowerBird/`
folder. Run it from a launchd job (e.g. daily) or by hand. Designed to finish
within the current session — no long-poll loop.
"""

from __future__ import annotations

from bower_bird import inbox, ingest, telegram
from bower_bird.config import Config, load_config
from bower_bird.fetch import fetch, fetch_rendered, needs_clipping
from bower_bird.llm import describe_link, synthesize_clipping
from bower_bird.router import Lane, parse
from bower_bird.state import State


def _handle(config: Config, state: State, text: str) -> str:
    parsed = parse(text)

    if parsed.lane is Lane.NO_LINK:
        # Nothing droppable — park it in the catch-all inbox, never discard.
        ingest.append_to_telegram_inbox(config, text, reason="no link")
        return "No link found — parked in _inbox.md."

    url = parsed.url
    assert url is not None  # NO_LINK is the only url-less lane

    if state.seen_url(url):
        return f"Already captured earlier: {url}"

    if parsed.lane is Lane.TOOL:
        # A keep-for-later shelf item. Repos/tool pages fetch fine over httpx.
        meta = fetch(url, timeout=config.fetch_timeout)
        oneline = describe_link(meta, model=config.model)
        added = ingest.append_to_tools(
            config, url, meta.title, oneline, note=parsed.note
        )
        state.mark_url(url)
        if not added:
            return f"Already on the tools shelf: {meta.title}"
        return f"Saved to tools: {meta.title}\n— {parsed.note or oneline}"

    if parsed.lane is Lane.TO_READ:
        # Known JS-walled domains can't be read over httpx — queue them for the
        # browser Web Clipper without wasting a fetch.
        if needs_clipping(url):
            ingest.append_to_clip_queue(config, url)
            state.mark_url(url)
            return f"Can't read that one solo — queued to clip:\n{url}"

        meta, body = fetch_rendered(url, timeout=config.fetch_timeout)
        if meta.is_thin:
            # Fetch came back empty (likely walled). Send it to the clip queue.
            ingest.append_to_clip_queue(config, url, meta.title)
            state.mark_url(url)
            return f"Couldn't read that one — queued to clip:\n{url}"

        # Bare link → rendered readable doc in inbox/. The human reads + marks
        # it there; moving it to trinkets/ is the read signal for further processing.
        path = ingest.write_inbox_doc(config, meta, body)
        state.mark_url(url)
        if path is None:
            return f"Already in inbox: {meta.title}"
        return f"Added to inbox: {meta.title}"

    # Lane.LEARNED — the user has read it and added a note.
    meta = fetch(url, timeout=config.fetch_timeout)
    candidates = ingest.list_concept_notes(config)
    plan = synthesize_clipping(meta, parsed.note, candidates, model=config.model)
    path = ingest.create_source_note(config, meta, plan, note=parsed.note)
    state.mark_url(url)
    if path is None:
        return f"Already filed: {meta.title}"

    # File named tool/person entities as their own leaf nodes (same as the clip lane).
    ingest.file_entities(config, plan, path.stem)

    links = ", ".join(f"[[{n}]]" for n in plan.topics) or "none yet"
    return f"Filed: {meta.title}\nLinked: {links}"


def run_telegram(config: Config | None = None) -> int:
    """Pull the Telegram queue once (links → inbox/). Returns items processed."""
    config = config or load_config()
    state = State.load(config.state_path)
    return _pull_telegram(config, state)


def run_gather(config: Config | None = None) -> int:
    """Gather read+annotated trinkets/ → brain/bowers/. Returns items processed."""
    config = config or load_config()
    state = State.load(config.state_path)
    clip_log = inbox.process_inbox(config, state)
    for line in clip_log:
        print(f"  clip: {line}")
    return len(clip_log)


def run_all(config: Config | None = None) -> int:
    """One full pass: Telegram queue + trinkets gather. Total items processed."""
    config = config or load_config()
    return run_telegram(config) + run_gather(config)


def _pull_telegram(config: Config, state: State) -> int:
    updates = telegram.get_updates(
        config.telegram_bot_token,
        offset=state.telegram_offset,
        limit=config.queue_limit,
        timeout=config.fetch_timeout,
    )

    processed = 0
    for update in sorted(updates, key=lambda u: u.update_id):
        try:
            receipt = _handle(config, state, update.text)
        except Exception as exc:  # noqa: BLE001 — report, don't crash the run
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
