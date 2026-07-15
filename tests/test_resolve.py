"""Tweet resolver tests — pure logic, no network.

Run: uv run python tests/test_resolve.py
"""

from bower_bird import resolve
from bower_bird.resolve import (
    article_markdown,
    expand_urls,
    from_fxtwitter,
    from_syndication,
    parse_tweet_id,
    syndication_token,
)

PARSE_ID_CASES = [
    ("https://x.com/user/status/123?s=52", "123"),
    ("https://twitter.com/user/status/456", "456"),
    ("https://mobile.twitter.com/user/status/789", "789"),
    ("https://x.com/user/status/321/photo/1", "321"),
    ("https://example.com/user/status/123", None),  # not a tweet host
    ("https://x.com/user", None),  # not a status path
    ("https://x.com/i/lists/123", None),  # non-status x.com path
]

FXTWITTER_FULL = {
    "code": 200,
    "tweet": {
        "id": "111",
        "text": "hello world",
        "author": {"screen_name": "alice", "name": "Alice"},
        "replying_to": "bob",
        "quote": {
            "text": "quoted text",
            "author": {"screen_name": "carol"},
        },
    },
}

FXTWITTER_MINIMAL = {
    "code": 200,
    "tweet": {
        "id": "222",
        "text": "minimal tweet",
        "author": {"screen_name": "dave", "name": "Dave"},
    },
}

SYNDICATION_FULL = {
    "id_str": "333",
    "text": "syndicated hello",
    "user": {"screen_name": "erin", "name": "Erin"},
    "in_reply_to_screen_name": "frank",
    "quoted_tweet": {
        "text": "syndicated quote",
        "user": {"screen_name": "gina"},
    },
}

SYNDICATION_MINIMAL = {
    "id_str": "444",
    "text": "minimal syndicated",
    "user": {"screen_name": "hank", "name": "Hank"},
}

ARTICLE_BLOCKS = {
    "blocks": [
        {"text": "Big Title", "type": "header-one"},
        {"text": "Some intro paragraph.", "type": "unstyled"},
        {"text": "first item", "type": "unordered-list-item"},
        {"text": "second item", "type": "unordered-list-item"},
        {"text": "a quote", "type": "blockquote"},
        {"text": "", "type": "atomic"},
        {"text": "closing paragraph", "type": "unstyled"},
    ]
}

FXTWITTER_ARTICLE = {
    "code": 200,
    "tweet": {
        "id": "555",
        "text": "https://t.co/whatever",
        "author": {"screen_name": "ivan", "name": "Ivan"},
        "article": {
            "title": "Long-running agents don't need tools",
            "content": ARTICLE_BLOCKS,
        },
    },
}


