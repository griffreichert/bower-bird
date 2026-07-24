"""Tweet lane tests — pure logic on a temp vault, no network.

Antilibrary model: a tweet send resolves via the proxy chain (stubbed here)
into ONE source node in brain/sources/, immediately — no tweets/ reading room,
no digest, no default-discard. A note alongside the link rides as a seed
thought; a resolution failure falls back to to-clip.

Run: uv run python tests/test_tweet_docs.py
"""

import tempfile
from pathlib import Path

from bower_bird import app
from bower_bird.config import Config
from bower_bird.schema import ClippingPlan, TweetText
from bower_bird.state import State

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if not cond:
        _failures += 1
        print(f"FAIL: {msg}")


def _config(root: Path) -> Config:
    return Config(
        telegram_bot_token="x",
        allowed_chat_ids="1",
        vault_path=root,
        state_path=root / "state.json",
        fetch_timeout=15,
        queue_limit=100,
    )


def _tweet(i: int, **kw) -> TweetText:
    defaults = dict(
        id=str(i),
        url=f"https://x.com/someone/status/{i}",
        author_handle="someone",
        author_name="Some One",
        text=f"tweet number {i} about agent harnesses",
    )
    defaults.update(kw)
    return TweetText(**defaults)


def _run(text: str, tweet: TweetText | None, plan_title: str = "Kept tweet"):
    """Send one Telegram text through handle_update with resolve + LLM stubbed.

    Returns (receipt, root, seeds) — seeds collects the note passed to
    synthesize_clipping so seed-thought threading is observable.
    """
    seeds: list[str] = []

    def fake_synthesize(meta, note, candidates, model, llm, **kw):
        seeds.append(note)
        return ClippingPlan(
            concise_title=plan_title,
            description="stub",
            category="test",
            topics=[],
            key_ideas=["one thin quizzable claim"],
        )

    orig = (app.resolve_tweet, app.synthesize_clipping)
    app.resolve_tweet = lambda url, timeout: tweet
    app.synthesize_clipping = fake_synthesize
    d = tempfile.TemporaryDirectory()
    root = Path(d.name) / "BowerBird"
    root.mkdir(parents=True)
    try:
        cfg = _config(root)
        state = State(path=root / "state.json")
        receipt = app.handle_update(cfg, state, text)
        return receipt, root, seeds, state, cfg, d
    finally:
        app.resolve_tweet, app.synthesize_clipping = orig


def test_bare_tweet_becomes_source_node() -> None:
    tweet = _tweet(1, quoted_handle="other", quoted_text="quoted line")
    receipt, root, seeds, state, cfg, d = _run("https://x.com/someone/status/1", tweet)
    with d:
        check(receipt.startswith("🧠"), f"tweet shelves straight to brain: {receipt!r}")
        node = root / "brain" / "sources" / "Kept tweet.md"
        check(node.exists(), "per-tweet source node written")
        text = node.read_text(encoding="utf-8")
        check("tweet number 1" in text, "tweet text inlined")
        check("## Body" in text, "tweet body lands under ## Body")
        check("> **quoting @other:**" in text, "quote block rendered in the body")
        check('author: "Some One"' in text, "tweet author in frontmatter")
        check("id: " in text, "node minted an id")
        check(not (root / "tweets").exists(), "no tweets/ reading room created")
        check(seeds == [""], "bare link carries no seed thought")
        check(
            state.seen_url("https://x.com/someone/status/1"),
            "origin url marked processed",
        )


def test_tweet_with_note_carries_seed_thought() -> None:
    receipt, root, seeds, state, cfg, d = _run(
        "sharp take on harnesses https://x.com/someone/status/2", _tweet(2)
    )
    with d:
        check(receipt.startswith("🧠"), f"tweet+note shelves to brain: {receipt!r}")
        check(seeds == ["sharp take on harnesses"], f"note fed as seed: {seeds}")
        text = (root / "brain" / "sources" / "Kept tweet.md").read_text(
            encoding="utf-8"
        )
        check(
            "## Seed thoughts\n- sharp take on harnesses" in text,
            "seed thought stored verbatim on the node",
        )


def test_unresolvable_tweet_falls_back_to_clip_queue() -> None:
    receipt, root, seeds, state, cfg, d = _run("https://x.com/someone/status/3", None)
    with d:
        check(
            receipt.startswith("✂️"), f"unresolvable tweet queues to clip: {receipt!r}"
        )
        check(seeds == [], "no LLM call for an unresolvable tweet")
        clip = (root / "to-clip.md").read_text(encoding="utf-8")
        check("https://x.com/someone/status/3" in clip, "url queued to clip")


