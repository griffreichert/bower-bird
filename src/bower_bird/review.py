"""Spaced-repetition review store + peck quiz loop.

``_review.json`` lives in the vault (``BowerBird/_review.json``), keyed by each
source node's immutable ``id:`` (``brain/sources/*.md`` only — bowers/people/
tools never enrol). It is the only file this module writes at runtime; it
never touches anything else in the vault.

Leitner ladder: [1, 3, 7, 16, 35, 75] days.
  - strong  → box + 1 (capped at last rung), due = today + ladder[box]
  - weak    → stay (box unchanged),           due = today + ladder[box]
  - wrong   → box 0,                          due = today + ladder[0]

Unit = one card per source node (#18). A node enrols the first time it's seen
due (``sync_sources``) rather than at ingest time — no ingest-side coupling.
The stored Feynman payload is dead: questions are generated fresh at quiz
time from the node's ``## Key ideas`` (+ ``## Note`` seed thought), depth
scaled to the Leitner box. A never-reviewed node (empty history) is a
teach-first card: key ideas are shown, no question/judge/grade, due bumps to
tomorrow.

``peck`` is pull-only: it is never called from the cron pass.
"""

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from bower_bird.config import Config

# ---------------------------------------------------------------------------
# Leitner constants
# ---------------------------------------------------------------------------

LEITNER_LADDER: list[int] = [1, 3, 7, 16, 35, 75]
"""Days between reviews for each Leitner box (0-indexed)."""

Grade = Literal["strong", "weak", "wrong"]

#: Session shape (#18): cap 10 cards/session, oldest-due first, real reviews
#: (non-empty history) ordered before teach cards (empty history).
SESSION_CAP = 10

#: A bad_streak at or above this flags thin key ideas for weave fodder.
BAD_STREAK_FLAG = 2


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ReviewEntry(BaseModel):
    """One past review event — stored in the history list."""

    model_config = ConfigDict(extra="forbid")

    reviewed_on: str = Field(description="ISO date of the review.")
    grade: Grade = Field(description="strong | weak | wrong.")
    box_before: int = Field(description="Box the node was in before this review.")
    box_after: int = Field(description="Box it moved to after grading.")


