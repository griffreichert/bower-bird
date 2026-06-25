"""Tests for forage gap-signal gathering — pure logic, no network.

Run: uv run python tests/test_forage.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bower_bird.config import Config  # noqa: E402
from bower_bird.forage import (  # noqa: E402
    GapSignals,
    _is_thin_bower,
    gather_dig_marks,
    gather_open_questions,
    gather_quiz_weak,
    gather_signals,
    gather_thin_bowers,
    render_forage,
    write_forage,
)
from bower_bird.review import ReviewStore  # noqa: E402

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
        state_path=root / "state.json",
        fetch_timeout=15,
        drain_limit=100,
    )


# ---------------------------------------------------------------------------
# _is_thin_bower
# ---------------------------------------------------------------------------

_FULL_BOWER = """\
---
title: "Caching"
id: abc-123
created: 2026-06-25
bower: generated
tags:
  - concept
---
# Caching

<!-- bower:concept -->
## Concept

**Definition:** Store results so repeated lookups skip recomputation — space for time.

**Why it matters:** Reduces latency and load on downstream systems.

**Test question:** What trade-off does caching make?

**Model answer:** Space for time — you use more memory to avoid recomputation.
<!-- /bower:concept -->
"""

_THIN_NO_BLOCK = """\
---
title: "Memoization"
id: xyz-456
created: 2026-06-25
bower: generated
tags:
  - concept
---
# Memoization

Some brief text, no concept block.
"""

_THIN_SHORT_DEF = """\
---
title: "Memoization"
id: xyz-456
created: 2026-06-25
bower: generated
tags:
  - concept
---
# Memoization

<!-- bower:concept -->
## Concept

**Definition:** Short def.

**Why it matters:** Yes.

**Test question:** What?

**Model answer:** This.
<!-- /bower:concept -->
"""


def test_is_thin_bower_full_note_not_thin() -> None:
    check(
        not _is_thin_bower(_FULL_BOWER),
        "full bower with long definition is not thin",
    )


def test_is_thin_bower_no_concept_block() -> None:
    check(_is_thin_bower(_THIN_NO_BLOCK), "note with no concept block is thin")


def test_is_thin_bower_short_definition() -> None:
    check(_is_thin_bower(_THIN_SHORT_DEF), "note with short definition is thin")


# ---------------------------------------------------------------------------
# gather_thin_bowers
# ---------------------------------------------------------------------------


def test_gather_thin_bowers_finds_thin_notes() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers"
        bowers.mkdir(parents=True)
        (bowers / "Caching.md").write_text(_FULL_BOWER, encoding="utf-8")
        (bowers / "Memoization.md").write_text(_THIN_NO_BLOCK, encoding="utf-8")

        cfg = _config(root)
        thin = gather_thin_bowers(cfg)
        check("Memoization" in thin, "thin bower detected")
        check("Caching" not in thin, "full bower not flagged as thin")


def test_gather_thin_bowers_empty_dir() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        check(gather_thin_bowers(cfg) == [], "empty bowers dir returns empty list")


# ---------------------------------------------------------------------------
# gather_quiz_weak
# ---------------------------------------------------------------------------


def test_gather_quiz_weak_weak_and_wrong() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers"
        bowers.mkdir(parents=True)
        root.mkdir(exist_ok=True)
        cfg = _config(root)

        # Seed review store and grade two bowers weak/wrong.
        store = ReviewStore.load(cfg)
        store.seed("id-weak")
        store.seed("id-wrong")
        store.seed("id-strong")
        from datetime import date

        store.apply_grade("id-weak", "weak", today=date(2026, 6, 25))
        store.apply_grade("id-wrong", "wrong", today=date(2026, 6, 25))
        store.apply_grade("id-strong", "strong", today=date(2026, 6, 25))
        store.save()

        # Write bower notes with matching ids.
        (bowers / "WeakConcept.md").write_text(
            '---\ntitle: "WeakConcept"\nid: id-weak\n---\n# WeakConcept\n',
            encoding="utf-8",
        )
        (bowers / "WrongConcept.md").write_text(
            '---\ntitle: "WrongConcept"\nid: id-wrong\n---\n# WrongConcept\n',
            encoding="utf-8",
        )
        (bowers / "StrongConcept.md").write_text(
            '---\ntitle: "StrongConcept"\nid: id-strong\n---\n# StrongConcept\n',
            encoding="utf-8",
        )

        titles = gather_quiz_weak(cfg)
        check("WeakConcept" in titles, "weak-graded bower in quiz_weak list")
        check("WrongConcept" in titles, "wrong-graded bower in quiz_weak list")
        check("StrongConcept" not in titles, "strong-graded bower excluded")


def test_gather_quiz_weak_empty_store() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        check(gather_quiz_weak(cfg) == [], "empty store returns empty list")


# ---------------------------------------------------------------------------
# gather_dig_marks
# ---------------------------------------------------------------------------

_SOURCE_WITH_DIG = """\
---
title: "Some Article"
source: "https://example.com/article"
---
# Some Article

This is interesting #dig

Another line with more detail #dig about caching strategies.
"""

_SOURCE_NO_DIG = """\
---
title: "No Digs"
---
# No Digs

