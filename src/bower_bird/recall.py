import argparse
import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import date, timedelta

from bower_bird.config import Config
from bower_bird.graph_index import SCHEMA, build_index, matching_sources, neighbors
from bower_bird.lint import frontmatter_topics, read_text
from bower_bird.schema import AskGroup, AskHit, Claim, NoteKind, SourceType

_KEY_IDEAS_RE = re.compile(r"^## Key ideas\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
_CONCEPT_BLOCK_RE = re.compile(
    r"<!-- bower:concept.*?-->(.*?)<!-- /bower:concept -->", re.DOTALL
)
_BULLET_RE = re.compile(r"^- (.+)$", re.MULTILINE)
_SOURCE_URL_RE = re.compile(r'^source:\s*"?(.*?)"?\s*$', re.MULTILINE)

_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "to", "for", "and", "or", "is", "are",
    "how", "what", "do", "does", "i", "my", "about", "with", "at", "as", "by",
}  # fmt: skip

TITLE_WEIGHT = 3.0
CONCEPT_WEIGHT = 1.5
SPREAD = 0.5
CLAIMS_SHOWN = 2


def frontmatter_source_url(text: str) -> str:
    """Return the frontmatter ``source:`` URL, or an empty string."""
    m = _SOURCE_URL_RE.search(text)
    return m.group(1).strip() if m else ""


def tokenize(text: str) -> set[str]:
    """Case-folded word tokens, stopwords dropped."""
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS}


def load_claims(config: Config) -> list[Claim]:
    """Walk brain/sources/ and brain/bowers/ for claim bullets.

    Read-only: only ever opens files, never writes.
    """
    claims: list[Claim] = []

    if config.sources_dir.is_dir():
        for path in sorted(config.sources_dir.glob("*.md")):
            text = read_text(path)
            topics = sorted(frontmatter_topics(text) or set())
            url = frontmatter_source_url(text)
            m = _KEY_IDEAS_RE.search(text)
            if not m:
                continue
            for bullet in _BULLET_RE.findall(m.group(1)):
                claims.append(
                    Claim(
                        text=bullet,
                        node=path.stem,
                        kind="source",
                        url=url,
                        topics=topics,
                    )
                )

    if config.notes_dir.is_dir():
        for path in sorted(config.notes_dir.rglob("*.md")):
            text = read_text(path)
            topics = sorted(frontmatter_topics(text) or set())
            m = _CONCEPT_BLOCK_RE.search(text)
            if not m:
                continue
            for bullet in _BULLET_RE.findall(m.group(1)):
                claims.append(
                    Claim(text=bullet, node=path.stem, kind="concept", topics=topics)
                )

    return claims


def since_date(value: str) -> date:
    """Parse ``14d`` as fourteen days ago, or an ISO date as itself."""
    if value.endswith("d") and value[:-1].isdigit():
        return date.today() - timedelta(days=int(value[:-1]))
    return date.fromisoformat(value)


def keyword_scores(
    db: sqlite3.Connection, query: str
) -> tuple[dict[str, float], dict[str, list[tuple[float, str]]]]:
    """Score notes by FTS5 BM25 over their claims and titles.

    Returns
    -------
    scores
        Note id to its top three claim scores plus a weighted title score.
    hits
        Note id to its matching claims, best first.
    """
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in _STOPWORDS]
    scores: dict[str, float] = defaultdict(float)
    hits: dict[str, list[tuple[float, str]]] = defaultdict(list)
    if not words:
        return scores, hits
    match = " OR ".join(f'"{w}"' for w in words)
    for row in db.execute(
        "SELECT c.note, c.kind, c.text, -bm25(claims_fts) AS s FROM claims_fts "
        "JOIN claims c ON c.rowid = claims_fts.rowid WHERE claims_fts MATCH ?",
        (match,),
    ):
        weight = CONCEPT_WEIGHT if row["kind"] == "concept" else 1.0
        hits[row["note"]].append((row["s"] * weight, row["text"]))
    for note, note_hits in hits.items():
        note_hits.sort(reverse=True)
        scores[note] = sum(s for s, _ in note_hits[:3])
    for row in db.execute(
        "SELECT n.id, -bm25(titles_fts) AS s FROM titles_fts "
        "JOIN notes n ON n.rowid = titles_fts.rowid WHERE titles_fts MATCH ?",
        (match,),
    ):
        scores[row["id"]] += TITLE_WEIGHT * row["s"]
    return scores, hits


def spread(scores: dict[str, float], adj: dict[str, set[str]]) -> dict[str, float]:
    """Pass half of each note's score to its neighbours, split by degree.

    A note linked to several matching hubs collects from each. Dividing by
    the square root of the degree keeps a hub with many sources from lifting
    all of them equally high.
    """
    total = dict(scores)
    for note, score in scores.items():
        linked = adj.get(note)
        if not linked:
            continue
        share = SPREAD * score / math.sqrt(len(linked))
        for other in linked:
            total[other] = total.get(other, 0.0) + share
    return total


