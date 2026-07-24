"""One-shot stock drain: shelve the legacy pre-antilibrary shelf docs
(`tweets/*.md`, `trinkets/*.md`) into `brain/sources/` via the NEW ingest
pipeline. This is the "full stock drain" pilot_ingest.py's docstring defers.

`inbox/` is NOT drained here — `bb build` already owns it; this script only
clears its stale url-dedup entries (see step 2) so build can pick those clips
up on its own next pass.

Per doc:
  1. Parse frontmatter (`source:`) + body — reused from inbox.py's clip
     parser, which tolerates docs with no frontmatter at all (some trinkets
     are bare prose with an inline URL, no YAML block).
  2. Stale-dedup clearing (once, up front, over tweets/ + trinkets/ + inbox/):
     a url marked processed in state.json with no matching brain/sources/
     node is unblocked — cleared from the dedup set so it can drain/build
     normally. inbox/ docs are cleared-only here: never drained/archived.
  3. Drain: a doc with a source url routes through app.handle_update exactly
     like a fresh Telegram send of that bare url — same resolver chain
     (resolve.py runs as normal for X/Twitter), same finish_shelve tail. A
     doc with NO url but real body prose (some trinkets) is sender-authored,
     so it routes through app.shelve_prose directly instead — the same
     no-link Telegram shape.
  4. Fallback: if the pipeline would queue the url to to-clip.md (resolution
     failed, or a tweet came back empty/media-only), don't let it — shelve
     using the doc's own locally-rendered body instead, via the same
     finish_shelve tail (so synthesize_clipping still runs and the body still
     lands under `## Body`). The to-clip.md write is blocked with a scoped
     monkeypatch (see `run_without_clip_queue`) rather than re-deriving
     success/failure ourselves before calling handle_update, which would mean
     duplicating shelve_link/shelve_tweet_url's resolution + media-only
     classification.
  5. Zero residue: every doc ends up either drained (new node, or an existing
     node already covered its url) or — for a genuinely unrecoverable doc (no
     url AND no usable body, or an error even after the local-body fallback)
     — moved to `_retry/` with a one-line reason in `_retry/README.md`. Never
     silently dropped, never left stuck mid-pipeline.
  6. Either way the original shelf doc is archived (or retried). Archiving
     reuses inbox.py's archive-move seam (collision-safe rename included).

Manual, y/N-gated before any real-vault write — same contract as
pilot_ingest.py. Makes real fetches + real LLM calls, so ANTHROPIC_API_KEY
must be set for a real (non-dry) run.

Usage:
    uv run python scripts/drain_shelves.py [--scratch DIR] [--limit N]

`--scratch DIR` runs against a scratch vault at DIR/BowerBird (mirrors
pilot_ingest) instead of the real vault. `--limit N` caps how many shelf docs
get drained, for smoke tests. Stale-dedup clearing always runs over the full
set regardless of `--limit` (it's a cheap, read-mostly pass).
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from bower_bird import app, inbox, ingest, queues  # noqa: E402
from bower_bird.config import Config  # noqa: E402
from bower_bird.marks import (  # noqa: E402
    extract_urls,
    pick_source_url,
    strip_directives,
)
from bower_bird.state import State, canonicalize_url  # noqa: E402

_BODY_LIMIT = 6000  # matches inbox.py's synthesis-grounding cap
_SAVE_EVERY = 5
_SENDER = "drain"


def shelf_docs(config: Config) -> list[Path]:
    tweets = sorted((config.vault_path / "tweets").glob("*.md"))
    trinkets = sorted((config.vault_path / "trinkets").glob("*.md"))
    return [p for p in tweets + trinkets if inbox.is_processable(p)]


def existing_node_urls(config: Config) -> set[str]:
    """Canonicalized `source:` urls of every node already in brain/sources/."""
    urls = set()
    if not config.sources_dir.is_dir():
        return urls
    for path in config.sources_dir.glob("*.md"):
        fm, _ = inbox.parse_clip(path.read_text(encoding="utf-8"))
        src = fm.get("source") or fm.get("url")
        if src:
            urls.add(canonicalize_url(src))
    return urls


def doc_url(path: Path, fm: dict[str, str], body: str) -> str:
    return inbox.clip_to_meta(
        path, fm, body, fallback_url=pick_source_url(extract_urls(body))
    ).url


def clear_stale_dedup(state: State, paths: list[Path], existing: set[str]) -> int:
    """Drop `state.processed_urls` entries for docs whose url has no matching
    brain/sources/ node yet — old-model sends marked processed but never
    actually turned into a node. Urls that DO have a node stay deduped."""
    cleared = 0
    for path in paths:
        try:
            fm, body = inbox.parse_clip(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        url = doc_url(path, fm, body)
        if not url:
            continue
        canon = canonicalize_url(url)
        if canon in state.processed_urls and canon not in existing:
            state.processed_urls.discard(canon)
            cleared += 1
    return cleared


def run_without_clip_queue(fn, *args, **kw):
    """Call `fn` with `queues.append_to_clip_queue` blocked (returns False,
    writes nothing).

    ponytail: a scoped monkeypatch on the shared module attribute, restored
    in `finally` — safe here because the script is single-threaded/one-shot.
    The alternative — re-deriving success/failure ourselves before deciding
    whether to call app.handle_update at all — means duplicating
    shelve_link/shelve_tweet_url's resolution + media-only classification,
    exactly the pipeline-duplication the spec says not to do.
    """
    original = queues.append_to_clip_queue
    queues.append_to_clip_queue = lambda *a, **k: False
    try:
        return fn(*args, **kw)
    finally:
        queues.append_to_clip_queue = original


def move_to_retry(config: Config, path: Path, reason: str) -> None:
    """Unrecoverable per-item failure: move the original doc to `_retry/`
    (never dropped, never left stuck) and log why."""
    retry_dir = config.vault_path / "_retry"
    retry_dir.mkdir(parents=True, exist_ok=True)
    dest = retry_dir / path.name
    if dest.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = retry_dir / f"{path.stem}.{stamp}{path.suffix}"
    ingest.assert_writable(config, dest)
    shutil.move(str(path), str(dest))

    readme = retry_dir / "README.md"
    ingest.assert_writable(config, readme)
    with readme.open("a", encoding="utf-8") as fh:
        fh.write(f"- {path.name} — {reason}\n")


def drain_one(
    config: Config, state: State, path: Path, existing: set[str]
) -> tuple[str, str]:
    """Drain one shelf doc. Returns (outcome, detail) — outcome is one of
    'drained' / 'fallback' / 'already_had_node' / 'retry'."""
    raw = path.read_text(encoding="utf-8")
    fm, body = inbox.parse_clip(raw)
    meta = inbox.clip_to_meta(
        path, fm, body, fallback_url=pick_source_url(extract_urls(body))
    )
    clean_body = strip_directives(body).strip()

    if not meta.url and not clean_body:
        return "retry", "no source url and no usable body"

    if not meta.url:
        # No link at all (some trinkets) — pasted-prose shape: sender-
        # authored path, same as a fresh no-link Telegram send.
        receipt = app.shelve_prose(config, state, clean_body, _SENDER)
        return "drained", receipt.splitlines()[0]

    canon = canonicalize_url(meta.url)
    if canon in existing:
        state.mark_url(meta.url)
        return "already_had_node", meta.url

    receipt = run_without_clip_queue(
        app.handle_update, config, state, meta.url, _SENDER
    )
    if receipt.startswith("✂️"):
        if not clean_body:
            return (
                "retry",
                f"resolution failed, no local body to fall back on ({meta.url})",
            )
        local_meta = meta.model_copy(update={"body_excerpt": clean_body[:_BODY_LIMIT]})
        receipt = app.finish_shelve(
            config, state, local_meta, "", model=config.llm.model, full_body=clean_body
        )
        return "fallback", receipt.splitlines()[0]

    return "drained", receipt.splitlines()[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scratch", type=Path, default=None, help="Run against DIR/BowerBird instead."
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Cap how many shelf docs get drained."
    )
    args = parser.parse_args()

    if args.scratch:
        vault = args.scratch / "BowerBird"
        vault.mkdir(parents=True, exist_ok=True)
        config = Config(
            BOWER_VAULT_PATH=str(vault),
            BOWER_STATE_PATH=str(args.scratch / "state.json"),
        )
    else:
        config = Config()

    docs = shelf_docs(config)
    limited = docs[: args.limit] if args.limit else docs

    print(f"drain: {len(docs)} shelf doc(s) in {config.vault_path}")
    if args.limit:
        print(f"  --limit {args.limit} -> draining {len(limited)}")
    print(
        "\nThis makes real fetches and real LLM calls, and writes into "
        f"{config.vault_path} (brain/sources/, archive/, _retry/, state.json)."
    )
    if input("Run the drain? [y/N] ").strip().lower() != "y":
        print("aborted.")
        return 0

    state = State.load(config.state_path)
    existing = existing_node_urls(config)

    inbox_paths = (
        [p for p in config.inbox_dir.glob("*.md") if inbox.is_processable(p)]
        if config.inbox_dir.is_dir()
        else []
    )
    shelf_cleared = clear_stale_dedup(state, docs, existing)
    inbox_cleared = clear_stale_dedup(state, inbox_paths, existing)
    print(
        f"drain: cleared {shelf_cleared} stale shelf dedup + "
        f"{inbox_cleared} stale inbox dedup (inbox docs untouched otherwise)"
    )

    counts = {"drained": 0, "fallback": 0, "already_had_node": 0, "retry": 0}
    label = {
        "drained": "resolver",
        "fallback": "local-body fallback",
        "already_had_node": "already had node",
        "retry": "RETRY",
    }
    for i, path in enumerate(limited, 1):
        try:
            outcome, detail = drain_one(config, state, path, existing)
        except Exception as exc:  # noqa: BLE001 — one bad doc must not kill the drain
            outcome, detail = "retry", f"error: {exc}"

        if outcome == "retry":
            move_to_retry(config, path, detail)
        else:
            inbox.archive_clip(config, path)
        print(f"  {path.name} -> {detail} [{label[outcome]}]")

        counts[outcome] += 1
        if i % _SAVE_EVERY == 0:
            state.save()

    state.save()
    drained_total = counts["drained"] + counts["fallback"]
    archived = drained_total + counts["already_had_node"]
    print(
        f"\ndrain done — drained={drained_total} (fallback-used={counts['fallback']}) "
        f"already_had_node={counts['already_had_node']} archived={archived}\n"
        f"retried={counts['retry']} | cleared: shelf={shelf_cleared} "
        f"inbox={inbox_cleared}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
