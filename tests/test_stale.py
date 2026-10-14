"""Tests for `bb stale` — the ranked weave queue. Pure logic, no network.

Run: uv run python tests/test_stale.py
"""

import sys
import tempfile
from pathlib import Path

from bower_bird.config import Config
from bower_bird.stale import parse_marker, stale_concepts

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


def _source(target: str) -> str:
    return f'---\ntitle: "S"\nid: id-s\n---\n# S\n\n## Links\n- [[{target}]]\n'


def test_marker_parsing() -> None:
    check(
        parse_marker("no marker here") == (None, None), "no block -> never synthesized"
    )
    check(
        parse_marker("<!-- bower:concept -->\nstuff\n<!-- /bower:concept -->")
        == (None, None),
        "bare block -> never synthesized",
    )
    synthesized, sources = parse_marker(
        "<!-- bower:concept synthesized=2026-07-01 sources=3 -->"
    )
    check(
        synthesized is not None and synthesized.isoformat() == "2026-07-01",
        "date parsed",
    )
    check(sources == 3, "sources parsed")


def test_never_synthesized_ranks_above_stale_but_synthesized() -> None:
    # Same new_sources count (3) for both, so the tiebreaker decides: a
    # never-synthesized concept (days_since = infinite) must outrank one
    # that was synthesized long ago but has since accrued the same number
    # of new sources.
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers"
        sources = root / "brain" / "sources"
        bowers.mkdir(parents=True)
        sources.mkdir(parents=True)

        (bowers / "Never.md").write_text(
            '---\ntitle: "Never"\nid: id-never\n---\n# Never\n', encoding="utf-8"
        )
        (bowers / "Stale.md").write_text(
            '---\ntitle: "Stale"\nid: id-stale\n---\n'
            "# Stale\n\n"
            "<!-- bower:concept synthesized=2026-01-01 sources=0 -->\nold\n"
            "<!-- /bower:concept -->\n",
            encoding="utf-8",
        )

        for i, name in enumerate(["Never"] * 3 + ["Stale"] * 3):
            (sources / f"s{i}.md").write_text(_source(name), encoding="utf-8")

        cfg = _config(root)
        concepts = stale_concepts(cfg)
        titles = [c.title for c in concepts]
        check(
            titles == ["Never", "Stale"],
            f"never-synthesized ranks above stale-but-synthesized on a "
            f"new_sources tie, got {titles}",
        )


def test_zero_new_sources_omitted() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers"
        sources = root / "brain" / "sources"
        bowers.mkdir(parents=True)
        sources.mkdir(parents=True)

        # Synthesized at 3 sources, still exactly 3 feeding it -> 0 new.
        (bowers / "Caught up.md").write_text(
            '---\ntitle: "Caught up"\nid: id-c\n---\n'
            "# Caught up\n\n"
            "<!-- bower:concept synthesized=2026-07-01 sources=3 -->\nx\n"
            "<!-- /bower:concept -->\n",
            encoding="utf-8",
        )
        for i in range(3):
            (sources / f"s{i}.md").write_text(_source("Caught up"), encoding="utf-8")

        cfg = _config(root)
        concepts = stale_concepts(cfg)
        check(concepts == [], f"0-new-source concept omitted, got {concepts}")


if __name__ == "__main__":
    test_marker_parsing()
    test_never_synthesized_ranks_above_stale_but_synthesized()
    test_zero_new_sources_omitted()

    if _failures:
        print(f"\n{_failures} test(s) failed.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
