"""Backfill frontmatter metadata onto existing brain/sources/ notes (#B of the
metadata-search-surface spec, notes/specs/metadata-search-surface.md).

Adds two projected facets to every source note's frontmatter — never invented,
always derived from data that already exists elsewhere:

  - `category` <- this node's own line in `_index.md` (`- [[title]] · cat · ...`).
  - `topics`   <- this node's own `## Links` wikilinks (never `## Sources`), so
                  frontmatter topics == `## Links` by construction.

Same pass also cleans drift that has built up in `tags:` and the key set:
mints a missing `id:` (enrols the node as a fresh box-0 peck card), strips
leaked wikilink tags and any tag outside the source allowlist
(source/dig/frozen), and drops legacy keys (`description:`, `bower:`).

Frontmatter only. Body prose (everything after the closing `---`) is carried
through byte-identical — never touched.

Run: uv run python scripts/backfill_metadata.py [--dry-run]
"""

import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bower_bird import ingest  # noqa: E402
from bower_bird.config import Config  # noqa: E402

_ALLOWED_TAGS = {"source", "dig", "frozen"}
_LEGACY_KEYS = ("description", "bower")

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?\n)---\n(.*)\Z", re.DOTALL)
# [ \t] (not \s) after the colon — \s matches newlines too, which would let a
# block-list value (e.g. a legacy multi-line `author:\n  - "..."`) bleed
# across lines into the "scalar" capture instead of correctly reading as empty.
_SCALAR_RE = r'^{key}:[ \t]*"?([^\n]*?)"?[ \t]*$'
_TAGS_BLOCK_RE = re.compile(r"^tags:\n((?:  - .*\n?)*)", re.MULTILINE)
_INDEX_LINE_RE = re.compile(r"^- \[\[([^\]]+)\]\](?: · (.*))?$")
_LINKS_SECTION_RE = re.compile(
    r"^## Links\n(.*?)(?=\n## |\Z)", re.MULTILINE | re.DOTALL
)
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


@dataclass
class ParsedNote:
    fm: str  # raw frontmatter inner text (between the --- delimiters)
    rest: str  # everything after the closing --- , untouched
    id: str
    title: str
    source: str
    author: str
    created: str
    tags: list[str] = field(default_factory=list)


def parse_note(text: str) -> ParsedNote | None:
    """Split a note into frontmatter + body and pull the fields we rewrite.
    None if the file has no frontmatter block at all."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    fm, rest = m.group(1), m.group(2)

    def scalar(key: str) -> str:
        sm = re.search(_SCALAR_RE.format(key=key), fm, re.MULTILINE)
        return sm.group(1).strip() if sm else ""

    tags_m = _TAGS_BLOCK_RE.search(fm)
    tags = []
    if tags_m:
        tags = [
            ln.strip()[2:].strip() for ln in tags_m.group(1).splitlines() if ln.strip()
        ]

    return ParsedNote(
        fm=fm,
        rest=rest,
        id=scalar("id"),
        title=scalar("title"),
        source=scalar("source"),
        author=scalar("author"),
        created=scalar("created"),
        tags=tags,
    )


def parse_index_categories(index_text: str) -> dict[str, str]:
    """title -> category, from `_index.md` lines (`nodes.upsert_index_line`
    format: `- [[title]] · category · oneline`)."""
    cats: dict[str, str] = {}
    for line in index_text.splitlines():
        m = _INDEX_LINE_RE.match(line)
        if not m:
            continue
        rest = m.group(2) or ""
        category = rest.split(" · ")[0].strip() if rest else ""
        cats[m.group(1)] = category
    return cats


def derive_topics(rest: str) -> list[str]:
    """This node's OWN `## Links` wikilinks — never `## Sources`."""
    m = _LINKS_SECTION_RE.search(rest)
    if not m:
        return []
    return [wm.group(1).strip() for wm in _WIKILINK_RE.finditer(m.group(1))]


def clean_tags(tags: list[str]) -> tuple[list[str], int, int]:
    """Drop leaked wikilink tags and anything outside the allowlist.
    Returns (cleaned, wikilink_tags_stripped, junk_tags_dropped)."""
    cleaned, wikilinks, junk = [], 0, 0
    for t in tags:
        if t in _ALLOWED_TAGS:
            cleaned.append(t)
        elif "[[" in t:
            wikilinks += 1
        else:
            junk += 1
    return cleaned, wikilinks, junk


@dataclass
class TransformResult:
    text: str
    changed: bool
    id_minted: bool
    category_filled: bool
    topics_filled: bool
    wikilink_tags_stripped: int
    junk_tags_dropped: int
    legacy_keys_dropped: int


