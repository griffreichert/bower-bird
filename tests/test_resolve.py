"""Tweet resolver tests — pure logic, no network.

Run: uv run python tests/test_resolve.py
"""

from bower_bird.resolve import (
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

    if failures:
        print(f"\n{failures} failure(s).")
        return 1
    print(f"OK: {len(PARSE_ID_CASES) + 8} resolve cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
