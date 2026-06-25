"""Ingest + inbox tests — pure logic on a temp vault, no network.

Run: uv run python tests/test_ingest.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bower_bird import inbox, ingest  # noqa: E402
from bower_bird.config import Config  # noqa: E402
from bower_bird.fetch import PageMeta, needs_clipping  # noqa: E402
from bower_bird.llm import ClippingPlan  # noqa: E402
from bower_bird.marks import Marks, extract_marks  # noqa: E402

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


def test_clip_queue() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)

        added = ingest.append_to_clip_queue(cfg, "https://x.com/a/status/1")
        check(added is True, "first clip-queue add returns True")
        dup = ingest.append_to_clip_queue(cfg, "https://x.com/a/status/1")
        check(dup is False, "duplicate clip-queue URL returns False")
        cq = cfg.to_clip_path.read_text(encoding="utf-8")
        check(cq.count("https://x.com/a/status/1") == 1, "url listed once")
        check("- [ ] https://x.com/a/status/1" in cq, "bare url checkbox when no title")

        ingest.append_to_clip_queue(cfg, "https://x.com/b", title="A Tweet")
        cq2 = cfg.to_clip_path.read_text(encoding="utf-8")
        check("- [ ] [A Tweet](https://x.com/b)" in cq2, "titled checkbox entry")


def test_tools_shelf() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)

        url = "https://github.com/x/y"
        added = ingest.append_to_tools(
            cfg, url, "x/y", "A CLI tool", note="claude loop plugin"
        )
        check(added is True, "first tools add returns True")
        dup = ingest.append_to_tools(cfg, url, "x/y", "A CLI tool")
        check(dup is False, "duplicate tools URL returns False")
        ts = cfg.tools_path.read_text(encoding="utf-8")
        check(f"[x/y]({url})" in ts, "tool link stored")
        check("claude loop plugin · A CLI tool" in ts, "user note first for recall")
        # tools shelf never touches the knowledge layer
        check(not cfg.brain_dir.exists(), "tools shelf does not create brain/")


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
            topics=["Caching", "Cache Invalidation"],
            connection="both about caches",
        )
        marks = Marks(
            highlights=["a kept passage"], further_links=[("Ref", "https://r.co")]
        )
        path = ingest.create_source_note(
            cfg, meta, plan, note="why it matters", marks=marks
        )
        check(path is not None and path.exists(), "source note written")
        src = path.read_text(encoding="utf-8")
        has_links = "[[Caching]]" in src and "[[Cache Invalidation]]" in src
        check(has_links, "links in source")
        check("## Note\nwhy it matters" in src, "human note stored")
        check("> a kept passage" in src, "highlight stored verbatim")
        check("[Ref](https://r.co)" in src, "further-reading link stored")
        check("## Captured" not in src, "full article body NOT stored")

        # reciprocal links asserted into notes/ (new concept note created)
        concept = cfg.notes_dir / "Cache Invalidation.md"
        check(concept.exists(), "new concept note created in notes/")
        concept_text = concept.read_text(encoding="utf-8")
        check("[[Cool Post]]" in concept_text, "reciprocal link")
        check(concept_text.startswith("---"), "new concept note has frontmatter")
        check("# Cache Invalidation" in concept_text, "new concept note has title")

        # filename-level dedup
        again = ingest.create_source_note(cfg, meta, plan)
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
            topics=["Verifiers"],
            connection="",
        )
        ingest.create_source_note(cfg, meta, plan)
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


def test_extract_marks() -> None:
    body = (
        "this is my article\n"
        "woo words ==a nugget I liked==\n"
        "> ? why havent they gotten to the point\n"
        "> is it length or bad editing\n"
        "how do verifiers generalize #dig\n"
        "a plain [reference](https://other.com/x) and the [self](https://self.co)\n"
        "> just a normal article quote, not a question\n"
    )
    m = extract_marks(body, self_url="https://self.co")
    check(m.highlights == ["a nugget I liked"], "highlight extracted")
    expect_q = "why havent they gotten to the point is it length or bad editing"
    check(
        m.questions == [expect_q],
        "multi-line question folded; plain quote ignored",
    )
    check(m.dig == ["how do verifiers generalize"], "#dig line extracted, tag stripped")
    check(
        m.further_links == [("reference", "https://other.com/x")],
        "outbound link kept, self-url dropped",
    )
    check(not m.empty, "marks not empty")


def test_dig_word_boundary() -> None:
    m = extract_marks("nothing here #digest or #digging\n")
    check(m.dig == [], "#digest / #digging do not trigger #dig")


def test_fetch_helpers() -> None:
    check(needs_clipping("https://x.com/a/status/1"), "x.com needs clipping")
    check(needs_clipping("https://twitter.com/a"), "twitter.com needs clipping")
    check(needs_clipping("https://mobile.twitter.com/a"), "subdomain needs clipping")
    check(not needs_clipping("https://example.com/p"), "normal site fetches fine")
    check(not needs_clipping("https://github.com/x/y"), "github fetches fine")

    u = "https://q.co"
    thin = PageMeta(url=u, title=u, description="", body_excerpt="")
    check(thin.is_thin, "no title/desc is thin")
    full = PageMeta(url=u, title="Real", description="", body_excerpt="")
    check(not full.is_thin, "a real title is not thin")


def test_conflict_files_skipped() -> None:
    check(not inbox._is_processable(Path("note (conflicted copy).md")), "skip conflict")
    check(not inbox._is_processable(Path(".hidden.md")), "skip dotfile")
    check(inbox._is_processable(Path("normal.md")), "process normal file")


def main() -> int:
    test_assert_writable()
    test_append_link_additive_and_idempotent()
    test_reading_list_and_inbox()
    test_clip_queue()
    test_tools_shelf()
    test_create_source_note_asserts_links()
    test_links_into_existing_nested_concept()
    test_parse_clip()
    test_extract_marks()
    test_dig_word_boundary()
    test_fetch_helpers()
    test_conflict_files_skipped()
    if _failures:
        print(f"\n{_failures} failure(s).")
        return 1
    print("OK: ingest + inbox tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
