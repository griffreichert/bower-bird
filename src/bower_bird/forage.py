"""forage: gap signals → forage.md proposals (propose-never-auto-fill).

Gathers four gap signal types and writes ``BowerBird/forage.md`` with two tiers:

  Tier 1 — Gap topics:  concepts that need more learning (pointers only).
  Tier 2 — Candidate sources: placeholder / search prompt for each gap topic.
            Web discovery is explicitly deferred — no live search here. Each
            gap emits a ``(search pending)`` marker so the human knows where to
            look next.

Gap signals gathered:
  - Thin bowers: concept notes with little or no body content (no concept block,
    or a very short definition).
  - Quiz-weak: bowers whose last_grade in ``_review.json`` is "weak" or "wrong".
  - #dig marks: lines tagged ``#dig`` in source notes under ``brain/sources/``.
  - Open questions (``> ?``): reader questions logged in source notes.

Nothing is written into ``inbox/``. Approval is an explicit human step.
Quiet by default: if no gaps are found, prints a one-liner and exits.

Pull-only: never called from the cron drain path.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .marks import _extract_dig, _extract_questions
from .review import ReviewStore

# ---------------------------------------------------------------------------
# Thin-bower detection
# ---------------------------------------------------------------------------

_ID_RE = re.compile(r"^id:\s*(.+)$", re.MULTILINE)
_TITLE_RE = re.compile(r"^title:\s*\"?(.+?)\"?\s*$", re.MULTILINE)
_CONCEPT_BLOCK_RE = re.compile(
    r"<!-- bower:concept -->.*?<!-- /bower:concept -->", re.DOTALL
)
_DEFINITION_RE = re.compile(r"\*\*Definition:\*\*\s*(.+)")

#: A definition shorter than this (characters) counts as thin.
THIN_DEFINITION_CHARS = 60


def _is_thin_bower(text: str) -> bool:
    """Return True when a bower note lacks a concept block or has a short definition."""
    block_m = _CONCEPT_BLOCK_RE.search(text)
    if block_m is None:
        return True  # no machine-managed concept block at all
    def_m = _DEFINITION_RE.search(block_m.group(0))
    if def_m is None:
        return True
    return len(def_m.group(1).strip()) < THIN_DEFINITION_CHARS


def _bower_title(text: str, stem: str) -> str:
    m = _TITLE_RE.search(text)
    return m.group(1).strip() if m else stem


def _bower_id(text: str) -> str | None:
    m = _ID_RE.search(text)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# Gap signal data structures
# ---------------------------------------------------------------------------


@dataclass
class GapSignals:
    """All gap signals gathered from the vault."""

    thin_bowers: list[str] = field(default_factory=list)
    """Titles of bower concept notes that are thin (no or very short definition)."""

    quiz_weak: list[str] = field(default_factory=list)
    """Titles of bowers graded weak or wrong in the most recent review."""

    dig_marks: list[str] = field(default_factory=list)
    """#dig text fragments pulled from source notes."""

    open_questions: list[str] = field(default_factory=list)
    """Open > ? questions pulled from source notes."""

    @property
    def empty(self) -> bool:
        return not (
            self.thin_bowers or self.quiz_weak or self.dig_marks or self.open_questions
        )


# ---------------------------------------------------------------------------
# Signal gathering
# ---------------------------------------------------------------------------

_SKIP_NOTE_STEMS = {"_index", "_archive", "_template", "__init__"}


def gather_thin_bowers(config: Config) -> list[str]:
    """Scan brain/bowers/ and return titles of thin concept notes."""
    if not config.notes_dir.is_dir():
        return []
    titles: list[str] = []
    for path in sorted(config.notes_dir.rglob("*.md")):
        if path.stem in _SKIP_NOTE_STEMS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _is_thin_bower(text):
            titles.append(_bower_title(text, path.stem))
    return titles


def gather_quiz_weak(config: Config) -> list[str]:
    """Return titles of bowers whose last_grade is weak or wrong.

    Correlates _review.json ids back to bower filenames via frontmatter ``id:``
    scanning. Only loads notes that are actually in the store (lazy scan).
    """
    store = ReviewStore.load(config)
    # Collect bower ids flagged as weak/wrong.
    weak_ids: set[str] = set()
    for review in store._entries.values():
        if review.last_grade in ("weak", "wrong"):
            weak_ids.add(review.id)

    if not weak_ids or not config.notes_dir.is_dir():
        return []

    titles: list[str] = []
    for path in sorted(config.notes_dir.rglob("*.md")):
        if path.stem in _SKIP_NOTE_STEMS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        bid = _bower_id(text)
        if bid and bid in weak_ids:
            titles.append(_bower_title(text, path.stem))

    return titles


def gather_dig_marks(config: Config) -> list[str]:
    """Scan brain/sources/ and return all #dig text fragments."""
    if not config.sources_dir.is_dir():
        return []
    digs: list[str] = []
    for path in sorted(config.sources_dir.rglob("*.md")):
        if path.stem in _SKIP_NOTE_STEMS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        digs.extend(_extract_dig(text))
    return digs


def gather_open_questions(config: Config) -> list[str]:
    """Scan brain/sources/ and return all > ? open questions."""
    if not config.sources_dir.is_dir():
        return []
    questions: list[str] = []
    for path in sorted(config.sources_dir.rglob("*.md")):
        if path.stem in _SKIP_NOTE_STEMS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        questions.extend(_extract_questions(text))
    return questions


