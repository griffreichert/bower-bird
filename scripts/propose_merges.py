"""Read-only merge-candidate proposal for `brain/bowers/` concepts.

Measured 2026-07-25 (`notes/2026-07-25-retrieval-plan.md`, Step 4): 213/320
concepts have exactly 1 feeding source, 11 have zero — the singleton tail is
mostly near-duplicate titles Haiku coined instead of reusing (`Agent
architecture` / `Agent architectures`), not genuinely new concepts. Merging
them is a precondition for concept synthesis (INVARIANTS: a concept note
earns substance only when >=2 sources converge on it — a title split three
ways never gets there).

Token-set (Jaccard) similarity over normalised title words, plus shared
feeding-source count, tiered by confidence:

  - `exact`  — identical after case-fold + singular/plural normalisation
               (`Agent architectures` == `Agent architecture`).
  - `high`   — sim >= 2/3 AND >= 1 shared feeding source (2/3 is the exact
               Jaccard of the commonest near-duplicate shape: 2-of-3 token
               overlap).
  - `review` — everything else that clears the noise floor.

A concept carrying a `<!-- bower:concept -->` synthesis block — hand-earned
prose with citations — never loses a merge. Any pair whose computed loser is
synthesized is dropped outright rather than flipped: flipping fabricates a
merge direction nobody vetted, and it inverted the hierarchy in practice
(proposing broad `Agent architecture` INTO narrow `Agent memory
architectures` to protect the prose). Within each tier, pairs are ranked by
title similarity, with shared feeding sources as the tie-break — shared
feeders measure co-occurrence, and co-occurrence is not synonymy.

Bower landing notes (`agents`, `llms`, `learning`, `research`, `life`, `nfl`,
`ai-and-work` — one per `brain/bowers/` subdirectory, matching its name
exactly) are excluded from candidacy entirely: they match everything
containing their word and produced garbage pairs in the first dry run
(`AI security tools -> AI design tools`, `Cost optimization in infrastructure
-> Cost optimization in prompting`).

Writes `notes/2026-07-25-merge-map.md`, grouped by tier, for Griffin to
review. Never touches the vault — `apply_merges.py` does the writing, and
only after this file is read (and, for anything above `exact`, edited/
approved).

Run: uv run python scripts/propose_merges.py [--out PATH] [--vault-path PATH]
"""

import argparse
import re
import sys
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bower_bird.config import Config  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "notes" / "2026-07-25-merge-map.md"

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_STOPWORDS = {
    "for",
    "in",
    "of",
    "and",
    "the",
    "a",
    "to",
    "with",
    "on",
    "as",
    "systems",
    "design",
}
_SYNTH_MARKER = "<!-- bower:concept"
# Exact fraction, not 0.67: the commonest near-duplicate shape is 2-of-3
# token overlap (Jaccard = 2/3 = 0.6667), which 0.67 excludes by a hair —
# don't "tidy" this back to a rounded literal.
_HIGH_SIM = 2 / 3
_TIERS = ("exact", "high", "review")


def norm_words(title: str) -> frozenset[str]:
    """Casefold + tokenize + naive singularize, stopwords dropped — the token
    set the Jaccard similarity runs over."""
    words = re.findall(r"[a-z0-9]+", title.lower())
    singular = {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words}
    return frozenset(singular - _STOPWORDS)


def norm_exact(title: str) -> tuple[str, ...]:
    """Casefold + naive singularize, ORDER preserved, stopwords kept. Used
    only for the `exact` tier — a trivial rename/pluralisation of the same
    concept, not just topically-close titles."""
    words = re.findall(r"[a-z0-9]+", title.lower())
    return tuple(w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words)


def landing_note_stems(notes_dir: Path) -> set[str]:
    """Concept stems that are bower landing pages — one per `brain/bowers/`
    subdirectory, named to match it exactly (`agents/` -> `agents.md`).
    These are folder indexes, not concepts: excluded from candidacy entirely,
    never a winner OR a loser."""
    if not notes_dir.is_dir():
        return set()
    return {d.name for d in notes_dir.iterdir() if d.is_dir()}


def load_concepts(notes_dir: Path) -> dict[str, Path]:
    excluded = landing_note_stems(notes_dir)
    if not notes_dir.is_dir():
        return {}
    return {
        p.stem: p
        for p in notes_dir.rglob("*.md")
        if not p.stem.startswith("_") and p.stem not in excluded
    }


def synthesized_stems(concepts: dict[str, Path]) -> set[str]:
    """Concept stems whose file carries a `<!-- bower:concept ... -->`
    synthesis block — hand-earned prose with citations. These must never be
    the merge loser."""
    return {
        stem
        for stem, path in concepts.items()
        if _SYNTH_MARKER in path.read_text(encoding="utf-8", errors="ignore")
    }


