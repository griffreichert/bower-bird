"""Ingest + inbox tests — pure logic on a temp vault, no network.

Run: uv run python tests/test_ingest.py
"""

import tempfile
from pathlib import Path

from bower_bird import app, inbox, ingest, nodes, queues
from bower_bird.config import Config
from bower_bird.fetch import needs_clipping
from bower_bird.marks import Marks, extract_marks, extract_urls, pick_source_url
from bower_bird.schema import ClippingPlan, EntityRef, PageMeta
from bower_bird.state import State

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


def test_assert_writable() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        # inside is fine
        ingest.assert_writable(cfg, root / "sources" / "x.md")
        # outside the owned folder is refused
        try:
            ingest.assert_writable(cfg, Path(d) / "elsewhere.md")
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

        nodes.append_link(cfg, note, "Some Source")
        text = note.read_text(encoding="utf-8")
        check("My own prose." in text, "human prose preserved on append")
        check("- [[Some Source]]" in text, "link appended")
        check("## Links" in text, "Links heading created")

        # idempotent: second append does not duplicate
        nodes.append_link(cfg, note, "Some Source")
        text2 = note.read_text(encoding="utf-8")
        check(text2.count("- [[Some Source]]") == 1, "append is idempotent")


def test_telegram_inbox() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)

        queues.append_to_telegram_inbox(cfg, "random thought", reason="no link")
        ib = cfg.telegram_inbox_path.read_text(encoding="utf-8")
        check("random thought" in ib, "telegram inbox captured the message")
        check("(no link)" in ib, "telegram inbox recorded the reason")


def test_clip_queue() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)

        added = queues.append_to_clip_queue(cfg, "https://x.com/a/status/1")
        check(added is True, "first clip-queue add returns True")
        dup = queues.append_to_clip_queue(cfg, "https://x.com/a/status/1")
        check(dup is False, "duplicate clip-queue URL returns False")
        cq = cfg.to_clip_path.read_text(encoding="utf-8")
        check(cq.count("https://x.com/a/status/1") == 1, "url listed once")
        check("- [ ] https://x.com/a/status/1" in cq, "bare url checkbox when no title")

        queues.append_to_clip_queue(cfg, "https://x.com/b", title="A Tweet")
        cq2 = cfg.to_clip_path.read_text(encoding="utf-8")
        check("- [ ] [A Tweet](https://x.com/b)" in cq2, "titled checkbox entry")


def test_tools_shelf() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)

        url = "https://github.com/x/y"
        added = queues.append_to_tools(
            cfg, url, "x/y", "A CLI tool", note="claude loop plugin"
        )
        check(added is True, "first tools add returns True")
        dup = queues.append_to_tools(cfg, url, "x/y", "A CLI tool")
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
            url="https://x.co/p",
            title="Cool Post: The Very Long Clickbait Subtitle Edition",
            description="",
            body_excerpt="body",
        )
        plan = ClippingPlan(
            concise_title="Cool Post",
            description="A blog post",
            topics=["Caching", "Cache Invalidation"],
            key_ideas=["caches go stale", "invalidation is hard"],
        )
        marks = Marks(
            highlights=["a kept passage"], further_links=[("Ref", "https://r.co")]
        )
        path = nodes.create_source_note(
            cfg,
            meta,
            plan,
            note="why it matters",
            marks=marks,
            full_body="The full rendered article body.",
        )
        check(path is not None and path.exists(), "source note written")
        check(path.stem == "Cool Post", "concise title used for graph node, not raw")
        src = path.read_text(encoding="utf-8")
        has_links = "[[Caching]]" in src and "[[Cache Invalidation]]" in src
        check(has_links, "links in source")
        check(
            "## Seed thoughts\n- why it matters" in src,
            "seed thought stored verbatim as a bullet",
        )
        has_ideas = "## Key ideas" in src and "- caches go stale" in src
        check(has_ideas, "key ideas distilled onto the node")
        check(
            "## Body\nThe full rendered article body." in src,
            "full body inlined under ## Body",
        )
        # #13 section order: Key ideas → Seed thoughts → Links → marks → Body.
        order = [
            src.index("## Key ideas"),
            src.index("## Seed thoughts"),
            src.index("## Links"),
            src.index("## Highlights"),
            src.index("## Body"),
        ]
        check(order == sorted(order), f"schema section order holds: {order}")
        check("> a kept passage" in src, "highlight stored verbatim")
        check("[Ref](https://r.co)" in src, "further-reading link stored")

        # reciprocal links asserted into notes/ (new concept note created)
        concept = cfg.notes_dir / "Cache Invalidation.md"
        check(concept.exists(), "new concept note created in notes/")
        concept_text = concept.read_text(encoding="utf-8")
        check("[[Cool Post]]" in concept_text, "reciprocal link")
        check(concept_text.startswith("---"), "new concept note has frontmatter")
        check("# Cache Invalidation" in concept_text, "new concept note has title")

        # filename-level dedup
        again = nodes.create_source_note(cfg, meta, plan)
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

        resolved = nodes.find_concept_path(cfg, "Verifiers")
        check(resolved == nested, "find_concept_path resolves into the nest")

        meta = PageMeta(
            url="https://x.co/v", title="RL Post", description="", body_excerpt="b"
        )
        plan = ClippingPlan(
            concise_title="RL Post",
            description="post",
            topics=["Verifiers"],
        )
        nodes.create_source_note(cfg, meta, plan)
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
    fm, body = inbox.parse_clip(raw)
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


