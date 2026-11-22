"""Tests for `bb ask` (recall.py) — pure logic, no network, no LLM.

Run: uv run python tests/test_recall.py
"""

import sys
import tempfile
from pathlib import Path

from bower_bird.config import Config
from bower_bird.recall import ask, load_claims

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


_SOURCE_A = """\
---
title: "Forgetting curves"
id: id-a
source: "https://example.com/a"
topics:
  - Agentic loops
---
# Forgetting curves

## Key ideas
- Agent memory needs a forgetting curve or context fills with stale facts
- Unrelated claim about pastry recipes
"""

_SOURCE_B = """\
---
title: "Unrelated source"
id: id-b
---
# Unrelated source

## Key ideas
- Agent memory helps loops stay grounded across long sessions
"""

_CONCEPT = """\
---
title: "Agentic loops"
id: id-c
---
# Agentic loops

<!-- bower:concept -->
## Concept
- Agent memory is the load-bearing piece of any agentic loop
<!-- /bower:concept -->
"""


def _build_vault(root: Path) -> None:
    sources = root / "brain" / "sources"
    bowers = root / "brain" / "bowers"
    sources.mkdir(parents=True)
    bowers.mkdir(parents=True)
    (sources / "Forgetting curves.md").write_text(_SOURCE_A, encoding="utf-8")
    (sources / "Unrelated source.md").write_text(_SOURCE_B, encoding="utf-8")
    (bowers / "Agentic loops.md").write_text(_CONCEPT, encoding="utf-8")


def test_load_claims_finds_source_and_concept_bullets() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _build_vault(root)
        cfg = _config(root)
        claims = load_claims(cfg)
        check(len(claims) == 4, f"expected 4 claims, got {len(claims)}")
        kinds = {c.kind for c in claims}
        check(kinds == {"source", "concept"}, f"both kinds present, got {kinds}")
        a = next(c for c in claims if c.node == "Forgetting curves")
        check(
            a.url == "https://example.com/a",
            f"url carried from frontmatter, got {a.url}",
        )
        check(
            a.topics == ["Agentic loops"],
            f"topics carried from frontmatter, got {a.topics}",
        )


def _hits(groups):
    return [hit for group in groups for hit in group.hits]


def test_ask_returns_matching_sources_and_their_claims() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _build_vault(root)
        cfg = _config(root)
        hits = _hits(ask(cfg, "agent memory"))
        nodes = {h.node for h in hits}
        check(
            nodes == {"Forgetting curves", "Unrelated source"},
            f"both matching sources returned, got {nodes}",
        )
        check(
            "pastry" not in " ".join(c for h in hits for c in h.claims),
            "zero-overlap claim not shown",
        )


def test_ask_limit_truncates() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        _build_vault(root)
        cfg = _config(root)
        hits = _hits(ask(cfg, "agent memory", limit=1))
        check(len(hits) == 1, f"limit=1 truncates to 1 result, got {len(hits)}")


if __name__ == "__main__":
    test_load_claims_finds_source_and_concept_bullets()
    test_ask_returns_matching_sources_and_their_claims()
    test_ask_limit_truncates()

    if _failures:
        print(f"\n{_failures} test(s) failed.")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
