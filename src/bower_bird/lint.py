"""`bb lint` — read-only, structural-only graph linter.

Seven checks, no LLM calls, no network, writes nothing:
  1. Orphan review entries — ids in `_review.json` with no matching brain node.
  2. Orphan nodes — brain files with zero outbound AND zero inbound wikilinks
     (skipping `_`-prefixed files like `_index.md`, `_log.md`).
  3. Broken links — `[[wikilinks]]` that resolve to no brain file stem.
  4. Topic drift — a source's frontmatter `topics` disagrees with its `## Links`
     wikilinks (the metadata-search-surface projection, #29/spec).
  5. Category drift — a source's frontmatter `category` disagrees with its
     `_index.md` line.
  6. Under-cited synthesis — a `<!-- bower:concept -->` block citing fewer
     than 2 distinct `[[wikilinks]]` inside the block (INVARIANTS.md:24-28:
     concept substance is earned synthesis, cited to the sources that earned
     it).
  7. Earned but unsynthesized — a concept note with >=3 inbound source links
     and no synthesis block at all.

Both projection checks only fire when the frontmatter key is present — a
not-yet-backfilled node (no `topics`/`category` at all) is not a finding.
Both synthesis checks are quiet on a concept with fewer than 2 feeding
sources — a young note hasn't earned anything yet.

Exit code 1 if any findings, 0 if clean.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from bower_bird.config import Config
from bower_bird.review import ReviewStore

_ID_RE = re.compile(r"^id:\s*(.+)$", re.MULTILINE)
_TITLE_RE = re.compile(r'^title:\s*"?(.*?)"?\s*$', re.MULTILINE)
_TOPICS_RE = re.compile(r"^topics:\n((?:  - .+\n)+)", re.MULTILINE)
_CATEGORY_RE = re.compile(r"^category:\s*(.+)$", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_LINKS_SECTION_RE = re.compile(r"^## Links\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
_INDEX_LINE_RE = re.compile(r"^- \[\[([^\]]+)\]\](?: · ([^·\n]*))?", re.MULTILINE)
_CONCEPT_BLOCK_RE = re.compile(
    r"<!-- bower:concept.*? -->(.*?)<!-- /bower:concept -->", re.DOTALL
)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def brain_files(config: Config) -> list[Path]:
    """All markdown files under brain/ (sources/, bowers/, people/, tools/)."""
    if not config.brain_dir.is_dir():
        return []
    return sorted(config.brain_dir.rglob("*.md"))


def node_id(text: str) -> str | None:
    m = _ID_RE.search(text)
    return m.group(1).strip() if m else None


def outbound_links(text: str) -> list[str]:
    return [m.group(1).strip() for m in _WIKILINK_RE.finditer(text)]


def frontmatter_title(text: str) -> str | None:
    m = _TITLE_RE.search(text)
    return m.group(1).strip() if m else None


def frontmatter_topics(text: str) -> set[str] | None:
    """Frontmatter `topics:` block as a set, or None if the key is absent."""
    m = _TOPICS_RE.search(text)
    if not m:
        return None
    return {line.strip()[2:].strip() for line in m.group(1).splitlines()}


def frontmatter_category(text: str) -> str | None:
    m = _CATEGORY_RE.search(text)
    return m.group(1).strip() if m else None


def links_section_targets(text: str) -> set[str]:
    """Wikilink targets under `## Links` only (never `## Sources`)."""
    m = _LINKS_SECTION_RE.search(text)
    if not m:
        return set()
    return {t.strip() for t in outbound_links(m.group(1))}


def index_categories(index_text: str) -> dict[str, str]:
    """Map `[[title]]` -> category, parsed from `_index.md` lines."""
    return {
        title.strip(): (category or "").strip()
        for title, category in _INDEX_LINE_RE.findall(index_text)
    }


def inbound_source_counts(config: Config) -> dict[str, int]:
    """Concept stem -> count of distinct `brain/sources/*.md` notes that
    wikilink to it. More reliable than a concept note's own `## Sources`
    heading, which can drift (list tools/people alongside real sources)."""
    counts: dict[str, int] = {}
    if not config.sources_dir.is_dir():
        return counts
    for path in config.sources_dir.glob("*.md"):
        for target in set(outbound_links(read_text(path))):
            counts[target] = counts.get(target, 0) + 1
    return counts


def concept_block_links(text: str) -> set[str]:
    """Distinct `[[wikilinks]]` cited *inside* a `<!-- bower:concept -->`
    block. Empty set if there's no block, or the block cites nothing."""
    m = _CONCEPT_BLOCK_RE.search(text)
    return set(outbound_links(m.group(1))) if m else set()


def has_concept_block(text: str) -> bool:
    return "<!-- bower:concept" in text


@dataclass
class LintFindings:
    """Findings from a lint pass, one entry per finding."""

    orphan_reviews: list[str] = field(default_factory=list)
    orphan_nodes: list[str] = field(default_factory=list)
    broken_links: list[str] = field(default_factory=list)
    topic_drift: list[str] = field(default_factory=list)
    category_drift: list[str] = field(default_factory=list)
    under_cited_synthesis: list[str] = field(default_factory=list)
    earned_unsynthesized: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (
            self.orphan_reviews
            or self.orphan_nodes
            or self.broken_links
            or self.topic_drift
            or self.category_drift
            or self.under_cited_synthesis
            or self.earned_unsynthesized
        )


