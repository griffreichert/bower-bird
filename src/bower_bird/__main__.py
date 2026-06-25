"""Entry point: `python -m bower_bird` (or the `bb` / `bower-bird` scripts).

Subcommands:
  (default)        One full pass: Telegram queue + trinkets gather (cron runs this).
  telegram         Pull the Telegram queue only (links → inbox/).
  gather           Gather read+annotated trinkets/ → brain/bowers/ only.
  peck             Run a spaced-rep quiz session over due bowers (pull-only).
  forage           Gather gap signals and write forage.md proposals (pull-only).
"""

from __future__ import annotations

import sys

from .app import run_all, run_gather, run_telegram
from .config import load_config


def main() -> int:
    # Parse a minimal subcommand — no dependency on argparse so the module
    # stays light. No arg runs both capture lanes; named verbs scope it.
    args = sys.argv[1:]
    subcommand = args[0] if args else None

    try:
        config = load_config()
    except RuntimeError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if subcommand == "peck":
        from .review import main as peck_main

        return peck_main(config)

    if subcommand == "forage":
        from .forage import main as forage_main

        return forage_main(config)

    if subcommand == "telegram":
        count = run_telegram(config)
    elif subcommand == "gather":
        count = run_gather(config)
    else:
        count = run_all(config)

    if count:
        print(f"bower-bird: processed {count} item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
