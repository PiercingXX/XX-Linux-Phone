"""Thumbnail choice for YouTube's image hosts.

Search, home and page responses carry several renditions of the same art.
``pick`` chooses the smallest rendition that still covers ``px`` square
pixels and rewrites its size parameters for exactly ``px``; ``sized``
rewrites one URL. URLs without size parameters (for example the
``i.ytimg.com`` playlist thumbs) are returned unchanged.
"""

from __future__ import annotations

import re
from typing import Any

from xx_music.engine import EngineError, ErrorKind

_WH_RE = re.compile(r"=w(\d+)-h(\d+)")
_S_RE = re.compile(r"=s(\d+)")


def sized(url: str, px: int) -> str:
    """The artwork URL asking the image host for ``px`` square pixels."""
    if not url or px <= 0:
        return url
    if _WH_RE.search(url):
        return _WH_RE.sub(f"=w{px}-h{px}", url)
    if _S_RE.search(url):
        return _S_RE.sub(f"=s{px}", url)
    return url


def pick(thumbnails: list[dict[str, Any]] | None, px: int = 256) -> str:
    """Best thumbnail URL for ``px`` square pixels, or "" when none qualify.

    Raises EngineError(SOURCE_CHANGED) when ``thumbnails`` is present but
    is not a list: YouTube always answers a list here.
    """
    if not thumbnails:
        return ""
    if not isinstance(thumbnails, list):
        raise EngineError(ErrorKind.SOURCE_CHANGED, "youtube: thumbnails")
    usable: list[tuple[int, str]] = []
    for thumb in thumbnails:
        if not isinstance(thumb, dict):
            continue
        url = thumb.get("url")
        if not isinstance(url, str) or not url:
            continue
        width = thumb.get("width")
        usable.append((width if isinstance(width, int) and width > 0 else 0,
                       url))
    if not usable:
        return ""
    big_enough = sorted(w for w, _ in usable if w >= px)
    want = big_enough[0] if big_enough else max(w for w, _ in usable)
    for width, url in usable:
        if width == want:
            return sized(url, px)
    return sized(usable[0][1], px)
