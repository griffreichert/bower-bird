"""Vault-write boundary guard + bits shared by `nodes.py` and `queues.py`.

Hard boundary (per INVARIANTS): bower-bird owns the `BowerBird/` folder
(`config.vault_path`) and writes *nowhere else*. `assert_writable` enforces
that in code so a future change can't quietly break it — every writer in
`nodes.py` and `queues.py` calls it before touching a path.

Additive-autonomous: bower-bird creates its own notes (sources/, new concept
notes in brain/bowers/) and asserts `[[links]]` freely, but it **never rewrites
or deletes an existing note**. Every write is either a brand-new file or a
strictly additive append — so nothing a human authored is ever clobbered.
"""

import re
from datetime import datetime
from pathlib import Path

from bower_bird.config import Config
from bower_bird.schema import TweetText

_INVALID_FILENAME = re.compile(r'[/:\\?%*|"<>]')


def assert_writable(config: Config, path: Path) -> None:
    """Refuse any write outside the owned `BowerBird/` folder."""
    resolved = path.resolve()
    root = config.vault_path.resolve()
    if resolved != root and root not in resolved.parents:
        raise PermissionError(
            f"Refusing to write outside the owned folder: {resolved}. "
            f"Only {root} and its contents are writable at runtime."
        )


def yaml_scalar(value: str) -> str:
    """Sanitize a string for a double-quoted YAML scalar in frontmatter.

    Page metadata and model output are attacker-influenceable (a hostile page's
    og:title, or prompt-injected model fields). Collapsing whitespace kills
    newline-injection of spurious frontmatter keys; swapping `"`→`'` keeps the
    closing quote intact. Apply to every value interpolated into frontmatter."""
    return " ".join(value.split()).replace('"', "'")


def safe_filename(title: str) -> str:
    cleaned = _INVALID_FILENAME.sub("-", title).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or "untitled")[:120]


def today_iso() -> str:
    return datetime.now().date().isoformat()


# --------------------------------------------------------------------------- #
# tweet body rendering (reused by the shelve lane's source-node ## Body)
# --------------------------------------------------------------------------- #


def format_tweet_body(tweet: TweetText) -> str:
    """Render a resolved tweet's text (+ reply hint / quote block) as markdown.

    Every send is shelved immediately now (antilibrary model, 2026-07-13) — a
    tweet gets one source node straight away, not a rendered doc in a separate
    reading room. This is the body renderer for that node's `## Body`.
    """
    lines = []
    if tweet.in_reply_to:
        hint = f"*↳ reply to @{tweet.in_reply_to} — open the link for the thread*"
        lines += [hint, ""]
    lines.append(tweet.text.strip())
    if tweet.quoted_text:
        quoted = "\n".join(f"> {ln}" for ln in tweet.quoted_text.strip().splitlines())
        lines += ["", f"> **quoting @{tweet.quoted_handle}:**", quoted]
    return "\n".join(lines)
