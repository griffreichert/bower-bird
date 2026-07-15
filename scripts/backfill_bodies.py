"""Backfill `## Body` onto existing brain/sources/ notes (#25 / #13 item 6).

The antilibrary node schema makes the source node canonical: it carries the
full rendered body inlined under `## Body`. Pre-#25 notes are thin husks whose
body lives cold in `archive/`. This script brings the whole graph onto the one
contract:

  1. For each `brain/sources/*.md` without a `## Body` section, look for its
     archived original (same stem, stamped copies included) and inline that
     clip's body under `## Body`.
  2. Where no archive copy exists but `source:` resolves, re-fetch the body
     (HTML render / PDF extract / tweet proxy chain).
  3. Report the stragglers that got neither.

Manual, y/N-gated, never crond. Appends only — a note's existing text is
never rewritten; the section lands at the end of the file.

Run: uv run python scripts/backfill_bodies.py [--dry-run]
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bower_bird import ingest  # noqa: E402
from bower_bird.config import Config  # noqa: E402
from bower_bird.fetch import fetch_pdf, fetch_rendered, is_pdf_url  # noqa: E402
from bower_bird.inbox import parse_clip  # noqa: E402
from bower_bird.resolve import parse_tweet_id, resolve_tweet  # noqa: E402

_SOURCE_URL = re.compile(r'^source:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE)
_BODY_HEADING = re.compile(r"^## Body$", re.MULTILINE)


def archived_body(config: Config, stem: str) -> str:
    """Body text of this note's archived original, or '' if none exists."""
    if not config.archive_dir.is_dir():
        return ""
    # inbox.archive_clip stamps collisions as `<stem>.YYYYmmdd-HHMMSS.md`.
    for path in sorted(config.archive_dir.glob(f"{stem}*.md")):
        _, body = parse_clip(path.read_text(encoding="utf-8"))
        if body.strip():
            return body.strip()
    return ""


def refetched_body(config: Config, url: str) -> str:
    """Re-fetch a body from the source URL. '' on any failure."""
    try:
        if parse_tweet_id(url) is not None:
            tweet = resolve_tweet(url, timeout=config.fetch_timeout)
            return ingest.format_tweet_body(tweet) if tweet else ""
        if is_pdf_url(url):
            return fetch_pdf(url, timeout=config.fetch_timeout).body_excerpt
        meta, body = fetch_rendered(url, timeout=config.fetch_timeout)
        return "" if meta.is_thin else body.strip()
    except Exception:
        return ""


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    config = Config()
    sources = sorted(config.sources_dir.glob("*.md"))
    print(f"vault: {config.vault_path}")
    print(f"scanning {len(sources)} source note(s) in brain/sources/ ...\n")

    todo: list[tuple[Path, str, str]] = []  # (note, body, origin)
    stragglers: list[str] = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        if _BODY_HEADING.search(text):
            continue  # already on the new contract
        body = archived_body(config, path.stem)
        origin = "archive"
        if not body:
            m = _SOURCE_URL.search(text)
            url = m.group(1).strip() if m else ""
            if url and dry_run:
                todo.append((path, "", f"would re-fetch {url}"))
                continue
            if url:
                body = refetched_body(config, url)
                origin = f"re-fetch {url}"
        if body:
            todo.append((path, body, origin))
        else:
            stragglers.append(path.name)

    if not todo and not stragglers:
        print("Nothing to do — every source note already carries ## Body.")
        return 0

    for path, _, origin in todo:
        print(f"  + {path.name}  ({origin})")
    if stragglers:
        print(
            f"\nSTRAGGLERS — no archive copy, no fetchable source ({len(stragglers)}):"
        )
        for name in stragglers:
            print(f"  ! {name}")

    if dry_run:
        print(f"\ndry run: would backfill {len(todo)} note(s).")
        return 0
    if not todo:
        return 0

    answer = input(f"\nInline ## Body into {len(todo)} note(s)? [y/N] ").strip().lower()
    if answer != "y":
        print("aborted, nothing written.")
        return 0

    for path, body, _ in todo:
        ingest.assert_writable(config, path)
        text = path.read_text(encoding="utf-8")
        sep = "" if text.endswith("\n") else "\n"
        path.write_text(f"{text}{sep}\n## Body\n{body}\n", encoding="utf-8")
    print(f"backfilled {len(todo)} note(s); {len(stragglers)} straggler(s) reported.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
