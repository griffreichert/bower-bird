"""Tests for the review module — Leitner logic + ReviewStore, pure logic / no network.

Run: uv run python tests/test_review.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from bower_bird.config import Config
from bower_bird.review import LEITNER_LADDER, Review, ReviewStore

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
        queue_limit=100,
    )


# ---------------------------------------------------------------------------
# Leitner transition rules
# ---------------------------------------------------------------------------


def test_leitner_strong_advances_box() -> None:
    """Strong grade moves the bower to the next box."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        today = date(2026, 6, 25)
        updated = store.apply_grade("abc", "strong", today=today)
        check(updated.box == 1, f"strong from box 0 → box 1, got {updated.box}")
        expected_due = (today + timedelta(days=LEITNER_LADDER[1])).isoformat()
        check(
            updated.due == expected_due,
            f"due after strong: {updated.due} vs {expected_due}",
        )
        check(updated.last_grade == "strong", "last_grade recorded")


def test_leitner_weak_stays_in_box() -> None:
    """Weak grade keeps the bower in its current box."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        # Move to box 2 first
        today = date(2026, 6, 25)
        store.apply_grade("abc", "strong", today=today)
        store.apply_grade("abc", "strong", today=today)
        before_box = store.get("abc").box  # type: ignore[union-attr]
        check(before_box == 2, f"pre-condition: box should be 2, got {before_box}")

        updated = store.apply_grade("abc", "weak", today=today)
        check(updated.box == 2, f"weak stays at box 2, got {updated.box}")
        expected_due = (today + timedelta(days=LEITNER_LADDER[2])).isoformat()
        check(
            updated.due == expected_due,
            f"due after weak: {updated.due} vs {expected_due}",
        )
        check(updated.last_grade == "weak", "last_grade recorded")


def test_leitner_wrong_resets_to_box_zero() -> None:
    """Wrong grade resets the bower to box 0."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        today = date(2026, 6, 25)
        # Move to box 3
        for _ in range(3):
            store.apply_grade("abc", "strong", today=today)
        check(store.get("abc").box == 3, "pre-condition: box 3")  # type: ignore[union-attr]

        updated = store.apply_grade("abc", "wrong", today=today)
        check(updated.box == 0, f"wrong resets to box 0, got {updated.box}")
        expected_due = (today + timedelta(days=LEITNER_LADDER[0])).isoformat()
        check(
            updated.due == expected_due,
            f"due after wrong: {updated.due} vs {expected_due}",
        )
        check(updated.last_grade == "wrong", "last_grade recorded")


def test_leitner_strong_capped_at_last_rung() -> None:
    """Strong at the last box stays capped — no IndexError."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        today = date(2026, 6, 25)
        max_box = len(LEITNER_LADDER) - 1
        # Advance to the last box
        for _ in range(max_box):
            store.apply_grade("abc", "strong", today=today)
        check(store.get("abc").box == max_box, f"pre-condition: box {max_box}")  # type: ignore[union-attr]

        updated = store.apply_grade("abc", "strong", today=today)
        check(updated.box == max_box, f"strong at max box stays capped at {max_box}")
        expected_due = (today + timedelta(days=LEITNER_LADDER[max_box])).isoformat()
        check(updated.due == expected_due, "due uses last ladder rung when capped")


def test_leitner_history_accumulates() -> None:
    """Each grade is appended to the reviews history."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        today = date(2026, 6, 25)
        store.apply_grade("abc", "strong", today=today)
        store.apply_grade("abc", "weak", today=today)
        store.apply_grade("abc", "wrong", today=today)
        r = store.get("abc")
        assert r is not None
        check(len(r.reviews) == 3, f"3 review entries, got {len(r.reviews)}")
        grades = [e.grade for e in r.reviews]
        check(grades == ["strong", "weak", "wrong"], f"history order: {grades}")


# ---------------------------------------------------------------------------
# ReviewStore persistence
# ---------------------------------------------------------------------------


def test_store_round_trips() -> None:
    """Save + reload produces identical Review objects."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("id-1")
        store.seed("id-2")
        today = date(2026, 6, 25)
        store.apply_grade("id-1", "strong", today=today)
        store.save()

        store2 = ReviewStore.load(cfg)
        r1 = store2.get("id-1")
        r2 = store2.get("id-2")
        check(r1 is not None, "id-1 reloaded")
        check(r2 is not None, "id-2 reloaded")
        check(
            r1 is not None and r1.box == 1,
            f"id-1 box survived round-trip: {r1 and r1.box}",
        )
        check(
            r2 is not None and r2.box == 0,
            f"id-2 box survived round-trip: {r2 and r2.box}",
        )


def test_store_seed_is_idempotent() -> None:
    """Seeding the same id twice does not overwrite a graded entry."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        today = date(2026, 6, 25)
        store.apply_grade("abc", "strong", today=today)
        check(store.get("abc").box == 1, "pre-condition: box 1 after strong")  # type: ignore[union-attr]

        seeded_again = store.seed("abc")
        check(not seeded_again, "seed returns False for existing id")
        check(store.get("abc").box == 1, "box unchanged after duplicate seed")  # type: ignore[union-attr]


def test_store_due_today_filter() -> None:
    """due_today returns only bowers due on or before the pivot date."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)

        today = date.today()
        future = today + timedelta(days=10)

        # Seed one due today, one with a future date
        store.seed("due-now")
        store.seed("future")
        # Push "future" out
        store.apply_grade("future", "strong", today=today)  # due = today + 3d

        due = store.due_today(today=today)
        ids = {r.id for r in due}
        check("due-now" in ids, "'due-now' is due today")
        check("future" not in ids, "'future' not due yet")

        # Both should be due well in the future from today's perspective
        due_far = store.due_today(today=future)
        ids_far = {r.id for r in due_far}
        check("due-now" in ids_far, "'due-now' still due at future date")
        check("future" in ids_far, "'future' now due at future date")


# ---------------------------------------------------------------------------
# Review model validation
# ---------------------------------------------------------------------------


def test_review_json_validates() -> None:
    """Review.model_validate round-trips a raw dict correctly."""
    raw = {
        "id": "some-uuid",
        "due": "2026-06-25",
        "box": 2,
        "last_grade": "weak",
        "reviews": [
            {
                "reviewed_on": "2026-06-22",
                "grade": "strong",
                "box_before": 0,
                "box_after": 1,
            },
            {
                "reviewed_on": "2026-06-25",
                "grade": "weak",
                "box_before": 1,
                "box_after": 2,
            },
        ],
    }
    r = Review.model_validate(raw)
    check(r.id == "some-uuid", "id parsed")
    check(r.box == 2, "box parsed")
    check(r.last_grade == "weak", "last_grade parsed")
    check(len(r.reviews) == 2, "history length")


def test_store_file_is_valid_json_after_save() -> None:
    """_review.json is well-formed JSON after a save."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("x")
        store.save()
        raw = cfg.review_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        check("x" in data, "bower key present in JSON")
        check("due" in data["x"], "due field present")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_leitner_strong_advances_box()
    test_leitner_weak_stays_in_box()
    test_leitner_wrong_resets_to_box_zero()
    test_leitner_strong_capped_at_last_rung()
    test_leitner_history_accumulates()
    test_store_round_trips()
    test_store_seed_is_idempotent()
    test_store_due_today_filter()
    test_review_json_validates()
    test_store_file_is_valid_json_after_save()

    if _failures:
        print(f"\n{_failures} test(s) failed.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
