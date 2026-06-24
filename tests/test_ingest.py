"""Ingest + inbox tests — pure logic on a temp vault, no network.

Run: uv run python tests/test_ingest.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bower_bird import inbox, ingest  # noqa: E402
from bower_bird.config import Config  # noqa: E402
from bower_bird.fetch import PageMeta  # noqa: E402
from bower_bird.llm import ClippingPlan  # noqa: E402

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
        model="test",
        state_path=root / "state.json",
        fetch_timeout=15,
        drain_limit=100,
    )


def test_assert_writable() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        # inside is fine
        ingest._assert_writable(cfg, root / "sources" / "x.md")
        # outside the owned folder is refused
        try:
            ingest._assert_writable(cfg, Path(d) / "elsewhere.md")
            check(False, "expected PermissionError writing outside owned folder")
        except PermissionError:
            pass


def test_append_link_additive_and_idempotent() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        note = root / "notes" / "Caching.md"
        note.parent.mkdir(parents=True)
        note.write_text(
            "---\ntitle: Caching\n---\n# Caching\n\nMy own prose.\n",
            encoding="utf-8",
        )

        ingest._append_link(cfg, note, "Some Source")
        text = note.read_text(encoding="utf-8")
        check("My own prose." in text, "human prose preserved on append")
        check("- [[Some Source]]" in text, "link appended")
        check("## Links" in text, "Links heading created")

        # idempotent: second append does not duplicate
        ingest._append_link(cfg, note, "Some Source")
        text2 = note.read_text(encoding="utf-8")
        check(text2.count("- [[Some Source]]") == 1, "append is idempotent")


def test_reading_list_and_inbox() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)

        added = ingest.append_to_reading_list(cfg, "https://a.co", "A", "what is a")
        check(added is True, "first reading-list add returns True")
        dup = ingest.append_to_reading_list(cfg, "https://a.co", "A", "what is a")
        check(dup is False, "duplicate reading-list URL returns False")
        rl = cfg.reading_list_path.read_text(encoding="utf-8")
        check(rl.count("https://a.co") == 1, "url listed once")

        ingest.append_to_telegram_inbox(cfg, "random thought", reason="no link")
        ib = cfg.telegram_inbox_path.read_text(encoding="utf-8")
        check("random thought" in ib, "telegram inbox captured the message")
        check("(no link)" in ib, "telegram inbox recorded the reason")


def test_create_source_note_asserts_links() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        meta = PageMeta(
            url="https://x.co/p", title="Cool Post", description="", body_excerpt="body"
        )
        plan = ClippingPlan(
            description="A blog post",
            proposed_backlinks=["Caching"],
            proposed_note_title="Cache Invalidation",
            connection="both about caches",
        )
        path = ingest.create_source_note(cfg, meta, "the captured body", plan)
        check(path is not None and path.exists(), "source note written")
        src = path.read_text(encoding="utf-8")
        has_links = "[[Caching]]" in src and "[[Cache Invalidation]]" in src
        check(has_links, "links in source")
        check("the captured body" in src, "captured content stored")

        # reciprocal links asserted into notes/ (new concept note created)
        concept = cfg.notes_dir / "Cache Invalidation.md"
        check(concept.exists(), "new concept note created in notes/")
        concept_text = concept.read_text(encoding="utf-8")
        check("[[Cool Post]]" in concept_text, "reciprocal link")
        check(concept_text.startswith("---"), "new concept note has frontmatter")
        check("# Cache Invalidation" in concept_text, "new concept note has title")

        # filename-level dedup
        again = ingest.create_source_note(cfg, meta, "x", plan)
        check(again is None, "duplicate source note returns None")


def test_links_into_existing_nested_concept() -> None:
    """A concept living in a nest gets the reciprocal link there — not a flat dup."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        nested = cfg.notes_dir / "llms" / "Verifiers.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("---\ntitle: Verifiers\n---\n# Verifiers\n", encoding="utf-8")

        resolved = ingest.find_concept_path(cfg, "Verifiers")
        check(resolved == nested, "find_concept_path resolves into the nest")

        meta = PageMeta(
            url="https://x.co/v", title="RL Post", description="", body_excerpt="b"
        )
        plan = ClippingPlan(
            description="post",
            proposed_backlinks=["Verifiers"],
            proposed_note_title="",
            connection="",
        )
        ingest.create_source_note(cfg, meta, "body", plan)
        check(
            "[[RL Post]]" in nested.read_text(encoding="utf-8"),
            "link into nested concept",
        )
        flat_dup = cfg.notes_dir / "Verifiers.md"
        check(not flat_dup.exists(), "no flat duplicate created")


def test_parse_clip() -> None:
    raw = (
        "---\n"
        'title: "My Clip"\n'
        "source: https://example.com/a\n"
        "description: a thing\n"
        "tags:\n  - clippings\n"
        "---\n"
        "# My Clip\n\nThe real content here.\n"
    )
    fm, body = inbox._parse_clip(raw)
    check(fm.get("title") == "My Clip", "clip title parsed")
    check(fm.get("source") == "https://example.com/a", "clip source parsed")
    check(body.startswith("# My Clip"), "body separated from frontmatter")
    check("The real content here." in body, "body content retained")


def test_conflict_files_skipped() -> None:
    check(not inbox._is_processable(Path("note (conflicted copy).md")), "skip conflict")
    check(not inbox._is_processable(Path(".hidden.md")), "skip dotfile")
    check(inbox._is_processable(Path("normal.md")), "process normal file")


def main() -> int:
    test_assert_writable()
    test_append_link_additive_and_idempotent()
    test_reading_list_and_inbox()
    test_create_source_note_asserts_links()
    test_links_into_existing_nested_concept()
    test_parse_clip()
    test_conflict_files_skipped()
    if _failures:
        print(f"\n{_failures} failure(s).")
        return 1
    print("OK: ingest + inbox tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
