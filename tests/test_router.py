"""Router lane tests — pure logic, no network.

Run: uv run python tests/test_router.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bower_bird.router import Lane, parse  # noqa: E402

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
