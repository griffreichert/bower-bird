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


HELP = """\
usage: bb <verb> [args]

  ask [query]   search the vault; no query lists notes (--author --since --type)
  peck          review what is due (the /peck skill also tutors a topic)
  anki          push cards to Anki, harvest reviews back

maintenance:
  pull build drain prune tidy lint stale touch sql
  (bb with no verb runs one full capture pass)"""


def main() -> int:
    """Run `bb [verb]`; no verb runs one full capture pass.

    Verbs are listed in ``HELP``. An unknown verb prints the help and exits 2
    so a typo never starts a capture pass.
    """
    args = sys.argv[1:]
    subcommand = args[0] if args else None

    if subcommand in {"help", "-h", "--help"}:
        print(HELP)
        return 0

    if subcommand == "peck":
        config = config_or_exit()
        if isinstance(config, int):
            return config
        from bower_bird.peck import main as peck_main

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

    if subcommand == "tidy":
        apply = "--apply" in args[1:]
        config = config_or_exit()
        if isinstance(config, int):
            return config
        from bower_bird.tidy import main as tidy_main

        return tidy_main(config, apply=apply)

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

    if subcommand in {"ask", "sql"}:
        config = config_or_exit()
        if isinstance(config, int):
            return config
        from bower_bird.recall import main as recall_main

        return recall_main(config, subcommand, args[1:])

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

    if subcommand == "anki":
        config = config_or_exit()
        if isinstance(config, int):
            return config
        from bower_bird.anki import main as anki_main

        return anki_main(config, args[1:])

    if subcommand == "drain":
        from bower_bird.app import run_drain

        return run_lane(run_drain)

    if subcommand == "pull":
        return run_lane(run_telegram)
    if subcommand == "build":
        return run_lane(run_gather)
    if subcommand is None:
        return run_lane(run_all)
    print(HELP, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