def ask(
    config: Config,
    query: str,
    limit: int = 15,
    author: str | None = None,
    since: date | None = None,
    type_: SourceType | None = None,
    links: bool = True,
) -> list[AskGroup]:
    """Rank source notes for ``query`` and group them under connecting hubs.

    Keyword scores come from claims and titles. With ``links``, every note
    then passes score along its wikilinks, so a source that never uses the
    query words still ranks when it links to a concept, person or tool that
    matches. Filters apply to the returned sources, never to the hubs.
    """
    db = build_index(config, load_claims(config))
    adj = neighbors(db)
    scores, hits = keyword_scores(db, query)
    total = spread(scores, adj) if links else dict(scores)

    candidates = {row["id"]: row for row in matching_sources(db, author, since, type_)}
    ranked = sorted(
        (n for n in candidates if total.get(n, 0) > 0), key=lambda n: -total[n]
    )[:limit]
    kinds = {
        row["id"]: NoteKind(row["kind"]) for row in db.execute("SELECT * FROM notes")
    }
    hubs = {n for n, s in total.items() if s > 0 and kinds[n] is not NoteKind.SOURCE}

    groups: dict[str | None, AskGroup] = {}
    for node in ranked:
        row = candidates[node]
        shown = [text for _, text in hits.get(node, [])[:CLAIMS_SHOWN]]
        if not shown:
            shown = [
                r["text"]
                for r in db.execute(
                    "SELECT text FROM claims WHERE note = ? LIMIT ?",
                    (node, CLAIMS_SHOWN),
                )
            ]
        via = max(adj.get(node, set()) & hubs, key=lambda h: total[h], default=None)
        if via not in groups:
            groups[via] = AskGroup(
                hub=via,
                kind=kinds[via] if via else None,
                linked=len(adj.get(via, ())) if via else 0,
                claims=[t for _, t in hits.get(via, [])[:CLAIMS_SHOWN]] if via else [],
                related=sorted(adj.get(via, set()) & hubs, key=lambda h: -total[h])[:3]
                if via
                else [],
            )
        groups[via].hits.append(
            AskHit(
                node=node,
                type=SourceType(row["type"]),
                url=row["url"],
                author=row["author"],
                created=row["created"],
                score=round(total[node], 3),
                claims=shown,
            )
        )
    return sorted(
        groups.values(),
        key=lambda g: (g.hub is None, -max(h.score for h in g.hits)),
    )


def format_groups(groups: list[AskGroup]) -> str:
    """Render ask results for the terminal, one block per hub."""
    lines: list[str] = []
    for group in groups:
        if group.hub:
            lines.append(f"[[{group.hub}]] · {group.kind} · {group.linked} linked")
        else:
            lines.append("No linked hub")
        lines.extend(f"  * {claim}" for claim in group.claims)
        for hit in group.hits:
            lines.extend(f"  - {claim}" for claim in hit.claims)
            meta = [f"[[{hit.node}]]", hit.type, hit.author, hit.created, hit.url]
            lines.append("    ↳ " + " · ".join(m for m in meta if m))
        if group.related:
            lines.append("  related: " + " · ".join(f"[[{r}]]" for r in group.related))
        lines.append("")
    return "\n".join(lines).rstrip()


def print_rows(rows: list[sqlite3.Row], as_json: bool) -> None:
    """Print query rows as JSON, or as tab-separated text under a header."""
    if as_json:
        print(json.dumps([dict(r) for r in rows], indent=2))
        return
    if rows:
        print("\t".join(rows[0].keys()))
    for row in rows:
        print("\t".join("" if v is None else str(v) for v in row))


def main(config: Config, verb: str, args: list[str]) -> int:
    """Run ``bb ask`` or ``bb sql`` against a fresh index.

    ``bb ask`` with no query lists source notes newest first, so one verb
    covers both ranked search and metadata listing.
    """
    parser = argparse.ArgumentParser(prog=f"bb {verb}")
    parser.add_argument("--json", action="store_true")
    if verb == "ask":
        parser.add_argument("query", nargs="*", help="omit to list notes")
        parser.add_argument("--author", help="substring, case-insensitive")
        parser.add_argument("--since", type=since_date, help="14d or 2026-11-01")
        parser.add_argument("--type", type=SourceType, choices=list(SourceType))
        parser.add_argument("--limit", type=int)
        parser.add_argument("--no-links", action="store_true")
    else:
        parser.add_argument("query", nargs="?", help="omit to print the schema")
    opts = parser.parse_args(args)

    if verb == "ask" and opts.query:
        groups = ask(
            config,
            " ".join(opts.query),
            limit=opts.limit or 15,
            author=opts.author,
            since=opts.since,
            type_=opts.type,
            links=not opts.no_links,
        )
        if opts.json:
            print(json.dumps([g.model_dump(mode="json") for g in groups], indent=2))
        else:
            print(format_groups(groups) or "No matches.")
        return 0

    db = build_index(config, load_claims(config))
    if verb == "ask":
        rows = matching_sources(db, opts.author, opts.since, opts.type)
        print_rows(rows[: opts.limit or 50], opts.json)
        return 0

    if not opts.query:
        print(SCHEMA)
        return 0
    try:
        print_rows(db.execute(opts.query).fetchall(), opts.json)
    except sqlite3.Error as exc:
        print(f"bb sql: {exc}")
        return 1
    return 0
