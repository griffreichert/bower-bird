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
from urllib.parse import urlparse

from bower_bird import inbox, ingest, telegram
from bower_bird.config import Config
from bower_bird.fetch import (
    PageMeta,
    fetch,
    fetch_pdf,
    fetch_rendered,
    is_pdf_url,
    needs_clipping,
)
from bower_bird.llm import describe_link, synthesize_clipping
from bower_bird.resolve import TweetText, parse_tweet_id, resolve_tweet
from bower_bird.router import Lane, parse
from bower_bird.state import State


def truncate_title(title: str, limit: int = 60) -> str:
    """Squash whitespace and cap a page title for a phone-width receipt."""
    title = " ".join(title.split())
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def finish_shelve(
    config: Config,
    state: State,
    meta: PageMeta,
    note: str,
    *,
    model: str,
    full_body: str,
) -> str:
    """Synthesize + write a source node, then catalog/log/receipt. Shared tail
    for every shelve-lane path (plain fetch, PDF, tweet, pasted prose)."""
    candidates = ingest.read_index(config)
    plan = synthesize_clipping(meta, note, candidates, model, config.llm)
    path = ingest.create_source_note(config, meta, plan, note=note, full_body=full_body)
    if meta.url:
        state.mark_url(meta.url)
    if path is None:
        return f"🔁 Already in brain — {truncate_title(plan.concise_title)}"

    ingest.file_entities(config, plan, path.stem)
    ingest.upsert_index_line(config, path.stem, plan.category, plan.description)
    for topic in plan.topics:
        ingest.upsert_index_line(config, topic, plan.category, "", insert_only=True)
    ingest.append_log(
        config, f"build sources/{path.name} [{plan.category or 'uncategorized'}]"
    )

    links = ", ".join(f"[[{n}]]" for n in plan.topics) or "none yet"
    return f"🧠 brain — {truncate_title(plan.concise_title)}\nLinked: {links}"


def shelve_link(config: Config, state: State, url: str, note: str) -> str:
    """Fetch/render a link and shelve it (or PDF-extract it) as a source node.

    Shared tail for any link the SHELVE lane resolves to an external page —
    the plain-link path in handle_update, and the tweet lane's link-wrapper
    case once the t.co shortlink is expanded to the real article."""
    if is_pdf_url(url):
        meta = fetch_pdf(url, timeout=config.fetch_timeout)
        if not meta.body_excerpt:
            # Unfetchable or scanned (no extractable text) — human's problem.
            ingest.append_to_clip_queue(config, url)
            state.mark_url(url)
            return "✂️ Couldn't read that PDF — queued to clip"
        # PDF lane runs synthesize_clipping on Sonnet — Haiku thins out on
        # dense multi-page papers.
        return finish_shelve(
            config,
            state,
            meta,
            note,
            model=config.llm.paper_model,
            full_body=meta.body_excerpt,
        )

    meta, body = fetch_rendered(url, timeout=config.fetch_timeout)
    if meta.is_thin:
        # Fetch came back empty (likely walled). Send it to the clip queue.
        ingest.append_to_clip_queue(config, url, meta.title)
        state.mark_url(url)
        return "✂️ Couldn't read that one — queued to clip"

    return finish_shelve(
        config, state, meta, note, model=config.llm.model, full_body=body
    )


_TRIVIAL_PROSE_CHARS = 30  # below this, tweet text is "basically just a link"
_TWEET_INTERNAL_HOSTS = {
    "x.com",
    "twitter.com",
    "mobile.twitter.com",
    "pbs.twimg.com",
    "pic.twitter.com",
    "t.co",  # unresolved shortlink — expand_urls already tried and failed
}
_URL_RE = re.compile(r"https?://\S+")