def load_feeders(sources_dir: Path, concepts: dict[str, Path]) -> dict[str, set[str]]:
    """Per concept, the set of `brain/sources/*.md` stems that wikilink to it."""
    feeders: dict[str, set[str]] = {c: set() for c in concepts}
    if not sources_dir.is_dir():
        return feeders
    for path in sorted(sources_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for target in _WIKILINK_RE.findall(text):
            target = target.strip()
            if target in feeders:
                feeders[target].add(path.stem)
    return feeders


@dataclass(frozen=True)
class MergeCandidate:
    loser: str
    winner: str
    tier: str
    sim: float
    shared: int
    loser_sources: int
    winner_sources: int

    def line(self) -> str:
        return (
            f"- [ ] [[{self.loser}]] → [[{self.winner}]]  "
            f"({self.tier}, sim={self.sim:.2f}, shared={self.shared}, "
            f"sources {self.loser_sources}→{self.winner_sources})"
        )


def classify_tier(a: str, b: str, sim: float, shared: int) -> str:
    if norm_exact(a) == norm_exact(b):
        return "exact"
    if sim >= _HIGH_SIM and shared >= 1:
        return "high"
    return "review"


def propose(
    concepts: dict[str, Path],
    feeders: dict[str, set[str]],
    synthesized: set[str],
) -> tuple[list[MergeCandidate], int]:
    """Returns (candidates, pairs dropped to protect a synthesized concept)."""
    candidates = []
    dropped = 0
    for a, b in combinations(sorted(concepts), 2):
        ta, tb = norm_words(a), norm_words(b)
        if not ta or not tb:
            continue
        sim = len(ta & tb) / len(ta | tb)
        shared = len(feeders[a] & feeders[b])
        # Noise floor. Title similarity is the ONLY admission test: shared
        # feeders used to buy a low-similarity pair in (`sim < 0.45 and
        # shared < 3`), on the theory that co-occurring titles are probably
        # one concept. Measured 2026-07-29 — all three pairs that hatch
        # admitted were junk (`Tool design for agents` <-> `Agent
        # architecture`, shared=11; `Agentic loops` <-> `Loop engineering`,
        # shared=8; `Agentic loops` <-> `Human-in-the-loop`, shared=4).
        # Co-occurrence is not synonymy — two concepts that legitimately
        # appear together in many sources are exactly the ones NOT to merge.
        # Shared feeders stays as a confirmer inside `classify_tier`, where
        # high similarity has already been established.
        if sim < 0.45:
            continue
        na, nb = len(feeders[a]), len(feeders[b])
        # Winner = more feeding sources (more established); tie-break shorter
        # title (the less redundant name).
        winner, loser = (a, b) if (na, -len(a)) >= (nb, -len(b)) else (b, a)

        # A synthesized concept never loses — and we don't flip to save the
        # pair either. Flipping fabricates a merge direction nobody vetted:
        # it proposed `Agent architecture` (31 sources, broad) INTO
        # `Agent memory architectures` (10 sources, narrow), inverting the
        # hierarchy to protect the prose. Any merge touching earned synthesis
        # is a human judgement call, so drop the pair and say so.
        if loser in synthesized:
            dropped += 1
            continue

        tier = classify_tier(a, b, sim, shared)
        candidates.append(
            MergeCandidate(
                loser=loser,
                winner=winner,
                tier=tier,
                sim=sim,
                shared=shared,
                loser_sources=len(feeders[loser]),
                winner_sources=len(feeders[winner]),
            )
        )
    return candidates, dropped


_HEADER = """\
---
author: claude
date: 2026-07-29
---

# Merge candidates — brain/bowers concepts

Generated by `scripts/propose_merges.py`. Read-only proposal.
`scripts/apply_merges.py` applies the `exact` tier by default; `high`/`review`
need Griffin's approval first (re-run apply with `--tier high` once this file
has been reviewed/edited).

A concept carrying a `<!-- bower:concept -->` synthesis block — hand-earned
prose with citations — never loses a merge. Any pair whose computed loser is
synthesized is dropped outright rather than flipped — merging into or out of
earned synthesis is a human judgement call, not a checkbox. Within each
tier, pairs are ranked by title similarity, with shared feeding sources as
the tie-break — shared feeders measure co-occurrence, not synonymy.

Bower landing notes (`agents`, `llms`, `learning`, `research`, `life`, `nfl`,
`ai-and-work`) are excluded from candidacy — they match everything
containing their word and produce garbage pairs.
"""


def format_map(candidates: list[MergeCandidate]) -> str:
    parts = [_HEADER]
    by_tier = {tier: [c for c in candidates if c.tier == tier] for tier in _TIERS}
    for tier in _TIERS:
        # Similarity first, shared feeders as the tie-break. Ranking by
        # shared feeders instead surfaced co-occurring but distinct concepts
        # at the top of the list (measured 2026-07-29) — synonymy is what a
        # merge needs, and only the title similarity measures that.
        group = sorted(by_tier[tier], key=lambda c: (-c.sim, -c.shared))
        parts.append(f"\n## {tier} ({len(group)})\n")
        parts.extend(f"{c.line()}\n" for c in group)
    return "".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--vault-path",
        type=Path,
        default=None,
        help="Override the vault path (defaults to Config's resolution).",
    )
    args = parser.parse_args()

    config = (
        Config(BOWER_VAULT_PATH=str(args.vault_path)) if args.vault_path else Config()
    )
    concepts = load_concepts(config.notes_dir)
    feeders = load_feeders(config.sources_dir, concepts)
    synthesized = synthesized_stems(concepts)
    candidates, dropped_synth = propose(concepts, feeders, synthesized)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(format_map(candidates), encoding="utf-8")

    counts = {tier: sum(1 for c in candidates if c.tier == tier) for tier in _TIERS}
    orphans = sum(1 for c in concepts if not feeders[c])
    singles = sum(1 for c in concepts if len(feeders[c]) == 1)
    multi = len(concepts) - orphans - singles
    print(f"{len(concepts)} candidate concepts ({multi} with >=2 feeding sources)")
    print(f"{len(candidates)} merge candidates -> {args.out}")
    print(f"  exact={counts['exact']} high={counts['high']} review={counts['review']}")
    print(f"  dropped (synthesized side would lose): {dropped_synth}")
    print(
        f"orphans (0 feeders): {orphans}, singletons (1 feeder): {singles}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