Plain text, no dig marks here.
"""


def test_gather_dig_marks_finds_digs() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        sources = root / "brain" / "sources"
        sources.mkdir(parents=True)
        (sources / "article.md").write_text(_SOURCE_WITH_DIG, encoding="utf-8")
        (sources / "nodigs.md").write_text(_SOURCE_NO_DIG, encoding="utf-8")

        cfg = _config(root)
        digs = gather_dig_marks(cfg)
        check(len(digs) == 2, f"two dig marks found, got {len(digs)}: {digs}")
        check(
            any("caching strategies" in d for d in digs),
            "dig about caching strategies present",
        )


def test_gather_dig_marks_empty_sources() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        check(gather_dig_marks(cfg) == [], "no sources dir returns empty list")


# ---------------------------------------------------------------------------
# gather_open_questions
# ---------------------------------------------------------------------------

_SOURCE_WITH_QUESTIONS = """\
---
title: "Research Paper"
---
# Research Paper

> ? What does this imply for distributed systems?

> ? Is there a better alternative to polling here?
"""


def test_gather_open_questions_finds_questions() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        sources = root / "brain" / "sources"
        sources.mkdir(parents=True)
        (sources / "paper.md").write_text(_SOURCE_WITH_QUESTIONS, encoding="utf-8")

        cfg = _config(root)
        qs = gather_open_questions(cfg)
        check(len(qs) == 2, f"two open questions found, got {len(qs)}: {qs}")
        check(
            any("distributed" in q for q in qs),
            "distributed systems question present",
        )


def test_gather_open_questions_empty_sources() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        check(gather_open_questions(cfg) == [], "no sources dir returns empty list")


# ---------------------------------------------------------------------------
# GapSignals.empty
# ---------------------------------------------------------------------------


def test_gap_signals_empty_when_all_empty() -> None:
    signals = GapSignals()
    check(signals.empty, "default GapSignals is empty")


def test_gap_signals_not_empty_with_any_signal() -> None:
    check(not GapSignals(thin_bowers=["X"]).empty, "thin_bowers makes it non-empty")
    check(not GapSignals(quiz_weak=["Y"]).empty, "quiz_weak makes it non-empty")
    check(not GapSignals(dig_marks=["z"]).empty, "dig_marks makes it non-empty")
    check(
        not GapSignals(open_questions=["q?"]).empty,
        "open_questions makes it non-empty",
    )


# ---------------------------------------------------------------------------
# render_forage
# ---------------------------------------------------------------------------


def test_render_forage_includes_all_sections() -> None:
    signals = GapSignals(
        thin_bowers=["Caching"],
        quiz_weak=["Memoization"],
        dig_marks=["how does eviction work"],
        open_questions=["Is LRU always optimal?"],
    )
    out = render_forage(signals)
    check("## Tier 1" in out, "Tier 1 heading present")
    check("## Tier 2" in out, "Tier 2 heading present")
    check("[[Caching]]" in out, "thin bower linked")
    check("[[Memoization]]" in out, "quiz-weak bower linked")
    check("how does eviction work" in out, "dig mark present")
    check("Is LRU always optimal?" in out, "open question present")
    check("search pending" in out, "search pending placeholder present")
    check("inbox/" in out, "inbox/ approval note present")


def test_render_forage_no_fabricated_urls() -> None:
    signals = GapSignals(thin_bowers=["SomeTopic"])
    out = render_forage(signals)
    # Should not contain http:// URLs in the tier-2 section (no fabricated links).
    import re as _re

    tier2_start = out.find("## Tier 2")
    tier2_text = out[tier2_start:] if tier2_start != -1 else ""
    urls = _re.findall(r"https?://\S+", tier2_text)
    check(len(urls) == 0, f"no fabricated URLs in Tier 2, found: {urls}")


# ---------------------------------------------------------------------------
# write_forage + forage_path in config
# ---------------------------------------------------------------------------


def test_write_forage_creates_file_in_vault() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        signals = GapSignals(thin_bowers=["SomeConcept"])
        path = write_forage(cfg, signals)
        check(path.exists(), "forage.md was created")
        check(path == cfg.forage_path, "path matches config.forage_path")
        check("forage.md" in str(path), "file is named forage.md")
        content = path.read_text(encoding="utf-8")
        check("SomeConcept" in content, "content includes the thin bower")


def test_forage_path_inside_owned_folder() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        check(
            cfg.forage_path.parent == root,
            f"forage_path parent is the vault root: {cfg.forage_path.parent}",
        )


# ---------------------------------------------------------------------------
# gather_signals integration (empty vault)
# ---------------------------------------------------------------------------


def test_gather_signals_empty_vault() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        signals = gather_signals(cfg)
        check(signals.empty, "empty vault produces no gap signals")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_is_thin_bower_full_note_not_thin()
    test_is_thin_bower_no_concept_block()
    test_is_thin_bower_short_definition()
    test_gather_thin_bowers_finds_thin_notes()
    test_gather_thin_bowers_empty_dir()
    test_gather_quiz_weak_weak_and_wrong()
    test_gather_quiz_weak_empty_store()
    test_gather_dig_marks_finds_digs()
    test_gather_dig_marks_empty_sources()
    test_gather_open_questions_finds_questions()
    test_gather_open_questions_empty_sources()
    test_gap_signals_empty_when_all_empty()
    test_gap_signals_not_empty_with_any_signal()
    test_render_forage_includes_all_sections()
    test_render_forage_no_fabricated_urls()
    test_write_forage_creates_file_in_vault()
    test_forage_path_inside_owned_folder()
    test_gather_signals_empty_vault()

    if _failures:
        print(f"\n{_failures} test(s) failed.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
