"""Migration script: peck rewrite (#18 / #20) — antilibrary review store.

Run manually ONLY: ``uv run python scripts/migrate_review.py``. Never wired
into a cron/launchd path, and never run automatically by any other tool in
this repo. Do NOT run it against the real vault without eyeballing the
printed summary and confirming.

Three steps (each explained in the summary before the y/N confirm, default No):
  1. Archive the existing ``_review.json`` to ``_review.pre-antilibrary.json``
     — nothing is lost; the old bower-keyed history is retired, not deleted.
  2. Write a fresh ``_review.json`` enrolling every ``brain/sources/`` node
     (keyed by its frontmatter ``id:``, minting one if the note predates the
     antilibrary model) at box 0, due today. Concept→source review history
     does not carry over — it was earned on a different unit (#18).
  3. Strip ``**Test question:** … **Model answer:** …`` Feynman blocks out of
     ``brain/bowers/*.md`` — the bot-authored plastic layer the fresh-question
     design (``llm.generate_question``) replaces. This is the ONE permitted
     edit to those files; nothing else in them is touched.
"""

import re
import shutil
import sys
import uuid
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from bower_bird.config import Config
from bower_bird.review import Review, ReviewStore

_ID_RE = re.compile(r"^id:\s*(.+)$", re.MULTILINE)

# From ingest._CONCEPT_SECTION: "**Test question:** ...\n\n**Model answer:** ..."
# sitting inside the <!-- bower:concept --> sentinel alongside Definition/Why,
# which stay untouched. Lazy match up to the sentinel close (or EOF).
_FEYNMAN_BLOCK_RE = re.compile(
    r"\n*\*\*Test question:\*\*.*?\*\*Model answer:\*\*.*?"
    r"(?=\n<!-- /bower:concept -->|\Z)",
    re.DOTALL,
)


def _ensure_source_id(path: Path) -> tuple[str, bool]:
    """Return (id, minted) — inject a fresh ``id:`` into frontmatter if absent.

    Append-once, mirroring ``ingest._inject_id_into_frontmatter`` for bowers:
    source notes written before this feature don't carry an id yet.
    """
    text = path.read_text(encoding="utf-8")
    m = _ID_RE.search(text)
    if m:
        return m.group(1).strip(), False
    node_id = str(uuid.uuid4())
    if not text.startswith("---"):
        return node_id, False  # no frontmatter to inject into — leave the file alone
    first_newline = text.index("\n")
    text = text[: first_newline + 1] + f"id: {node_id}\n" + text[first_newline + 1 :]
    path.write_text(text, encoding="utf-8")
    return node_id, True


def strip_feynman_blocks(text: str) -> str:
    """Remove the Test question / Model answer lines. Pure — used by tests too."""
    return _FEYNMAN_BLOCK_RE.sub("\n", text)


def _source_paths(config: Config) -> list[Path]:
    if not config.sources_dir.is_dir():
        return []
    return sorted(config.sources_dir.rglob("*.md"))


def _bower_paths(config: Config) -> list[Path]:
    if not config.notes_dir.is_dir():
        return []
    return sorted(config.notes_dir.rglob("*.md"))


def build_summary(config: Config) -> list[str]:
    """Dry-run: describe what would change, without writing anything."""
    lines: list[str] = []

    old_path = config.review_path
    if old_path.exists():
        lines.append(f"1. Archive {old_path.name} -> _review.pre-antilibrary.json")
    else:
        lines.append("1. No existing _review.json — nothing to archive.")

    sources = _source_paths(config)
    to_mint = sum(
        1 for p in sources if _ID_RE.search(p.read_text(encoding="utf-8")) is None
    )
    lines.append(
        f"2. Enrol {len(sources)} source node(s) at box 0, due today "
        f"({to_mint} need a fresh id: minted)."
    )

    stripped = sum(
        1
        for p in _bower_paths(config)
        if _FEYNMAN_BLOCK_RE.search(p.read_text(encoding="utf-8"))
    )
    lines.append(
        f"3. Strip Feynman test-question/model-answer blocks from "
        f"{stripped} bower note(s)."
    )

    return lines


def run_migration(config: Config) -> tuple[int, int, int]:
    """Execute the three steps for real. Returns (enrolled, minted, stripped)."""
    old_path = config.review_path
    if old_path.exists():
        archive_path = config.vault_path / "_review.pre-antilibrary.json"
        shutil.copy2(old_path, archive_path)
        print(f"  archived -> {archive_path.name}")

    store = ReviewStore(config, {})
    minted = 0
    for path in _source_paths(config):
        node_id, was_minted = _ensure_source_id(path)
        if was_minted:
            minted += 1
        store._entries[node_id] = Review(
            id=node_id,
            due=date.today().isoformat(),
            box=0,
            last_grade=None,
            reviews=[],
        )
    store.save()
    print(f"  enrolled {len(store._entries)} source node(s) ({minted} ids minted)")

    stripped = 0
    for path in _bower_paths(config):
        text = path.read_text(encoding="utf-8")
        new_text = strip_feynman_blocks(text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            stripped += 1
    print(f"  stripped Feynman blocks from {stripped} bower note(s)")

    return len(store._entries), minted, stripped


def main() -> int:
    try:
        config = Config()
    except ValidationError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    print(f"peck migration — vault: {config.vault_path}\n")
    for line in build_summary(config):
        print(line)
    print()
    answer = input("Proceed? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted — nothing written.")
        return 1
    run_migration(config)
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
