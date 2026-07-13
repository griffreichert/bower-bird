"""Tweet-doc lane tests — pure logic on a temp vault, no network.

Covers the per-tweet reading flow: write_tweet_doc (create / dup / same-title
disambiguation / unread pull-log line) and the gather treating a moved tweet
doc as a normal clip — including the bot-rendered exemption from the url
dup-guard (tweet urls are marked at capture, so without it every tweet doc
would false-dup at gather). Discard is decay's job now: an unmoved tweet doc
ages out via let_go; a moved one is a keep.

Run: uv run python tests/test_tweet_docs.py
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


def test_write_creates_and_dedups() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)

        path = ingest.write_tweet_doc(cfg, _tweet(1))
        check(path is not None, "first write creates a tweet doc")
        check(path.parent.name == "tweets", "doc lands in tweets/")
        text = path.read_text(encoding="utf-8")
        check('source: "https://x.com/someone/status/1"' in text, "source url")
        check("tweet number 1" in text, "tweet text present")
        check("- tweet" in text, "tweet tag present")
        check("bower: bot-rendered" in text, "bot-rendered marker present")

        log = (root / "brain" / "_log.md").read_text(encoding="utf-8")
        check(
            "pull tweet @someone → tweets/" in log and "(unread)" in log,
            "arrival logged as an unread pull line",
        )

        again = ingest.write_tweet_doc(cfg, _tweet(1))
        check(again is None, "same tweet twice → dup, not rewritten")
        log = (root / "brain" / "_log.md").read_text(encoding="utf-8")
        check(log.count("pull tweet") == 1, "dup write logs no second pull line")

        # Same author + same opening words, different tweet id → new file.
        clash = ingest.write_tweet_doc(
            cfg,
            _tweet(
                2, text=_tweet(1).text, id="999", url="https://x.com/someone/status/999"
            ),
        )
        check(clash is not None and clash != path, "title clash disambiguated by id")

        quoted = ingest.write_tweet_doc(
            cfg, _tweet(3, quoted_handle="other", quoted_text="quoted line")
        )
        qtext = quoted.read_text(encoding="utf-8")
        check("> **quoting @other:**" in qtext, "quote block rendered")

        reply = ingest.write_tweet_doc(cfg, _tweet(4, in_reply_to="parent"))
        check(
            "reply to @parent" in reply.read_text(encoding="utf-8"),
            "thread hint rendered for replies",
        )


def test_gather_files_moved_tweet_doc() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        state = State.load(cfg.state_path)

        doc = ingest.write_tweet_doc(
            cfg, _tweet(2, text="this one is ==worth keeping== for sure")
        )
        # Capture marks the url — the gather's dup-guard must NOT eat the doc.
        state.mark_url("https://x.com/someone/status/2")

        # Human moves it to trinkets/ — the read/keep signal.
        trinket = root / "trinkets" / doc.name
        trinket.parent.mkdir(parents=True)
        doc.rename(trinket)

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

        check(calls == ["https://x.com/someone/status/2"], f"synthesised (got {calls})")
        check((root / "brain" / "sources" / "Kept tweet.md").exists(), "source note")
        check(not trinket.exists(), "tweet doc archived after gather")
        check(any("sources/Kept tweet.md" in line for line in log), f"log line: {log}")


if __name__ == "__main__":
    test_write_creates_and_dedups()
    test_gather_files_moved_tweet_doc()
    if _failures:
        print(f"{_failures} failure(s).")
        raise SystemExit(1)
    print("OK: tweet-doc cases passed.")
