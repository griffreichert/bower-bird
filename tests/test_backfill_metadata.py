"""Test for scripts/backfill_metadata.py's transform functions (pure logic,
no network, no vault writes).

Run: uv run python tests/test_backfill_metadata.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from backfill_metadata import (  # noqa: E402
    clean_tags,
    derive_topics,
    parse_index_categories,
    transform_note,
)

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if not cond:
        _failures += 1
        print(f"FAIL: {msg}")


_NOTE = """\
---
title: "Some Article"
source: "https://example.com/a"
created: 2026-07-18
tags:
  - source
  - "[[@satyanadella]]"
  - type/reference
---
# Some Article

## Key ideas
- A claim.

## Links
- [[Agentic loops]]
- [[Verification]]

## Sources
- some-other-node
"""


def test_derive_topics_only_links_section() -> None:
    topics = derive_topics(_NOTE.split("---\n", 2)[2])
    check(
        topics == ["Agentic loops", "Verification"],
        f"topics from ## Links only: {topics}",
    )
    check(
        "some-other-node" not in topics, "## Sources bullets must not leak into topics"
    )


def test_clean_tags() -> None:
    cleaned, wikilinks, junk = clean_tags(
        ["source", "[[@satyanadella]]", "type/reference"]
    )
    check(cleaned == ["source"], f"allowlist filter: {cleaned}")
    check(wikilinks == 1, f"wikilink tag counted: {wikilinks}")
    check(junk == 1, f"junk tag counted: {junk}")


def test_transform_note_end_to_end() -> None:
    cats = parse_index_categories("- [[Some Article]] · ai · a one-liner\n")
    result = transform_note(_NOTE, cats)
    assert result is not None
    check(result.id_minted, "missing id gets minted")
    check(result.category_filled, "category filled from _index.md")
    check(result.topics_filled, "topics filled from ## Links")
    check(result.wikilink_tags_stripped == 1, "wikilink tag stripped")
    check(result.junk_tags_dropped == 1, "junk tag dropped")
    check("category: ai" in result.text, "category line present")
    check("  - Agentic loops" in result.text, "topics block present")
    check("[[@satyanadella]]" not in result.text, "leaked wikilink tag removed")
    check("type/reference" not in result.text, "junk tag removed")

    body_before = _NOTE.split("---\n", 2)[2]
    body_after = result.text.split("---\n", 2)[2]
    check(body_after == body_before, "body prose untouched")

    # Idempotent: transforming the already-rewritten text changes nothing.
    second = transform_note(result.text, cats)
    assert second is not None
    check(not second.changed, "re-running is a no-op")


_LEGACY_NOTE_BLOCK_AUTHOR = """\
---
title: "Some Tweet"
source: "https://x.com/someone/status/123"
author:
  - "[[@someone]]"
published: 2026-01-03
created: 2026-06-15
description: "a description"
tags:
  - "clippings"
---
## Article

body text
"""


def test_block_form_author_does_not_leak() -> None:
    """Regression: a legacy multi-line `author:\\n  - "[[@x]]"` block must not
    bleed across lines into the rewritten `author:` scalar (a naive `\\s*`
    regex would match through the newline and copy the leaked wikilink into
    the new frontmatter verbatim)."""
    cats: dict[str, str] = {}
    result = transform_note(_LEGACY_NOTE_BLOCK_AUTHOR, cats)
    assert result is not None
    check(
        "[[@someone]]" not in result.text, "leaked wikilink author not copied through"
    )
    check('author: "-' not in result.text, "block-list value not folded into scalar")
    check("published:" not in result.text, "non-canonical key dropped")
    check("description:" not in result.text, "legacy description key dropped")


test_derive_topics_only_links_section()
test_clean_tags()
test_transform_note_end_to_end()
test_block_form_author_does_not_leak()

if _failures:
    print(f"\n{_failures} failure(s)")
    sys.exit(1)
print("all tests passed")
