"""`bb lint` — read-only, structural-only graph linter.

Three checks, no LLM calls, no network, writes nothing:
  1. Orphan review entries — ids in `_review.json` with no matching brain node.
  2. Orphan nodes — brain files with zero outbound AND zero inbound wikilinks
     (skipping `_`-prefixed files like `_index.md`, `_log.md`).
  3. Broken links — `[[wikilinks]]` that resolve to no brain file stem.

Exit code 1 if any findings, 0 if clean.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from bower_bird.config import Config
from bower_bird.review import ReviewStore

_ID_RE = re.compile(r"^id:\s*(.+)$", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


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


@dataclass
class LintFindings:
    """Findings from a lint pass, one entry per finding."""

    orphan_reviews: list[str] = field(default_factory=list)
    orphan_nodes: list[str] = field(default_factory=list)
    broken_links: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.orphan_reviews or self.orphan_nodes or self.broken_links)


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

    findings = LintFindings(
        orphan_reviews=orphan_reviews,
        orphan_nodes=orphan_nodes,
        broken_links=broken_links,
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

    return 1


def main(config: Config) -> int:
    findings, node_count, review_count = run_lint(config)
    return print_findings(findings, node_count, review_count)
