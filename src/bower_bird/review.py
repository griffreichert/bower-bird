"""Spaced-repetition review store + peck quiz loop.

``_review.json`` lives in the vault (``BowerBird/_review.json``), keyed by each
bower's immutable ``id:``. It is the only file this module writes at runtime; it
never touches anything else in the vault.

Leitner ladder: [1, 3, 7, 16, 35, 75] days.
  - strong  → box + 1 (capped at last rung), due = today + ladder[box]
  - weak    → stay (box unchanged),           due = today + ladder[box]
  - wrong   → box 0,                          due = today + ladder[0]

``peck`` is pull-only: it is never called from the cron pass.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import Config

# ---------------------------------------------------------------------------
# Leitner constants
# ---------------------------------------------------------------------------

LEITNER_LADDER: list[int] = [1, 3, 7, 16, 35, 75]
"""Days between reviews for each Leitner box (0-indexed)."""

Grade = Literal["strong", "weak", "wrong"]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ReviewEntry(BaseModel):
    """One past review event — stored in the history list."""

    model_config = ConfigDict(extra="forbid")

    reviewed_on: str = Field(description="ISO date of the review.")
    grade: Grade = Field(description="strong | weak | wrong.")
    box_before: int = Field(description="Box the bower was in before this review.")
    box_after: int = Field(description="Box it moved to after grading.")


class Review(BaseModel):
    """Per-bower spaced-rep state, keyed by the bower's immutable id."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Bower id — immutable, matches frontmatter id:.")
    due: str = Field(
        description="ISO date when next review is due (due <= today → quiz it)."
    )
    box: int = Field(
        default=0,
        description="Current Leitner box (0 = just minted / wrong, max = 5).",
    )
    last_grade: Grade | None = Field(
        default=None,
        description="Grade from the most recent review, or None if never reviewed.",
    )
    reviews: list[ReviewEntry] = Field(
        default_factory=list,
        description="Full grading history, oldest first.",
    )


# ---------------------------------------------------------------------------
# ReviewStore — load / save / mutate
# ---------------------------------------------------------------------------


class ReviewStore:
    """Validated in-memory view of ``_review.json``.

    Load with ``ReviewStore.load(config)``, mutate, then ``save()``.
    """

    def __init__(self, config: Config, entries: dict[str, Review]) -> None:
        self._config = config
        self._entries: dict[str, Review] = entries

    # -- persistence ---------------------------------------------------------

    @classmethod
    def load(cls, config: Config) -> ReviewStore:
        """Load from the vault, creating an empty store if the file doesn't exist."""
        path = config.review_path
        if not path.exists():
            return cls(config, {})
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries: dict[str, Review] = {}
        for bower_id, data in raw.items():
            entries[bower_id] = Review.model_validate(data)
        return cls(config, entries)

    def save(self) -> None:
        """Atomically write back to the vault."""
        path = self._config.review_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {bid: r.model_dump() for bid, r in self._entries.items()}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(path)

    # -- seeding -------------------------------------------------------------

    def seed(self, bower_id: str) -> bool:
        """Seed a newly minted bower as due today (box 0).

        Idempotent: if the bower is already in the store, does nothing and returns
        False. Returns True when a new entry is created.
        """
        if bower_id in self._entries:
            return False
        self._entries[bower_id] = Review(
            id=bower_id,
            due=date.today().isoformat(),
            box=0,
            last_grade=None,
            reviews=[],
        )
        return True

    # -- querying ------------------------------------------------------------

    def due_today(self, today: date | None = None) -> list[Review]:
        """Return all bowers whose ``due`` date is on or before ``today``."""
        pivot = (today or date.today()).isoformat()
        return [r for r in self._entries.values() if r.due <= pivot]

    def get(self, bower_id: str) -> Review | None:
        return self._entries.get(bower_id)

    # -- grading -------------------------------------------------------------

    def apply_grade(
        self, bower_id: str, grade: Grade, today: date | None = None
    ) -> Review:
        """Apply a Leitner grade, update box + due, and record history.

        Raises KeyError if ``bower_id`` is not in the store.
        """
        review = self._entries[bower_id]
        pivot = today or date.today()

        box_before = review.box
        if grade == "strong":
            box_after = min(box_before + 1, len(LEITNER_LADDER) - 1)
        elif grade == "weak":
            box_after = box_before
        else:  # wrong
            box_after = 0

        due = (pivot + timedelta(days=LEITNER_LADDER[box_after])).isoformat()

        entry = ReviewEntry(
            reviewed_on=pivot.isoformat(),
            grade=grade,
            box_before=box_before,
            box_after=box_after,
        )
        updated = Review(
            id=bower_id,
            due=due,
            box=box_after,
            last_grade=grade,
            reviews=[*review.reviews, entry],
        )
        self._entries[bower_id] = updated
        return updated


