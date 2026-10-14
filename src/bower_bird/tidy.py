import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from bower_bird.catalog import read_index
from bower_bird.config import Config
from bower_bird.ingest import assert_writable
from bower_bird.lint import links_section_targets, read_text, run_lint

_PAGES_HEADING = "## Pages"
_PAGE_LINE_RE = re.compile(r"^- \[\[([^\]]+)\]\](.*)$", re.MULTILINE)
_DEFINITION_RE = re.compile(r"\*\*Definition:\*\*\s*(.+)")
_ONELINER_LIMIT = 140


def parse_page_lines(index_text: str) -> dict[str, tuple[str, str]]:
    """Parse catalog lines into ``stem -> (category, oneline)``.

    A bare title line parses with both fields empty.
    """
    parsed = {}
    for stem, rest in _PAGE_LINE_RE.findall(index_text):
        parts = rest.split(" · ")
        category = parts[1].strip() if len(parts) > 1 else ""
        oneline = parts[2].strip() if len(parts) > 2 else ""
        parsed[stem] = (category, oneline)
    return parsed


def index_prefix(old_text: str) -> str:
    """Return the index text up to and including the ``## Pages`` heading.

    Only the line list below the heading is rebuilt from disk.
    """
    if _PAGES_HEADING in old_text:
        head = old_text.split(_PAGES_HEADING, 1)[0]
        return f"{head}{_PAGES_HEADING}\n"
    sep = "" if old_text == "" or old_text.endswith("\n") else "\n"
    return f"{old_text}{sep}{_PAGES_HEADING}\n"


def page_stems(config: Config) -> list[str]:
    """Return the stem of every page the index catalogs.

    Covers sources/ and bowers/ recursively. People and tools are leaves and
    stay out of the catalog.
    """
    stems = []
    if config.sources_dir.is_dir():
        stems += [p.stem for p in config.sources_dir.glob("*.md")]
    if config.notes_dir.is_dir():
        stems += [p.stem for p in config.notes_dir.rglob("*.md")]
    return stems


def concept_definitions(config: Config) -> dict[str, str]:
    """Map concept stems to the first sentence of their definition.

    Reads the ``**Definition:**`` line inside a concept block. Synthesized
    concepts carry a real definition; capture time stubs
    (`upsert_index_line(..., "", insert_only=True)`) never do — this backfills
    `rebuild_index`'s empty one-liners with something meaningful instead of
    leaving them bare forever. First sentence only, markdown emphasis
    stripped, capped at ~140 chars.
    """
    definitions: dict[str, str] = {}
    if not config.notes_dir.is_dir():
        return definitions
    for path in config.notes_dir.rglob("*.md"):
        m = _DEFINITION_RE.search(read_text(path))
        if not m:
            continue
        line = m.group(1).strip()
        sentence_match = re.match(r"(.+?[.!?])(?:\s|$)", line)
        sentence = sentence_match.group(1) if sentence_match else line
        sentence = re.sub(r"[*_]", "", sentence).strip()
        if sentence:
            definitions[path.stem] = sentence[:_ONELINER_LIMIT]
    return definitions


class IndexRebuild(BaseModel):
    """A rebuilt ``_index.md`` and how it differs from the old catalog."""

    text: str
    pages: int
    added: list[str] = Field(
        default_factory=list, description="Pages on disk with no catalog line yet."
    )
    dropped: list[str] = Field(
        default_factory=list, description="Catalog lines with no page on disk."
    )


def rebuild_index(config: Config) -> IndexRebuild:
    """Rebuild the ``## Pages`` list from the pages that exist on disk.

    Each page keeps its existing category and one liner. An empty one liner
    takes the concept definition when there is one; nothing else is invented.
    """
    old_text = read_index(config)
    old_lines = parse_page_lines(old_text)
    definitions = concept_definitions(config)
    stems = sorted(set(page_stems(config)), key=str.lower)
    lines = []
    for stem in stems:
        category, oneline = old_lines.get(stem, ("", ""))
        if not oneline and stem in definitions:
            oneline = definitions[stem]
        lines.append(f"- [[{stem}]] · {category} · {oneline}".rstrip(" ·"))
    body = "\n".join(lines)
    prefix = index_prefix(old_text)
    text = f"{prefix}{body}\n" if body else prefix
    return IndexRebuild(
        text=text,
        pages=len(stems),
        added=sorted(set(stems) - set(old_lines)),
        dropped=sorted(set(old_lines) - set(stems)),
    )


class FilingProposal(BaseModel):
    """A flat concept and the bower its links point into."""

    path: Path
    bower: str
    matched: int = Field(description="Linked concepts already filed in `bower`.")


