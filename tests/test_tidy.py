"""Tests for `bb tidy` (tidy.py) — pure logic, no network, no LLM.

Run: uv run python tests/test_tidy.py
"""

import sys
import tempfile
from pathlib import Path

from bower_bird.config import Config
from bower_bird.tidy import main, propose_filings, rebuild_index

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
- [[Kept]] · ai · A one-liner worth keeping.
- [[Ghost]] · ai · A page that no longer exists on disk.
"""


def _build_vault(root: Path) -> None:
    sources = root / "brain" / "sources"
    bowers = root / "brain" / "bowers"
    sources.mkdir(parents=True)
    bowers.mkdir(parents=True)
    (root / "brain" / "_index.md").write_text(_INDEX, encoding="utf-8")
    (sources / "Kept.md").write_text(
        '---\ntitle: "Kept"\nid: id-kept\n---\n# Kept\n', encoding="utf-8"
    )
    (sources / "New.md").write_text(
        '---\ntitle: "New"\nid: id-new\n---\n# New\n', encoding="utf-8"
    )


def test_index_rebuild_preserves_oneliners_drops_ghosts_adds_new() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _build_vault(root)
        cfg = _config(root)
        rebuild = rebuild_index(cfg)
        check(
            "- [[Kept]] · ai · A one-liner worth keeping." in rebuild.text,
            "existing category + one-liner preserved for Kept",
        )
        check("[[Ghost]]" not in rebuild.text, "ghost entry with no page dropped")
        check("- [[New]]" in rebuild.text, "new page on disk added, bare line")
        check(rebuild.added == ["New"], f"added list names New, got {rebuild.added}")
        check(
            rebuild.dropped == ["Ghost"],
            f"dropped list names Ghost, got {rebuild.dropped}",
        )


def _concept(title: str, links: list[str]) -> str:
    body = "\n".join(f"- [[{t}]]" for t in links)
    return f'---\ntitle: "{title}"\n---\n# {title}\n\n## Links\n{body}\n'


def test_flat_concept_with_two_links_into_one_bower_moves() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers"
        (bowers / "agents").mkdir(parents=True)
        (bowers / "agents" / "A.md").write_text(_concept("A", []), encoding="utf-8")
        (bowers / "agents" / "B.md").write_text(_concept("B", []), encoding="utf-8")
        (bowers / "Flat.md").write_text(_concept("Flat", ["A", "B"]), encoding="utf-8")

        cfg = _config(root)
        proposals = propose_filings(cfg)
        check(len(proposals) == 1, f"one proposal, got {len(proposals)}")
        check(
            proposals[0].bower == "agents",
            f"files into agents, got {proposals[0].bower}",
        )

        main(cfg, apply=True)
        check(
            (bowers / "agents" / "Flat.md").exists(),
            "Flat.md moved into bowers/agents/",
        )
        check(not (bowers / "Flat.md").exists(), "Flat.md no longer flat")


def test_flat_concept_with_one_link_each_into_two_bowers_does_not_move() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers"
        (bowers / "agents").mkdir(parents=True)
        (bowers / "llms").mkdir(parents=True)
        (bowers / "agents" / "A.md").write_text(_concept("A", []), encoding="utf-8")
        (bowers / "llms" / "L.md").write_text(_concept("L", []), encoding="utf-8")
        (bowers / "Flat.md").write_text(_concept("Flat", ["A", "L"]), encoding="utf-8")

        cfg = _config(root)
        proposals = propose_filings(cfg)
        check(proposals == [], f"tied bowers -> no proposal, got {proposals}")

        main(cfg, apply=True)
        check((bowers / "Flat.md").exists(), "Flat.md left flat on a tie")


def test_bower_landing_note_never_files_into_itself() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers"
        (bowers / "life").mkdir(parents=True)
        (bowers / "life" / "A.md").write_text(_concept("A", []), encoding="utf-8")
        (bowers / "life" / "B.md").write_text(_concept("B", []), encoding="utf-8")
        (bowers / "life.md").write_text(_concept("life", ["A", "B"]), encoding="utf-8")

        cfg = _config(root)
        proposals = propose_filings(cfg)
        check(
            proposals == [],
            f"life.md is the bower's own landing note, not a proposal, got {proposals}",
        )


def test_dry_run_writes_nothing() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers"
        _build_vault(root)
        (bowers / "agents").mkdir(parents=True)
        (bowers / "agents" / "A.md").write_text(_concept("A", []), encoding="utf-8")
        (bowers / "agents" / "B.md").write_text(_concept("B", []), encoding="utf-8")
        (bowers / "Flat.md").write_text(_concept("Flat", ["A", "B"]), encoding="utf-8")

        index_before = (root / "brain" / "_index.md").read_text(encoding="utf-8")
        cfg = _config(root)
        main(cfg, apply=False)

        index_after = (root / "brain" / "_index.md").read_text(encoding="utf-8")
        check(index_before == index_after, "dry-run never rewrites _index.md")
        check((bowers / "Flat.md").exists(), "dry-run never moves a flat concept")
        check(
            not (bowers / "agents" / "Flat.md").exists(),
            "dry-run doesn't create the moved file either",
        )


if __name__ == "__main__":
    test_index_rebuild_preserves_oneliners_drops_ghosts_adds_new()
    test_flat_concept_with_two_links_into_one_bower_moves()
    test_flat_concept_with_one_link_each_into_two_bowers_does_not_move()
    test_bower_landing_note_never_files_into_itself()
    test_dry_run_writes_nothing()

    if _failures:
        print(f"\n{_failures} test(s) failed.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