def transform_note(
    text: str, index_categories: dict[str, str]
) -> TransformResult | None:
    """Rewrite one note's frontmatter. None if it has no frontmatter to parse."""
    parsed = parse_note(text)
    if parsed is None:
        return None

    id_minted = not parsed.id
    note_id = parsed.id or str(uuid.uuid4())

    category = index_categories.get(parsed.title, "")
    topics = derive_topics(parsed.rest)
    tags, wikilink_stripped, junk_dropped = clean_tags(parsed.tags)
    legacy_dropped = sum(
        1 for key in _LEGACY_KEYS if re.search(rf"^{key}:", parsed.fm, re.MULTILINE)
    )

    lines = [f"id: {note_id}", f'title: "{ingest.yaml_scalar(parsed.title)}"']
    lines.append(f'source: "{ingest.yaml_scalar(parsed.source)}"')
    if parsed.author:
        lines.append(f'author: "{ingest.yaml_scalar(parsed.author)}"')
    lines.append(f"created: {parsed.created}")
    if category:
        lines.append(f"category: {category}")
    if topics:
        lines.append("topics:")
        lines += [f"  - {t}" for t in topics]
    lines.append("tags:")
    lines += [f"  - {t}" for t in tags]
    new_fm = "\n".join(lines) + "\n"

    new_text = f"---\n{new_fm}---\n{parsed.rest}"
    return TransformResult(
        text=new_text,
        changed=new_text != text,
        id_minted=id_minted,
        category_filled=bool(category),
        topics_filled=bool(topics),
        wikilink_tags_stripped=wikilink_stripped,
        junk_tags_dropped=junk_dropped,
        legacy_keys_dropped=legacy_dropped,
    )


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    config = Config()
    index_categories = parse_index_categories(
        config.index_path.read_text(encoding="utf-8")
        if config.index_path.exists()
        else ""
    )
    sources = sorted(config.sources_dir.glob("*.md"))
    print(f"vault: {config.vault_path}")
    print(f"scanning {len(sources)} source note(s) in brain/sources/ ...\n")

    todo: list[tuple[Path, TransformResult]] = []
    totals = {
        "category_filled": 0,
        "topics_filled": 0,
        "ids_minted": 0,
        "wikilink_tags_stripped": 0,
        "junk_tags_dropped": 0,
        "legacy_keys_dropped": 0,
    }

    for path in sources:
        text = path.read_text(encoding="utf-8")
        result = transform_note(text, index_categories)
        if result is None:
            print(f"  ! {path.name}  (no frontmatter, skipped)")
            continue
        if not result.changed:
            continue
        todo.append((path, result))
        totals["category_filled"] += int(result.category_filled)
        totals["topics_filled"] += int(result.topics_filled)
        totals["ids_minted"] += int(result.id_minted)
        totals["wikilink_tags_stripped"] += result.wikilink_tags_stripped
        totals["junk_tags_dropped"] += result.junk_tags_dropped
        totals["legacy_keys_dropped"] += result.legacy_keys_dropped

    if not todo:
        print("Nothing to do — every source note's frontmatter is already current.")
        return 0

    for path, result in todo:
        flags = []
        if result.id_minted:
            flags.append("id minted")
        if result.category_filled:
            flags.append("category")
        if result.topics_filled:
            flags.append("topics")
        if result.wikilink_tags_stripped or result.junk_tags_dropped:
            flags.append("tags cleaned")
        if result.legacy_keys_dropped:
            flags.append("legacy keys dropped")
        print(f"  + {path.name}  ({', '.join(flags) or 'no-op'})")

    print(
        f"\ntotals: category filled {totals['category_filled']}, "
        f"topics filled {totals['topics_filled']}, "
        f"ids minted {totals['ids_minted']} (new review cards), "
        f"wikilink tags stripped {totals['wikilink_tags_stripped']}, "
        f"junk tags dropped {totals['junk_tags_dropped']}, "
        f"legacy keys dropped {totals['legacy_keys_dropped']}"
    )

    if dry_run:
        print(f"\ndry run: would rewrite {len(todo)} note(s).")
        return 0

    answer = (
        input(f"\nRewrite frontmatter on {len(todo)} note(s)? [y/N] ").strip().lower()
    )
    if answer != "y":
        print("aborted, nothing written.")
        return 0

    for path, result in todo:
        ingest.assert_writable(config, path)
        path.write_text(result.text, encoding="utf-8")
    print(f"rewrote {len(todo)} note(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
