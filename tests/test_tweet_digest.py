"""Tweet-digest lane tests — pure logic on a temp vault, no network.

Covers the batched tweet reading flow: append_tweet_to_digest (create /
append / dup / roll past the cap), the court's section splitter, and the
marked-sections-only rule (unmarked tweets are let go, never distilled) with
the LLM call stubbed out.

Run: uv run python tests/test_tweet_digest.py
"""

import tempfile
from pathlib import Path

from bower_bird import inbox, ingest
from bower_bird.config import Config
from bower_bird.llm import ClippingPlan
from bower_bird.resolve import TweetText
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


def test_append_creates_appends_dedups() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)

        path = ingest.append_tweet_to_digest(cfg, _tweet(1))
        check(path is not None, "first append writes a digest doc")
        text = path.read_text(encoding="utf-8")
        check("digest: tweets" in text, "digest doc carries the digest marker")
        check("## @someone — https://x.com/someone/status/1" in text, "section heading")
        check("tweet number 1" in text, "tweet text present")

        log = (root / "brain" / "_log.md").read_text(encoding="utf-8")
        check(
            "pull tweet @someone → tweets/" in log and "(unread)" in log,
            "arrival logged as an unread pull line",
        )

        again = ingest.append_tweet_to_digest(cfg, _tweet(1))
        check(again is None, "same url twice → dup, not re-appended")
        log = (root / "brain" / "_log.md").read_text(encoding="utf-8")
        check(log.count("pull tweet") == 1, "dup append logs no second pull line")

        quoted = _tweet(2, quoted_handle="other", quoted_text="quoted line")
        path2 = ingest.append_tweet_to_digest(cfg, quoted)
        check(path2 == path, "second tweet appends to the same day's doc")
        text = path.read_text(encoding="utf-8")
        check("> **quoting @other:**" in text, "quote block rendered")
        check("> quoted line" in text, "quoted text blockquoted")

        reply = _tweet(3, in_reply_to="parent")
        ingest.append_tweet_to_digest(cfg, reply)
        check(
            "reply to @parent" in path.read_text(encoding="utf-8"),
            "thread hint rendered for replies",
        )


def test_append_rolls_past_cap() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        for i in range(ingest._TWEET_DIGEST_CAP + 1):
            ingest.append_tweet_to_digest(cfg, _tweet(i))
        docs = sorted(p.name for p in (root / "tweets").glob("tweets-*.md"))
        check(len(docs) == 2, f"cap rolls a second doc (got {docs})")


def test_split_tweet_sections() -> None:
    body = (
        "intro prose the splitter must ignore\n\n"
        "## @alice — https://x.com/alice/status/11\n\nfirst tweet text\n\n"
        "## @bob — https://x.com/bob/status/22\n\nsecond ==marked== text\n"
    )
    sections = inbox._split_tweet_sections(body)
    check(len(sections) == 2, "two sections split")
    check(sections[0][0] == "alice" and sections[0][1].endswith("/11"), "handle + url")
    check("first tweet text" in sections[0][2], "section body captured")
    check("## @bob" not in sections[0][2], "sections don't bleed into each other")


def test_court_keeps_marked_drops_unmarked() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        state = State.load(cfg.state_path)

        ingest.append_tweet_to_digest(cfg, _tweet(1, text="plain, will be let go"))
        ingest.append_tweet_to_digest(
            cfg, _tweet(2, text="this one is ==worth keeping== for sure")
        )
        digest_doc = next((root / "tweets").glob("tweets-*.md"))
        # Human reads + moves it to trinkets/ — the read signal.
        trinket = root / "trinkets" / digest_doc.name
        trinket.parent.mkdir(parents=True)
        digest_doc.rename(trinket)

        calls: list[str] = []

        def fake_synthesize(meta, note, candidates, model, **kw):
            calls.append(meta.url)
            return ClippingPlan(
                concise_title="Kept tweet",
                description="stub",
                category="test",
                topics=[],
                key_ideas=[],
            )

        real = inbox.synthesize_clipping
        inbox.synthesize_clipping = fake_synthesize
        try:
            log = inbox.process_inbox(cfg, state)
        finally:
            inbox.synthesize_clipping = real

        check(len(calls) == 1, f"only the marked section synthesised (got {calls})")
        check(calls == ["https://x.com/someone/status/2"], "marked section's url")
        check((root / "brain" / "sources" / "Kept tweet.md").exists(), "source note")
        check(any("kept 1, let go 1" in line for line in log), f"summary line: {log}")
        check(not trinket.exists(), "digest doc archived after court")
        check(state.seen_url("https://x.com/someone/status/2"), "kept url marked")


if __name__ == "__main__":
    test_append_creates_appends_dedups()
    test_append_rolls_past_cap()
    test_split_tweet_sections()
    test_court_keeps_marked_drops_unmarked()
    if _failures:
        print(f"{_failures} failure(s).")
        raise SystemExit(1)
    print("OK: tweet-digest cases passed.")
