"""Router lane tests — pure logic, no network.

Run: uv run python tests/test_router.py
"""

from bower_bird.router import Lane, parse

CASES = [
    # bare link -> to-read
    ("https://example.com/post", Lane.TO_READ, "https://example.com/post", ""),
    ("  https://example.com/x  ", Lane.TO_READ, "https://example.com/x", ""),
    # link + note -> learned
    (
        "great take on caching https://example.com/c",
        Lane.LEARNED,
        "https://example.com/c",
        "great take on caching",
    ),
    (
        "https://example.com/c — note after",
        Lane.LEARNED,
        "https://example.com/c",
        "— note after",
    ),
    # read: prefix -> learned even with no note
    (
        "read: https://example.com/r",
        Lane.LEARNED,
        "https://example.com/r",
        "",
    ),
    (
        "READ:https://example.com/r2",
        Lane.LEARNED,
        "https://example.com/r2",
        "",
    ),
    # trailing punctuation stripped off URL (and discarded, not returned to note)
    (
        "(https://example.com/p).",
        Lane.LEARNED,  # leading "(" counts as surrounding text -> note lane
        "https://example.com/p",
        "(",
    ),
    # tool: prefix -> tool shelf (overrides note/read routing)
    (
        "tool: https://github.com/x/y",
        Lane.TOOL,
        "https://github.com/x/y",
        "",
    ),
    (
        "tool: claude loop plugin https://github.com/x/y",
        Lane.TOOL,
        "https://github.com/x/y",
        "claude loop plugin",
    ),
    # bare "read" marker, any case, before or after the link (share-sheet flow:
    # link lands first, then type "read") -> learned, marker is not a note
    (
        "https://x.com/u/status/1 read",
        Lane.LEARNED,
        "https://x.com/u/status/1",
        "",
    ),
    (
        "https://x.com/u/status/1 Read.",
        Lane.LEARNED,
        "https://x.com/u/status/1",
        "",
    ),
    (
        "READ https://x.com/u/status/2",
        Lane.LEARNED,
        "https://x.com/u/status/2",
        "",
    ),
    # "read" as an ordinary word inside a real note stays in the note
    (
        "great read on caching https://example.com/c2",
        Lane.LEARNED,
        "https://example.com/c2",
        "great read on caching",
    ),
    # tool: with no link is not collectible -> parked
    ("tool: icon gen thing", Lane.NO_LINK, None, "tool: icon gen thing"),
    # no link
    ("just some thoughts", Lane.NO_LINK, None, "just some thoughts"),
]


def main() -> int:
    failures = 0
    for text, lane, url, note in CASES:
        got = parse(text)
        if got.lane is not lane or got.url != url or got.note != note:
            failures += 1
            print(
                f"FAIL: {text!r}\n  want lane={lane} url={url!r} note={note!r}\n"
                f"  got  lane={got.lane} url={got.url!r} note={got.note!r}"
            )
    if failures:
        print(f"\n{failures} failure(s).")
        return 1
    print(f"OK: {len(CASES)} router cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
