"""Tests for review.touch — weave's harvest due-pull hook (#29).

Run: uv run python tests/test_touch.py
"""

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from bower_bird.config import Config
from bower_bird.review import ReviewStore, touch

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


def test_touch_pulls_future_due_to_today() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        future = date.today() + timedelta(days=30)
        store.apply_grade("abc", "strong", today=date.today())
        store._entries["abc"] = store._entries["abc"].model_copy(
            update={"due": future.isoformat()}
        )
        store.save()

        count = touch(store, ["abc"])
        check(count == 1, f"one node touched, got {count}")
        reloaded = ReviewStore.load(cfg).get("abc")
        check(
            reloaded.due == date.today().isoformat(),  # type: ignore[union-attr]
            f"future due pulled to today: {reloaded.due}",  # type: ignore[union-attr]
        )


def test_touch_leaves_past_due_unchanged() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        past = date.today() - timedelta(days=5)
        store._entries["abc"] = store._entries["abc"].model_copy(
            update={"due": past.isoformat()}
        )
        store.save()

        touch(store, ["abc"])
        reloaded = ReviewStore.load(cfg).get("abc")
        check(
            reloaded.due == past.isoformat(),  # type: ignore[union-attr]
            f"already-past due left unchanged (min semantics): {reloaded.due}",  # type: ignore[union-attr]
        )


def test_touch_leaves_box_and_history_untouched() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        store.apply_grade("abc", "strong", today=date.today())
        before = store.get("abc")

        touch(store, ["abc"])
        after = store.get("abc")
        check(after.box == before.box, "box untouched")  # type: ignore[union-attr]
        check(after.last_grade == before.last_grade, "last_grade untouched")  # type: ignore[union-attr]
        check(len(after.reviews) == len(before.reviews), "review history untouched")  # type: ignore[union-attr]
        check(after.retired == before.retired, "retired untouched")  # type: ignore[union-attr]
        check(after.bad_streak == before.bad_streak, "bad_streak untouched")  # type: ignore[union-attr]


def test_touch_warns_on_unknown_id_but_touches_the_rest() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("known")
        future = date.today() + timedelta(days=10)
        store._entries["known"] = store._entries["known"].model_copy(
            update={"due": future.isoformat()}
        )
        store.save()

        count = touch(store, ["known", "ghost-id"])
        check(count == 1, f"only the known id counted, got {count}")
        reloaded = ReviewStore.load(cfg).get("known")
        check(
            reloaded.due == date.today().isoformat(),  # type: ignore[union-attr]
            "known id still touched despite unknown sibling",
        )


if __name__ == "__main__":
    test_touch_pulls_future_due_to_today()
    test_touch_leaves_past_due_unchanged()
    test_touch_leaves_box_and_history_untouched()
    test_touch_warns_on_unknown_id_but_touches_the_rest()

    if _failures:
        print(f"\n{_failures} test(s) failed.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