def gather_signals(config: Config) -> GapSignals:
    """Collect all gap signals from the vault. Pure I/O — no network."""
    return GapSignals(
        thin_bowers=gather_thin_bowers(config),
        quiz_weak=gather_quiz_weak(config),
        dig_marks=gather_dig_marks(config),
        open_questions=gather_open_questions(config),
    )


# ---------------------------------------------------------------------------
# forage.md rendering
# ---------------------------------------------------------------------------

_FORAGE_HEADER = """\
---
title: Forage
bower: generated
tags:
  - forage
---
# Forage

What to learn next — gap signals surfaced automatically. Tier 1 lists the topics;
Tier 2 suggests where to start reading. **Nothing is auto-added to inbox/** —
approve items manually (move a link to inbox/ or open + Web Clipper).

"""

_DIVIDER = "---\n\n"


def _render_tier1(signals: GapSignals) -> str:
    """Render the gap topics section."""
    lines: list[str] = ["## Tier 1 — Gap topics\n\n"]

    if signals.thin_bowers:
        lines.append("### Thin concept notes\n\n")
        lines.append(
            "*These bowers have little or no definition — worth deepening.*\n\n"
        )
        for title in signals.thin_bowers:
            lines.append(f"- [[{title}]]\n")
        lines.append("\n")

    if signals.quiz_weak:
        lines.append("### Quiz-weak areas\n\n")
        lines.append(
            "*Graded weak or wrong in the most recent review"
            " — needs reinforcement.*\n\n"
        )
        for title in signals.quiz_weak:
            lines.append(f"- [[{title}]]\n")
        lines.append("\n")

    if signals.dig_marks:
        lines.append("### Dig deeper\n\n")
        lines.append(
            "*Threads you flagged `#dig` while reading — leads to follow.*\n\n"
        )
        for dig in signals.dig_marks:
            lines.append(f"- {dig}\n")
        lines.append("\n")

    if signals.open_questions:
        lines.append("### Open questions\n\n")
        lines.append("*Your own `> ?` questions from reading — still unanswered.*\n\n")
        for q in signals.open_questions:
            lines.append(f"- {q}\n")
        lines.append("\n")

    return "".join(lines)


def _render_tier2(signals: GapSignals) -> str:
    """Render the candidate sources section.

    Web discovery is deferred — emits a search-pending placeholder per gap topic
    so the human knows exactly what to search for. No URLs are fabricated.
    """
    lines: list[str] = [
        "## Tier 2 — Candidate sources\n\n",
        "*Web discovery is deferred (no live search wired). "
        "Search for each topic below and paste promising URLs into `to-clip.md` "
        "or open them with the Web Clipper into `inbox/`.*\n\n",
    ]

    # Consolidate all topics into a flat search list.
    topics: list[str] = []
    topics.extend(signals.thin_bowers)
    topics.extend(signals.quiz_weak)
    # For dig marks and open questions, use them as search strings directly.
    topics.extend(signals.dig_marks)
    topics.extend(signals.open_questions)

    if topics:
        lines.append("| Topic / query | Status |\n")
        lines.append("|---|---|\n")
        seen: set[str] = set()
        for t in topics:
            if t in seen:
                continue
            seen.add(t)
            # Escape pipes in the topic text for the markdown table.
            safe_t = t.replace("|", "\\|")
            lines.append(f"| {safe_t} | (search pending) |\n")
        lines.append("\n")
    else:
        lines.append("*(no topics to search)*\n\n")

    return "".join(lines)


def render_forage(signals: GapSignals) -> str:
    """Render the full forage.md content from gathered gap signals."""
    return _FORAGE_HEADER + _render_tier1(signals) + _DIVIDER + _render_tier2(signals)


# ---------------------------------------------------------------------------
# forage write
# ---------------------------------------------------------------------------


def write_forage(config: Config, signals: GapSignals) -> Path:
    """Write forage.md into the vault. Returns the path written."""
    path = config.forage_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_forage(signals), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# forage entry point
# ---------------------------------------------------------------------------


def forage(config: Config) -> int:
    """Gather gap signals and write forage.md. Pull-only — never crond.

    Returns 0 on success. Quiet when there are no gaps.
    """
    signals = gather_signals(config)

    if signals.empty:
        print("forage: no gaps found. Nothing to surface.")
        return 0

    path = write_forage(config, signals)
    counts: list[str] = []
    if signals.thin_bowers:
        counts.append(f"{len(signals.thin_bowers)} thin bower(s)")
    if signals.quiz_weak:
        counts.append(f"{len(signals.quiz_weak)} quiz-weak bower(s)")
    if signals.dig_marks:
        counts.append(f"{len(signals.dig_marks)} dig mark(s)")
    if signals.open_questions:
        counts.append(f"{len(signals.open_questions)} open question(s)")

    print(f"forage: {', '.join(counts)} → {path}")
    return 0


def main(config: Config) -> int:
    """Entry point called from __main__.py for `python -m bower_bird forage`."""
    try:
        return forage(config)
    except KeyboardInterrupt:
        print("\nforage: interrupted.")
        return 0


if __name__ == "__main__":
    # Allow running directly: `uv run python -m bower_bird.forage`
    from .config import load_config

    try:
        _cfg = load_config()
    except RuntimeError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(_cfg))