def tweet_prose_and_external_urls(text: str) -> tuple[str, list[str]]:
    """Split (expanded) tweet text into bare prose (URLs stripped, whitespace
    collapsed) and the external URLs it contains — anything not hosted on
    x.com/twitter's own domains (media, quote links, a t.co we couldn't
    expand). Used to tell a link-wrapper or media-only tweet apart from one
    with real prose worth its own node."""
    urls = _URL_RE.findall(text)
    prose = " ".join(_URL_RE.sub("", text).split())
    external = [
        u
        for u in urls
        if (urlparse(u).hostname or "").removeprefix("www.")
        not in _TWEET_INTERNAL_HOSTS
    ]
    return prose, external


def shelve_tweet(
    config: Config, state: State, tweet: TweetText, note: str = "", origin_url: str = ""
) -> str:
    """Shelve a resolved tweet straight into brain/ as its own source node.

    `origin_url` is the URL as sent (may differ from the tweet's canonical
    URL) — marked too so a resend of either form dedups.
    """
    meta = PageMeta(
        url=tweet.url,
        title=f"@{tweet.author_handle}: {' '.join(tweet.text.split())}"[:80],
        description="",
        body_excerpt=tweet.text,
        author=tweet.author_name,
    )
    receipt = finish_shelve(
        config,
        state,
        meta,
        note,
        model=config.llm.model,
        full_body=ingest.format_tweet_body(tweet),
    )
    if origin_url and origin_url != tweet.url:
        state.mark_url(origin_url)
    return receipt


def shelve_tweet_url(config: Config, state: State, url: str, note: str = "") -> str:
    """Resolve a tweet URL, classify it, and shelve accordingly.

    A tweet whose text is basically nothing but a t.co link is useless as its
    own source node — it's either a link-wrapper (pointer at an outside
    article: shelve the article instead) or media-only (content's in an
    image: queue to clip, a human has to look). Only a tweet with real prose
    becomes its own node. Resolution failure falls back to the clip queue as
    before.
    """
    tweet = resolve_tweet(url, timeout=config.fetch_timeout)
    if tweet is None:
        ingest.append_to_clip_queue(config, url)
        state.mark_url(url)
        return "✂️ Couldn't resolve that tweet — queued to clip"

    def mark_tweet_urls() -> None:
        state.mark_url(url)
        if url != tweet.url:
            state.mark_url(tweet.url)

    prose, external_urls = tweet_prose_and_external_urls(tweet.text)
    # A quote-tweet's substance can live entirely in quoted_text — never
    # classify one as a wrapper/media-only send.
    trivial_prose = len(prose) < _TRIVIAL_PROSE_CHARS and not tweet.quoted_text

    if trivial_prose and len(external_urls) == 1:
        external = external_urls[0]
        if prose:
            tail = f"{prose} — via @{tweet.author_handle}"
            merged_note = f"{note} · {tail}" if note else tail
        else:
            merged_note = note
        receipt = shelve_link(config, state, external, merged_note)
        mark_tweet_urls()
        state.mark_url(external)
        return f"🔗 via @{tweet.author_handle} — {receipt}"

    if trivial_prose and not external_urls:
        # Content lives in an image, not text — a human has to look at it.
        ingest.append_to_clip_queue(config, tweet.url)
        mark_tweet_urls()
        return "✂️ Media-only tweet — queued to clip"

    return shelve_tweet(config, state, tweet, note, origin_url=url)


def shelve_prose(config: Config, state: State, text: str, sender: str) -> str:
    """Pasted prose, no link: its own source node, sender as author, the text
    itself as the immutable body."""
    stripped = text.strip()
    meta = PageMeta(
        url="",
        title=stripped[:80] or "Untitled note",
        description="",
        body_excerpt=stripped,
        author=sender,
    )
    return finish_shelve(
        config, state, meta, "", model=config.llm.model, full_body=stripped
    )


