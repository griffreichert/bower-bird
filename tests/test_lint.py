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


def test_topics_matching_links_is_clean() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        sources = root / "brain" / "sources"
        sources.mkdir(parents=True)
        (sources / "S.md").write_text(
            '---\ntitle: "S"\nid: id-s\ntopics:\n  - Concept\n---\n'
            "# S\n\n## Links\n- [[Concept]]\n",
            encoding="utf-8",
        )

        cfg = _config(root)
        findings, _, _ = run_lint(cfg)
        check(
            not findings.topic_drift,
            f"matching topics is clean, got: {findings.topic_drift}",
        )


def test_topics_diverging_from_links_detected() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        sources = root / "brain" / "sources"
        sources.mkdir(parents=True)
        (sources / "S.md").write_text(
            '---\ntitle: "S"\nid: id-s\ntopics:\n  - Stale Topic\n---\n'
            "# S\n\n## Links\n- [[Concept]]\n",
            encoding="utf-8",
        )

        cfg = _config(root)
        findings, _, _ = run_lint(cfg)
        check(
            any("S.md" in f for f in findings.topic_drift),
            f"diverging topics flagged, got: {findings.topic_drift}",
        )


def test_missing_topics_key_is_not_a_finding() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        sources = root / "brain" / "sources"
        sources.mkdir(parents=True)
        (sources / "S.md").write_text(
            '---\ntitle: "S"\nid: id-s\n---\n# S\n\n## Links\n- [[Concept]]\n',
            encoding="utf-8",
        )

        cfg = _config(root)
        findings, _, _ = run_lint(cfg)
        check(
            not findings.topic_drift,
            "pre-backfill node without topics is not a finding",
        )


def test_category_matching_index_is_clean() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        sources = root / "brain" / "sources"
        sources.mkdir(parents=True)
        (sources / "S.md").write_text(
            '---\ntitle: "S"\nid: id-s\ncategory: ai\n---\n# S\n\n## Links\n',
            encoding="utf-8",
        )
        (root / "brain" / "_index.md").write_text(
            "- [[S]] · ai · a source\n", encoding="utf-8"
        )

        cfg = _config(root)
        findings, _, _ = run_lint(cfg)
        check(
            not findings.category_drift,
            f"matching category is clean, got: {findings.category_drift}",
        )


def test_category_diverging_from_index_detected() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        sources = root / "brain" / "sources"
        sources.mkdir(parents=True)
        (sources / "S.md").write_text(
            '---\ntitle: "S"\nid: id-s\ncategory: ai\n---\n# S\n\n## Links\n',
            encoding="utf-8",
        )
        (root / "brain" / "_index.md").write_text(
            "- [[S]] · productivity · a source\n", encoding="utf-8"
        )

        cfg = _config(root)
        findings, _, _ = run_lint(cfg)
        check(
            any("S.md" in f for f in findings.category_drift),
            f"diverging category flagged, got: {findings.category_drift}",
        )


def _concept(root: Path, name: str, block: str = "") -> None:
    bowers = root / "brain" / "bowers"
    bowers.mkdir(parents=True, exist_ok=True)
    (bowers / f"{name}.md").write_text(
        f'---\ntitle: "{name}"\nid: id-{name.lower()}\n---\n# {name}\n\n{block}',
        encoding="utf-8",
    )


def _feeding_sources(root: Path, target: str, count: int, start: int = 0) -> None:
    sources = root / "brain" / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    for i in range(start, start + count):
        (sources / f"src{i}.md").write_text(
            f'---\ntitle: "src{i}"\nid: id-src{i}\n---\n'
            f"# src{i}\n\n## Links\n- [[{target}]]\n",
            encoding="utf-8",
        )


def test_under_cited_synthesis_detected() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        # Block cites 1 wikilink inside it, but 2 sources feed the concept.
        _concept(
            root,
            "Thin",
            "<!-- bower:concept -->\n[[OnlyOne]]\n<!-- /bower:concept -->\n",
        )
        _feeding_sources(root, "Thin", 2)

        cfg = _config(root)
        findings, _, _ = run_lint(cfg)
        check(
            any("Thin.md" in f for f in findings.under_cited_synthesis),
            f"under-cited block flagged, got: {findings.under_cited_synthesis}",
        )


def test_well_cited_synthesis_is_clean() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _concept(
            root,
            "Rich",
            "<!-- bower:concept -->\n[[One]] and [[Two]]\n<!-- /bower:concept -->\n",
        )
        _feeding_sources(root, "Rich", 2)

        cfg = _config(root)
        findings, _, _ = run_lint(cfg)
        check(
            not findings.under_cited_synthesis,
            f"2+ citations inside block is clean, "
            f"got: {findings.under_cited_synthesis}",
        )


def test_earned_but_unsynthesized_detected() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _concept(root, "Earned")  # no synthesis block at all
        _feeding_sources(root, "Earned", 3)

        cfg = _config(root)
        findings, _, _ = run_lint(cfg)
        check(
            any("Earned.md" in f for f in findings.earned_unsynthesized),
            f"3+ inbound sources with no block flagged, "
            f"got: {findings.earned_unsynthesized}",
        )


def test_single_source_concept_is_not_a_finding() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        # 1 feeding source, no block: too young to have earned anything.
        _concept(root, "Young")
        _feeding_sources(root, "Young", 1)
        # 1 feeding source, under-cited block: also too young to flag.
        _concept(
            root,
            "YoungWithBlock",
            "<!-- bower:concept -->\nno citations\n<!-- /bower:concept -->\n",
        )
        _feeding_sources(root, "YoungWithBlock", 1, start=1)

        cfg = _config(root)
        findings, _, _ = run_lint(cfg)
        check(
            not findings.earned_unsynthesized,
            f"1-source concept is not an earned-unsynthesized finding, "
            f"got: {findings.earned_unsynthesized}",
        )
        check(
            not findings.under_cited_synthesis,
            f"1-source concept with a block is not an under-cited finding, "
            f"got: {findings.under_cited_synthesis}",
        )


def test_two_sources_with_no_block_is_not_earned() -> None:
    # >=3 is the earned threshold; 2 is not enough.
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _concept(root, "TwoSources")
        _feeding_sources(root, "TwoSources", 2)

        cfg = _config(root)
        findings, _, _ = run_lint(cfg)
        check(
            not findings.earned_unsynthesized,
            f"2-source concept with no block is not earned-unsynthesized yet, "
            f"got: {findings.earned_unsynthesized}",
        )


if __name__ == "__main__":
    test_clean_vault_returns_zero()
    test_orphan_review_entry_detected()
    test_orphan_node_detected()
    test_broken_link_detected()
    test_aliased_link_resolves()
    test_topics_matching_links_is_clean()
    test_topics_diverging_from_links_detected()
    test_missing_topics_key_is_not_a_finding()
    test_category_matching_index_is_clean()
    test_category_diverging_from_index_detected()
    test_under_cited_synthesis_detected()
    test_well_cited_synthesis_is_clean()
    test_earned_but_unsynthesized_detected()
    test_single_source_concept_is_not_a_finding()
    test_two_sources_with_no_block_is_not_earned()

    if _failures:
        print(f"\n{_failures} test(s) failed.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
