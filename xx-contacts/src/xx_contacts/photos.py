"""L4 contact photos: pick, normalize, store, and vCard PHOTO wire-up.

Photos live as re-encoded JPEG files under
``~/.local/share/xx-contacts/photos/<uid>.jpg`` (mode 0600, EXIF
stripped by re-encoding) with only the path in the ``contacts`` row.
Files — not DB blobs — keep the database small and WAL-friendly: the
list only ever reads the handful of rows on screen, while multi-KB
blobs in every row would slow every full-table scan and bloat backups.
Pixel work uses GdkPixbuf (already in the GTK stack) with Pillow as a
fallback; no new hard dependency is added.

Import-safe without a display: GI imports happen inside the functions
that need them, so ``pytest`` can import this module headless (pixel
decoding itself needs no display).
"""

from __future__ import annotations

import base64
import binascii
import os
import stat

from xx_contacts.privacy import ensure_private_dir, fix_file_mode

MAX_PHOTO_PX = 512
PHOTO_JPEG_QUALITY = 85
PHOTO_MODE = 0o600


class PhotoError(Exception):
    pass


def photo_path_for(photos_dir: str, uid: str) -> str:
    """Filesystem path for one contact's normalized photo."""
    return os.path.join(photos_dir, f"{uid}.jpg")


def _pixbuf_modules():
    """(GdkPixbuf, GLib) when available, else (None, None). No display."""
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf, GLib
    except (ImportError, ValueError) as exc:
        return None, None, exc
    return GdkPixbuf, GLib, None


def _decode_pixels(raw: bytes):
    """Decode image bytes to ``(pixels, width, height)`` row-major RGB."""
    if not raw:
        raise PhotoError("empty image")
    GdkPixbuf, GLib, loader_error = _pixbuf_modules()
    if GdkPixbuf is not None and GLib is not None:
        try:
            loader = GdkPixbuf.PixbufLoader.new()
            try:
                loader.write_bytes(GLib.Bytes(raw))
            except AttributeError:
                loader.write(raw)
            loader.close()
            pixbuf = loader.get_pixbuf()
        except Exception as exc:
            raise PhotoError(f"cannot decode image: {exc}") from exc
        if pixbuf is None:
            raise PhotoError("cannot decode image")
        pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
        if pixbuf.get_has_alpha():
            pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        return (bytes(pixbuf.get_pixels()), width, height,
                pixbuf.get_rowstride(), pixbuf.get_n_channels())
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        raise PhotoError(
            f"no image backend (GdkPixbuf unavailable: {loader_error})"
        ) from loader_error
    import io

    try:
        with Image.open(io.BytesIO(raw)) as image:
            oriented = image
            try:
                from PIL import ImageOps

                oriented = ImageOps.exif_transpose(image)
            except Exception:
                pass
            rgb = oriented.convert("RGB")
            width, height = rgb.size
            return (rgb.tobytes(), width, height, width * 3, 3)
    except Exception as exc:
        raise PhotoError(f"cannot decode image: {exc}") from exc


def _encode_jpeg(pixels: bytes, width: int, height: int) -> bytes:
    """Encode row-major RGB pixels as a JPEG (EXIF never survives this)."""
    GdkPixbuf, GLib, _error = _pixbuf_modules()
    if GdkPixbuf is not None and GLib is not None:
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes(pixels), GdkPixbuf.Colorspace.RGB, False, 8,
            width, height, width * 3)
        _ok, blob = pixbuf.save_to_bufferv(
            "jpeg", ["quality"], [str(PHOTO_JPEG_QUALITY)])
        return bytes(blob)
    from PIL import Image  # type: ignore[import-not-found]

    import io

    image = Image.frombytes("RGB", (width, height), pixels)
    with io.BytesIO() as out:
        image.save(out, format="JPEG", quality=PHOTO_JPEG_QUALITY)
        return out.getvalue()


def _crop_square(pixels: bytes, width: int, height: int
                 ) -> tuple[bytes, int, int]:
    """Center-crop RGB pixels to a square."""
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    rows = []
    stride = width * 3
    for row in range(top, top + side):
        start = row * stride + left * 3
        rows.append(pixels[start:start + side * 3])
    return b"".join(rows), side, side