def handle_update(config: Config, state: State, text: str, sender: str = "") -> str:
    parsed = parse(text)

    if parsed.lane is Lane.NO_LINK:
        # Nothing droppable — park it in the catch-all inbox, never discard.
        ingest.append_to_telegram_inbox(config, text, reason="no link")
        return "🗃️ No link — parked in _inbox"

    if parsed.lane is Lane.PASTE:
        return shelve_prose(config, state, parsed.note, sender)

    url = parsed.url
    assert url is not None  # NO_LINK/PASTE are the only url-less lanes

    if state.seen_url(url):
        return "🔁 Already captured"

    if parsed.lane is Lane.TOOL:
        # A keep-for-later shelf item. Repos/tool pages fetch fine over httpx.
        meta = fetch(url, timeout=config.fetch_timeout)
        oneline = describe_link(meta, config.llm)
        added = ingest.append_to_tools(
            config, url, meta.title, oneline, note=parsed.note
        )
        state.mark_url(url)
        if not added:
            return f"🔁 Already in tools — {truncate_title(meta.title)}"
        return f"🔧 tools — {truncate_title(meta.title)}"

    # Lane.SHELVE — every link becomes a source node immediately (antilibrary
    # model, 2026-07-13): no read-status fork, just "can we get the body".
    if needs_clipping(url):
        return shelve_tweet_url(config, state, url, parsed.note)

    return shelve_link(config, state, url, parsed.note)


def run_telegram(config: Config | None = None) -> int:
    """Pull the Telegram queue once. Returns items processed."""
    config = config or Config()
    state = State.load(config.state_path)
    return pull_telegram(config, state)


def run_gather(config: Config | None = None) -> int:
    """Gather Web Clipper drops in inbox/ → brain/. Returns items processed."""
    config = config or Config()
    state = State.load(config.state_path)
    clip_log = inbox.process_inbox(config, state)
    for line in clip_log:
        print(f"  clip: {line}")
    return len(clip_log)


def run_all(config: Config | None = None) -> int:
    """One full pass: Telegram queue + clipper inbox gather."""
    config = config or Config()
    return run_telegram(config) + run_gather(config)


def run_drain(config: Config | None = None) -> int:
    """`bb drain` — resolve the unchecked X links stuck in to-clip.md.

    One-shot backlog eater: each unchecked tweet URL is resolved through the
    proxy chain straight into its own source node and its box checked.
    Non-tweet links and resolution failures are left untouched (still yours
    to clip). Edits to-clip.md in place — a bot-owned file whose whole
    contract is checkboxes.
    """
    config = config or Config()
    path = config.to_clip_path
    if not path.exists():
        print("bb drain: no to-clip.md — nothing to do.")
        return 0

    state = State.load(config.state_path)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    unchecked = re.compile(r"^(\s*)- \[ \] (?:\[[^\]]*\]\()?(https?://[^\s)]+)\)?\s*$")
    drained = 0
    for i, line in enumerate(lines):
        m = unchecked.match(line)
        if not m or parse_tweet_id(m.group(2)) is None:
            continue
        url = m.group(2)
        receipt = shelve_tweet_url(config, state, url)
        if receipt.startswith("✂️"):
            print(f"  drain: could not resolve {url}")
            continue
        lines[i] = line.replace("- [ ]", "- [x]", 1)
        drained += 1
        print(f"  drain: {receipt.splitlines()[0]}")
        time.sleep(0.5)  # be a polite proxy citizen on long queues

    if drained:
        path.write_text("".join(lines), encoding="utf-8")
        print(f"bb drain: resolved {drained} tweet(s) into brain/sources/.")
    else:
        print("bb drain: nothing drained.")
    return drained


def pull_telegram(config: Config, state: State) -> int:
    token = config.telegram_bot_token.get_secret_value()
    updates = telegram.get_updates(
        token,
        offset=state.telegram_offset,
        limit=config.queue_limit,
        timeout=config.fetch_timeout,
    )

    allowed = config.allowed_chat_ids
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
            receipt = handle_update(config, state, update.text, update.author)
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
            receipt = "⚠️ Couldn't process that one."

        try:
            telegram.send_message(token, update.chat_id, receipt)
        except Exception:  # noqa: BLE001 — a failed receipt must not stall the queue
            pass

        # Acknowledge the update regardless, so a poison message can't loop.
        state.telegram_offset = update.update_id + 1
        state.save()
        processed += 1

    return processed
