import math
import re
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from bower_bird.config import Config
from bower_bird.lint import frontmatter_title, inbound_source_counts, read_text

_MARKER_RE = re.compile(r"<!-- bower:concept(.*?) -->")
_SYNTHESIZED_RE = re.compile(r"synthesized=(\d{4}-\d{2}-\d{2})")
_SOURCES_RE = re.compile(r"sources=(\d+)")


def parse_marker(text: str) -> tuple[date | None, int | None]:
    """Return the concept marker's ``synthesized=`` and ``sources=`` values.

    Both are None when the block is absent or bare, which means the concept
    was never synthesized.
    """
    m = _MARKER_RE.search(text)
    if not m:
        return None, None
    attrs = m.group(1)
    sd = _SYNTHESIZED_RE.search(attrs)
    sn = _SOURCES_RE.search(attrs)
    synthesized = date.fromisoformat(sd.group(1)) if sd else None
    sources = int(sn.group(1)) if sn else None
    return synthesized, sources


class StaleConcept(BaseModel):
    """One concept with feeding sources that its synthesis has not absorbed."""

    title: str
    current_sources: int
    synthesized: date | None
    sources_at_synthesis: int | None

    @property
    def new_sources(self) -> int:
        """Feeding sources added since the last synthesis."""
        return self.current_sources - (self.sources_at_synthesis or 0)

    @property
    def days_since(self) -> int | None:
        """Days since the last synthesis, or None if never synthesized."""
        if self.synthesized is None:
            return None
        return (date.today() - self.synthesized).days

    @property
    def sort_key(self) -> tuple[int, float]:
        """Rank by new sources, then age; never synthesized sorts as oldest."""
        return (
            self.new_sources,
            self.days_since if self.days_since is not None else math.inf,
        )


def concept_notes(config: Config) -> list[Path]:
    """Return every concept note under brain/bowers/, recursively."""
    if not config.notes_dir.is_dir():
        return []
    return sorted(config.notes_dir.rglob("*.md"))


def stale_concepts(config: Config) -> list[StaleConcept]:
    """Return concepts with unsynthesized sources, most stale first.

    Concepts with no new sources are left out, so a caught up graph prints
    nothing.
    """
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
    """Render one queue row: title, new source count and staleness."""
    status = (
        "never synthesized"
        if concept.synthesized is None
        else f"{concept.days_since} days stale"
    )
    gap = " " * max(2, 24 - len(concept.title))
    return f"{concept.title}{gap}{concept.new_sources} new sources · {status}"


def main(config: Config, limit: int = 20) -> int:
    """Run `bb stale`: print which concepts weave should synthesize next.

    Reads files only. The count of feeding sources comes from the source
    notes that wikilink to each concept, and the last synthesis from the
    `<!-- bower:concept synthesized=YYYY-MM-DD sources=N -->` marker.
    """
    concepts = stale_concepts(config)[:limit]
    if not concepts:
        print("stale: nothing to weave — every concept is caught up.")
        return 0
    for concept in concepts:
        print(format_line(concept))
    return 0