def bower_of_concept(config: Config) -> dict[str, str]:
    """Map each concept filed under a ``brain/bowers/<bower>/`` folder to it."""
    mapping: dict[str, str] = {}
    if not config.notes_dir.is_dir():
        return mapping
    for bower_dir in config.notes_dir.iterdir():
        if bower_dir.is_dir():
            for path in bower_dir.glob("*.md"):
                mapping[path.stem] = bower_dir.name
    return mapping


def propose_filings(config: Config) -> list[FilingProposal]:
    """Propose a bower for flat concepts whose links point into one.

    A concept qualifies when its ``## Links`` reach 2 or more concepts filed
    in the same bower. A tie between two bowers is ambiguous and stays flat.

    A flat note whose stem names an existing bower folder (`life.md` next to
    `bowers/life/`) is that bower's own landing note, not a concept to file
    into it — filing it "into itself" would be self-referential nonsense, so
    it's excluded regardless of how its links score.
    """
    if not config.notes_dir.is_dir():
        return []
    bower_names = {d.name for d in config.notes_dir.iterdir() if d.is_dir()}
    bower_of = bower_of_concept(config)
    proposals = []
    for path in sorted(config.notes_dir.glob("*.md")):
        if path.stem in bower_names:
            continue
        targets = links_section_targets(read_text(path))
        counts = Counter(bower_of[t] for t in targets if t in bower_of)
        if not counts:
            continue
        top_bower, top_count = counts.most_common(1)[0]
        if top_count < 2:
            continue
        if sum(1 for c in counts.values() if c == top_count) > 1:
            continue  # tied top bowers -> ambiguous, leave flat
        proposals.append(FilingProposal(path=path, bower=top_bower, matched=top_count))
    return proposals


def apply_filing(config: Config, proposal: FilingProposal) -> Path | None:
    """Move a flat concept into its proposed bower and return the new path.

    Returns None without moving when a file with that name is already there.
    """
    dest_dir = config.notes_dir / proposal.bower
    dest = dest_dir / proposal.path.name
    if dest.exists():
        return None
    assert_writable(config, dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    proposal.path.rename(dest)
    return dest


def main(config: Config, apply: bool = False) -> int:
    """Run `bb tidy`, the bookkeeping half of weave, with no LLM calls.

    Rebuilds the ``_index.md`` page list, files flat concepts into bowers,
    and reports broken links and orphan nodes for a human. Prints the plan
    only unless ``apply`` is set. Every write goes through
    ``ingest.assert_writable``, and a move never overwrites a file.
    """
    mode = "apply" if apply else "dry-run"
    print(f"tidy ({mode}):")

    rebuild = rebuild_index(config)
    print(f"\nindex: {rebuild.pages} pages", end="")
    if rebuild.added:
        print(f", {len(rebuild.added)} newly catalogued", end="")
    if rebuild.dropped:
        print(f", {len(rebuild.dropped)} stale entries dropped", end="")
    print()
    for stem in rebuild.added:
        print(f"  + [[{stem}]]")
    for stem in rebuild.dropped:
        print(f"  - [[{stem}]] (catalogued, but no matching page on disk)")
    if apply:
        assert_writable(config, config.index_path)
        config.index_path.write_text(rebuild.text, encoding="utf-8")
        print("  written.")

    proposals = propose_filings(config)
    if not proposals:
        print("\nfiling: no flat concept meets the >=2-links-into-one-bower bar.")
    else:
        print(f"\nfiling: {len(proposals)} flat concept(s) to file:")
        for p in proposals:
            print(
                f"  [[{p.path.stem}]] -> bowers/{p.bower}/ "
                f"({p.matched} linked concepts already there)"
            )
        if apply:
            for p in proposals:
                dest = apply_filing(config, p)
                if dest is None:
                    print(f"  skip (target exists): [[{p.path.stem}]]")
                else:
                    print(f"  moved: {dest.relative_to(config.vault_path)}")

    findings, _, _ = run_lint(config)
    if findings.broken_links or findings.orphan_nodes:
        print("\nneeds a human decision (not fixed by tidy):")
        if findings.broken_links:
            print(f"  broken links ({len(findings.broken_links)}):")
            for entry in findings.broken_links:
                print(f"    - {entry}")
        if findings.orphan_nodes:
            print(f"  orphan nodes ({len(findings.orphan_nodes)}):")
            for entry in findings.orphan_nodes:
                print(f"    - {entry}")
    else:
        print("\nno broken links or orphan nodes — nothing else needs a human.")

    return 0
