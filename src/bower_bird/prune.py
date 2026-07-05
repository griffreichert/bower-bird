"""`bb prune` — manual garbage-collection of the cold `archive/`.

`archive/` holds every processed clip original (moved there at capture, never
deleted). It's cold: not in `_index.md`, not read by build/dive/weave — pure disk
cost. This deletes husks older than `config.archive_ttl_days`, but ONLY when the
content is captured in the graph (a matching source node, or the source URL is in
processed-state). Orphans — husks with no graph node — are held back and flagged;
those are the only ones whose deletion would actually lose knowledge (e.g. a node
weave renamed, or a never-built clip). Manual + confirmed, never crond;
`state.json` is untouched so nothing is ever re-processed.
"""

import re
import time
from pathlib import Path

from bower_bird.config import Config
from bower_bird.state import State

# _archive appends `.YYYYmmdd-HHMMSS` on a filename collision — strip it to
# recover the stem the source note was written under.
_STAMP_SUFFIX = re.compile(r"\.\d{8}-\d{6}$")
_SOURCE_URL = re.compile(r'^source:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE)


def _age_days(path: Path, now: float) -> float:
    return (now - path.stat().st_mtime) / 86400


def _base_stem(path: Path) -> str:
    return _STAMP_SUFFIX.sub("", path.stem)


def _source_url(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = _SOURCE_URL.search(text)
    return m.group(1).strip() if m else ""


def _in_kb(config: Config, state: State, path: Path) -> bool:
    """True when this husk's content is captured in the graph — safe to delete.

    Primary: a source note exists under the same stem (husk + note share the
    concise title — new captures feed the same display_title to both). Fallback:
    the husk's `source:` URL is in processed-state (catches dup husks archived
    under a collision stamp).

    # ponytail: exact stem/url match only. Legacy husks (pre-URL-state, or an
    # older archive name that predates the concise-title rename) fail both and
    # fall to held-back — reviewed by hand, never auto-deleted. Deliberate: a
    # false hold-back is safe, a false delete loses data. Add fuzzy title match
    # only if the held-back pile gets annoying.
    """
    if (config.sources_dir / f"{_base_stem(path)}.md").exists():
        return True
    url = _source_url(path)
    return bool(url and state.seen_url(url))


def prune(
    config: Config, state: State, *, prompt=input, now: float | None = None
) -> int:
    now = time.time() if now is None else now
    archive = config.archive_dir
    ttl = config.archive_ttl_days
    if not archive.is_dir():
        print("bb prune: no archive/ yet — nothing to do.")
        return 0

    eligible: list[Path] = []
    orphans: list[Path] = []
    for path in sorted(archive.glob("*.md")):
        if _age_days(path, now) < ttl:
            continue
        (eligible if _in_kb(config, state, path) else orphans).append(path)

    if orphans:
        print(
            f"\nHELD BACK — {len(orphans)} husk(s) older than {ttl}d with NO graph "
            "node (not deleting; check these by hand):"
        )
        for p in orphans:
            print(f"  ! {p.name}")

    if not eligible:
        print(f"\nbb prune: nothing eligible (0 captured husks older than {ttl}d).")
        return 0

    print(
        f"\n{len(eligible)} husk(s) older than {ttl}d, captured in the graph — "
        "WILL DELETE:"
    )
    for p in eligible:
        print(f"  - {p.name}  ({int(_age_days(p, now))}d)")

    answer = prompt(f"\nDelete these {len(eligible)} file(s)? [y/N] ").strip().lower()
    if answer != "y":
        print("bb prune: aborted, nothing deleted.")
        return 0

    for p in eligible:
        p.unlink()
    print(f"bb prune: deleted {len(eligible)} husk(s).")
    return 0


def main(config: Config) -> int:
    return prune(config, State.load(config.state_path))