class Review(BaseModel):
    """Per-node spaced-rep state, keyed by the source node's immutable id."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Source node id — immutable, matches frontmatter id:.")
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
        description="Full grading history, oldest first. Empty = teach-first card.",
    )
    retired: bool = Field(
        default=False,
        description="Retired via the 'd' grading letter — excluded from due "
        "forever, stays in the store/census.",
    )
    bad_streak: int = Field(
        default=0,
        description="Consecutive 'bad question' flags with no intervening real "
        "grade. Reset to 0 by any real grade. >= BAD_STREAK_FLAG flags the "
        "node's key ideas as thin (weave fodder).",
    )


# ---------------------------------------------------------------------------
# ReviewStore — load / save / mutate
# ---------------------------------------------------------------------------

_ReviewMap = TypeAdapter(dict[str, Review])


class ReviewStore:
    """Validated in-memory view of ``_review.json``.

    Load with ``ReviewStore.load(config)``, mutate, then ``save()``.
    """

    def __init__(self, config: Config, entries: dict[str, Review]) -> None:
        self._config = config
        self._entries: dict[str, Review] = entries

    # -- persistence ---------------------------------------------------------

    @classmethod
    def load(cls, config: Config) -> "ReviewStore":
        """Load from the vault, creating an empty store if the file doesn't exist."""
        path = config.review_path
        if not path.exists():
            return cls(config, {})
        entries = _ReviewMap.validate_json(path.read_text(encoding="utf-8"))
        return cls(config, entries)

    def save(self) -> None:
        """Atomically write back to the vault."""
        path = self._config.review_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(_ReviewMap.dump_json(self._entries, indent=2))
        tmp.replace(path)

    # -- seeding / enrolment ---------------------------------------------------

    def seed(self, node_id: str) -> bool:
        """Seed a newly-seen node as due today (box 0).

        Idempotent: if the id is already in the store, does nothing and
        returns False. Returns True when a new entry is created.
        """
        if node_id in self._entries:
            return False
        self._entries[node_id] = Review(
            id=node_id,
            due=date.today().isoformat(),
            box=0,
            last_grade=None,
            reviews=[],
        )
        return True

    def sync_sources(self, config: Config) -> list[str]:
        """Enrol any brain/sources/ node not yet in the store (#18, enrol-by-sync).

        No ingest-side coupling: this is the *only* place a source's id enters
        the store. Returns the list of newly-enrolled ids.
        """
        added: list[str] = []
        for node_id in scan_sources(config):
            if self.seed(node_id):
                added.append(node_id)
        return added

    # -- querying ------------------------------------------------------------

    def due_today(self, today: date | None = None) -> list[Review]:
        """Return all non-retired nodes whose ``due`` date is on or before ``today``."""
        pivot = (today or date.today()).isoformat()
        return [r for r in self._entries.values() if not r.retired and r.due <= pivot]

    def get(self, node_id: str) -> Review | None:
        return self._entries.get(node_id)

    def session(
        self, today: date | None = None, cap: int = SESSION_CAP
    ) -> list[Review]:
        """Due nodes for one peck session: real reviews before teach cards,
        oldest-due-first within each group, capped at ``cap``."""
        due = sorted(self.due_today(today), key=lambda r: r.due)
        real = [r for r in due if r.reviews]
        teach = [r for r in due if not r.reviews]
        return (real + teach)[:cap]

    # -- grading -------------------------------------------------------------

    def apply_grade(
        self, node_id: str, grade: Grade, today: date | None = None
    ) -> Review:
        """Apply a Leitner grade, update box + due, and record history.

        Any real grade resets ``bad_streak`` to 0. Raises KeyError if
        ``node_id`` is not in the store.
        """
        review = self._entries[node_id]
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
            id=node_id,
            due=due,
            box=box_after,
            last_grade=grade,
            reviews=[*review.reviews, entry],
            retired=review.retired,
            bad_streak=0,
        )
        self._entries[node_id] = updated
        return updated

    def teach(self, node_id: str, today: date | None = None) -> Review:
        """Teach-first completion: no question/judge/grade — due bumps to
        tomorrow, box + history + bad_streak untouched."""
        review = self._entries[node_id]
        pivot = today or date.today()
        updated = review.model_copy(
            update={"due": (pivot + timedelta(days=1)).isoformat()}
        )
        self._entries[node_id] = updated
        return updated

    def mark_bad(self, node_id: str) -> Review:
        """'b' = bad question: no grade, box/due untouched, bad_streak += 1."""
        review = self._entries[node_id]
        updated = review.model_copy(update={"bad_streak": review.bad_streak + 1})
        self._entries[node_id] = updated
        return updated

    def retire(self, node_id: str) -> Review:
        """'d' = retire: excluded from due forever, stays in the store/census."""
        review = self._entries[node_id]
        updated = review.model_copy(update={"retired": True})
        self._entries[node_id] = updated
        return updated


# ---------------------------------------------------------------------------
# ShelfCensus — one bucket model, rendered identically everywhere (#20)
# ---------------------------------------------------------------------------


class ShelfCensus(BaseModel):
    """Nodes bucketed by Leitner box, computed from a ReviewStore.

    Bucket thresholds are defined ONCE here — every surface (peck end-of-session,
    list_due, future /due + recap renderers) renders this same model; no caller
    recomputes the buckets.
    """

    model_config = ConfigDict(extra="forbid")

    total: int = 0
    strangers: int = 0  # box 0
    climbing: int = 0  # box 1-3
    known: int = 0  # box 4-5
    retired: int = 0

    @classmethod
    def from_store(cls, store: ReviewStore) -> "ShelfCensus":
        strangers = climbing = known = retired = 0
        for r in store._entries.values():
            if r.retired:
                retired += 1
            elif r.box == 0:
                strangers += 1
            elif r.box <= 3:
                climbing += 1
            else:
                known += 1
        return cls(
            total=len(store._entries),
            strangers=strangers,
            climbing=climbing,
            known=known,
            retired=retired,
        )

    def render(self) -> str:
        return (
            f"shelf: {self.total} nodes — {self.strangers} strangers · "
            f"{self.climbing} climbing · {self.known} known · {self.retired} retired"
        )


# ---------------------------------------------------------------------------
# Source node scanning (brain/sources/) — key ideas, seed, linked titles
# ---------------------------------------------------------------------------

