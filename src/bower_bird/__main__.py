"""Entry point: `python -m bower_bird` (or the `bower-bird` script).

Drains the Telegram queue once and reports how many items were handled. Quiet
when there's nothing to do.
"""

from __future__ import annotations

import sys

from .app import drain
from .config import load_config


def main() -> int:
    try:
        config = load_config()
    except RuntimeError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    count = drain(config)
    if count:
        print(f"bower-bird: processed {count} item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
