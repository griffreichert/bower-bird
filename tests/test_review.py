"""Tests for the review module — Leitner logic, ReviewStore, source-node scan,
teach-first / grading surface, and ShelfCensus. Pure logic / no network.

Run: uv run python tests/test_review.py
"""

import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import bower_bird.llm as llm
import bower_bird.review as review
from bower_bird.config import Config
from bower_bird.review import (
    BAD_STREAK_FLAG,
    LEITNER_LADDER,
    ReviewStore,
    ShelfCensus,
    judge_review,
    read_grade,
    scan_sources,
)
from bower_bird.schema import Review

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


_SOURCE_TEMPLATE = """\
---
title: "{title}"
id: {id}
---
# {title}

## Links
{links}

## Key ideas
{key_ideas}
{note_section}
"""


def _write_source(
    root: Path,
    node_id: str,
    title: str = "Some Source",
    key_ideas: list[str] | None = None,
    note: str = "",
    links: list[str] | None = None,
) -> Path:
    sources = root / "brain" / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    key_ideas = key_ideas if key_ideas is not None else ["idea one", "idea two"]
    ideas_block = "\n".join(f"- {i}" for i in key_ideas)
    links_block = "\n".join(f"- [[{link}]]" for link in (links or []))
    note_section = f"\n## Note\n{note}\n" if note else ""
    path = sources / f"{title.replace(' ', '_')}.md"
    path.write_text(
        _SOURCE_TEMPLATE.format(
            title=title,
            id=node_id,
            links=links_block,
            key_ideas=ideas_block,
            note_section=note_section,
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Leitner transition rules
# ---------------------------------------------------------------------------


def test_leitner_strong_advances_box() -> None:
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
        check(updated.due == expected_due, f"due after strong: {updated.due}")
        check(updated.last_grade == "strong", "last_grade recorded")


def test_leitner_weak_stays_in_box() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        today = date(2026, 6, 25)
        store.apply_grade("abc", "strong", today=today)
        store.apply_grade("abc", "strong", today=today)
        before_box = store.get("abc").box  # type: ignore[union-attr]
        check(before_box == 2, f"pre-condition: box should be 2, got {before_box}")

        updated = store.apply_grade("abc", "weak", today=today)
        check(updated.box == 2, f"weak stays at box 2, got {updated.box}")
        expected_due = (today + timedelta(days=LEITNER_LADDER[2])).isoformat()
        check(updated.due == expected_due, f"due after weak: {updated.due}")


def test_leitner_wrong_resets_to_box_zero() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        today = date(2026, 6, 25)
        for _ in range(3):
            store.apply_grade("abc", "strong", today=today)
        check(store.get("abc").box == 3, "pre-condition: box 3")  # type: ignore[union-attr]

        updated = store.apply_grade("abc", "wrong", today=today)
        check(updated.box == 0, f"wrong resets to box 0, got {updated.box}")


def test_leitner_strong_capped_at_last_rung() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        today = date(2026, 6, 25)
        max_box = len(LEITNER_LADDER) - 1
        for _ in range(max_box):
            store.apply_grade("abc", "strong", today=today)
        updated = store.apply_grade("abc", "strong", today=today)
        check(updated.box == max_box, f"strong at max box stays capped at {max_box}")


def test_leitner_history_accumulates() -> None:
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
        check(r1 is not None and r1.box == 1, f"id-1 box survived: {r1 and r1.box}")
        check(r2 is not None and r2.box == 0, f"id-2 box survived: {r2 and r2.box}")


def test_store_seed_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        today = date(2026, 6, 25)
        store.apply_grade("abc", "strong", today=today)
        seeded_again = store.seed("abc")
        check(not seeded_again, "seed returns False for existing id")
        check(store.get("abc").box == 1, "box unchanged after duplicate seed")  # type: ignore[union-attr]


def test_store_due_today_filter() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)

        today = date.today()

        store.seed("due-now")
        store.seed("future")
        store.apply_grade("future", "strong", today=today)

        due = store.due_today(today=today)
        ids = {r.id for r in due}
        check("due-now" in ids, "'due-now' is due today")
        check("future" not in ids, "'future' not due yet")


def test_review_json_validates() -> None:
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
    check(r.retired is False, "retired defaults False")
    check(r.bad_streak == 0, "bad_streak defaults 0")


def test_store_file_is_valid_json_after_save() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("x")
        store.save()
        raw = cfg.review_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        check("x" in data, "node key present in JSON")
        check("due" in data["x"], "due field present")


# ---------------------------------------------------------------------------
# Enrol-by-sync (#18): brain/sources/ nodes enrol into the store on sight.
# ---------------------------------------------------------------------------


def test_sync_sources_enrols_new_nodes() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        _write_source(root, "src-1", title="Source One")
        _write_source(root, "src-2", title="Source Two")

        store = ReviewStore.load(cfg)
        added = store.sync_sources(cfg)
        check(set(added) == {"src-1", "src-2"}, f"both sources enrolled: {added}")
        r1 = store.get("src-1")
        check(r1 is not None and r1.box == 0, "enrolled at box 0")
        check(r1 is not None and r1.due <= date.today().isoformat(), "enrolled due now")


def test_sync_sources_idempotent() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        _write_source(root, "src-1", title="Source One")

        store = ReviewStore.load(cfg)
        store.sync_sources(cfg)
        store.apply_grade("src-1", "strong", today=date(2026, 6, 25))
        added_again = store.sync_sources(cfg)
        check(added_again == [], "already-enrolled node not re-added")
        check(store.get("src-1").box == 1, "graded state untouched by re-sync")  # type: ignore[union-attr]


def test_scan_sources_parses_key_ideas_seed_links() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        _write_source(
            root,
            "src-1",
            title="Rich Source",
            key_ideas=["caches go stale", "invalidation is hard"],
            note="my own gloss on it",
            links=["Caching", "Distributed systems"],
        )
        cfg = _config(root)
        nodes = scan_sources(cfg)
        node = nodes["src-1"]
        check(node.title == "Rich Source", f"title parsed: {node.title}")
        check(
            node.key_ideas == ["caches go stale", "invalidation is hard"],
            f"key ideas parsed: {node.key_ideas}",
        )
        check(node.seed == "my own gloss on it", f"seed parsed: {node.seed}")
        check(
            node.linked_titles == ["Caching", "Distributed systems"],
            f"linked titles parsed: {node.linked_titles}",
        )


def test_scan_sources_skips_nodes_without_id() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        sources = root / "brain" / "sources"
        sources.mkdir(parents=True)
        (sources / "no-id.md").write_text(
            '---\ntitle: "No Id"\n---\n# No Id\n', encoding="utf-8"
        )
        cfg = _config(root)
        nodes = scan_sources(cfg)
        check(nodes == {}, f"node without id: is skipped, got {nodes}")


# ---------------------------------------------------------------------------
# Teach-first (#18): empty history → key ideas only, due tomorrow, box 0.
# ---------------------------------------------------------------------------


def test_teach_bumps_due_tomorrow_keeps_box() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        today = date(2026, 6, 25)
        updated = store.teach("abc", today=today)
        check(updated.box == 0, "teach leaves box at 0")
        check(updated.reviews == [], "teach adds no history entry")
        check(
            updated.due == (today + timedelta(days=1)).isoformat(),
            f"teach bumps due to tomorrow: {updated.due}",
        )


def test_session_orders_real_reviews_before_teach_cards() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        today = date.today()
        # never-reviewed (teach), due today (seed()'s default)
        store.seed("teach-1")
        # reviewed once (real), due today too (wrong resets due to today+1d ladder[0]
        # from 5 days ago) but should still sort ahead of the teach card
        store.seed("real-1")
        store.apply_grade("real-1", "wrong", today=today - timedelta(days=5))
        session = store.session(today=today)
        ids = [r.id for r in session]
        check(ids == ["real-1", "teach-1"], f"real review ordered before teach: {ids}")


def test_session_caps_at_ten() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        for i in range(15):
            store.seed(f"id-{i}")
        session = store.session()
        check(len(session) == 10, f"session capped at 10, got {len(session)}")


def test_session_oldest_due_first_within_group() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        today = date(2026, 6, 25)
        store.seed("late")
        store.seed("early")
        # both teach cards (never reviewed) but seeded due differs via manual edit
        store._entries["late"] = store._entries["late"].model_copy(
            update={"due": (today).isoformat()}
        )
        store._entries["early"] = store._entries["early"].model_copy(
            update={"due": (today - timedelta(days=3)).isoformat()}
        )
        session = store.session(today=today)
        ids = [r.id for r in session]
        check(ids == ["early", "late"], f"oldest due first: {ids}")


# ---------------------------------------------------------------------------
# Grading surface: b (bad question) streak + flag, d (retire), idk → wrong
# ---------------------------------------------------------------------------


def test_mark_bad_does_not_touch_box_or_due() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        before = store.get("abc")
        updated = store.mark_bad("abc")
        check(updated.box == before.box, "box untouched by bad")  # type: ignore[union-attr]
        check(updated.due == before.due, "due untouched by bad")  # type: ignore[union-attr]
        check(updated.bad_streak == 1, f"bad_streak incremented: {updated.bad_streak}")


def test_bad_streak_flags_at_threshold() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        store.mark_bad("abc")
        updated = store.mark_bad("abc")
        check(
            updated.bad_streak == BAD_STREAK_FLAG,
            "two bad calls hit the flag threshold",
        )


def test_real_grade_resets_bad_streak() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        store.mark_bad("abc")
        store.mark_bad("abc")
        updated = store.apply_grade("abc", "strong", today=date(2026, 6, 25))
        check(
            updated.bad_streak == 0,
            f"real grade resets bad_streak: {updated.bad_streak}",
        )


def test_retire_excludes_from_due_forever() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        today = date.today()
        check("abc" in {r.id for r in store.due_today(today)}, "due before retiring")
        updated = store.retire("abc")
        check(updated.retired is True, "retired flag set")
        check(
            "abc" not in {r.id for r in store.due_today(today)},
            "excluded from due after retiring",
        )
        far_future = today + timedelta(days=3650)
        check(
            "abc" not in {r.id for r in store.due_today(far_future)},
            "retired stays excluded forever, not just today",
        )


def test_grade_cli_bad_and_retire_letters() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        store.seed("abc")
        store.save()

        review.grade(cfg, "abc", "b")
        r = ReviewStore.load(cfg).get("abc")
        check(r.bad_streak == 1, "grade CLI 'b' marks bad")  # type: ignore[union-attr]

        review.grade(cfg, "abc", "d")
        r = ReviewStore.load(cfg).get("abc")
        check(r.retired is True, "grade CLI 'd' retires")  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# ShelfCensus (#20)
# ---------------------------------------------------------------------------


def test_shelf_census_buckets() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        store = ReviewStore.load(cfg)
        today = date(2026, 6, 25)

        store.seed("stranger-1")  # box 0

        store.seed("climb-1")
        store.apply_grade("climb-1", "strong", today=today)  # box 1

        store.seed("known-1")
        for _ in range(4):
            store.apply_grade("known-1", "strong", today=today)  # box 4

        store.seed("retired-1")
        store.retire("retired-1")

        census = ShelfCensus.from_store(store)
        check(census.total == 4, f"total nodes: {census.total}")
        check(census.strangers == 1, f"strangers: {census.strangers}")
        check(census.climbing == 1, f"climbing: {census.climbing}")
        check(census.known == 1, f"known: {census.known}")
        check(census.retired == 1, f"retired: {census.retired}")
        rendered = census.render()
        check(rendered.startswith("shelf: 4 nodes"), f"render line: {rendered}")


# ---------------------------------------------------------------------------
# peck judge / question generation — no network: stubbed
# ---------------------------------------------------------------------------


def _queue_prompts(answers: list[str]) -> None:
    it = iter(answers)
    review.prompt_user = lambda msg: next(it)  # type: ignore[assignment]


def test_read_grade_enter_accepts_judge_default() -> None:
    _queue_prompts([""])
    check(read_grade("strong") == "strong", "enter accepts judge default")


def test_read_grade_override_beats_default() -> None:
    _queue_prompts(["x"])
    check(read_grade("strong") == "wrong", "override wins over default")


def test_read_grade_no_default_requires_explicit() -> None:
    _queue_prompts(["", "w"])
    check(read_grade(None) == "weak", "no default → enter reprompts, then w")


def test_read_action_recognises_bad_and_retire() -> None:
    _queue_prompts(["b"])
    check(review.read_action(None) == "bad", "'b' → bad")
    _queue_prompts(["d"])
    check(review.read_action(None) == "retire", "'d' → retire")


def testjudge_review_returns_grade_from_verdict() -> None:
    class _V:
        grade = "weak"
        rationale = "missed the core point"

    orig = llm.judge_answer
    llm.judge_answer = lambda q, ki, seed, a, model, linked=None: _V()  # type: ignore[assignment]
    try:
        with tempfile.TemporaryDirectory() as d:
            cfg = _config(Path(d))
            node = review.SourceNode(id="x", title="X", key_ideas=["a"])
            check(
                judge_review(cfg, "q", node, 0, "ans") == "weak", "judge grade surfaced"
            )
    finally:
        llm.judge_answer = orig


def testjudge_review_failure_falls_back_to_manual() -> None:
    def _boom(q, ki, seed, a, model, linked=None):
        raise RuntimeError("no API key")

    orig = llm.judge_answer
    llm.judge_answer = _boom  # type: ignore[assignment]
    try:
        with tempfile.TemporaryDirectory() as d:
            cfg = _config(Path(d))
            node = review.SourceNode(id="x", title="X", key_ideas=["a"])
            check(judge_review(cfg, "q", node, 0, "ans") is None, "judge error → None")
    finally:
        llm.judge_answer = orig


def test_generate_question_failure_falls_back_to_none() -> None:
    def _boom(key_ideas, seed, linked, box, model):
        raise RuntimeError("no API key")

    orig = llm.generate_question
    llm.generate_question = _boom  # type: ignore[assignment]
    try:
        with tempfile.TemporaryDirectory() as d:
            cfg = _config(Path(d))
            node = review.SourceNode(id="x", title="X", key_ideas=["a"])
            check(
                review.question_for_node(cfg, node, 0) is None,
                "question generator error → None (self-quiz fallback)",
            )
    finally:
        llm.generate_question = orig


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
    test_sync_sources_enrols_new_nodes()
    test_sync_sources_idempotent()
    test_scan_sources_parses_key_ideas_seed_links()
    test_scan_sources_skips_nodes_without_id()
    test_teach_bumps_due_tomorrow_keeps_box()
    test_session_orders_real_reviews_before_teach_cards()
    test_session_caps_at_ten()
    test_session_oldest_due_first_within_group()
    test_mark_bad_does_not_touch_box_or_due()
    test_bad_streak_flags_at_threshold()
    test_real_grade_resets_bad_streak()
    test_retire_excludes_from_due_forever()
    test_grade_cli_bad_and_retire_letters()
    test_shelf_census_buckets()
    test_read_grade_enter_accepts_judge_default()
    test_read_grade_override_beats_default()
    test_read_grade_no_default_requires_explicit()
    test_read_action_recognises_bad_and_retire()
    testjudge_review_returns_grade_from_verdict()
    testjudge_review_failure_falls_back_to_manual()
    test_generate_question_failure_falls_back_to_none()

    if _failures:
        print(f"\n{_failures} test(s) failed.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
