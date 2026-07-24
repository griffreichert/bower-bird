"""Router lane tests — pure logic, no network.

Run: uv run python tests/test_router.py
"""

from bower_bird.router import parse
from bower_bird.schema import Lane

CASES = [
    # bare link -> shelve (every link becomes a source node immediately now)
    ("https://example.com/post", Lane.SHELVE, "https://example.com/post", ""),
    ("  https://example.com/x  ", Lane.SHELVE, "https://example.com/x", ""),
    # link + note -> shelve, note rides along as the seed thought
    (
        "great take on caching https://example.com/c",
        Lane.SHELVE,
        "https://example.com/c",
        "great take on caching",
    ),
    (
        "https://example.com/c — note after",
        Lane.SHELVE,
        "https://example.com/c",
        "— note after",
    ),
    # the retired `read:` prefix is now just ordinary note text
    (
        "read: https://example.com/r",
        Lane.SHELVE,
        "https://example.com/r",
        "read:",
    ),
    # trailing punctuation stripped off URL (and discarded, not returned to note)
    (
        "(https://example.com/p).",
        Lane.SHELVE,
        "https://example.com/p",
        "(",
    ),
    # tool: prefix -> tool shelf (overrides note/shelve routing)
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
    # the bare "read" marker is now just ordinary note text riding the shelve lane
    (
        "https://x.com/u/status/1 read",
        Lane.SHELVE,
        "https://x.com/u/status/1",
        "read",
    ),
    (
        "great read on caching https://example.com/c2",
        Lane.SHELVE,
        "https://example.com/c2",
        "great read on caching",
    ),
    # tool: with no link is not collectible -> parked
    ("tool: icon gen thing", Lane.NO_LINK, None, "tool: icon gen thing"),
    # no link, real text -> pasted prose, its own source node
    ("just some thoughts", Lane.PASTE, None, "just some thoughts"),
    (
        "  a rambling note with no url at all  ",
        Lane.PASTE,
        None,
        "a rambling note with no url at all",
    ),
    # no link, nothing usable -> parked
    ("", Lane.NO_LINK, None, ""),
    ("   ", Lane.NO_LINK, None, ""),
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
