import re
import time
from pathlib import Path

from bower_bird.config import Config
from bower_bird.state import State

# Matches the `.YYYYmmdd-HHMMSS` stamp inbox.archive_clip adds on a collision.
_STAMP_SUFFIX = re.compile(r"\.\d{8}-\d{6}$")
_SOURCE_URL = re.compile(r'^source:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE)


def age_days(path: Path, now: float) -> float:
    """Return the file's age in days from its mtime."""
    return (now - path.stat().st_mtime) / 86400


def base_stem(path: Path) -> str:
    """Return the stem without any collision stamp, the source note's stem."""
    return _STAMP_SUFFIX.sub("", path.stem)


def read_text(path: Path) -> str:
    """Return the file's text, or an empty string when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def source_url(path: Path) -> str:
    """Return the husk's frontmatter ``source:`` URL, or an empty string."""
    m = _SOURCE_URL.search(read_text(path))
    return m.group(1).strip() if m else ""


def has_graph_node(config: Config, state: State, path: Path) -> bool:
    """Return True when the husk's content is captured in the graph.

    A source note under the same stem counts, since capture writes the husk
    and the note under one title. Failing that, the husk's ``source:`` URL in
    processed state counts, which catches duplicates archived under a
    collision stamp. Matching is exact only: a husk that fails both is held
    back for a human, because a false hold back is safe and a false delete
    loses data.
    """
    if (config.sources_dir / f"{base_stem(path)}.md").exists():
        return True
    url = source_url(path)
    return bool(url and state.seen_url(url))


def prune(
    config: Config, state: State, *, prompt=input, now: float | None = None
) -> int:
    """Delete archive/ husks older than the TTL once the user confirms.

    archive/ holds every processed clip original. Source nodes inline the full
    body, so a captured husk is pure disk cost. Husks with no graph node are
    listed as held back and never deleted. state.json is untouched, so nothing
    is processed again.

    Parameters
    ----------
    prompt
        Reads the confirmation; tests pass a stub in place of ``input``.
    now
        Epoch seconds to age files against; defaults to the current time.
    """
    now = time.time() if now is None else now
    archive = config.archive_dir
    ttl = config.archive_ttl_days
    if not archive.is_dir():
        print("bb prune: no archive/ yet — nothing to do.")
        return 0

    eligible: list[Path] = []
    orphans: list[Path] = []
    for path in sorted(archive.glob("*.md")):
        if age_days(path, now) < ttl:
            continue
        (eligible if has_graph_node(config, state, path) else orphans).append(path)

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
        print(f"  - {p.name}  ({int(age_days(p, now))}d)")

    answer = prompt(f"\nDelete these {len(eligible)} file(s)? [y/N] ").strip().lower()
    if answer != "y":
        print("bb prune: aborted, nothing deleted.")
        return 0

    for p in eligible:
        p.unlink()
    print(f"bb prune: deleted {len(eligible)} husk(s).")
    return 0


def main(config: Config) -> int:
    """Run `bb prune` against the configured archive and state."""
    return prune(config, State.load(config.state_path))
