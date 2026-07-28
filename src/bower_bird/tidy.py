"""`bb tidy` — weave's bookkeeping half (D5, notes/2026-07-25-retrieval-plan.md).

Zero LLM calls, zero network — same class as `bb lint`, but this is the one
piece here that writes. Three jobs, each independently useful:

  1. Rebuild `_index.md`'s `## Pages` section from what actually exists under
     brain/ (sources/ + bowers/, recursively) — `nodes.upsert_index_line`
     maintains it incrementally per-capture and it drifts. Preserves each
     page's existing category + one-liner where a catalog line already names
     it; never invents one for a page that has none.
  2. File a flat-root concept (`brain/bowers/<title>.md`) into a bower
     (`brain/bowers/<bower>/<title>.md`) when >=2 of the concepts it links to
     (its `## Links` section) already live in that same bower. A tie across
     bowers, or fewer than 2 matches, is left flat — ambiguous filing is worse
     than a flat concept. Moving needs no link rewriting: Obsidian resolves
     `[[wikilinks]]` by stem regardless of folder.
  3. Report what it cannot fix: `bb lint`'s broken links and orphan nodes are
     read out, never silently stubbed or deleted — those need a human call.

Default is `--dry-run` (prints the plan, writes nothing); `--apply` performs
the index rewrite and the filing moves. Every write goes through
`ingest.assert_writable`. Nothing is ever overwritten: a filing move that
would collide with an existing file is skipped, not clobbered.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from bower_bird.config import Config
from bower_bird.ingest import assert_writable
from bower_bird.lint import links_section_targets, read_text, run_lint
from bower_bird.nodes import read_index

_PAGES_HEADING = "## Pages"
_PAGE_LINE_RE = re.compile(r"^- \[\[([^\]]+)\]\](.*)$", re.MULTILINE)
_DEFINITION_RE = re.compile(r"\*\*Definition:\*\*\s*(.+)")
_ONELINER_LIMIT = 140


def parse_page_lines(index_text: str) -> dict[str, tuple[str, str]]:
    """Existing catalog lines as stem -> (category, oneline). Tolerant of a
    bare title-only line (both default to '')."""
    parsed = {}
    for stem, rest in _PAGE_LINE_RE.findall(index_text):
        parts = rest.split(" · ")
        category = parts[1].strip() if len(parts) > 1 else ""
        oneline = parts[2].strip() if len(parts) > 2 else ""
        parsed[stem] = (category, oneline)
    return parsed


def index_prefix(old_text: str) -> str:
    """Everything up to and including the '## Pages' heading, preserved
    verbatim — only the line list below it gets rebuilt from disk."""
    if _PAGES_HEADING in old_text:
        head = old_text.split(_PAGES_HEADING, 1)[0]
        return f"{head}{_PAGES_HEADING}\n"
    sep = "" if old_text == "" or old_text.endswith("\n") else "\n"
    return f"{old_text}{sep}{_PAGES_HEADING}\n"


def page_stems(config: Config) -> list[str]:
    """Every brain/ page tidy's index catalogs: sources/ + bowers/ (recursive).
    People/tools are unquizzed leaves and were never part of this catalog."""
    stems = []
    if config.sources_dir.is_dir():
        stems += [p.stem for p in config.sources_dir.glob("*.md")]
    if config.notes_dir.is_dir():
        stems += [p.stem for p in config.notes_dir.rglob("*.md")]
    return stems


def concept_definitions(config: Config) -> dict[str, str]:
    """stem -> a short definition, for concept notes under `notes_dir` whose
    `<!-- bower:concept -->` block has a `**Definition:** ...` line.

    Synthesized concepts carry a real definition; capture-time stubs
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


@dataclass
class IndexRebuild:
    text: str
    pages: int
    added: list[str] = field(default_factory=list)  # on disk, no catalog line yet
    dropped: list[str] = field(default_factory=list)  # catalogued, no page on disk


def rebuild_index(config: Config) -> IndexRebuild:
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


@dataclass
class FilingProposal:
    path: Path
    bower: str
    matched: int  # linked concepts already filed in `bower`


def bower_of_concept(config: Config) -> dict[str, str]:
    """stem -> bower name, for every concept already filed under a
    brain/bowers/<bower>/ subfolder."""
    mapping: dict[str, str] = {}
    if not config.notes_dir.is_dir():
        return mapping
    for bower_dir in config.notes_dir.iterdir():
        if bower_dir.is_dir():
            for path in bower_dir.glob("*.md"):
                mapping[path.stem] = bower_dir.name
    return mapping


def propose_filings(config: Config) -> list[FilingProposal]:
    """Flat concepts that link (via `## Links`) to >=2 concepts already filed
    in the same bower. A tie at the top between two bowers is ambiguous and
    left flat.

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
    """Move a flat concept into its proposed bower. Returns the new path, or
    None if a file with that name is already there — never overwrites."""
    dest_dir = config.notes_dir / proposal.bower
    dest = dest_dir / proposal.path.name
    if dest.exists():
        return None
    assert_writable(config, dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    proposal.path.rename(dest)
    return dest


def main(config: Config, apply: bool = False) -> int:
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
