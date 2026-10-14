"""Apply approved merge pairs from `notes/2026-07-25-merge-map.md`
(`scripts/propose_merges.py`'s output) to the live vault.

Per applied pair (loser -> winner):
  1. Append the loser's `## Sources` entries onto the winner via
     `nodes.append_link` (additive, idempotent — never rewrites existing text).
  2. Rewrite `[[loser]]` -> `[[winner]]` across every `brain/*.md` file. This is
     concept-layer relinking, not prose rewriting (INVARIANTS: "concepts are
     plastic... relink, split, merge, demote freely") — only the bracketed
     wikilink target changes, never surrounding text.
  3. Move the loser's note file to `archive/` (never hard-deleted — "Nothing
     is lost").

Applies only the `exact` tier by default. Pass `--tier high` to also apply
`high` (cumulative: exact + high). `review`-tier pairs are never applied by a
flag — they're for Griffin to hand-pick into a fresh map/tier if wanted.

Defaults to `--dry-run` (prints every planned edit, writes nothing). Pass
`--apply` to actually write.

Run: uv run python scripts/apply_merges.py [--map PATH] [--tier exact|high]
     [--apply] [--vault-path PATH]
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bower_bird import inbox, ingest, nodes  # noqa: E402
from bower_bird.config import Config  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP = REPO_ROOT / "notes" / "2026-07-25-merge-map.md"

_MAP_LINE_RE = re.compile(
    r"^- \[[ xX]\] \[\[([^\]]+)\]\] → \[\[([^\]]+)\]\]\s+"
    r"\((\w+), sim=([\d.]+), shared=(\d+), sources (\d+)→(\d+)\)",
    re.MULTILINE,
)
_SOURCES_SECTION_RE = re.compile(
    r"^## Sources\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL
)
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

_TIER_ORDER = ("exact", "high", "review")


@dataclass(frozen=True)
class MergePair:
    loser: str
    winner: str
    tier: str
    sim: float
    shared: int
    loser_sources: int
    winner_sources: int


def parse_map(text: str) -> list[MergePair]:
    """Parse `- [ ] [[loser]] -> [[winner]]  (tier, sim=X, shared=N, sources
    L->W)` lines. The tier is read from the line itself, not the section
    header it sits under — a hand-moved line still applies correctly."""
    return [
        MergePair(
            loser=m.group(1),
            winner=m.group(2),
            tier=m.group(3),
            sim=float(m.group(4)),
            shared=int(m.group(5)),
            loser_sources=int(m.group(6)),
            winner_sources=int(m.group(7)),
        )
        for m in _MAP_LINE_RE.finditer(text)
    ]


def loser_source_stems(loser_text: str) -> list[str]:
    """Wikilink targets under the loser's own `## Sources` heading — the
    feeding sources to carry over to the winner."""
    m = _SOURCES_SECTION_RE.search(loser_text)
    if not m:
        return []
    return [t.strip() for t in _WIKILINK_RE.findall(m.group(1))]


def _wikilink_pattern(title: str) -> re.Pattern[str]:
    return re.compile(rf"\[\[{re.escape(title)}(\|[^\]]*)?\]\]")


def _topic_line_pattern(title: str) -> re.Pattern[str]:
    # A source's frontmatter `topics:` block is a bare-string YAML list (not
    # wikilinked) that must track `## Links` 1:1 or `bb lint`'s topic-drift
    # check fires. Whole-line match only, so an unrelated line ending in the
    # same words is never touched.
    return re.compile(rf"^(\s*- ){re.escape(title)}\s*$", re.MULTILINE)


def count_hits(text: str, title: str) -> int:
    return len(_wikilink_pattern(title).findall(text)) + len(
        _topic_line_pattern(title).findall(text)
    )


def files_linking_to(config: Config, title: str) -> list[tuple[Path, int]]:
    """brain/*.md files referencing `title` — a `[[wikilink]]` (any alias) or a
    frontmatter `topics:` bare-string entry — with match count."""
    hits = []
    if not config.brain_dir.is_dir():
        return hits
    for path in sorted(config.brain_dir.rglob("*.md")):
        n = count_hits(path.read_text(encoding="utf-8"), title)
        if n:
            hits.append((path, n))
    return hits


def rewrite_links(config: Config, path: Path, loser: str, winner: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_text = _wikilink_pattern(loser).sub(
        lambda m: f"[[{winner}{m.group(1) or ''}]]", text
    )
    new_text = _topic_line_pattern(loser).sub(
        lambda m: f"{m.group(1)}{winner}", new_text
    )
    if new_text != text:
        ingest.assert_writable(config, path)
        path.write_text(new_text, encoding="utf-8")


def apply_pair(config: Config, pair: MergePair, *, apply: bool) -> None:
    loser_path = nodes.find_concept_path(config, pair.loser)
    winner_path = nodes.find_concept_path(config, pair.winner)
    print(f"[{pair.tier}] [[{pair.loser}]] -> [[{pair.winner}]]")

    if not loser_path.exists():
        print("  loser note not found (already merged?) — skipping")
        return

    sources = loser_source_stems(loser_path.read_text(encoding="utf-8"))
    hits = files_linking_to(config, pair.loser)
    total_matches = sum(n for _, n in hits)
    carried = ", ".join(sources) or "(none)"
    print(f"  carry {len(sources)} source(s) onto winner: {carried}")
    print(
        f"  relink {len(hits)} file(s), {total_matches} occurrence(s) of "
        f"[[{pair.loser}]]"
    )
    print(f"  archive {loser_path.relative_to(config.vault_path)} -> archive/")

    if not apply:
        return

    for source_stem in sources:
        nodes.append_link(config, winner_path, source_stem, heading="## Sources")
    for path, _n in hits:
        rewrite_links(config, path, pair.loser, pair.winner)
    inbox.archive_clip(config, loser_path)
    print("  applied.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--tier", choices=_TIER_ORDER, default="exact")
    parser.add_argument(
        "--apply", action="store_true", help="Write. Default is dry-run."
    )
    parser.add_argument("--vault-path", type=Path, default=None)
    args = parser.parse_args()

    if not args.map.exists():
        print(f"no map: {args.map} — run propose_merges.py first", file=sys.stderr)
        return 1

    config = (
        Config(BOWER_VAULT_PATH=str(args.vault_path)) if args.vault_path else Config()
    )
    pairs = parse_map(args.map.read_text(encoding="utf-8"))

    selected_tiers = set(_TIER_ORDER[: _TIER_ORDER.index(args.tier) + 1])
    todo = [p for p in pairs if p.tier in selected_tiers]
    print(
        f"{len(pairs)} pairs in map, {len(todo)} in scope for --tier {args.tier} "
        f"({sorted(selected_tiers)})\n"
    )

    for pair in todo:
        apply_pair(config, pair, apply=args.apply)
        print()

    if not args.apply:
        print("(dry-run — nothing written; pass --apply to write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
