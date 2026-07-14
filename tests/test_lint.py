"""Tests for the lint (bb lint) graph linter — pure logic, no network.

Run: uv run python tests/test_lint.py
"""

import json
import sys
import tempfile
from pathlib import Path

from bower_bird.config import Config
from bower_bird.lint import run_lint

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
        model="test",
        build_model="test",
        state_path=root / "state.json",
        fetch_timeout=15,
        queue_limit=100,
    )


_A = """\
---
title: "A"
id: id-a
---
# A

Links to [[B]].
"""

_B = """\
---
title: "B"
id: id-b
---
# B

Links back to [[A]].
"""


def test_clean_vault_returns_zero() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers"
        bowers.mkdir(parents=True)
        (bowers / "A.md").write_text(_A, encoding="utf-8")
        (bowers / "B.md").write_text(_B, encoding="utf-8")

        cfg = _config(root)
        findings, node_count, review_count = run_lint(cfg)
        check(findings.empty, f"clean vault has no findings, got: {findings}")
        check(node_count == 2, f"two nodes counted, got {node_count}")
        check(review_count == 0, f"no review entries, got {review_count}")


def test_orphan_review_entry_detected() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers"
        bowers.mkdir(parents=True)
        (bowers / "A.md").write_text(_A, encoding="utf-8")
        (bowers / "B.md").write_text(_B, encoding="utf-8")

        # _review.json references an id with no matching brain node.
        review_path = root / "_review.json"
        review_path.write_text(
            json.dumps(
                {
                    "ghost-id": {
                        "id": "ghost-id",
                        "due": "2026-07-13",
                        "box": 0,
                        "last_grade": None,
                        "reviews": [],
                    }
                }
            ),
            encoding="utf-8",
        )

        cfg = _config(root)
        findings, _, review_count = run_lint(cfg)
        check("ghost-id" in findings.orphan_reviews, "orphan review id detected")
        check(review_count == 1, f"one review entry counted, got {review_count}")


def test_orphan_node_detected() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers"
        bowers.mkdir(parents=True)
        (bowers / "A.md").write_text(_A, encoding="utf-8")
        (bowers / "B.md").write_text(_B, encoding="utf-8")
        # No links in or out — orphan.
        (bowers / "Lonely.md").write_text(
            '---\ntitle: "Lonely"\nid: id-lonely\n---\n# Lonely\n\nNo links here.\n',
            encoding="utf-8",
        )
        # Underscore-prefixed files are skipped even with zero links.
        (bowers / "_index.md").write_text("# Index\n", encoding="utf-8")

        cfg = _config(root)
        findings, _, _ = run_lint(cfg)
        check(
            any("Lonely.md" in f for f in findings.orphan_nodes),
            f"Lonely.md flagged as orphan, got: {findings.orphan_nodes}",
        )
        check(
            not any("_index.md" in f for f in findings.orphan_nodes),
            "_index.md skipped despite zero links",
        )


def test_broken_link_detected() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers"
        bowers.mkdir(parents=True)
        (bowers / "A.md").write_text(
            '---\ntitle: "A"\nid: id-a\n---\n# A\n\nLinks to [[Nonexistent]].\n',
            encoding="utf-8",
        )

        cfg = _config(root)
        findings, _, _ = run_lint(cfg)
        check(
            any("Nonexistent" in f for f in findings.broken_links),
            f"broken link to Nonexistent detected, got: {findings.broken_links}",
        )


def test_aliased_link_resolves() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers"
        bowers.mkdir(parents=True)
        (bowers / "A.md").write_text(
            '---\ntitle: "A"\nid: id-a\n---\n# A\n\nLinks to [[B|the other one]].\n',
            encoding="utf-8",
        )
        (bowers / "B.md").write_text(_B, encoding="utf-8")

        cfg = _config(root)
        findings, _, _ = run_lint(cfg)
        check(
            not findings.broken_links,
            f"aliased link to B resolves, got broken: {findings.broken_links}",
        )


if __name__ == "__main__":
    test_clean_vault_returns_zero()
    test_orphan_review_entry_detected()
    test_orphan_node_detected()
    test_broken_link_detected()
    test_aliased_link_resolves()

    if _failures:
        print(f"\n{_failures} test(s) failed.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