def run_lint(config: Config) -> tuple[LintFindings, int, int]:
    """Run all three checks. Returns (findings, node_count, review_count).

    Pure I/O — no network.
    """
    files = brain_files(config)
    node_ids: set[str] = set()
    stems: set[str] = set()
    outbound: dict[Path, list[str]] = {}

    for path in files:
        text = read_text(path)
        stems.add(path.stem)
        nid = node_id(text)
        if nid:
            node_ids.add(nid)
        outbound[path] = outbound_links(text)

    # Orphan review entries: ids in _review.json with no matching node.
    store = ReviewStore.load(config)
    orphan_reviews = sorted(
        bower_id for bower_id in store._entries if bower_id not in node_ids
    )

    # Inbound link count per stem, for the orphan-node check.
    inbound_count: dict[str, int] = {stem: 0 for stem in stems}
    for targets in outbound.values():
        for target in targets:
            if target in inbound_count:
                inbound_count[target] += 1

    orphan_nodes = sorted(
        path.relative_to(config.vault_path).as_posix()
        for path in files
        if not path.stem.startswith("_")
        and not outbound[path]
        and inbound_count.get(path.stem, 0) == 0
    )

    # Broken links: wikilink targets that match no brain file stem.
    broken: list[str] = []
    for path, targets in outbound.items():
        for target in targets:
            if target not in stems:
                broken.append(
                    f"{path.relative_to(config.vault_path).as_posix()}: [[{target}]]"
                )
    broken_links = sorted(broken)

    # Topic/category drift: the metadata-search-surface projection (spec #29)
    # must agree with its source of truth — `## Links` for topics, `_index.md`
    # for category. Only checked when the frontmatter key is present; a
    # not-yet-backfilled node has neither and is not a finding.
    index_cats = index_categories(read_text(config.index_path))
    topic_drift: list[str] = []
    category_drift: list[str] = []
    for path in config.sources_dir.glob("*.md") if config.sources_dir.is_dir() else []:
        text = read_text(path)
        rel = path.relative_to(config.vault_path).as_posix()

        fm_topics = frontmatter_topics(text)
        if fm_topics is not None:
            link_topics = links_section_targets(text)
            if fm_topics != link_topics:
                topic_drift.append(
                    f"{rel}: frontmatter topics {sorted(fm_topics)} != "
                    f"## Links {sorted(link_topics)}"
                )

        fm_category = frontmatter_category(text)
        if fm_category is not None:
            title = frontmatter_title(text)
            index_category = index_cats.get(title) if title else None
            if index_category is not None and fm_category != index_category:
                category_drift.append(
                    f"{rel}: frontmatter category '{fm_category}' != "
                    f"_index.md category '{index_category}'"
                )

    # Synthesis checks (INVARIANTS.md:24-28): concept substance is earned
    # synthesis, cited to the sources that earned it. Quiet on a concept with
    # fewer than 2 feeding sources — it hasn't earned anything yet.
    source_counts = inbound_source_counts(config)
    under_cited: list[str] = []
    earned_unsynth: list[str] = []
    concept_files = config.notes_dir.rglob("*.md") if config.notes_dir.is_dir() else []
    for path in concept_files:
        feeding = source_counts.get(path.stem, 0)
        if feeding < 2:
            continue
        text = read_text(path)
        rel = path.relative_to(config.vault_path).as_posix()
        if has_concept_block(text):
            block_links = concept_block_links(text)
            if len(block_links) < 2:
                under_cited.append(
                    f"{rel}: cites {len(block_links)} source(s) inside the block"
                )
        elif feeding >= 3:
            earned_unsynth.append(
                f"{rel}: {feeding} inbound sources, no synthesis block"
            )

    findings = LintFindings(
        orphan_reviews=orphan_reviews,
        orphan_nodes=orphan_nodes,
        broken_links=broken_links,
        topic_drift=sorted(topic_drift),
        category_drift=sorted(category_drift),
        under_cited_synthesis=sorted(under_cited),
        earned_unsynthesized=sorted(earned_unsynth),
    )
    return findings, len(files), len(store._entries)


def print_findings(findings: LintFindings, node_count: int, review_count: int) -> int:
    if findings.empty:
        print(f"lint: clean ({node_count} nodes, {review_count} review entries)")
        return 0

    if findings.orphan_reviews:
        print(f"\nOrphan review entries ({len(findings.orphan_reviews)}):")
        for bower_id in findings.orphan_reviews:
            print(f"  - {bower_id}")

    if findings.orphan_nodes:
        print(f"\nOrphan nodes ({len(findings.orphan_nodes)}):")
        for rel_path in findings.orphan_nodes:
            print(f"  - {rel_path}")

    if findings.broken_links:
        print(f"\nBroken links ({len(findings.broken_links)}):")
        for entry in findings.broken_links:
            print(f"  - {entry}")

    if findings.topic_drift:
        print(f"\nTopic drift ({len(findings.topic_drift)}):")
        for entry in findings.topic_drift:
            print(f"  - {entry}")

    if findings.category_drift:
        print(f"\nCategory drift ({len(findings.category_drift)}):")
        for entry in findings.category_drift:
            print(f"  - {entry}")

    if findings.under_cited_synthesis:
        print(f"\nUnder-cited synthesis ({len(findings.under_cited_synthesis)}):")
        for entry in findings.under_cited_synthesis:
            print(f"  - {entry}")

    if findings.earned_unsynthesized:
        print(f"\nEarned but unsynthesized ({len(findings.earned_unsynthesized)}):")
        for entry in findings.earned_unsynthesized:
            print(f"  - {entry}")

    return 1


def main(config: Config) -> int:
    findings, node_count, review_count = run_lint(config)
    return print_findings(findings, node_count, review_count)
