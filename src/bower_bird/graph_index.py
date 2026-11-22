import re
import sqlite3
from collections import defaultdict
from datetime import date
from urllib.parse import urlparse

from bower_bird.config import Config
from bower_bird.lint import outbound_links, read_text
from bower_bird.schema import Claim, NoteKind, SourceType

_SOURCE_URL_RE = re.compile(r'^source:\s*"?(.*?)"?\s*$', re.MULTILINE)
_AUTHOR_RE = re.compile(r'^author:\s*"?(.*?)"?\s*$', re.MULTILINE)
_CREATED_RE = re.compile(r"^created:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)

KIND_FOLDERS = {
    NoteKind.SOURCE: "sources",
    NoteKind.BOWER: "bowers",
    NoteKind.PERSON: "people",
    NoteKind.TOOL: "tools",
}
SCHEMA = """\
CREATE TABLE notes (
    id TEXT PRIMARY KEY,  -- file stem, the [[wikilink]] target
    kind TEXT,            -- source, bower, person, tool
    type TEXT,            -- sources only: tweet, article, paper, repo, prose
    url TEXT,
    author TEXT,
    created TEXT          -- YYYY-MM-DD, the day the note was shelved
);
CREATE TABLE claims (
    note TEXT,            -- notes.id
    kind TEXT,            -- source (a key idea) or concept (synthesized)
    text TEXT
);
CREATE TABLE links (
    src TEXT,             -- notes.id
    dst TEXT              -- the [[wikilink]] target as written
);
CREATE VIRTUAL TABLE claims_fts USING fts5(text, tokenize='porter unicode61');
CREATE VIRTUAL TABLE titles_fts USING fts5(title, tokenize='porter unicode61');
"""


def frontmatter(text: str) -> str:
    """Return the YAML frontmatter block, or an empty string."""
    if not text.startswith("---\n"):
        return ""
    return text[4:].split("\n---", 1)[0]


def source_type(url: str) -> SourceType:
    """Read a source's medium from its URL; no URL means the sender wrote it."""
    if not url:
        return SourceType.PROSE
    parsed = urlparse(url)
    host = parsed.netloc.removeprefix("www.")
    if host in {"x.com", "twitter.com"}:
        return SourceType.TWEET
    if host == "arxiv.org" or parsed.path.endswith(".pdf"):
        return SourceType.PAPER
    if host == "github.com":
        return SourceType.REPO
    return SourceType.ARTICLE


def build_index(config: Config, claims: list[Claim]) -> sqlite3.Connection:
    """Parse brain/ into a read-only in-memory SQLite database.

    The vault stays the source of truth. A build reads every note, so each
    call sees the vault as it is now and nothing is left to go stale.
    Wikilinks come from the note above ``## Body``, so links inside an
    inlined source text are not graph edges.
    """
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    for kind, folder in KIND_FOLDERS.items():
        folder_dir = config.brain_dir / folder
        if not folder_dir.is_dir():
            continue
        for path in sorted(folder_dir.rglob("*.md")):
            text = read_text(path)
            head = frontmatter(text)
            url = _SOURCE_URL_RE.search(head)
            url = url.group(1).strip() if url else ""
            author = _AUTHOR_RE.search(head)
            created = _CREATED_RE.search(head)
            cur = db.execute(
                "INSERT OR IGNORE INTO notes VALUES (?, ?, ?, ?, ?, ?)",
                (
                    path.stem,
                    kind,
                    source_type(url) if kind is NoteKind.SOURCE else None,
                    url,
                    author.group(1).strip() if author else "",
                    created.group(1) if created else "",
                ),
            )
            if not cur.rowcount:
                continue
            db.execute(
                "INSERT INTO titles_fts(rowid, title) VALUES (?, ?)",
                (cur.lastrowid, path.stem),
            )
            graph_part = text.split("\n## Body\n", 1)[0]
            db.executemany(
                "INSERT INTO links VALUES (?, ?)",
                [(path.stem, dst) for dst in set(outbound_links(graph_part))],
            )
    for claim in claims:
        cur = db.execute(
            "INSERT INTO claims VALUES (?, ?, ?)", (claim.node, claim.kind, claim.text)
        )
        db.execute(
            "INSERT INTO claims_fts(rowid, text) VALUES (?, ?)",
            (cur.lastrowid, claim.text),
        )
    db.execute("PRAGMA query_only = ON")
    return db


def matching_sources(
    db: sqlite3.Connection,
    author: str | None = None,
    since: date | None = None,
    type_: SourceType | None = None,
) -> list[sqlite3.Row]:
    """Source notes passing the metadata filters, newest first."""
    sql = "SELECT * FROM notes WHERE kind = 'source'"
    params: list[str] = []
    if author:
        sql += " AND author LIKE ?"
        params.append(f"%{author}%")
    if since:
        sql += " AND created >= ?"
        params.append(since.isoformat())
    if type_:
        sql += " AND type = ?"
        params.append(type_)
    return db.execute(sql + " ORDER BY created DESC, id", params).fetchall()


def neighbors(db: sqlite3.Connection) -> dict[str, set[str]]:
    """Undirected adjacency over links whose target is a note in the index.

    Obsidian resolves ``[[links]]`` case-insensitively, so targets match
    note stems the same way.
    """
    ids = {row["id"].lower(): row["id"] for row in db.execute("SELECT id FROM notes")}
    adj: dict[str, set[str]] = defaultdict(set)
    for row in db.execute("SELECT src, dst FROM links"):
        dst = ids.get(row["dst"].lower())
        if dst and dst != row["src"]:
            adj[row["src"]].add(dst)
            adj[dst].add(row["src"])
    return adj