def test_directive_marks() -> None:
    # inline #person on the mention line; #promote implies frozen
    body = (
        "Jerry Liu is a cofounder/CEO of [[llamaindex]] #person\n"
        "some content\n"
        "#promote\n"
    )
    m = extract_marks(body)
    check(
        m.person_anchors == ["Jerry Liu is a cofounder/CEO of llamaindex"],
        "#person anchor = mention line, tag + wikilink stripped",
    )
    check(m.promote is True, "#promote flagged")
    check(m.frozen is True, "#promote implies frozen")

    # #person alone on a line reaches back to the mention above it
    m2 = extract_marks("the ceo of apple steve jobs was a cool guy\n\n#person\n")
    check(
        m2.person_anchors == ["the ceo of apple steve jobs was a cool guy"],
        "standalone #person reaches back to prior non-empty line",
    )

    # no directives → all off, #personal doesn't trigger #person
    m3 = extract_marks("a #personal note, nothing tagged\n")
    check(m3.person_anchors == [], "#personal does not trigger #person")
    check(not m3.promote and not m3.frozen, "no promote/frozen without tags")

    # bare #frozen without promote
    m4 = extract_marks("pin this\n#frozen\n")
    check(m4.frozen and not m4.promote, "#frozen alone freezes without promoting")


def test_source_note_author_and_frozen() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        meta = PageMeta(
            url="https://x.com/jerryjliu0",
            title="Retrieval Harness",
            description="",
            body_excerpt="full article body here",
            author="Jerry Liu",
        )
        plan = ClippingPlan(
            concise_title="Retrieval Harness",
            description="A tweet on agentic retrieval.",
            category="ai",
            topics=[],
            key_ideas=[],
        )
        marks = Marks(promote=True, frozen=True)
        path = nodes.create_source_note(
            cfg, meta, plan, marks=marks, full_body="full article body here"
        )
        text = path.read_text(encoding="utf-8")
        check('author: "Jerry Liu"' in text, "author byline written to frontmatter")
        check("  - frozen" in text, "#promote/#frozen adds the frozen tag")
        check("## Body" in text, "full body section written")
        check("full article body here" in text, "full body content present")


def test_source_note_no_author_no_frozen_by_default() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        meta = PageMeta(
            url="https://ex.com/a", title="A", description="", body_excerpt="b"
        )
        plan = ClippingPlan(
            concise_title="A", description="desc", category="x", topics=[], key_ideas=[]
        )
        path = nodes.create_source_note(cfg, meta, plan, marks=Marks(), full_body="b")
        text = path.read_text(encoding="utf-8")
        check("author:" not in text, "no author line when none known")
        check("frozen" not in text, "no frozen tag without the directive")
        check("## Body\nb" in text, "body inlined even without #promote")
        check("## Seed thoughts" not in text, "no seed section without a seed")


