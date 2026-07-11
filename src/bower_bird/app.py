"""Run orchestration: one pass over both capture lanes.

Capture is instant; processing waits until the laptop is awake. Each
invocation (a) pulls the Telegram queue, and (b) processes any Web Clipper
drops in the inbox, routing every item and writing into the owned `BowerBird/`
folder. Run it from a launchd job (e.g. daily) or by hand. Designed to finish
within the current session — no long-poll loop.
"""

import re
import sys
import time

from bower_bird import inbox, ingest, telegram
from bower_bird.config import Config, load_config
from bower_bird.fetch import PageMeta, fetch, fetch_rendered, needs_clipping
from bower_bird.llm import describe_link, synthesize_clipping
from bower_bird.resolve import parse_tweet_id, resolve_tweet
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
        # Known JS-walled domains can't be read over httpx. Tweets resolve via
        # the proxy chain into a per-tweet doc in tweets/; anything the chain
        # can't get falls back to the clip queue (now the residue lane).
        if needs_clipping(url):
            tweet = resolve_tweet(url, timeout=config.fetch_timeout)
            if tweet is not None:
                path = ingest.write_tweet_doc(config, tweet)
                state.mark_url(url)
                if tweet.url != url:
                    state.mark_url(tweet.url)
                if path is None:
                    return f"Already in tweets/: {tweet.url}"
                return f"Tweet from @{tweet.author_handle} → tweets/{path.name}"
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

    # Lane.LEARNED — the user has read it and added a note (`read:` / link+note).
    # A tweet read on X itself skips the digest queue: resolve its text and file
    # it straight into the graph — the human's read already happened out there.
    if needs_clipping(url):
        tweet = resolve_tweet(url, timeout=config.fetch_timeout)
        if tweet is None:
            ingest.append_to_clip_queue(config, url)
            state.mark_url(url)
            return f"Couldn't resolve that tweet — queued to clip:\n{url}"
        body = tweet.text
        if tweet.quoted_text:
            body += f"\n\nQuoting @{tweet.quoted_handle}:\n{tweet.quoted_text}"
        meta = PageMeta(
            url=tweet.url,
            title=f"@{tweet.author_handle}: {' '.join(tweet.text.split())}"[:80],
            description="",
            body_excerpt=body,
            author=tweet.author_name,
        )
    else:
        meta = fetch(url, timeout=config.fetch_timeout)
    candidates = ingest.read_index(config)
    plan = synthesize_clipping(meta, parsed.note, candidates, model=config.build_model)
    path = ingest.create_source_note(config, meta, plan, note=parsed.note)
    state.mark_url(url)
    if path is None:
        return f"Already filed: {meta.title}"

    # File named tool/person entities as their own leaf nodes (same as the clip lane).
    ingest.file_entities(config, plan, path.stem)

    # Catalog the source + ensure concept stubs in the index; log the build.
    ingest.upsert_index_line(config, path.stem, plan.category, plan.description)
    for topic in plan.topics:
        ingest.upsert_index_line(config, topic, plan.category, "", insert_only=True)
    ingest.append_log(
        config, f"build sources/{path.name} [{plan.category or 'uncategorized'}]"
    )

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
    """One full pass: Telegram queue + trinkets gather + inbox decay."""
    config = config or load_config()
    count = run_telegram(config) + run_gather(config)
    from bower_bird.prune import let_go

    for line in let_go(config):
        print(f"  let-go: {line}")
    return count


def run_drain(config: Config | None = None) -> int:
    """`bb drain` — resolve the unchecked X links stuck in to-clip.md.

    One-shot backlog eater: each unchecked tweet URL is resolved through the
    proxy chain into a per-tweet doc in tweets/ and its box checked. Non-tweet links
    and resolution failures are left untouched (still yours to clip). Edits
    to-clip.md in place — a bot-owned file whose whole contract is checkboxes.
    """
    config = config or load_config()
    path = config.to_clip_path
    if not path.exists():
        print("bb drain: no to-clip.md — nothing to do.")
        return 0

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    unchecked = re.compile(r"^(\s*)- \[ \] (?:\[[^\]]*\]\()?(https?://[^\s)]+)\)?\s*$")
    drained = 0
    for i, line in enumerate(lines):
        m = unchecked.match(line)
        if not m or parse_tweet_id(m.group(2)) is None:
            continue
        url = m.group(2)
        tweet = resolve_tweet(url, timeout=config.fetch_timeout)
        if tweet is None:
            print(f"  drain: could not resolve {url}")
            continue
        ingest.write_tweet_doc(config, tweet)
        lines[i] = line.replace("- [ ]", "- [x]", 1)
        drained += 1
        print(f"  drain: @{tweet.author_handle} → tweets/")
        time.sleep(0.5)  # be a polite proxy citizen on long queues

    if drained:
        path.write_text("".join(lines), encoding="utf-8")
        print(f"bb drain: resolved {drained} tweet(s) into tweets/ docs.")
    else:
        print("bb drain: nothing drained.")
    return drained


def _pull_telegram(config: Config, state: State) -> int:
    updates = telegram.get_updates(
        config.telegram_bot_token,
        offset=state.telegram_offset,
        limit=config.queue_limit,
        timeout=config.fetch_timeout,
    )

    allowed = config.allowed_chat_id_set
    if not allowed:
        print(
            "warning: BOWER_ALLOWED_CHAT_IDS is unset — the bot accepts messages "
            "from any Telegram sender. Set it to your chat_id to lock the bot.",
            file=sys.stderr,
        )

    processed = 0
    for update in sorted(updates, key=lambda u: u.update_id):
        if allowed and update.chat_id not in allowed:
            # Unauthorized sender: drop silently (don't confirm the bot exists),
            # but still advance the offset so the message can't replay forever.
            state.telegram_offset = update.update_id + 1
            state.save()
            continue

        try:
            receipt = _handle(config, state, update.text)
        except OSError as exc:
            # Transient filesystem error — iCloud raises EDEADLK (errno 11 on
            # macOS) reading a dataless file it hasn't materialized yet. Don't
            # ack: stop here so this and later updates retry next tick.
            # ponytail: a *permanent* OSError stalls the queue; the cron log
            # shows it, unblock by hand.
            print(
                f"retrying update {update.update_id} next tick: {exc}",
                file=sys.stderr,
            )
            return processed
        except Exception as exc:  # noqa: BLE001 — report, don't crash the run
            # Generic receipt: never reflect internal detail (paths, tokens) to
            # the sender. Full exception goes to the local cron log only.
            print(f"error handling update {update.update_id}: {exc}", file=sys.stderr)
            receipt = "Couldn't process that one."

        try:
            telegram.send_message(config.telegram_bot_token, update.chat_id, receipt)
        except Exception:  # noqa: BLE001 — a failed receipt must not stall the queue
            pass

        # Acknowledge the update regardless, so a poison message can't loop.
        state.telegram_offset = update.update_id + 1
        state.save()
        processed += 1

    return processed
