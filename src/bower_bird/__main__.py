"""Entry point: `python -m bower_bird` (or the `bower-bird` script).

Subcommands:
  (default)        Drain the Telegram queue once.
  peck             Run a spaced-rep quiz session over due bowers (pull-only).
  forage           Gather gap signals and write forage.md proposals (pull-only).
"""

from __future__ import annotations

import sys

from .app import drain
from .config import load_config


def main() -> int:
    # Parse a minimal subcommand — no dependency on argparse so the module
    # stays light. Only `peck` is special; everything else drains.
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

    count = drain(config)
    if count:
        print(f"bower-bird: processed {count} item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
