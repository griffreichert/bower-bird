"""Entry point: `python -m bower_bird` (or the `bb` / `bower-bird` scripts).

Subcommands (also exposed as their own `uv run <verb>` scripts):
  (default)        One full pass: Telegram queue + clipper inbox gather (cron).
  pull             Pull the Telegram queue only (every send shelves to brain/).
  build            Gather Web Clipper drops in inbox/ → brain/sources/.
  peck             Run a spaced-rep quiz session over due nodes (pull-only).
  prune            Delete cold archive/ husks older than the TTL (manual, confirmed).
  drain            Resolve unchecked X links in to-clip.md into source nodes.
  lint             Read-only structural graph lint (orphans, broken links).
  stale            Ranked weave queue: which concept to synthesize next.
  ask              Ranked claim recall over the graph (read-only, no LLM).
  touch            Pull review due-dates to today for harvested nodes (weave's
                    hook, #29).
"""

import sys

from pydantic import ValidationError

from bower_bird.app import run_all, run_gather, run_telegram
from bower_bird.config import Config


def config_or_exit() -> Config | int:
    """Load config, or return exit code 2 on failure (with a message)."""
    try:
        return Config()
    except ValidationError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


def run_lane(fn) -> int:
    """Run a count-returning lane; print a summary, exit 0 (count != exit code)."""
    config = config_or_exit()
    if isinstance(config, int):
        return config
    count = fn(config)
    if count:
        print(f"bower-bird: processed {count} item(s).")
    return 0


def pull() -> int:
    """`uv run pull` — pull the Telegram queue."""
    return run_lane(run_telegram)


def build() -> int:
    """`uv run build` — gather Web Clipper drops in inbox/."""
    return run_lane(run_gather)


def main() -> int:
    # Parse a minimal subcommand — no dependency on argparse so the module
    # stays light. No arg runs both capture lanes; named verbs scope it.
    args = sys.argv[1:]
    subcommand = args[0] if args else None

    if subcommand == "peck":
        config = config_or_exit()
        if isinstance(config, int):
            return config
        from bower_bird.review import main as peck_main

        return peck_main(config, args[1:])

    if subcommand == "prune":
        config = config_or_exit()
        if isinstance(config, int):
            return config
        from bower_bird.prune import main as prune_main

        return prune_main(config)

    if subcommand == "lint":
        config = config_or_exit()
        if isinstance(config, int):
            return config
        from bower_bird.lint import main as lint_main

        return lint_main(config)

    if subcommand == "stale":
        rest = args[1:]
        limit = 20
        if "--limit" in rest:
            i = rest.index("--limit")
            limit = int(rest[i + 1])
        config = config_or_exit()
        if isinstance(config, int):
            return config
        from bower_bird.stale import main as stale_main

        return stale_main(config, limit=limit)

    if subcommand == "ask":
        rest = args[1:]
        as_json = "--json" in rest
        rest = [a for a in rest if a != "--json"]
        limit = 20
        if "--limit" in rest:
            i = rest.index("--limit")
            limit = int(rest[i + 1])
            del rest[i : i + 2]
        query = " ".join(rest).strip()
        if not query:
            print('bb ask: usage: bb ask "<query>" [--json] [--limit N]')
            return 2
        config = config_or_exit()
        if isinstance(config, int):
            return config
        from bower_bird.recall import ask, format_json, format_text

        claims = ask(config, query, limit=limit)
        print(format_json(claims) if as_json else format_text(claims))
        return 0

    if subcommand == "touch":
        ids = args[1:]
        if not ids:
            print("bb touch: usage: bb touch <id> [<id>...]")
            return 2
        config = config_or_exit()
        if isinstance(config, int):
            return config
        from bower_bird.review import ReviewStore, touch

        store = ReviewStore.load(config)
        count = touch(store, ids)
        print(f"bb touch: pulled {count} node(s) due to today.")
        return 0

    if subcommand == "drain":
        from bower_bird.app import run_drain

        return run_lane(run_drain)

    if subcommand == "pull":
        return run_lane(run_telegram)
    if subcommand == "build":
        return run_lane(run_gather)
    return run_lane(run_all)


if __name__ == "__main__":
    raise SystemExit(main())
