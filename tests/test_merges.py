"""Tests for scripts/propose_merges.py + scripts/apply_merges.py — pure logic
+ a tmp vault, no network, no live-vault writes.

Run: uv run python tests/test_merges.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from apply_merges import (  # noqa: E402
    apply_pair,
    files_linking_to,
    loser_source_stems,
    parse_map,
)
from propose_merges import (  # noqa: E402
    MergeCandidate,
    classify_tier,
    format_map,
    landing_note_stems,
    load_concepts,
    load_feeders,
    propose,
    synthesized_stems,
)

from bower_bird.config import Config  # noqa: E402

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if not cond:
        _failures += 1
        print(f"FAIL: {msg}")


def _config(root: Path) -> Config:
    return Config(
        telegram_bot_token="x",
        vault_path=root,
        state_path=root / "state.json",
        fetch_timeout=15,
        queue_limit=100,
    )


def _note(title: str, sources: list[str] = ()) -> str:
    body = "\n".join(f"- [[{s}]]" for s in sources) or "_(none)_"
    return f'---\ntitle: "{title}"\n---\n# {title}\n\n## Sources\n{body}\n'


# --------------------------------------------------------------------------- #
# bower landing notes excluded from candidacy
# --------------------------------------------------------------------------- #


def test_landing_notes_excluded_from_candidacy() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers"
        (bowers / "agents").mkdir(parents=True)
        # A landing note: stem == the bower directory name.
        (bowers / "agents.md").write_text(_note("agents"), encoding="utf-8")
        # A real concept living inside that bower.
        (bowers / "agents" / "Agent architecture.md").write_text(
            _note("Agent architecture"), encoding="utf-8"
        )
        (bowers / "agents" / "Agent architectures.md").write_text(
            _note("Agent architectures"), encoding="utf-8"
        )

        stems = landing_note_stems(root / "brain" / "bowers")
        check("agents" in stems, f"agents.md recognised as a landing note, got {stems}")

        concepts = load_concepts(root / "brain" / "bowers")
        check("agents" not in concepts, "landing note excluded from candidacy")
        check("Agent architecture" in concepts, "real concept still a candidate")
        check("Agent architectures" in concepts, "real concept still a candidate")


# --------------------------------------------------------------------------- #
# tier classification
# --------------------------------------------------------------------------- #


def test_exact_tier_on_plural_variant() -> None:
    tier = classify_tier("Agent architectures", "Agent architecture", sim=1.0, shared=0)
    check(tier == "exact", f"plural variant classified exact, got {tier}")


def test_high_tier_needs_similarity_and_shared_source() -> None:
    tier = classify_tier("Foo bar baz", "Foo bar qux", sim=0.67, shared=1)
    check(tier == "high", f"sim>=0.67 + shared>=1 classified high, got {tier}")


def test_high_tier_rejected_without_shared_source() -> None:
    tier = classify_tier("Foo bar baz", "Foo bar qux", sim=0.67, shared=0)
    check(tier == "review", f"sim>=0.67 but shared=0 falls to review, got {tier}")


def test_low_similarity_falls_to_review() -> None:
    tier = classify_tier("AI security tools", "AI design tools", sim=0.5, shared=0)
    check(tier == "review", f"noisy near-miss classified review, got {tier}")


def test_propose_end_to_end_tiers_the_exact_pair() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers" / "agents"
        sources = root / "brain" / "sources"
        bowers.mkdir(parents=True)
        sources.mkdir(parents=True)
        (bowers / "Agent architecture.md").write_text(
            _note("Agent architecture"), encoding="utf-8"
        )
        (bowers / "Agent architectures.md").write_text(
            _note("Agent architectures"), encoding="utf-8"
        )
        for i in range(3):
            (sources / f"S{i}.md").write_text(
                f"# S{i}\n\n## Links\n- [[Agent architecture]]\n", encoding="utf-8"
            )
        (sources / "S3.md").write_text(
            "# S3\n\n## Links\n- [[Agent architectures]]\n", encoding="utf-8"
        )

        concepts = load_concepts(root / "brain" / "bowers")
        feeders = load_feeders(sources, concepts)
        candidates, dropped = propose(concepts, feeders, synthesized_stems(concepts))
        check(dropped == 0, f"no synthesized concepts here, got {dropped} dropped")
        exact = [c for c in candidates if c.tier == "exact"]
        check(len(exact) == 1, f"exactly one exact-tier pair, got {len(exact)}")
        if exact:
            check(exact[0].loser == "Agent architectures", "loser is fewer-sourced")
            check(exact[0].winner == "Agent architecture", "winner is more-fed")


# --------------------------------------------------------------------------- #
# synthesized concepts never lose a merge
# --------------------------------------------------------------------------- #


def test_pair_dropped_when_synthesized_concept_would_lose() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers" / "agents"
        sources = root / "brain" / "sources"
        bowers.mkdir(parents=True)
        sources.mkdir(parents=True)
        # "Agent architecture" has more feeding sources, so the plain
        # feeder-count rule makes the synthesized "Agent memory
        # architectures" the loser — which must kill the pair outright.
        (bowers / "Agent architecture.md").write_text(
            _note("Agent architecture"), encoding="utf-8"
        )
        (bowers / "Agent memory architectures.md").write_text(
            _note("Agent memory architectures") + "\n<!-- bower:concept -->\n",
            encoding="utf-8",
        )
        (sources / "S0.md").write_text(
            "# S0\n\n## Links\n- [[Agent architecture]]\n"
            "- [[Agent memory architectures]]\n",
            encoding="utf-8",
        )
        for i in (1, 2):
            (sources / f"S{i}.md").write_text(
                f"# S{i}\n\n## Links\n- [[Agent architecture]]\n", encoding="utf-8"
            )

        concepts = load_concepts(root / "brain" / "bowers")
        feeders = load_feeders(sources, concepts)
        synthesized = synthesized_stems(concepts)
        check(
            synthesized == {"Agent memory architectures"},
            f"synthesis marker detected, got {synthesized}",
        )

        # Dropped, not flipped. Flipping would propose merging the broad,
        # more-fed `Agent architecture` INTO the narrow synthesized one —
        # a direction nobody vetted, inverting the hierarchy to save prose.
        candidates, dropped = propose(concepts, feeders, synthesized)
        check(dropped == 1, f"pair dropped to protect the prose, got {dropped}")
        check(not candidates, f"no candidate proposed, got {len(candidates)}")


def test_both_synthesized_drops_pair() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers" / "agents"
        sources = root / "brain" / "sources"
        bowers.mkdir(parents=True)
        sources.mkdir(parents=True)
        marker = "\n<!-- bower:concept -->\n"
        (bowers / "Agent architecture.md").write_text(
            _note("Agent architecture") + marker, encoding="utf-8"
        )
        (bowers / "Agent memory architectures.md").write_text(
            _note("Agent memory architectures") + marker, encoding="utf-8"
        )
        (sources / "S0.md").write_text(
            "# S0\n\n## Links\n- [[Agent architecture]]\n"
            "- [[Agent memory architectures]]\n",
            encoding="utf-8",
        )

        concepts = load_concepts(root / "brain" / "bowers")
        feeders = load_feeders(sources, concepts)
        synthesized = synthesized_stems(concepts)
        candidates, dropped = propose(concepts, feeders, synthesized)
        check(len(candidates) == 0, f"no candidate produced, got {candidates}")
        check(dropped == 1, f"one pair dropped for both-synthesized, got {dropped}")


# --------------------------------------------------------------------------- #
# high tier reachable at exactly 2/3 (2-of-3 token overlap)
# --------------------------------------------------------------------------- #


def test_high_tier_reachable_at_two_of_three_overlap() -> None:
    tier = classify_tier("Foo bar baz", "Foo bar qux", sim=2 / 3, shared=1)
    check(
        tier == "high",
        f"2-of-3 overlap with a shared feeder classified high, got {tier}",
    )


# --------------------------------------------------------------------------- #
# tier output ranked by shared feeders, not sim
# --------------------------------------------------------------------------- #


def test_format_map_orders_by_shared_feeders() -> None:
    noise = MergeCandidate(
        loser="Legal AI tools",
        winner="AI design tools",
        tier="review",
        sim=0.5,
        shared=0,
        loser_sources=1,
        winner_sources=1,
    )
    signal = MergeCandidate(
        loser="Agent memory architectures",
        winner="Agent architecture",
        tier="review",
        sim=0.5,
        shared=3,
        loser_sources=1,
        winner_sources=3,
    )
    out = format_map([noise, signal])
    check(
        out.index("Agent memory architectures") < out.index("Legal AI tools"),
        "higher shared-feeder pair listed first despite equal sim",
    )


# --------------------------------------------------------------------------- #
# apply_merges: map parsing + dry-run writes nothing
# --------------------------------------------------------------------------- #


_MAP_TEXT = """\
## exact (1)
- [ ] [[AAs]] → [[AA]]  (exact, sim=1.00, shared=0, sources 1→3)

