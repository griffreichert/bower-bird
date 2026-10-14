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
    closing quote intact. Apply to every value interpolated into frontmatter.
    """
    return " ".join(value.split()).replace('"', "'")


def safe_filename(title: str) -> str:
    """Return ``title`` as a filename: invalid characters dashed, 120 chars max."""
    cleaned = _INVALID_FILENAME.sub("-", title).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or "untitled")[:120]


def today_iso() -> str:
    """Return today's local date as ``YYYY-MM-DD``."""
    return datetime.now().date().isoformat()


def format_tweet_body(tweet: TweetText) -> str:
    """Render a resolved tweet, its reply hint and quote block as markdown.

    The result is the ``## Body`` of the tweet's source node.
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
