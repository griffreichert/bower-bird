"""`bb prune` — manual garbage-collection of the cold `archive/`.

`archive/` holds every processed clip original (moved there at capture, never
deleted). It's cold: not in `_index.md`, not read by build/dive/weave — pure disk
cost. This deletes husks older than `config.archive_ttl_days`, but ONLY when the
content is captured in the graph (a matching source node, or the source URL is in
processed-state). Orphans — husks with no graph node — are held back and flagged;
those are the only ones whose deletion would actually lose knowledge (e.g. a node
weave renamed, or a never-built clip). Manual + confirmed, never crond;
`state.json` is untouched so nothing is ever re-processed.

`let_go` is a second, unrelated GC lane: the inbox/ reading queue, not the cold
archive. Docs sitting unread past `config.inbox_ttl_days` are moved (never
deleted) to `archive/unread/` and logged in `let-go.md`, so the queue stays a
list of things worth reading rather than a guilt pile. Same non-destructive
contract as prune — a move + a ledger line, nothing is lost.
"""

import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from bower_bird import ingest
from bower_bird.config import Config
from bower_bird.inbox import _is_processable
from bower_bird.state import State

# _archive appends `.YYYYmmdd-HHMMSS` on a filename collision — strip it to
# recover the stem the source note was written under.
_STAMP_SUFFIX = re.compile(r"\.\d{8}-\d{6}$")
_SOURCE_URL = re.compile(r'^source:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE)
_URL_FM = re.compile(r'^url:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE)
_TITLE_FM = re.compile(r'^title:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE)
_CREATED_FM = re.compile(r'^created:\s*"?(\d{4}-\d{2}-\d{2})"?\s*$', re.MULTILINE)


def _age_days(path: Path, now: float) -> float:
    return (now - path.stat().st_mtime) / 86400


def _base_stem(path: Path) -> str:
    return _STAMP_SUFFIX.sub("", path.stem)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _source_url(path: Path) -> str:
    m = _SOURCE_URL.search(_read_text(path))
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


def _inbox_age_days(path: Path, text: str, now: float) -> float:
    """Age in days, preferring frontmatter `created:` over file mtime.

    iCloud mtimes drift with sync churn (touched on every re-sync, not just on
    write), so `created:` frontmatter is trusted first; mtime is only the
    fallback when `created:` is absent or unparsable.
    """
    m = _CREATED_FM.search(text)
    if m:
        created = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        return (datetime.fromtimestamp(now).date() - created).days
    return _age_days(path, now)


def _title_and_url(path: Path, text: str) -> tuple[str, str]:
    m = _TITLE_FM.search(text)
    title = m.group(1).strip() if m else path.stem
    m = _SOURCE_URL.search(text) or _URL_FM.search(text)
    url = m.group(1).strip() if m else ""
    return title, url


def _move_to_unread(config: Config, path: Path) -> Path:
    config.unread_archive_dir.mkdir(parents=True, exist_ok=True)
    dest = config.unread_archive_dir / path.name
    if dest.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = config.unread_archive_dir / f"{path.stem}.{stamp}{path.suffix}"
    ingest._assert_writable(config, dest)
    shutil.move(str(path), str(dest))
    return dest


_LET_GO_HEADER = """\
---
title: Let go
created: {today}
bower: generated
tags:
  - let-go
---
# Let go

Items captured but never read within the TTL. Nothing is deleted — originals
live in `archive/unread/`. If one still calls to you, pull it back into
`inbox/`.

"""


def _append_let_go(config: Config, title: str, url: str, age_days: int) -> None:
    path = config.let_go_path
    ingest._assert_writable(config, path)
    if not path.exists():
        path.write_text(_LET_GO_HEADER.format(today=ingest._today()), encoding="utf-8")
    label = f"[{title}]({url})" if url else title
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {ingest._today()} · let go after {age_days}d unread: {label}\n")


def let_go(config: Config, now: float | None = None) -> list[str]:
    """Move reading-room docs unread past `config.inbox_ttl_days` to
    archive/unread/, logging one line per doc to let-go.md. Covers both rooms —
    inbox/ (articles) and tweets/ (digests). Never deletes — move + ledger only.

    Returns one log line per moved doc; empty when nothing is due.
    """
    now = time.time() if now is None else now
    ttl = config.inbox_ttl_days
    rooms = [d for d in (config.inbox_dir, config.tweets_dir) if d.is_dir()]
    if ttl <= 0 or not rooms:
        return []

    log: list[str] = []
    for path in sorted(p for room in rooms for p in room.glob("*.md")):
        if not _is_processable(path):
            continue
        text = _read_text(path)
        age = _inbox_age_days(path, text, now)
        if age < ttl:
            continue
        title, url = _title_and_url(path, text)
        dest = _move_to_unread(config, path)
        _append_let_go(config, title, url, int(age))
        log.append(f"let go {path.name} -> {dest.name} ({int(age)}d unread)")
    return log


def main(config: Config) -> int:
    return prune(config, State.load(config.state_path))