def test_source_link_divide() -> None:
    """A source lands under a concept's ## Sources, not its ## Links (concepts)."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        meta = PageMeta(
            url="https://ex.com/p", title="Post", description="", body_excerpt="b"
        )
        plan = ClippingPlan(
            concise_title="Post", description="d", topics=["Widgets"], key_ideas=[]
        )
        nodes.create_source_note(cfg, meta, plan)
        concept = (cfg.notes_dir / "Widgets.md").read_text(encoding="utf-8")
        check("## Sources" in concept, "concept has a Sources heading")
        check(
            "## Sources" in concept and "[[Post]]" in concept.split("## Sources", 1)[1],
            "source backlink sits under ## Sources",
        )


def test_strip_directives() -> None:
    from bower_bird.marks import strip_directives

    body = "#promote\n\nreal prose here\n==kept== line #dig\n#frozen"
    out = strip_directives(body)
    check("#promote" not in out, "#promote token stripped")
    check("#dig" not in out and "#frozen" not in out, "all directive tokens stripped")
    check("real prose here" in out, "prose retained")
    check("==kept== line" in out, "inline directive line keeps its text")
    check("\n\n\n" not in out, "blank lines left by stripping collapsed")


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
    check(not inbox.is_processable(Path("note (conflicted copy).md")), "skip conflict")
    check(not inbox.is_processable(Path(".hidden.md")), "skip dotfile")
    check(inbox.is_processable(Path("normal.md")), "process normal file")


def test_process_inbox_url_dedup() -> None:
    """A re-clipped source (same URL, fresh bytes → fresh hash) is not minted
    twice. The URL guard returns before any LLM call, so this stays offline."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        (root / "inbox").mkdir(parents=True)
        cfg = _config(root)
        state = State(path=root / "state.json")
        url = "https://x.com/foo/status/123"
        state.mark_url(url)  # already processed earlier under a different title
        clip = root / "inbox" / "some-clip.md"
        clip.write_text(
            f'---\ntitle: "Foo"\nsource: "{url}"\n---\nbody text\n', encoding="utf-8"
        )
        log = inbox.process_inbox(cfg, state)
        check(
            any("url already processed" in line for line in log),
            "re-clipped URL is skipped as a dup",
        )
        check(not clip.exists(), "dup clip moved out of inbox/")
        check(
            not cfg.sources_dir.exists() or not any(cfg.sources_dir.glob("*.md")),
            "no source note minted for a dup URL",
        )


def test_extract_urls() -> None:
    body = (
        "repo https://github.com/a/b and [tweet](https://x.com/u) and an image "
        "https://pbs.twimg.com/x.jpg then a dup https://github.com/a/b."
    )
    urls = extract_urls(body)
    check("https://github.com/a/b" in urls, "bare url extracted")
    check("https://x.com/u" in urls, "markdown-link url extracted")
    check(not any("twimg" in u for u in urls), "image url filtered out")
    check(urls.count("https://github.com/a/b") == 1, "duplicate url collapsed")
    check(all(not u.endswith(".") for u in urls), "trailing punctuation stripped")


def test_pick_source_url() -> None:
    # author profile first, then the post + the repo — should skip the profile
    urls = [
        "https://x.com/skalskip92",
        "https://x.com/skalskip92/status/123",
        "https://github.com/roboflow/supervision",
    ]
    check(
        pick_source_url(urls) == "https://x.com/skalskip92/status/123",
        "deep-path post chosen over bare profile",
    )
    check(pick_source_url([]) == "", "empty list -> empty string")
    check(
        pick_source_url(["https://x.com/onlyprofile"]) == "https://x.com/onlyprofile",
        "falls back to the only url when none are deep",
    )