_ID_RE = re.compile(r"^id:\s*(.+)$", re.MULTILINE)
_TITLE_RE = re.compile(r'^title:\s*"?(.+?)"?\s*$', re.MULTILINE)
_KEY_IDEAS_RE = re.compile(
    r"^## Key ideas\n(.*?)(?=\n## |\Z)", re.MULTILINE | re.DOTALL
)
_NOTE_RE = re.compile(r"^## Note\n(.*?)(?=\n## |\Z)", re.MULTILINE | re.DOTALL)
_LINKS_RE = re.compile(r"^## Links\n(.*?)(?=\n## |\Z)", re.MULTILINE | re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_BULLET_RE = re.compile(r"^-\s+(.*)$", re.MULTILINE)


@dataclass
class SourceNode:
    """A quizzable brain/sources/ node — plumbing, not a validated contract."""

    id: str
    title: str
    key_ideas: list[str] = field(default_factory=list)
    seed: str = ""
    linked_titles: list[str] = field(default_factory=list)


def parse_source_node(path: Path) -> SourceNode | None:
    """Parse one sources/ note. None if it has no ``id:`` frontmatter."""
    text = path.read_text(encoding="utf-8")
    id_m = _ID_RE.search(text)
    if id_m is None:
        return None
    title_m = _TITLE_RE.search(text)
    title = title_m.group(1) if title_m else path.stem

    key_ideas: list[str] = []
    ki_m = _KEY_IDEAS_RE.search(text)
    if ki_m:
        key_ideas = [b.strip() for b in _BULLET_RE.findall(ki_m.group(1)) if b.strip()]

    seed = ""
    note_m = _NOTE_RE.search(text)
    if note_m:
        seed = note_m.group(1).strip()

    linked_titles: list[str] = []
    links_m = _LINKS_RE.search(text)
    if links_m:
        linked_titles = [
            m.group(1).strip() for m in _WIKILINK_RE.finditer(links_m.group(1))
        ]

    return SourceNode(
        id=id_m.group(1).strip(),
        title=title,
        key_ideas=key_ideas,
        seed=seed,
        linked_titles=linked_titles,
    )


def scan_sources(config: Config) -> dict[str, SourceNode]:
    """Scan brain/sources/ for every node with an ``id:``. Keyed by id."""
    result: dict[str, SourceNode] = {}
    if not config.sources_dir.is_dir():
        return result
    for path in sorted(config.sources_dir.rglob("*.md")):
        node = parse_source_node(path)
        if node is not None:
            result[node.id] = node
    return result


# ---------------------------------------------------------------------------
# peck — interactive quiz loop
# ---------------------------------------------------------------------------

_GRADE_ALIASES: dict[str, str] = {
    "s": "strong",
    "strong": "strong",
    "w": "weak",
    "weak": "weak",
    "x": "wrong",
    "wrong": "wrong",
    "0": "wrong",
    "b": "bad",
    "bad": "bad",
    "d": "retire",
    "retire": "retire",
}


def prompt_user(msg: str) -> str:
    """Print ``msg`` and return stripped input. Raises EOFError on Ctrl-D."""
    print(msg, end="", flush=True)
    return input().strip()


def render_key_ideas(node: SourceNode) -> None:
    """Teach surface: the node's key ideas (+ seed thought). No question."""
    print("  Key ideas:")
    if node.key_ideas:
        for idea in node.key_ideas:
            print(f"   - {idea}")
    else:
        print("   (none distilled yet)")
    if node.seed:
        print(f"  Seed: {node.seed}")
    print()


def question_for_node(config: Config, node: SourceNode, box: int) -> str | None:
    """Generate a fresh, box-appropriate question. None if the LLM is unavailable.

    Mirrors the judge-unavailable fallback style: never crash a pull-only
    review, degrade to a self-quiz over the key ideas.
    """
    from bower_bird.llm import generate_question

    linked = node.linked_titles if box >= 4 else []
    try:
        result = generate_question(node.key_ideas, node.seed, linked, box, config.llm)
    except Exception as exc:  # noqa: BLE001 — never abort the session
        print(f"  (question generator unavailable: {exc} — self-quiz below)\n")
        return None
    return result.question


def judge_review(
    config: Config,
    question: str,
    node: SourceNode,
    box: int,
    answer: str,
) -> Grade | None:
    """Grade the answer via LLM-as-judge, printing the verdict. None if unavailable.

    Grades against the node's key ideas (+ seed) as ground truth — the full
    node body is never sent. Linked titles are included at application depth
    (box 4-5) so connection answers get credit. The judge must never crash a
    pull-only review; any failure degrades to manual grading.
    """
    from bower_bird.llm import judge_answer

    linked = node.linked_titles if box >= 4 else []
    try:
        verdict = judge_answer(
            question, node.key_ideas, node.seed, answer, config.llm, linked
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  (judge unavailable: {exc} — grade manually)\n")
        return None
    print(f"  Judge: {verdict.grade.upper()} — {verdict.rationale}\n")
    return verdict.grade


def read_action(default: Grade | None) -> str | None:
    """Prompt for a grading action; enter accepts ``default`` (the judge).

    Returns "strong" | "weak" | "wrong" | "bad" | "retire", or None on EOF.
    """
    if default is not None:
        hint = f"  Grade [enter={default}, s/w/x=override, b=bad q, d=retire]: "
    else:
        hint = "  Grade (s=strong / w=weak / x=wrong, b=bad q, d=retire): "
    while True:
        try:
            raw = prompt_user(hint).lower()
        except EOFError:
            return None
        if raw == "" and default is not None:
            return default
        action = _GRADE_ALIASES.get(raw)
        if action is not None:
            return action
        print("  Please enter s, w, x, b, or d.")


# Kept for the test suite's existing entry point (accepts only s/w/x, no
# default when a grade default isn't given).
def read_grade(default: Grade | None) -> Grade | None:
    action = read_action(default)
    if action in ("strong", "weak", "wrong"):
        return action  # type: ignore[return-value]
    return None


def peck(config: Config) -> int:
    """Run an interactive spaced-rep quiz session over due source nodes.

    Loads the review store, enrols any not-yet-seen source (sync), selects a
    capped session (real reviews before teach cards, oldest-due-first), and
    for each entry:
      - teach-first (empty history): show key ideas, bump due to tomorrow.
      - quiz: generate a fresh box-appropriate question, wait for an answer,
        judge it (advisory), grade with s/w/x/b/d.

    Grades are written back after each item so a Ctrl-D mid-session doesn't
    lose work. Returns the number of cards graded (bad/retire don't count).
    """
    store = ReviewStore.load(config)
    if store.sync_sources(config):
        store.save()

    session = store.session()
    if not session:
        print("peck: nothing due today. Come back later.")
        print(ShelfCensus.from_store(store).render())
        return 0

    sources = scan_sources(config)
    print(f"peck: {len(session)} node(s) due today.\n")

    reviewed = 0
    for review in session:
        node = sources.get(review.id)
        if node is None:
            print(f"  [skip] source id {review.id!r} — node not found.\n")
            continue

        print(f"{'─' * 60}")
        print(f"  {node.title}")
        print(f"  Box:   {review.box}  |  Due: {review.due}")
        print(f"{'─' * 60}")

        if not review.reviews:  # teach-first
            render_key_ideas(node)
            updated = store.teach(review.id)
            store.save()
            print(f"  → teach card; due {updated.due}\n")
            continue

        question = question_for_node(config, node, review.box)
        if question is None:
            render_key_ideas(node)
            try:
                answer = prompt_user("  Self-quiz — your answer (grade yourself): ")
            except EOFError:
                print("\npeck: session ended early.")
                store.save()
                return reviewed
            action = read_action(None)
            if action is None:
                print("\npeck: session ended early.")
                store.save()
                return reviewed
        else:
            print(f"\n  Q: {question}\n")
            try:
                answer = prompt_user("  Your answer: ")
            except EOFError:
                print("\npeck: session ended early.")
                store.save()
                return reviewed

            if not answer or answer.lower() == "idk":
                # blank / idk = auto-wrong, skip the judge, straight to re-teach.
                updated = store.apply_grade(review.id, "wrong")
                store.save()
                reviewed += 1
                print(f"  → wrong (blank/idk), next due {updated.due}\n")
                render_key_ideas(node)
                continue

            default = judge_review(config, question, node, review.box, answer)
            action = read_action(default)
            if action is None:
                print("\npeck: session ended early.")
                store.save()
                return reviewed

        if action == "bad":
            updated = store.mark_bad(review.id)
            store.save()
            print(f"  → bad question logged (streak {updated.bad_streak})")
            if updated.bad_streak >= BAD_STREAK_FLAG:
                print("  ⚑ thin key ideas — weave fodder")
            print()
            continue
        if action == "retire":
            store.retire(review.id)
            store.save()
            print("  → retired\n")
            continue

        grade: Grade = action  # type: ignore[assignment]
        updated = store.apply_grade(review.id, grade)
        store.save()
        reviewed += 1
        print(f"  → box {updated.box}, next due {updated.due}\n")
        if grade == "wrong":
            render_key_ideas(node)

    print(f"peck: reviewed {reviewed} node(s).")
    print(ShelfCensus.from_store(store).render())
    return reviewed


# ---------------------------------------------------------------------------
# CLI halves — used by the /peck skill (judge on the subscription model)
# ---------------------------------------------------------------------------


def list_due(config: Config) -> int:
    """Print the session's due nodes as JSON — the read half of the peck skill.

    Claude Code calls this, generates a fresh box-appropriate question from
    each node's key_ideas/seed/linked_titles in-session (no API call), judges
    the answer, then writes each grade back via ``--grade``. A teach-mode
    entry ("mode": "teach") carries no question — show key_ideas/seed and
    write the grade back as ``teach``.
    """
    store = ReviewStore.load(config)
    if store.sync_sources(config):
        store.save()

    sources = scan_sources(config)
    out = []
    for review in store.session():
        node = sources.get(review.id)
        if node is None:
            continue
        is_teach = not review.reviews
        entry = {
            "id": review.id,
            "title": node.title,
            "box": review.box,
            "mode": "teach" if is_teach else "quiz",
            "key_ideas": node.key_ideas,
            "seed": node.seed,
        }
        if not is_teach and review.box >= 4:
            entry["linked_titles"] = node.linked_titles
        out.append(entry)
    print(json.dumps(out, indent=2))
    print(ShelfCensus.from_store(store).render())
    return 0


def grade(config: Config, node_id: str, raw_grade: str) -> int:
    """Apply one grading action — the write half of the peck skill.

    ``raw_grade`` is strong/weak/wrong (s/w/x), bad (b), retire (d), or the
    special ``teach`` action that completes a teach-first card (no letter —
    bumps due to tomorrow, box untouched).
    """
    action = (
        "teach"
        if raw_grade.lower() == "teach"
        else _GRADE_ALIASES.get(raw_grade.lower())
    )
    if action is None:
        print(
            f"peck: invalid grade {raw_grade!r} "
            "(use strong/weak/wrong/bad/retire/teach)."
        )
        return 2
    store = ReviewStore.load(config)
    if store.get(node_id) is None:
        print(f"peck: unknown source id {node_id!r}.")
        return 2

    if action == "teach":
        updated = store.teach(node_id)
        store.save()
        print(json.dumps({"id": node_id, "action": "teach", "due": updated.due}))
        return 0
    if action == "bad":
        updated = store.mark_bad(node_id)
        store.save()
        print(
            json.dumps(
                {
                    "id": node_id,
                    "action": "bad",
                    "bad_streak": updated.bad_streak,
                    "flag": updated.bad_streak >= BAD_STREAK_FLAG,
                }
            )
        )
        return 0
    if action == "retire":
        updated = store.retire(node_id)
        store.save()
        print(
            json.dumps({"id": node_id, "action": "retire", "retired": updated.retired})
        )
        return 0

    updated = store.apply_grade(node_id, action)  # type: ignore[arg-type]
    store.save()
    print(
        json.dumps(
            {
                "id": node_id,
                "grade": action,
                "box": updated.box,
                "due": str(updated.due),
            }
        )
    )
    return 0


def main(config: Config, argv: list[str] | None = None) -> int:
    """Entry point for `peck [--list-due|--grade <id> <action>]` (else interactive)."""
    argv = argv or []
    try:
        if argv and argv[0] == "--list-due":
            return list_due(config)
        if argv and argv[0] == "--grade":
            if len(argv) != 3:
                print(
                    "peck: usage: peck --grade <id> "
                    "<strong|weak|wrong|bad|retire|teach>"
                )
                return 2
            return grade(config, argv[1], argv[2])
        return peck(config)
    except KeyboardInterrupt:
        print("\npeck: interrupted.")
        return 0


if __name__ == "__main__":
    # Allow running directly: `uv run python -m bower_bird.review`
    from pydantic import ValidationError

    try:
        _cfg = Config()
    except ValidationError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(_cfg))
