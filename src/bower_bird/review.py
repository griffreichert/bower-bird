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

from bower_bird.config import Config

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


def _judge(
    config: Config, question: str, model_answer: str, answer: str
) -> Grade | None:
    """Grade the answer via LLM-as-judge, printing the verdict. None if unavailable.

    The judge must never crash a pull-only review, so any failure (no API key,
    network, refusal) degrades to manual grading.
    """
    # ponytail: broad except is intentional — a judge failure falls back to
    # manual grading, never aborts the session.
    from bower_bird.llm import judge_answer

    try:
        verdict = judge_answer(question, model_answer, answer, config.model)
    except Exception as exc:  # noqa: BLE001
        print(f"  (judge unavailable: {exc} — grade manually)\n")
        return None
    print(f"  Judge: {verdict.grade.upper()} — {verdict.rationale}\n")
    return verdict.grade


def _read_grade(default: Grade | None) -> Grade | None:
    """Prompt for a grade; enter accepts ``default`` (the judge). None on EOF."""
    if default is not None:
        hint = f"  Grade [enter={default}, s/w/x=override]: "
    else:
        hint = "  Grade (s=strong / w=weak / x=wrong): "
    while True:
        try:
            raw = _prompt(hint).lower()
        except EOFError:
            return None
        if raw == "" and default is not None:
            return default
        grade = _GRADE_ALIASES.get(raw)
        if grade is not None:
            return grade
        print("  Please enter s, w, or x.")


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
            user_answer = _prompt("  Your answer: ")
        except EOFError:
            print("\npeck: session ended early.")
            break

        print(f"\n  Model answer:\n  {model_answer}\n")

        # LLM-as-judge grades the answer; the human accepts (enter) or overrides.
        default = _judge(config, test_question, model_answer, user_answer)
        grade = _read_grade(default)
        if grade is None:  # EOF at the grade prompt
            print("\npeck: session ended early.")
            store.save()
            return reviewed

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


def list_due(config: Config) -> int:
    """Print due bowers as JSON — the read half of the Claude Code peck skill.

    Claude Code calls this, quizzes the user in-chat (grading on the subscription
    model, not the API), then writes each grade back via ``--grade``.
    """
    store = ReviewStore.load(config)
    due = store.due_today()
    payloads = _load_feynman_payloads(config, {r.id for r in due})
    out = []
    for review in due:
        payload = payloads.get(review.id)
        if payload is None:
            continue
        title, question, model_answer = payload
        out.append(
            {
                "id": review.id,
                "title": title,
                "box": review.box,
                "question": question,
                "model_answer": model_answer,
            }
        )
    print(json.dumps(out, indent=2))
    return 0


def grade(config: Config, bower_id: str, raw_grade: str) -> int:
    """Apply one Leitner grade — the write half of the Claude Code peck skill."""
    g = _GRADE_ALIASES.get(raw_grade.lower())
    if g is None:
        print(f"peck: invalid grade {raw_grade!r} (use strong/weak/wrong).")
        return 2
    store = ReviewStore.load(config)
    if store.get(bower_id) is None:
        print(f"peck: unknown bower id {bower_id!r}.")
        return 2
    updated = store.apply_grade(bower_id, g)
    store.save()
    print(
        json.dumps(
            {"id": bower_id, "grade": g, "box": updated.box, "due": str(updated.due)}
        )
    )
    return 0


def main(config: Config, argv: list[str] | None = None) -> int:
    """Entry point for `peck [--list-due | --grade <id> <grade>]` (else interactive)."""
    argv = argv or []
    try:
        if argv and argv[0] == "--list-due":
            return list_due(config)
        if argv and argv[0] == "--grade":
            if len(argv) != 3:
                print("peck: usage: peck --grade <bower_id> <strong|weak|wrong>")
                return 2
            return grade(config, argv[1], argv[2])
        return peck(config)
    except KeyboardInterrupt:
        print("\npeck: interrupted.")
        return 0


if __name__ == "__main__":
    # Allow running directly: `uv run python -m bower_bird.review`
    from bower_bird.config import load_config

    try:
        _cfg = load_config()
    except RuntimeError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(_cfg))