def test_index_upsert_add_update_idempotent() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)

        nodes.upsert_index_line(cfg, "Agentic loops", "ai", "how agents iterate")
        text = cfg.index_path.read_text(encoding="utf-8")
        check(
            "- [[Agentic loops]] · ai · how agents iterate" in text,
            "index line added",
        )

        # Second identical call is a no-op (no duplicate line).
        nodes.upsert_index_line(cfg, "Agentic loops", "ai", "how agents iterate")
        text = cfg.index_path.read_text(encoding="utf-8")
        check(
            text.count("[[Agentic loops]]") == 1, "identical upsert does not duplicate"
        )

        # Changing the payload replaces the line in place (still one line).
        nodes.upsert_index_line(
            cfg, "Agentic loops", "ai", "the agent iterate-verify loop"
        )
        text = cfg.index_path.read_text(encoding="utf-8")
        check(text.count("[[Agentic loops]]") == 1, "update replaces, not appends")
        check("the agent iterate-verify loop" in text, "update takes new one-liner")
        check("how agents iterate" not in text, "old one-liner gone")

        # A title that is a prefix of an existing one is not clobbered.
        nodes.upsert_index_line(cfg, "Agentic", "ai", "distinct shorter title")
        text = cfg.index_path.read_text(encoding="utf-8")
        check(text.count("[[Agentic loops]]") == 1, "prefix title left intact")
        check(
            "[[Agentic]] · ai · distinct shorter title" in text,
            "prefix title added separately",
        )

        # Empty category/one-liner degrades gracefully (no trailing separators).
        nodes.upsert_index_line(cfg, "Bare", "", "")
        text = cfg.index_path.read_text(encoding="utf-8")
        check("- [[Bare]]\n" in text, "empty fields yield a clean bare line")

        # insert_only: never clobbers an existing (richer) line...
        nodes.upsert_index_line(
            cfg, "Agentic loops", "xx", "thin stub", insert_only=True
        )
        text = cfg.index_path.read_text(encoding="utf-8")
        check(
            "the agent iterate-verify loop" in text and "thin stub" not in text,
            "insert_only leaves the existing richer line untouched",
        )
        # ...but does add a line when the title is absent.
        nodes.upsert_index_line(cfg, "Brand New", "ai", "fresh stub", insert_only=True)
        text = cfg.index_path.read_text(encoding="utf-8")
        check(
            "[[Brand New]] · ai · fresh stub" in text, "insert_only adds absent title"
        )


def test_append_log() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        nodes.append_log(cfg, "built 2 pages")
        nodes.append_log(cfg, "built 1 page")
        text = cfg.log_path.read_text(encoding="utf-8")
        check(
            "built 2 pages" in text and "built 1 page" in text, "both log lines present"
        )
        check(text.count("- `") == 2, "one timestamped line per append")


def test_file_entities_creates_leaf_nodes() -> None:
    """Tools/people become unquizzed leaf notes, linked to concepts + source,
    and are NOT seeded into the review store."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        meta = PageMeta(
            url="https://x.co/p", title="CV in sport", description="", body_excerpt="b"
        )
        plan = ClippingPlan(
            concise_title="Supervision in sport",
            description="A CV library used for sports analytics",
            topics=["Computer vision"],
            tools=[
                EntityRef(
                    name="roboflow/supervision",
                    url="https://github.com/roboflow/supervision",
                    note="A computer-vision library",
                    topics=["Computer vision"],
                )
            ],
            people=[
                EntityRef(
                    name="Piotr Skalski",
                    url="https://x.com/skalskip92",
                    note="Author of the library",
                    topics=["Computer vision"],
                )
            ],
        )
        src = nodes.create_source_note(cfg, meta, plan)
        ids = nodes.file_entities(cfg, plan, src.stem)

        tool = cfg.tools_dir / "roboflow-supervision.md"
        person = cfg.people_dir / "Piotr Skalski.md"
        check(tool.exists(), "tool leaf created in brain/tools/")
        check(person.exists(), "person leaf created in brain/people/")

        ttext = tool.read_text(encoding="utf-8")
        check("- tool" in ttext, "tool leaf tagged tool")
        check("[[Computer vision]]" in ttext, "tool leaf links to concept")
        check(f"[[{src.stem}]]" in ttext, "tool leaf links back to source")
        check("A computer-vision library" in ttext, "tool note body stored")

        ptext = person.read_text(encoding="utf-8")
        check("- person" in ptext, "person leaf tagged person")

        concept = cfg.notes_dir / "Computer vision.md"
        check(
            "[[roboflow-supervision]]" in concept.read_text(encoding="utf-8"),
            "concept bower backlinks the tool leaf",
        )
        check(
            "[[roboflow-supervision]]" in src.read_text(encoding="utf-8"),
            "source note surfaces the tool leaf",
        )
        check(not cfg.review_path.exists(), "leaves not seeded into review store")
        check(any("roboflow" in i for i in ids), "filed-entity id returned")


def test_yaml_scalar_neutralizes_injection() -> None:
    # Newline-injected frontmatter key + a double-quote are both neutralized.
    hostile = 'Real Title"\ninjected: true\nmore'
    out = ingest.yaml_scalar(hostile)
    check("\n" not in out, "yaml scalar strips newlines (no key injection)")
    check('"' not in out, "yaml scalar removes double-quotes")


def test_source_note_frontmatter_is_injection_safe() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _config(Path(tmp))
        meta = PageMeta(
            url='https://x.test/a"b',  # a quote in the URL used to break quoting
            title='T"\ntags: [pwned]',
            description="d",
            body_excerpt="",
        )
        plan = ClippingPlan(
            concise_title='T"\ntags: [pwned]',
            description="d",
            topics=[],
            category="",
        )
        path = nodes.create_source_note(cfg, meta, plan)
        fm = path.read_text(encoding="utf-8").split("---")[1]
        # The payload survives harmlessly inside the quoted title value, but must
        # NOT become a standalone frontmatter key (that needs an un-collapsed
        # newline, which _yaml_scalar removes).
        lines = [ln.strip() for ln in fm.splitlines()]
        check("tags: [pwned]" not in lines, "no injected frontmatter key from title")
        check(fm.count("source:") == 1, "url quote didn't spawn stray frontmatter")


def test_pasted_prose_becomes_source_node() -> None:
    """No link, real text → the paste lane: its own source node, sender as
    author, the pasted text as the immutable ## Body. LLM stubbed."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "BowerBird"
        root.mkdir()
        cfg = _config(root)
        state = State(path=root / "state.json")

        def fake_synthesize(meta, note, candidates, model, llm, **kw):
            return ClippingPlan(
                concise_title="A Shower Thought",
                description="pasted prose",
                category="test",
                topics=[],
                key_ideas=["one real claim"],
            )

        orig = app.synthesize_clipping
        app.synthesize_clipping = fake_synthesize
        try:
            receipt = app.handle_update(
                cfg, state, "a paragraph of my own thinking, no links", "Griffin"
            )
        finally:
            app.synthesize_clipping = orig

        check(receipt.startswith("🧠"), f"pasted prose shelves to brain: {receipt!r}")
        node = cfg.sources_dir / "A Shower Thought.md"
        check(node.exists(), "paste lane writes a source node")
        text = node.read_text(encoding="utf-8")
        check('author: "Griffin"' in text, "sender attributed as author")
        check(
            "## Body\na paragraph of my own thinking, no links" in text,
            "pasted text is the immutable body",
        )
        # _inbox.md is only for the genuinely unprocessable now.
        check(not cfg.telegram_inbox_path.exists(), "prose does not fall to _inbox")


