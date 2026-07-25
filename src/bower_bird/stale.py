"""`bb stale` — ranked, resumable queue for whole-graph synthesis (weave).

Read-only, no LLM calls, no network, writes nothing. Turns "whole-graph
synthesis" from unbounded work into a ranked list: which concept to weave
next, and why.

Parses the `<!-- bower:concept synthesized=YYYY-MM-DD sources=N -->` marker on
each `brain/bowers/**/*.md` note. A block with no attributes, or no block at
all, means the concept was never synthesized. Current feeding-source count is
the number of distinct `brain/sources/*.md` notes that wikilink to the
concept (measured more reliable than the note's own `## Sources` heading,
which can drift — e.g. list tool/people links alongside sources).
"""

import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from bower_bird.config import Config
from bower_bird.lint import frontmatter_title, inbound_source_counts, read_text

_MARKER_RE = re.compile(r"<!-- bower:concept(.*?) -->")
_SYNTHESIZED_RE = re.compile(r"synthesized=(\d{4}-\d{2}-\d{2})")
_SOURCES_RE = re.compile(r"sources=(\d+)")


def parse_marker(text: str) -> tuple[date | None, int | None]:
    """The concept marker's `synthesized=`/`sources=` attributes, or (None,
    None) if the block is absent or bare — both mean never synthesized."""
    m = _MARKER_RE.search(text)
    if not m:
        return None, None
    attrs = m.group(1)
    sd = _SYNTHESIZED_RE.search(attrs)
    sn = _SOURCES_RE.search(attrs)
    synthesized = date.fromisoformat(sd.group(1)) if sd else None
    sources = int(sn.group(1)) if sn else None
    return synthesized, sources


@dataclass
class StaleConcept:
    title: str
    current_sources: int
    synthesized: date | None
    sources_at_synthesis: int | None

    @property
    def new_sources(self) -> int:
        return self.current_sources - (self.sources_at_synthesis or 0)

    @property
    def days_since(self) -> int | None:
        if self.synthesized is None:
            return None
        return (date.today() - self.synthesized).days

    @property
    def sort_key(self) -> tuple[int, float]:
        # Never-synthesized notes sort as maximally stale on the tiebreaker.
        return (
            self.new_sources,
            self.days_since if self.days_since is not None else math.inf,
        )


def concept_notes(config: Config) -> list[Path]:
    if not config.notes_dir.is_dir():
        return []
    return sorted(config.notes_dir.rglob("*.md"))


def stale_concepts(config: Config) -> list[StaleConcept]:
    """Concepts with unsynthesized work, ranked by (new_sources, days_since)
    descending. Concepts with 0 new sources are omitted — quiet by default."""
    counts = inbound_source_counts(config)
    concepts = []
    for path in concept_notes(config):
        text = read_text(path)
        title = frontmatter_title(text) or path.stem
        synthesized, sources_at_synthesis = parse_marker(text)
        current = counts.get(path.stem, 0)
        concept = StaleConcept(
            title=title,
            current_sources=current,
            synthesized=synthesized,
            sources_at_synthesis=sources_at_synthesis,
        )
        if concept.new_sources > 0:
            concepts.append(concept)
    return sorted(concepts, key=lambda c: c.sort_key, reverse=True)


def format_line(concept: StaleConcept) -> str:
    status = (
        "never synthesized"
        if concept.synthesized is None
        else f"{concept.days_since} days stale"
    )
    gap = " " * max(2, 24 - len(concept.title))
    return f"{concept.title}{gap}{concept.new_sources} new sources · {status}"


def main(config: Config, limit: int = 20) -> int:
    concepts = stale_concepts(config)[:limit]
    if not concepts:
        print("stale: nothing to weave — every concept is caught up.")
        return 0
    for concept in concepts:
        print(format_line(concept))
    return 0