## high (1)
- [ ] [[Foo]] → [[Bar]]  (high, sim=0.70, shared=2, sources 2→5)

## review (1)
- [ ] [[Baz]] → [[Qux]]  (review, sim=0.40, shared=0, sources 1→1)
"""


def test_parse_map_reads_tier_from_each_line() -> None:
    pairs = parse_map(_MAP_TEXT)
    check(len(pairs) == 3, f"three pairs parsed, got {len(pairs)}")
    tiers = {p.loser: p.tier for p in pairs}
    check(tiers.get("AAs") == "exact", "exact pair tiered exact")
    check(tiers.get("Foo") == "high", "high pair tiered high")
    check(tiers.get("Baz") == "review", "review pair tiered review")


def test_loser_source_stems_reads_sources_heading_only() -> None:
    text = "# X\n\n## Links\n- [[Not a source]]\n\n## Sources\n- [[S0]]\n- [[S1]]\n"
    stems = loser_source_stems(text)
    check(stems == ["S0", "S1"], f"only ## Sources entries pulled, got {stems}")


def test_dry_run_writes_nothing() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers" / "agents"
        sources = root / "brain" / "sources"
        bowers.mkdir(parents=True)
        sources.mkdir(parents=True)
        winner_path = bowers / "Agent architecture.md"
        loser_path = bowers / "Agent architectures.md"
        winner_path.write_text(_note("Agent architecture"), encoding="utf-8")
        loser_path.write_text(_note("Agent architectures", ["S0"]), encoding="utf-8")
        source_path = sources / "S0.md"
        source_path.write_text(
            "# S0\n\n## Links\n- [[Agent architectures]]\n", encoding="utf-8"
        )

        before_winner = winner_path.read_text(encoding="utf-8")
        before_loser = loser_path.read_text(encoding="utf-8")
        before_source = source_path.read_text(encoding="utf-8")

        cfg = _config(root)
        pairs = parse_map(
            "- [ ] [[Agent architectures]] → [[Agent architecture]]  "
            "(exact, sim=1.00, shared=0, sources 1→0)\n"
        )
        apply_pair(cfg, pairs[0], apply=False)

        check(loser_path.exists(), "dry-run: loser file untouched (not archived)")
        check(
            winner_path.read_text(encoding="utf-8") == before_winner,
            "dry-run: winner file byte-identical",
        )
        check(
            loser_path.read_text(encoding="utf-8") == before_loser,
            "dry-run: loser file byte-identical",
        )
        check(
            source_path.read_text(encoding="utf-8") == before_source,
            "dry-run: source file byte-identical",
        )
        check(
            not (root / "archive").exists() or not any((root / "archive").iterdir()),
            "dry-run: nothing archived",
        )


def test_apply_relinks_carries_sources_and_archives_loser() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        bowers = root / "brain" / "bowers" / "agents"
        sources = root / "brain" / "sources"
        bowers.mkdir(parents=True)
        sources.mkdir(parents=True)
        winner_path = bowers / "Agent architecture.md"
        loser_path = bowers / "Agent architectures.md"
        winner_path.write_text(_note("Agent architecture"), encoding="utf-8")
        loser_path.write_text(_note("Agent architectures", ["S0"]), encoding="utf-8")
        source_path = sources / "S0.md"
        # Real source notes carry the same title twice: a bare-string
        # frontmatter `topics:` entry (the search-surface projection) AND a
        # `[[wikilink]]` under `## Links` — both must end up on the winner,
        # or `bb lint`'s topic-drift check fires (this is the bug the live
        # apply run caught: only the wikilink was rewritten).
        source_path.write_text(
            '---\ntitle: "S0"\ntopics:\n  - Agent architectures\n---\n'
            "# S0\n\n## Links\n- [[Agent architectures]]\n",
            encoding="utf-8",
        )

        cfg = _config(root)
        pairs = parse_map(
            "- [ ] [[Agent architectures]] → [[Agent architecture]]  "
            "(exact, sim=1.00, shared=0, sources 1→0)\n"
        )
        apply_pair(cfg, pairs[0], apply=True)

        check(not loser_path.exists(), "loser file moved out of brain/bowers/")
        archived = list((root / "archive").glob("Agent architectures*.md"))
        check(len(archived) == 1, f"loser archived, never hard-deleted: {archived}")

        winner_text = winner_path.read_text(encoding="utf-8")
        check("[[S0]]" in winner_text, "winner's ## Sources carries the loser's source")

        source_text = source_path.read_text(encoding="utf-8")
        check(
            "[[Agent architecture]]" in source_text
            and "[[Agent architectures]]" not in source_text,
            f"source's ## Links rewritten loser -> winner, got: {source_text}",
        )
        check(
            "  - Agent architecture\n" in source_text
            and "Agent architectures" not in source_text,
            f"frontmatter topics: rewritten loser -> winner too, got: {source_text}",
        )


def test_files_linking_to_finds_aliased_links() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        sources = root / "brain" / "sources"
        sources.mkdir(parents=True)
        (sources / "S0.md").write_text(
            "# S0\n\n[[Agent architectures|the old name]]\n", encoding="utf-8"
        )
        cfg = _config(root)
        hits = files_linking_to(cfg, "Agent architectures")
        check(len(hits) == 1, f"aliased link found, got {hits}")


if __name__ == "__main__":
    test_landing_notes_excluded_from_candidacy()
    test_exact_tier_on_plural_variant()
    test_high_tier_needs_similarity_and_shared_source()
    test_high_tier_rejected_without_shared_source()
    test_low_similarity_falls_to_review()
    test_propose_end_to_end_tiers_the_exact_pair()
    test_pair_dropped_when_synthesized_concept_would_lose()
    test_both_synthesized_drops_pair()
    test_high_tier_reachable_at_two_of_three_overlap()
    test_format_map_orders_by_shared_feeders()
    test_parse_map_reads_tier_from_each_line()
    test_loser_source_stems_reads_sources_heading_only()
    test_dry_run_writes_nothing()
    test_apply_relinks_carries_sources_and_archives_loser()
    test_files_linking_to_finds_aliased_links()

    if _failures:
        print(f"\n{_failures} test(s) failed.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
