import math
import re
from urllib.parse import urlparse

from bower_bird.fetch import follow_redirect, safe_get
from bower_bird.schema import TweetText

_TCO_URL_RE = re.compile(r"https://t\.co/\w+")

_TWEET_HOSTS = {
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
}
_STATUS_PATH_RE = re.compile(r"^/([^/]+)/status/(\d+)(?:/|$)")

_SYNDICATION_FRAC_DIGITS = 12
_BASE36_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


def parse_tweet_id(url: str) -> str | None:
    """Extract the numeric tweet id from an x.com/twitter.com status URL.

    Accepts share-link junk (query strings like `?s=52`, trailing path
    segments like `/photo/1`). Returns None for anything else.
    """
    parsed = urlparse(url)
    if parsed.hostname is None or parsed.hostname.lower() not in _TWEET_HOSTS:
        return None
    match = _STATUS_PATH_RE.match(parsed.path)
    if not match:
        return None
    return match.group(2)


def base36(n: int) -> str:
    """Return ``n`` in base 36, lowercase digits."""
    if n == 0:
        return "0"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = _BASE36_DIGITS[r] + out
    return out


def syndication_token(tweet_id: str) -> str:
    """Compute the token Twitter's syndication CDN requires.

    The token is base36 of ``(id / 1e15) * pi``, integer part plus about 12
    fractional digits, with every ``0`` and the ``.`` stripped.
    """
    value = (int(tweet_id) / 1e15) * math.pi
    whole = int(value)
    frac = value - whole
    frac_digits = ""
    for _ in range(_SYNDICATION_FRAC_DIGITS):
        frac *= 36
        digit = int(frac)
        frac_digits += _BASE36_DIGITS[digit]
        frac -= digit
    token = f"{base36(whole)}.{frac_digits}"
    return token.replace("0", "").replace(".", "")


def canonical_url(handle: str, tweet_id: str) -> str:
    """Return the canonical ``x.com`` status URL for a tweet."""
    return f"https://x.com/{handle}/status/{tweet_id}"


_ARTICLE_BLOCK_PREFIXES = {
    "header-one": "# ",
    "header-two": "## ",
    "header-three": "### ",
    "unordered-list-item": "- ",
    "ordered-list-item": "1. ",
    "blockquote": "> ",
}
_ARTICLE_LIST_TYPES = {"unordered-list-item", "ordered-list-item"}


def article_markdown(content: dict) -> str:
    """Render a native X Article's Draft.js block list to markdown.

    Ignores entityRanges/inlineStyleRanges — plain text carries the substance.
    Blank line between blocks, except consecutive list items stay adjacent.
    """
    lines: list[str] = []
    prev_type = None
    for block in content.get("blocks") or []:
        block_type = block.get("type") or ""
        text = block.get("text") or ""
        if block_type == "atomic" or not text:
            prev_type = None
            continue
        if lines and not (
            block_type in _ARTICLE_LIST_TYPES and prev_type in _ARTICLE_LIST_TYPES
        ):
            lines.append("")
        prefix = _ARTICLE_BLOCK_PREFIXES.get(block_type, "")
        lines.append(f"{prefix}{text}")
        prev_type = block_type
    return "\n".join(lines)


def from_fxtwitter(data: dict) -> TweetText | None:
    """Parse an fxtwitter payload, including any native X Article body."""
    if data.get("code") != 200:
        return None
    tweet = data.get("tweet")
    # An Article tweet can have empty `text` with its content in `article`.
    if not tweet or (not tweet.get("text") and not tweet.get("article")):
        return None
    author = tweet.get("author") or {}
    handle = author.get("screen_name", "")
    quote = tweet.get("quote")
    article = tweet.get("article")
    article_title = ""
    article_body = ""
    if article:
        try:
            article_title = article.get("title") or ""
            article_body = article_markdown(article.get("content") or {})
        except Exception:
            # Malformed article payload — leave fields empty, never raise.
            article_title = ""
            article_body = ""
    return TweetText(
        id=str(tweet["id"]),
        url=canonical_url(handle, str(tweet["id"])),
        author_handle=handle,
        author_name=author.get("name", ""),
        text=tweet.get("text") or "",
        quoted_handle=(quote or {}).get("author", {}).get("screen_name", ""),
        quoted_text=(quote or {}).get("text", ""),
        in_reply_to=tweet.get("replying_to") or "",
        article_title=article_title,
        article_body=article_body,
    )


def from_syndication(data: dict) -> TweetText | None:
    """Parse a syndication CDN payload; it never carries an Article body."""
    if not data.get("text") or not data.get("id_str"):
        return None
    user = data.get("user") or {}
    handle = user.get("screen_name", "")
    quoted = data.get("quoted_tweet") or {}
    return TweetText(
        id=data["id_str"],
        url=canonical_url(handle, data["id_str"]),
        author_handle=handle,
        author_name=user.get("name", ""),
        text=data["text"],
        quoted_handle=quoted.get("user", {}).get("screen_name", ""),
        quoted_text=quoted.get("text", ""),
        in_reply_to=data.get("in_reply_to_screen_name") or "",
    )


def expand_urls(text: str, timeout: float) -> str:
    """Replace every ``https://t.co/<id>`` shortlink with its redirect target.

    Neither fxtwitter's
    `text` nor its `raw_text.text` expand t.co, and syndication's `text` is
    also unexpanded (its `entities.urls[].expanded_url` does carry it, but
    only on that one path) — so this always does the HTTP hop rather than
    trusting either payload. A link that fails to resolve (network error,
    blocked host, too many redirects) is left as-is. Never raises.
    """
    for shortlink in set(_TCO_URL_RE.findall(text)):
        try:
            expanded = follow_redirect(shortlink, timeout)
        except Exception:
            continue
        if expanded and expanded != shortlink:
            text = text.replace(shortlink, expanded)
    return text


def expand_tweet_text(tweet: TweetText, timeout: float) -> TweetText:
    """Expand t.co shortlinks in a tweet's text and quoted text.

    Every consumer, from shelving to dedup, then sees real URLs.
    """
    return tweet.model_copy(
        update={
            "text": expand_urls(tweet.text, timeout),
            "quoted_text": expand_urls(tweet.quoted_text, timeout),
        }
    )


def resolve_tweet(url: str, timeout: float) -> TweetText | None:
    """Resolve tweet text for an x.com/twitter.com status URL.

    Tries fxtwitter first, then Twitter's syndication CDN. Never raises —
    any failure at any step falls through to the next, and the final
    fallback is None (caller routes to the to-clip queue).
    """
    tweet_id = parse_tweet_id(url)
    if tweet_id is None:
        return None

    try:
        resp = safe_get(f"https://api.fxtwitter.com/status/{tweet_id}", timeout)
        if resp.status_code == 200:
            result = from_fxtwitter(resp.json())
            if result is not None:
                return expand_tweet_text(result, timeout)
    except Exception:
        pass

    try:
        token = syndication_token(tweet_id)
        resp = safe_get(
            f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&token={token}",
            timeout,
        )
        if resp.status_code == 200:
            result = from_syndication(resp.json())
            if result is not None:
                return expand_tweet_text(result, timeout)
    except Exception:
        pass

    return None