def main() -> int:
    failures = 0

    for url, want in PARSE_ID_CASES:
        got = parse_tweet_id(url)
        if got != want:
            failures += 1
            print(f"FAIL parse_tweet_id({url!r}): want {want!r} got {got!r}")

    # Golden syndication token, computed by hand against the live endpoint.
    token = syndication_token("2073277317464682723")
    want_token = "5xe51zd2qill18"
    if token != want_token:
        failures += 1
        print(f"FAIL syndication_token: want {want_token!r} got {token!r}")

    tweet = from_fxtwitter(FXTWITTER_FULL)
    if tweet is None or (
        tweet.id,
        tweet.url,
        tweet.author_handle,
        tweet.author_name,
        tweet.text,
        tweet.quoted_handle,
        tweet.quoted_text,
        tweet.in_reply_to,
    ) != (
        "111",
        "https://x.com/alice/status/111",
        "alice",
        "Alice",
        "hello world",
        "carol",
        "quoted text",
        "bob",
    ):
        failures += 1
        print(f"FAIL from_fxtwitter full: got {tweet!r}")

    tweet = from_fxtwitter(FXTWITTER_MINIMAL)
    if tweet is None or (
        tweet.id,
        tweet.url,
        tweet.author_handle,
        tweet.text,
        tweet.quoted_handle,
        tweet.quoted_text,
        tweet.in_reply_to,
    ) != ("222", "https://x.com/dave/status/222", "dave", "minimal tweet", "", "", ""):
        failures += 1
        print(f"FAIL from_fxtwitter minimal: got {tweet!r}")

    if from_fxtwitter({"code": 404}) is not None:
        failures += 1
        print("FAIL from_fxtwitter: code!=200 should return None")

    tweet = from_syndication(SYNDICATION_FULL)
    if tweet is None or (
        tweet.id,
        tweet.url,
        tweet.author_handle,
        tweet.author_name,
        tweet.text,
        tweet.quoted_handle,
        tweet.quoted_text,
        tweet.in_reply_to,
    ) != (
        "333",
        "https://x.com/erin/status/333",
        "erin",
        "Erin",
        "syndicated hello",
        "gina",
        "syndicated quote",
        "frank",
    ):
        failures += 1
        print(f"FAIL from_syndication full: got {tweet!r}")

    tweet = from_syndication(SYNDICATION_MINIMAL)
    if tweet is None or (
        tweet.id,
        tweet.url,
        tweet.author_handle,
        tweet.text,
        tweet.quoted_handle,
        tweet.quoted_text,
        tweet.in_reply_to,
    ) != (
        "444",
        "https://x.com/hank/status/444",
        "hank",
        "minimal syndicated",
        "",
        "",
        "",
    ):
        failures += 1
        print(f"FAIL from_syndication minimal: got {tweet!r}")

    # expand_urls: replaces a resolvable t.co link, leaves an unresolvable
    # one as-is, never raises on a lookup failure.
    orig_follow_redirect = resolve.follow_redirect

    def fake_follow_redirect(url: str, timeout: float) -> str:
        if url == "https://t.co/good":
            return "https://example.com/real-article"
        raise ValueError("network down")

    resolve.follow_redirect = fake_follow_redirect
    try:
        got = expand_urls(
            "check this out https://t.co/good and also https://t.co/bad", timeout=5
        )
    finally:
        resolve.follow_redirect = orig_follow_redirect

    want = "check this out https://example.com/real-article and also https://t.co/bad"
    if got != want:
        failures += 1
        print(f"FAIL expand_urls: want {want!r} got {got!r}")

    if expand_urls("no links here", timeout=5) != "no links here":
        failures += 1
        print("FAIL expand_urls: text with no t.co links should pass through unchanged")

    # article_markdown: mixed block list, list items stay adjacent, atomic
    # skipped, blank line elsewhere.
    got = article_markdown(ARTICLE_BLOCKS)
    want = (
        "# Big Title\n"
        "\n"
        "Some intro paragraph.\n"
        "\n"
        "- first item\n"
        "- second item\n"
        "\n"
        "> a quote\n"
        "\n"
        "closing paragraph"
    )
    if got != want:
        failures += 1
        print(f"FAIL article_markdown: want {want!r} got {got!r}")

    if article_markdown({}) != "":
        failures += 1
        print("FAIL article_markdown: empty content should render empty")

    # from_fxtwitter with an article payload populates article_title/body;
    # without one, both stay empty.
    tweet = from_fxtwitter(FXTWITTER_ARTICLE)
    if tweet is None or tweet.article_title != "Long-running agents don't need tools":
        failures += 1
        print(f"FAIL from_fxtwitter article title: got {tweet!r}")
    if tweet is None or "# Big Title" not in tweet.article_body:
        failures += 1
        print(f"FAIL from_fxtwitter article body: got {tweet!r}")

    tweet = from_fxtwitter(FXTWITTER_MINIMAL)
    if tweet is None or tweet.article_title != "" or tweet.article_body != "":
        failures += 1
        print(f"FAIL from_fxtwitter no article: got {tweet!r}")

    if failures:
        print(f"\n{failures} failure(s).")
        return 1
    print(f"OK: {len(PARSE_ID_CASES) + 13} resolve cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
