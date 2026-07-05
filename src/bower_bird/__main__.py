"""Entry point: `python -m bower_bird` (or the `bb` / `bower-bird` scripts).

Subcommands (also exposed as their own `uv run <verb>` scripts):
  (default)        One full pass: Telegram queue + trinkets gather (cron runs this).
  pull             Pull the Telegram queue only (links → inbox/).
  build            Build bowers from read+annotated trinkets/ → brain/bowers/.
  peck             Run a spaced-rep quiz session over due bowers (pull-only).
  forage           Gather gap signals and write forage.md proposals (pull-only).
  prune            Delete cold archive/ husks older than the TTL (manual, confirmed).
"""

from __future__ import annotations

import sys

from bower_bird.app import run_all, run_gather, run_telegram
from bower_bird.config import Config, load_config


def _load() -> Config | int:
    """Load config, or return exit code 2 on failure (with a message)."""
    try:
        return load_config()
    except RuntimeError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


def _run(fn) -> int:
    """Run a count-returning lane; print a summary, exit 0 (count != exit code)."""
    config = _load()
    if isinstance(config, int):
        return config
    count = fn(config)
    if count:
        print(f"bower-bird: processed {count} item(s).")
    return 0


def pull() -> int:
    """`uv run pull` — pull the Telegram queue."""
    return _run(run_telegram)


def build() -> int:
    """`uv run build` — build bowers from trinkets/."""
    return _run(run_gather)


def main() -> int:
    # Parse a minimal subcommand — no dependency on argparse so the module
    # stays light. No arg runs both capture lanes; named verbs scope it.
    args = sys.argv[1:]
    subcommand = args[0] if args else None

    if subcommand == "peck":
        config = _load()
        if isinstance(config, int):
            return config
        from bower_bird.review import main as peck_main

        return peck_main(config)

    if subcommand == "forage":
        config = _load()
        if isinstance(config, int):
            return config
        from bower_bird.forage import main as forage_main

        return forage_main(config)

    if subcommand == "prune":
        config = _load()
        if isinstance(config, int):
            return config
        from bower_bird.prune import main as prune_main

        return prune_main(config)

    if subcommand == "pull":
        return _run(run_telegram)
    if subcommand == "build":
        return _run(run_gather)
    return _run(run_all)


if __name__ == "__main__":
    raise SystemExit(main())