def test_link_wrapper_tweet_shelves_the_linked_article() -> None:
    """A tweet that's basically just a link to an outside article routes to
    shelve_link with the external URL, not a near-empty tweet node."""
    tweet = _tweet(4, text="https://example.com/great-article")
    orig_shelve_link = app.shelve_link
    calls: list[tuple] = []

    def fake_shelve_link(config, state, url, note):
        calls.append((url, note))
        return "🧠 brain — Great Article"

    app.shelve_link = fake_shelve_link
    try:
        receipt, root, seeds, state, cfg, d = _run(
            "https://x.com/someone/status/4", tweet
        )
        with d:
            check(len(calls) == 1, f"shelve_link called once: {calls}")
            check(
                calls[0][0] == "https://example.com/great-article",
                f"external url passed through: {calls}",
            )
            check(
                receipt.startswith("🔗 via @someone —"),
                f"receipt prefixed: {receipt!r}",
            )
            check(
                state.seen_url("https://x.com/someone/status/4"),
                "origin tweet url marked",
            )
            check(state.seen_url(tweet.url), "canonical tweet url marked")
            check(seeds == [], "no LLM synthesis call for the tweet itself")
    finally:
        app.shelve_link = orig_shelve_link


def test_media_only_tweet_queues_to_clip() -> None:
    """A tweet with no real prose and no external link — content's in an
    image — goes to the clip queue for a human to look at."""
    tweet = _tweet(5, text="")
    receipt, root, seeds, state, cfg, d = _run("https://x.com/someone/status/5", tweet)
    with d:
        check(
            receipt == "✂️ Media-only tweet — queued to clip",
            f"media-only receipt: {receipt!r}",
        )
        check(seeds == [], "no LLM call for a media-only tweet")
        clip = (root / "to-clip.md").read_text(encoding="utf-8")
        check(tweet.url in clip, "canonical tweet url queued to clip")
        check(state.seen_url("https://x.com/someone/status/5"), "origin url marked")


def test_bare_quote_tweet_still_shelves_as_a_tweet() -> None:
    """A quote-tweet with no commentary of its own is NOT media-only — the
    substance rides in quoted_text and belongs on its own node."""
    tweet = _tweet(7, text="", quoted_handle="other", quoted_text="the actual take")
    receipt, root, seeds, state, cfg, d = _run("https://x.com/someone/status/7", tweet)
    with d:
        check(receipt.startswith("🧠"), f"bare quote-tweet shelves: {receipt!r}")
        text = (root / "brain" / "sources" / "Kept tweet.md").read_text(
            encoding="utf-8"
        )
        check("the actual take" in text, "quoted text inlined in the body")


def test_article_tweet_shelves_full_article_and_skips_clip_queue() -> None:
    """A native X Article's tweet text is often just a bare internal link
    (x.com/i/article/...) — trivial prose, no external url, which would
    normally land in the media-only clip bucket. The article path must
    short-circuit that classification and shelve the full article body."""
    tweet = _tweet(
        8,
        text="https://x.com/i/article/1234567890",
        article_title="Long-running agents don't need tools",
        article_body="# Long-running agents don't need tools\n\nBash is enough.",
    )
    receipt, root, seeds, state, cfg, d = _run(
        "https://x.com/someone/status/8", tweet, plan_title="Long article"
    )
    with d:
        check(
            receipt.startswith("📝"), f"article tweet gets its own receipt: {receipt!r}"
        )
        node = root / "brain" / "sources" / "Long article.md"
        check(node.exists(), "article source node written")
        text = node.read_text(encoding="utf-8")
        check("Bash is enough." in text, "article markdown inlined in body")
        check(
            seeds == [""],
            f"article body is the synthesis input, not tweet prose: {seeds}",
        )
        clip = root / "to-clip.md"
        check(
            not clip.exists() or tweet.url not in clip.read_text(encoding="utf-8"),
            "article tweet does not land in the clip queue",
        )
        check(state.seen_url("https://x.com/someone/status/8"), "origin url marked")
        check(state.seen_url(tweet.url), "canonical tweet url marked")


def test_normal_tweet_with_a_link_still_shelves_as_a_tweet() -> None:
    """Real prose alongside a link — not a bare wrapper — still becomes its
    own tweet source node; the link rides in the body for further-reading
    harvest."""
    tweet = _tweet(
        6,
        text=(
            "a long take on agent harnesses and why bash beats bespoke tools "
            "https://example.com/great-article"
        ),
    )
    receipt, root, seeds, state, cfg, d = _run("https://x.com/someone/status/6", tweet)
    with d:
        check(receipt.startswith("🧠"), f"normal tweet shelves to brain: {receipt!r}")
        check(seeds == [""], f"tweet text feeds the LLM synthesis as usual: {seeds}")
        node = root / "brain" / "sources" / "Kept tweet.md"
        check(node.exists(), "tweet source node written")
        text = node.read_text(encoding="utf-8")
        check(
            "https://example.com/great-article" in text,
            "expanded link rides in the body",
        )


if __name__ == "__main__":
    test_bare_tweet_becomes_source_node()
    test_tweet_with_note_carries_seed_thought()
    test_unresolvable_tweet_falls_back_to_clip_queue()
    test_link_wrapper_tweet_shelves_the_linked_article()
    test_media_only_tweet_queues_to_clip()
    test_bare_quote_tweet_still_shelves_as_a_tweet()
    test_article_tweet_shelves_full_article_and_skips_clip_queue()
    test_normal_tweet_with_a_link_still_shelves_as_a_tweet()
    if _failures:
        print(f"{_failures} failure(s).")
        raise SystemExit(1)
    print("OK: tweet lane cases passed.")
