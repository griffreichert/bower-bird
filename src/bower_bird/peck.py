import json
import sys

from bower_bird.config import Config
from bower_bird.review import (
    BAD_STREAK_FLAG,
    Grade,
    ReviewStore,
    ShelfCensus,
    SourceNode,
    scan_sources,
)

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

        if review.is_teach:  # teach-first
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
        is_teach = review.is_teach
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


def prune_reviews(config: Config) -> int:
    """Remove review entries whose source node is gone — the ``--prune`` half."""
    store = ReviewStore.load(config)
    removed = store.prune_orphans(config)
    if removed:
        store.save()
    noun = "entry" if len(removed) == 1 else "entries"
    print(f"peck: pruned {len(removed)} orphan review {noun}.")
    print(ShelfCensus.from_store(store).render())
    return 0


def main(config: Config, argv: list[str] | None = None) -> int:
    """Entry: `peck [--list-due|--prune|--grade <id> <action>]` (else interactive)."""
    argv = argv or []
    try:
        if argv and argv[0] == "--list-due":
            return list_due(config)
        if argv and argv[0] == "--prune":
            return prune_reviews(config)
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