def _downscale(pixels: bytes, width: int, height: int
               ) -> tuple[bytes, int, int]:
    """Bilinear downscale so the long edge fits ``MAX_PHOTO_PX``."""
    longest = max(width, height)
    if longest <= MAX_PHOTO_PX:
        return pixels, width, height
    scale = MAX_PHOTO_PX / longest
    target_w = max(1, round(width * scale))
    target_h = max(1, round(height * scale))
    src_stride = width * 3
    out = bytearray(target_w * target_h * 3)
    for out_y in range(target_h):
        src_y = min(height - 1, int((out_y + 0.5) / scale - 0.5))
        for out_x in range(target_w):
            src_x = min(width - 1, int((out_x + 0.5) / scale - 0.5))
            src = (src_y * src_stride + src_x * 3)
            dst = (out_y * target_w + out_x) * 3
            out[dst:dst + 3] = pixels[src:src + 3]
    return bytes(out), target_w, target_h


def process_photo(raw: bytes) -> bytes:
    """Normalize picked bytes: square crop, ≤512 px, JPEG, no EXIF."""
    pixels, width, height, _stride, _channels = _decode_pixels(raw)
    pixels, width, height = _crop_square(pixels, width, height)
    pixels, width, height = _downscale(pixels, width, height)
    return _encode_jpeg(pixels, width, height)


def secure_write_bytes(path: str, blob: bytes) -> None:
    """Atomically replace *path* with *blob* at mode 0600 in a 0700 dir."""
    ensure_private_dir(os.path.dirname(os.path.abspath(path)))
    tmp = path + ".tmp"
    old = os.umask(0o077)
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                      PHOTO_MODE)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(blob)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    finally:
        os.umask(old)
    fix_file_mode(tmp, PHOTO_MODE)
    os.replace(tmp, path)
    fix_file_mode(path, PHOTO_MODE)


def save_photo(photos_dir: str, uid: str, raw: bytes) -> str:
    """Normalize picked bytes and store them for ``uid``; return path."""
    ensure_private_dir(photos_dir)
    blob = process_photo(raw)
    path = photo_path_for(photos_dir, uid)
    secure_write_bytes(path, blob)
    return path


def delete_photo(photo_path: str) -> None:
    """Remove a stored photo file; missing files are not an error."""
    if photo_path:
        try:
            os.unlink(photo_path)
        except OSError:
            pass


def read_photo_bytes(photo_path: str) -> bytes | None:
    """Raw JPEG bytes for a stored photo, or None when unreadable."""
    if not photo_path:
        return None
    try:
        with open(photo_path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def photo_file_mode_ok(photo_path: str) -> bool:
    """True when a stored photo file exists with mode 0600."""
    try:
        return stat.S_IMODE(os.stat(photo_path).st_mode) == PHOTO_MODE
    except OSError:
        return False


def photo_data_uri(jpeg: bytes) -> str:
    """vCard 4.0 data-URI value for normalized JPEG bytes."""
    return ("data:image/jpeg;base64,"
            + base64.b64encode(jpeg).decode("ascii"))


def photo_bytes_from_value(params: str, value: str) -> bytes | None:
    """Decode a PHOTO value: data URI or base64 (3.0 ``ENCODING=b``)."""
    text = (value or "").strip()
    if text.lower().startswith("data:"):
        _header, comma, payload = text.partition(",")
        if not comma:
            return None
        if ";base64" not in _header.lower():
            return None
        try:
            return base64.b64decode("".join(payload.split()))
        except (binascii.Error, ValueError):
            return None
    lowered_params = (params or "").upper()
    if "QUOTED-PRINTABLE" in lowered_params:
        return None
    compact = "".join(text.split())
    if not compact or len(compact) < 8:
        return None
    try:
        blob = base64.b64decode(compact, validate=False)
    except (binascii.Error, ValueError):
        return None
    if blob[:2] != b"\xff\xd8":
        return None
    return blob


def photo_bytes_from_extra(extra_lines) -> bytes | None:
    """First decodable PHOTO payload carried in ``extra`` lines, if any."""
    for line in extra_lines or ():
        if (line.prop or "").upper() != "PHOTO":
            continue
        blob = photo_bytes_from_value(line.params or "", line.value or "")
        if blob:
            return blob
    return None


def adopt_photo_from_extra(data, photos_dir: str, uid: str) -> str:
    """Move an imported PHOTO payload into the photo file; return path.

    Decodes the first base64/data-URI PHOTO carried in ``data.extra``,
    normalizes it through :func:`process_photo`, stores it, sets
    ``data.photo_path``, and drops the adopted line from ``extra`` so a
    later export does not emit the photo twice. Returns ``""`` when the
    card carries no decodable photo.
    """
    blob = photo_bytes_from_extra(data.extra)
    if not blob:
        return ""
    path = save_photo(photos_dir, uid, blob)
    data.photo_path = path
    for index, line in enumerate(data.extra):
        if (line.prop or "").upper() != "PHOTO":
            continue
        if photo_bytes_from_value(line.params or "",
                                  line.value or "") is not None:
            del data.extra[index]
            break
    return path
