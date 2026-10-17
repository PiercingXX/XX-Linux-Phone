"""Thumbnail choice: size parameters for the pixel size asked."""

import pytest

from xx_music import engine as e
from xx_radio.youtube import art

W60 = ("https://yt3.googleusercontent.com/abc"
       "=w60-h60-l90-rj")
S192 = "https://yt3.ggpht.com/abc=s192"
PLAIN = ("https://i.ytimg.com/vi/a5uQMwRMHcs/hqdefault.jpg"
         "?sqp=-oaymwEWCJADEOEBIAQqCggAEOADGC0guwJIWg")


def test_sized_rewrites_host_parameters():
    assert art.sized(W60, 640) == ("https://yt3.googleusercontent.com/abc"
                                   "=w640-h640-l90-rj")
    assert art.sized(S192, 256).endswith("=s256")
    assert art.sized(PLAIN, 640) == PLAIN
    assert art.sized("", 64) == ""
    assert art.sized(W60, 0) == W60


def _thumb(name, width):
    return {"url": f"https://img/{name}=w{width}-h{width}-l90-rj",
            "width": width, "height": width}


def test_pick_prefers_smallest_covering_rendition():
    thumbs = [_thumb("small", 60), _thumb("mid", 120), _thumb("big", 544)]
    got = art.pick(thumbs, 100)
    assert got.startswith("https://img/mid=")
    assert "=w100-h100" in got


def test_pick_falls_back_to_largest():
    got = art.pick([_thumb("small", 60)], 640)
    assert got.startswith("https://img/small=")
    assert "=w640-h640" in got


def test_pick_empty_and_broken():
    assert art.pick(None) == ""
    assert art.pick([]) == ""
    assert art.pick([{"width": 60}]) == ""
    with pytest.raises(e.EngineError) as caught:
        art.pick("nope")
    assert caught.value.kind == e.ErrorKind.SOURCE_CHANGED
