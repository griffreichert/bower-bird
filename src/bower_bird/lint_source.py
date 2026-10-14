import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "bower_bird"
MAX_LINES = 400
RATIONALE = re.compile(
    r"\b(for now|temporar\w*|workaround|hack|fallback|legacy|until|because"
    r"|so that|in case|to avoid|we (need|want))\b",
    re.IGNORECASE,
)
HISTORY = re.compile(r"#\d{2,}|20\d\d-\d\d-\d\d")
MESSAGES = {
    "LS001": f"file is over {MAX_LINES} lines, split it",
    "LS002": "module docstring, say it in the file name or in notes/",
    "LS003": "noqa comment, fix the code",
    "LS004": "rationale in a comment, put the why in the docstring or notes/",
    "LS005": "issue number or date in a comment or docstring, history lives in git",
    "LS006": "block of 3 or more comment lines, move it to notes/",
}


class Hit(BaseModel, frozen=True):
    """One rule violation at one line."""

    line: int
    code: str


def comment_hits(source: str) -> list[Hit]:
    """Flag noqa, rationale, history and long blocks in ``#`` comments."""
    hits: list[Hit] = []
    full_line: list[int] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type != tokenize.COMMENT:
            continue
        line = tok.start[0]
        if "noqa" in tok.string:
            hits.append(Hit(line=line, code="LS003"))
        if RATIONALE.search(tok.string):
            hits.append(Hit(line=line, code="LS004"))
        if HISTORY.search(tok.string):
            hits.append(Hit(line=line, code="LS005"))
        if not tok.line[: tok.start[1]].strip():
            full_line.append(line)
    run_start = prev = None
    run_len = 0
    for line in full_line:
        if prev is not None and line == prev + 1:
            run_len += 1
        else:
            run_start, run_len = line, 1
        if run_len == 3:
            hits.append(Hit(line=run_start, code="LS006"))
        prev = line
    return hits


def docstring_hits(tree: ast.Module) -> list[Hit]:
    """Flag a module docstring and history references in any docstring.

    Rationale stays allowed here: a docstring is where a function states its
    reasons and caller obligations.
    """
    hits: list[Hit] = []
    if ast.get_docstring(tree) is not None:
        hits.append(Hit(line=tree.body[0].lineno, code="LS002"))
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        doc = ast.get_docstring(node, clean=False)
        if doc is None:
            continue
        if HISTORY.search(doc):
            hits.append(Hit(line=node.body[0].lineno, code="LS005"))
    return hits


def check_file(path: Path) -> list[str]:
    """Return one ``path:line: code message`` row per hit in ``path``."""
    rel = path.relative_to(ROOT).as_posix()
    source = path.read_text()
    hits = comment_hits(source) + docstring_hits(ast.parse(source))
    n_lines = len(source.splitlines())
    if n_lines > MAX_LINES:
        hits.append(Hit(line=1, code="LS001"))
    return [
        f"{rel}:{h.line}: {h.code} {MESSAGES[h.code]}"
        for h in sorted(hits, key=lambda h: (h.line, h.code))
    ]


def main() -> None:
    """Lint the given paths, or all of ``src/bower_bird``; exit 1 on any hit."""
    parser = argparse.ArgumentParser(prog="lint-source")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()

    roots = [p.resolve() for p in args.paths] or [SRC]
    files = sorted(
        f for root in roots for f in (root.rglob("*.py") if root.is_dir() else [root])
    )
    failures = [line for path in files for line in check_file(path)]
    for line in failures:
        print(line)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
