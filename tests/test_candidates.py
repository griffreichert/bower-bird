"""Tests for `nodes.candidate_index` — relevance-first, popularity-backed
candidate selection (see config.LLMSettings.topic_relevance_limit).

Run: uv run python tests/test_candidates.py
"""

import sys
import tempfile
from pathlib import Path

from bower_bird.config import Config, LLMSettings
from bower_bird.nodes import candidate_index, inbound_link_counts

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


_INDEX = """\
---
title: Index
bower: generated
tags:
  - index
---
# Index

## Pages
- [[Data extraction]] · ai · Pulling structured fields out of raw documents.
- [[Popular A]] · ai · popular note A.
- [[Popular B]] · ai · popular note B.
- [[Popular C]] · ai · popular note C.
- [[Popular D]] · ai · popular note D.
- [[Popular E]] · ai · popular note E.
"""

_POPULAR_TITLES = ["Popular A", "Popular B", "Popular C", "Popular D", "Popular E"]


def _build_vault(root: Path) -> None:
    sources = root / "brain" / "sources"
    bowers = root / "brain" / "bowers"
    sources.mkdir(parents=True)
    bowers.mkdir(parents=True)
    (root / "brain" / "_index.md").write_text(_INDEX, encoding="utf-8")

    for title in ["Data extraction", *_POPULAR_TITLES]:
        (bowers / f"{title}.md").write_text(
            f'---\ntitle: "{title}"\n---\n# {title}\n', encoding="utf-8"
        )

    # "Data extraction" is fed by 1 source; each popular concept by 2, so
    # a popularity-only top-5 cut (topic_candidate_limit=5) fills entirely
    # with the 5 popular concepts and excludes "Data extraction".
    (sources / "src-relevant.md").write_text(
        "# Source\n[[Data extraction]]\n", encoding="utf-8"
    )
    for i, title in enumerate(_POPULAR_TITLES):
        for j in range(2):
            (sources / f"src-{i}-{j}.md").write_text(
                f"# Source\n[[{title}]]\n", encoding="utf-8"
            )


def test_low_count_relevant_concept_included_despite_popularity_cutoff() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _build_vault(root)
        cfg = _config(root)
        llm = LLMSettings(topic_relevance_limit=3, topic_candidate_limit=5)

        result = candidate_index(cfg, llm, "extraction pipeline processing")

        check(
            "[[Data extraction]]" in result,
            "low-count query-relevant concept is included via relevance, not "
            "excluded by the popularity cutoff",
        )


def test_relevance_hits_come_before_popularity_only_titles() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _build_vault(root)
        cfg = _config(root)
        llm = LLMSettings(topic_relevance_limit=3, topic_candidate_limit=5)

        result = candidate_index(cfg, llm, "extraction pipeline processing")
        lines = result.splitlines()

        relevant_idx = next(
            i for i, line in enumerate(lines) if "Data extraction" in line
        )
        popular_idx = next(i for i, line in enumerate(lines) if "Popular" in line)
        check(
            relevant_idx < popular_idx,
            "relevance hit precedes popularity-only titles",
        )


def test_emitted_line_preserves_full_index_line() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _build_vault(root)
        cfg = _config(root)
        llm = LLMSettings(topic_relevance_limit=3, topic_candidate_limit=5)

        result = candidate_index(cfg, llm, "extraction pipeline processing")

        check(
            "- [[Data extraction]] · ai · Pulling structured fields out of "
            "raw documents." in result,
            "full raw index line (category + one-liner) preserved verbatim",
        )
        check(
            "- [[Popular A]] · ai · popular note A." in result,
            "popularity-half line also preserved verbatim",
        )


def test_tool_leaves_count_toward_the_popularity_backbone() -> None:
    """A concept fed by tool leaves is a hub, not an orphan. Sources-only
    counting read `Data extraction` (1 source, 7 tool leaves) as a singleton
    and cut it from the backbone — the Harvey miss, 2026-07-28."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _build_vault(root)
        cfg = _config(root)
        tools = root / "brain" / "tools"
        tools.mkdir(parents=True)
        for i in range(4):
            (tools / f"tool-{i}.md").write_text(
                "# Tool\n[[Data extraction]]\n", encoding="utf-8"
            )

        counts = inbound_link_counts(cfg)
        check(
            counts["Data extraction"] == 5,
            f"1 source + 4 tool leaves = 5 inbound, got {counts['Data extraction']}",
        )

        # Popularity alone must now carry it, with relevance switched off.
        llm = LLMSettings(topic_relevance_limit=1, topic_candidate_limit=2)
        result = candidate_index(cfg, llm, "nothing matches here")
        check(
            "[[Data extraction]]" in result,
            "leaf-fed concept reaches the popularity backbone",
        )


if __name__ == "__main__":
    test_low_count_relevant_concept_included_despite_popularity_cutoff()
    test_relevance_hits_come_before_popularity_only_titles()
    test_emitted_line_preserves_full_index_line()
    test_tool_leaves_count_toward_the_popularity_backbone()

    if _failures:
        print(f"\n{_failures} test(s) failed.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
