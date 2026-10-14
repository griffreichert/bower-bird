import json
import re

from bower_bird.config import Config
from bower_bird.lint import frontmatter_topics, read_text
from bower_bird.schema import Claim

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


def score(claim: Claim, query_tokens: set[str]) -> float:
    """Token overlap, boosted by a title hit, a topic hit, and concept kind."""
    overlap = query_tokens & tokenize(claim.text)
    if not overlap:
        return 0.0
    s = float(len(overlap))
    if query_tokens & tokenize(claim.node):
        s *= 3
    if query_tokens & tokenize(" ".join(claim.topics)):
        s *= 2
    if claim.kind == "concept":
        s *= 1.5
    return s


def ask(config: Config, query: str, limit: int = 20) -> list[Claim]:
    """Rank every claim against ``query`` and return the top ``limit``.

    Plain token overlap over claims, titles and topics: no LLM, no
    embeddings, no network. Concept claims from synthesis blocks outrank
    source key ideas. Claims that share no token with the query are dropped.
    """
    query_tokens = tokenize(query)
    scored = ((score(c, query_tokens), c) for c in load_claims(config))
    ranked = sorted(
        (sc for sc in scored if sc[0] > 0), key=lambda sc: sc[0], reverse=True
    )
    return [c for _, c in ranked[:limit]]


def format_text(claims: list[Claim]) -> str:
    """Render claims for the terminal, each followed by its node and URL."""
    lines = []
    for c in claims:
        lines.append(c.text)
        tail = f"[[{c.node}]] · {c.url}".rstrip(" ·")
        lines.append(f"  ↳ {tail}")
    return "\n".join(lines)


def format_json(claims: list[Claim]) -> str:
    """Render claims as a JSON list, for skills that call `bb ask --json`."""
    return json.dumps([c.model_dump() for c in claims], indent=2)
