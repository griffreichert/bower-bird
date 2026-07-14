"""Resolve X/Twitter links to tweet text via public proxy endpoints.

X/Twitter pages are JS-walled (fetch.needs_clipping routes them straight to
the clip queue — a plain httpx GET gets nothing). Rather than dead-end those
links, we resolve the tweet text ourselves via two public proxies that don't
require auth: fxtwitter.com first, Twitter's own syndication CDN (used to
embed tweets on third-party pages) as fallback. Both verified working
2026-07-09. If both fail, resolve_tweet returns None and the caller falls
back to the clip queue as before.
"""

import math
import re
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from bower_bird.fetch import safe_get

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


class TweetText(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    url: str  # canonical: https://x.com/{handle}/status/{id}
    author_handle: str  # screen_name, no @
    author_name: str
    text: str
    quoted_handle: str = ""  # set when the tweet quotes another
    quoted_text: str = ""
    in_reply_to: str = ""  # screen_name this tweet replies to ("" if not a reply)


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
    if n == 0:
        return "0"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = _BASE36_DIGITS[r] + out
    return out


def syndication_token(tweet_id: str) -> str:
    """Compute the token Twitter's syndication CDN requires: base36((id/1e15)*pi),
    integer + ~12 fractional digits, with all '0' chars and the '.' stripped.
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
    return f"https://x.com/{handle}/status/{tweet_id}"


def from_fxtwitter(data: dict) -> TweetText | None:
    if data.get("code") != 200:
        return None
    tweet = data.get("tweet")
    if not tweet or not tweet.get("text"):
        return None
    author = tweet.get("author") or {}
    handle = author.get("screen_name", "")
    quote = tweet.get("quote")
    return TweetText(
        id=str(tweet["id"]),
        url=canonical_url(handle, str(tweet["id"])),
        author_handle=handle,
        author_name=author.get("name", ""),
        text=tweet["text"],
        quoted_handle=(quote or {}).get("author", {}).get("screen_name", ""),
        quoted_text=(quote or {}).get("text", ""),
        in_reply_to=tweet.get("replying_to") or "",
    )


def from_syndication(data: dict) -> TweetText | None:
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
                return result
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
                return result
    except Exception:
        pass

    return None