def test_ssrf_guard_rejects_internal_hosts() -> None:
    from bower_bird import fetch as _fetch

    check(not _fetch.is_public_host("localhost"), "localhost blocked")
    check(not _fetch.is_public_host("127.0.0.1"), "loopback blocked")
    check(not _fetch.is_public_host("169.254.169.254"), "link-local metadata blocked")
    check(not _fetch.is_public_host("10.0.0.1"), "RFC1918 blocked")
    check(not _fetch.is_public_host(""), "empty host blocked")
    check(_fetch.is_public_host("example.com"), "public host allowed")


def test_allowed_chat_id_set_parsing() -> None:
    cfg = Config(telegram_bot_token="x", vault_path=Path("/"), allowed_chat_ids="")
    check(cfg.allowed_chat_ids == set(), "empty allowlist parses to empty set")
    cfg2 = Config(
        telegram_bot_token="x", vault_path=Path("/"), allowed_chat_ids="1, 2 3"
    )
    check(cfg2.allowed_chat_ids == {1, 2, 3}, "comma/space allowlist parses to ints")


def main() -> int:
    test_yaml_scalar_neutralizes_injection()
    test_source_note_frontmatter_is_injection_safe()
    test_pasted_prose_becomes_source_node()
    test_ssrf_guard_rejects_internal_hosts()
    test_allowed_chat_id_set_parsing()
    test_assert_writable()
    test_append_link_additive_and_idempotent()
    test_telegram_inbox()
    test_clip_queue()
    test_tools_shelf()
    test_create_source_note_asserts_links()
    test_links_into_existing_nested_concept()
    test_parse_clip()
    test_extract_marks()
    test_dig_word_boundary()
    test_directive_marks()
    test_source_note_author_and_frozen()
    test_source_note_no_author_no_frozen_by_default()
    test_source_link_divide()
    test_strip_directives()
    test_fetch_helpers()
    test_conflict_files_skipped()
    test_process_inbox_url_dedup()
    test_extract_urls()
    test_pick_source_url()
    test_file_entities_creates_leaf_nodes()
    test_index_upsert_add_update_idempotent()
    test_append_log()
    if _failures:
        print(f"\n{_failures} failure(s).")
        return 1
    print("OK: ingest + inbox tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
