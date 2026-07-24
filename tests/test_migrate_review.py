"""Tests for scripts/migrate_review.py against a tmp fake vault. No network.

Run: uv run python tests/test_migrate_review.py
"""

import json
import sys
import tempfile
from pathlib import Path

# scripts/ isn't a package — import by path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import migrate_review  # noqa: E402

from bower_bird.config import Config  # noqa: E402

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


_OLD_REVIEW = {
    "old-bower-id": {
        "id": "old-bower-id",
        "due": "2026-06-01",
        "box": 3,
        "last_grade": "strong",
        "reviews": [],
    }
}

_SOURCE_WITH_ID = """\
---
title: "Has Id"
id: existing-id
---
# Has Id
"""

_SOURCE_WITHOUT_ID = """\
---
title: "No Id Yet"
---
# No Id Yet
"""

_BOWER_WITH_FEYNMAN = """\
---
title: "Some Bower"
id: bower-1
---
# Some Bower

<!-- bower:concept -->
## Concept

**Definition:** it is a thing.

**Why it matters:** because.

**Test question:** what is it?

**Model answer:** it is a thing.
<!-- /bower:concept -->
"""


def _seed_vault(root: Path) -> None:
    sources = root / "brain" / "sources"
    sources.mkdir(parents=True)
    (sources / "has-id.md").write_text(_SOURCE_WITH_ID, encoding="utf-8")
    (sources / "no-id.md").write_text(_SOURCE_WITHOUT_ID, encoding="utf-8")

    bowers = root / "brain" / "bowers"
    bowers.mkdir(parents=True)
    (bowers / "some-bower.md").write_text(_BOWER_WITH_FEYNMAN, encoding="utf-8")

    root.joinpath("_review.json").write_text(json.dumps(_OLD_REVIEW), encoding="utf-8")


def test_strip_feynman_blocks_removes_only_qa_lines() -> None:
    out = migrate_review.strip_feynman_blocks(_BOWER_WITH_FEYNMAN)
    check("**Test question:**" not in out, "test question stripped")
    check("**Model answer:**" not in out, "model answer stripped")
    check("**Definition:** it is a thing." in out, "definition untouched")
    check("**Why it matters:** because." in out, "why-it-matters untouched")
    check(
        "<!-- bower:concept -->" in out and "<!-- /bower:concept -->" in out,
        "sentinel kept",
    )


def test_build_summary_reports_counts() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _seed_vault(root)
        cfg = _config(root)
        lines = migrate_review.build_summary(cfg)
        joined = "\n".join(lines)
        check("Archive _review.json" in joined, f"mentions archiving: {joined}")
        check("Enrol 2 source node(s)" in joined, f"counts 2 sources: {joined}")
        check("1 need a fresh id" in joined, f"counts 1 id to mint: {joined}")
        check(
            "Strip Feynman" in joined and "1 bower note(s)" in joined,
            f"counts 1 bower: {joined}",
        )


def test_run_migration_archives_old_store() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _seed_vault(root)
        cfg = _config(root)
        migrate_review.run_migration(cfg)

        archive = root / "_review.pre-antilibrary.json"
        check(archive.exists(), "old store archived")
        archived = json.loads(archive.read_text(encoding="utf-8"))
        check("old-bower-id" in archived, "old bower-keyed entry preserved verbatim")


def test_run_migration_writes_fresh_store_enrolling_all_sources() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _seed_vault(root)
        cfg = _config(root)
        migrate_review.run_migration(cfg)

        fresh = json.loads(cfg.review_path.read_text(encoding="utf-8"))
        check(len(fresh) == 2, f"2 sources enrolled, got {len(fresh)}")
        check("old-bower-id" not in fresh, "old bower id does not carry over")
        for entry in fresh.values():
            check(entry["box"] == 0, "enrolled at box 0")
            check(entry["reviews"] == [], "no history transferred")


def test_run_migration_mints_missing_source_ids() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _seed_vault(root)
        cfg = _config(root)
        migrate_review.run_migration(cfg)

        no_id_path = root / "brain" / "sources" / "no-id.md"
        text = no_id_path.read_text(encoding="utf-8")
        check("\nid: " in text, "id: injected into frontmatter")

        has_id_path = root / "brain" / "sources" / "has-id.md"
        check(
            "id: existing-id" in has_id_path.read_text(encoding="utf-8"),
            "existing id left untouched",
        )


def test_run_migration_strips_bower_feynman_blocks_only() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _seed_vault(root)
        cfg = _config(root)
        migrate_review.run_migration(cfg)

        bower_path = root / "brain" / "bowers" / "some-bower.md"
        text = bower_path.read_text(encoding="utf-8")
        check("**Test question:**" not in text, "Feynman block stripped from bower")
        check("**Definition:** it is a thing." in text, "rest of bower body untouched")


if __name__ == "__main__":
    test_strip_feynman_blocks_removes_only_qa_lines()
    test_build_summary_reports_counts()
    test_run_migration_archives_old_store()
    test_run_migration_writes_fresh_store_enrolling_all_sources()
    test_run_migration_mints_missing_source_ids()
    test_run_migration_strips_bower_feynman_blocks_only()

    if _failures:
        print(f"\n{_failures} test(s) failed.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
