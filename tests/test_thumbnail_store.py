"""Thumbnail cache and shm downscale for recents stills."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from thumbnail_store import Thumbnail, ThumbnailStore, downsample_shm


def _solid_xrgb(width: int, height: int, b: int, g: int, r: int) -> bytes:
    row = bytes([b, g, r, 0]) * width
    return row * height


def test_downsample_xrgb_to_rgba_and_size() -> None:
    src = _solid_xrgb(40, 80, 10, 20, 30)
    thumb = downsample_shm(src, 40, 80, 40 * 4, shm_format=1, max_edge=20)
    assert thumb.width == 10
    assert thumb.height == 20
    assert len(thumb.rgba) == 10 * 20 * 4
    assert thumb.rgba[0:4] == bytes([30, 20, 10, 255])


def test_store_put_get_and_own_app_rejected() -> None:
    store = ThumbnailStore(max_items=2)
    t = Thumbnail(2, 2, b'\x00' * 16)
    store.put('io.piercingxx.XXWM', t)
    assert store.get('io.piercingxx.XXWM') is None
    store.put('org.gnome.Calculator', t)
    assert store.get('org.gnome.Calculator') is t


def test_store_lru_evicts_oldest() -> None:
    store = ThumbnailStore(max_items=2)
    a = Thumbnail(1, 1, b'\x01\x00\x00\xff')
    b = Thumbnail(1, 1, b'\x02\x00\x00\xff')
    c = Thumbnail(1, 1, b'\x03\x00\x00\xff')
    store.put('a', a)
    store.put('b', b)
    store.put('c', c)
    assert store.get('a') is None
    assert store.get('b') is b
    assert store.get('c') is c


def test_store_on_change() -> None:
    store = ThumbnailStore()
    hits: list[int] = []
    store.on_change(lambda: hits.append(1))
    store.put('org.a.App', Thumbnail(1, 1, b'\x00\x00\x00\xff'))
    assert hits == [1]


def test_ppm_to_thumb(tmp_path) -> None:
    from output_capture import _ppm_to_thumb
    rgb = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
    path = tmp_path / 't.ppm'
    path.write_bytes(b'P6\n2 2\n255\n' + rgb)
    thumb = _ppm_to_thumb(str(path))
    assert thumb is not None
    assert thumb.width == 2
    assert thumb.height == 2
    assert thumb.rgba[0:4] == bytes([255, 0, 0, 255])


def test_switcher_thumb_seam() -> None:
    from app_switcher import AppInfo, AppSwitcher

    store = ThumbnailStore()
    thumb = Thumbnail(1, 1, b'\x00\x00\x00\xff')
    store.put('org.gnome.Calculator', thumb)
    app = AppInfo('org.gnome.Calculator', 'Calculator', handle='h1')
    assert AppSwitcher._thumb_for_app(store, app) is thumb
    assert AppSwitcher._thumb_for_app(None, app) is None
    missing = AppInfo('org.other', 'Other', handle='h2')
    assert AppSwitcher._thumb_for_app(store, missing) is None