# ---------------------------------------------------------------------------
# peck — interactive quiz loop
# ---------------------------------------------------------------------------

_GRADE_ALIASES: dict[str, Grade] = {
    "s": "strong",
    "strong": "strong",
    "w": "weak",
    "weak": "weak",
    "x": "wrong",
    "wrong": "wrong",
    "0": "wrong",
}


def _prompt(msg: str) -> str:
    """Print ``msg`` and return stripped input. Raises EOFError on Ctrl-D."""
    print(msg, end="", flush=True)
    return input().strip()


def peck(config: Config) -> int:
    """Run an interactive spaced-rep quiz session over due bowers.

    Loads the review store, selects bowers due today, and for each one:
      1. Shows the test_question (Feynman Prompt 2 — the question).
      2. Waits for the user to type their answer.
      3. Shows the model_answer (Feynman Prompt 3 — grading target).
      4. Asks for a grade: strong / weak / wrong (s/w/x).

    Grades are written back after each item so a Ctrl-D mid-session doesn't
    lose work. Returns the number of bowers reviewed.

    The Feynman payload lives in the bower note file, so we locate each bower
    by scanning brain/bowers/ for a note whose frontmatter ``id:`` matches.
    """
    store = ReviewStore.load(config)
    due = store.due_today()

    if not due:
        print("peck: nothing due today. Come back later.")
        return 0

    print(f"peck: {len(due)} bower(s) due today.\n")

    # Build a map from bower_id → (test_question, model_answer) by scanning the
    # bowers directory. We only load notes for items that are actually due.
    payload_map = _load_feynman_payloads(config, {r.id for r in due})

    reviewed = 0
    for review in due:
        payload = payload_map.get(review.id)
        if payload is None:
            print(
                f"  [skip] bower id {review.id!r} "
                "— note not found or no concept block.\n"
            )
            continue

        title, test_question, model_answer = payload
        print(f"{'─' * 60}")
        print(f"  Bower: {title}")
        print(f"  Box:   {review.box}  |  Due: {review.due}")
        print(f"{'─' * 60}")
        print(f"\n  Q: {test_question}\n")

        try:
            _prompt("  Your answer: ")
        except EOFError:
            print("\npeck: session ended early.")
            break

        print(f"\n  Model answer:\n  {model_answer}\n")

        grade: Grade | None = None
        while grade is None:
            try:
                raw = _prompt("  Grade (s=strong / w=weak / x=wrong): ").lower()
            except EOFError:
                print("\npeck: session ended early.")
                store.save()
                return reviewed
            grade = _GRADE_ALIASES.get(raw)
            if grade is None:
                print("  Please enter s, w, or x.")

        updated = store.apply_grade(review.id, grade)
        store.save()  # persist after each item — safe against mid-session exit
        reviewed += 1
        print(f"  → box {updated.box}, next due {updated.due}\n")

    print(f"peck: reviewed {reviewed} bower(s).")
    return reviewed


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

import re  # noqa: E402  (moved import here to keep top-of-module clean)

_ID_RE = re.compile(r"^id:\s*(.+)$", re.MULTILINE)
_TITLE_RE = re.compile(r"^title:\s*\"?(.+?)\"?\s*$", re.MULTILINE)
_QUESTION_RE = re.compile(r"\*\*Test question:\*\*\s*(.+)")
_ANSWER_RE = re.compile(r"\*\*Model answer:\*\*\s*(.+)")


def _load_feynman_payloads(
    config: Config,
    ids: set[str],
) -> dict[str, tuple[str, str, str]]:
    """Scan brain/bowers/ for due bowers, extract (title, test_question, model_answer).

    Returns a dict keyed by bower_id.
    """
    result: dict[str, tuple[str, str, str]] = {}
    if not config.notes_dir.is_dir():
        return result

    for path in config.notes_dir.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        id_m = _ID_RE.search(text)
        if id_m is None:
            continue
        bower_id = id_m.group(1).strip()
        if bower_id not in ids:
            continue
        title_m = _TITLE_RE.search(text)
        title = title_m.group(1) if title_m else path.stem
        q_m = _QUESTION_RE.search(text)
        a_m = _ANSWER_RE.search(text)
        if q_m and a_m:
            result[bower_id] = (title, q_m.group(1).strip(), a_m.group(1).strip())

    return result


def main(config: Config) -> int:
    """Entry point called from __main__.py for `python -m bower_bird peck`."""
    try:
        return peck(config)
    except KeyboardInterrupt:
        print("\npeck: interrupted.")
        return 0


if __name__ == "__main__":
    # Allow running directly: `uv run python -m bower_bird.review`
    from .config import load_config

    try:
        _cfg = load_config()
    except RuntimeError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(_cfg))
