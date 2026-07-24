"""URL canonicalization + dedup tests — pure logic, no network.

Run: uv run python tests/test_state.py
"""

import json
import tempfile
from pathlib import Path

from bower_bird.state import State, canonicalize_url

CANON_CASES = [
    # tracking params stripped
    (
        "https://example.com/post?utm_source=x&utm_campaign=y",
        "https://example.com/post",
    ),
    ("https://example.com/post?fbclid=abc", "https://example.com/post"),
    ("https://example.com/post?gclid=abc", "https://example.com/post"),
    ("https://example.com/post?ref=abc", "https://example.com/post"),
    # non-tracking params survive (arXiv-style)
    (
        "https://arxiv.org/abs/2101.00001?context=cs.LG",
        "https://arxiv.org/abs/2101.00001?context=cs.LG",
    ),
    # mixed: tracking stripped, real param kept
    (
        "https://example.com/post?id=42&utm_source=x",
        "https://example.com/post?id=42",
    ),
    # www stripped, host lowercased (path case untouched)
    ("https://WWW.Example.com/Post", "https://example.com/Post"),
    # fragment stripped
    ("https://example.com/post#section", "https://example.com/post"),
    # trailing slash stripped on non-root paths
    ("https://example.com/post/", "https://example.com/post"),
    # bare root keeps its slash
    ("https://example.com/", "https://example.com/"),
    # tweet variants collapse to the same canonical id form
    ("https://x.com/user/status/123?s=52", "https://x.com/user/status/123"),
    ("https://twitter.com/user/status/123", "https://x.com/user/status/123"),
    ("https://mobile.twitter.com/user/status/123", "https://x.com/user/status/123"),
    ("https://x.com/USER/status/123", "https://x.com/user/status/123"),
]


def main() -> int:
    failures = 0

    for url, want in CANON_CASES:
        got = canonicalize_url(url)
        if got != want:
            failures += 1
            print(f"FAIL canonicalize_url({url!r}): want {want!r} got {got!r}")

    # host is lowercased even without a www prefix
    if canonicalize_url("https://EXAMPLE.com/post") != "https://example.com/post":
        failures += 1
        print("FAIL canonicalize_url: host not lowercased")

    # seen_url/mark_url dedup across variants
    with tempfile.TemporaryDirectory() as tmp:
        state = State(path=Path(tmp) / "state.json")
        state.mark_url("https://example.com/post?utm_source=x")
        if not state.seen_url("https://example.com/post/"):
            failures += 1
            print("FAIL seen_url: tracking-junk variant not deduped vs trailing slash")
        if not state.seen_url("https://EXAMPLE.com/post#frag"):
            failures += 1
            print("FAIL seen_url: case/fragment variant not deduped")

        state.mark_url("https://x.com/user/status/1?s=52")
        if not state.seen_url("https://mobile.twitter.com/User/status/1"):
            failures += 1
            print("FAIL seen_url: tweet variant not deduped")

    # State.load migrates existing processed_urls to canonical form,
    # collapsing pre-existing variants (idempotent)
    with tempfile.TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "state.json"
        state_path.write_text(
            json.dumps(
                {
                    "telegram_offset": 0,
                    "processed_urls": [
                        "https://example.com/post?utm_source=x",
                        "https://example.com/post/",
                        "https://twitter.com/user/status/1",
                    ],
                    "processed_hashes": [],
                }
            ),
            encoding="utf-8",
        )
        state = State.load(state_path)
        if len(state.processed_urls) != 2:
            failures += 1
            print(
                f"FAIL State.load migration: want 2 urls, got {state.processed_urls!r}"
            )
        if not state.seen_url("https://example.com/post"):
            failures += 1
            print("FAIL State.load migration: variant not deduped after load")
        if not state.seen_url("https://x.com/user/status/1"):
            failures += 1
            print("FAIL State.load migration: tweet variant not deduped after load")

        # re-loading an already-canonical file is a no-op (idempotent)
        state.save()
        reloaded = State.load(state_path)
        if reloaded.processed_urls != state.processed_urls:
            failures += 1
            print("FAIL State.load: re-load of canonical state changed urls")

    if failures:
        print(f"\n{failures} failure(s).")
        return 1
    print(f"OK: {len(CANON_CASES) + 6} state cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
