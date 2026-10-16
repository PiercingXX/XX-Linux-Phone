"""MMS photo scrub + fit (port of ``core/ImageMetadataScrubber``,
``core/PngMetadataScrubber``, ``core/MetadataScrubber`` and
``service/MmsImageFit``).

Every outbound photo passes :func:`prepare_for_send` before it reaches
mmsd-tng: the bytes are sniffed, scrubbed, then fit to the carrier cap.
Anything that cannot be proven scrubbed returns None — the caller must
NOT send (fail closed, never unscrubbed bytes off device).

Container rules mirror Android ``MetadataScrubber.scrub``: JPEG and PNG
are stripped in place with no re-encode; HEIF/HEIC/AVIF, WebP, GIF, MP4
and unknown bytes return None.

Size rules mirror ``SendPipeline``: the carrier PDU cap defaults to
300_000 bytes (``DEFAULT_MMS_MAX_BYTES``) minus a 24_000 header/SMIL
allowance, floored at 80_000. Bytes already under budget pass through
scrubbed and untouched so tiny fixtures need no decoder. Oversize
JPEGs are downscaled (long side to 1600 first, then 0.7 steps to a
320 px floor) and re-encoded with falling JPEG quality (75 down to 35)
via ``GdkPixbuf`` — stdlib + GI only, no pip dependencies. PNG input
is scrubbed but never re-encoded here: the send path converts the
scrubbed PNG through ``GdkPixbuf`` to JPEG when it must fit, and
refuses when no decoder is available.

Pure except for the ``GdkPixbuf`` re-encode seam, which is lazy so
headless tests exercise scrub + fit math without a display.
"""

from __future__ import annotations

#: Carrier PDU cap when the modem reports nothing usable (Android
#: ``SendPipeline.DEFAULT_MMS_MAX_BYTES``).
DEFAULT_MMS_MAX_BYTES = 300_000
#: Header/SMIL allowance subtracted from the PDU cap (``maxImageBytes``).
MMS_HEADER_ALLOWANCE = 24_000
#: Floor for the image budget (``maxImageBytes`` ``coerceAtLeast``).
MIN_IMAGE_BYTES = 80_000
#: Long-side cap before quality stepping (``MmsImageFit`` sample loop).
MAX_SIDE_PX = 1600
#: Downscale floor for the shrink loop.
MIN_SIDE_PX = 320

_JPEG_SOI = b"\xff\xd8"
_PNG_SIG = b"\x89PNG\r\n\x1a\n"

#: JPEG markers whose segments carry privacy metadata (APP1 EXIF/XMP,
#: APP13 IPTC/Photoshop, COM comment). APP0 (JFIF) and APP2 (ICC) are
#: display data and are preserved.
_METADATA_MARKERS = frozenset({0xE1, 0xED, 0xFE})

#: PNG chunk types carrying privacy metadata (EXIF + free-form text/XMP).
_METADATA_CHUNKS = frozenset({"eXIf", "tEXt", "zTXt", "iTXt"})

_HEIF_BRANDS = frozenset({
    "heic", "heif", "heix", "hevc", "hevx", "mif1", "msf1", "avif",
})


def max_image_bytes(cap: int = 0) -> int:
    """Image budget for a carrier PDU cap (Android ``maxImageBytes``)."""
    base = cap if cap and cap > 32_000 else DEFAULT_MMS_MAX_BYTES
    return max(base - MMS_HEADER_ALLOWANCE, MIN_IMAGE_BYTES)


def sniff(data: bytes) -> str | None:
    """Container of ``data`` by magic: jpeg/png/gif/webp/heif/mp4/None."""
    if not data:
        return None
    if len(data) >= 2 and data[0] == 0xFF and data[1] == 0xD8:
        return "jpeg"
    if len(data) >= 8 and bytes(data[:8]) == _PNG_SIG:
        return "png"
    if len(data) >= 6 and bytes(data[:3]) == b"GIF" and bytes(data[3:6]) in (b"87a", b"89a"):
        return "gif"
    if (
        len(data) >= 12 and bytes(data[:4]) == b"RIFF"
        and bytes(data[8:12]) == b"WEBP"
    ):
        return "webp"
    if len(data) >= 12 and bytes(data[4:8]) == b"ftyp":
        brand = bytes(data[8:12]).decode("latin-1").lower()
        if brand in _HEIF_BRANDS:
            return "heif"
        return "mp4"
    return None


def scrub_jpeg(data: bytes) -> bytes | None:
    """JPEG with APP1/APP13/COM segments removed, scan byte-identical.

    Non-JPEG input passes through unchanged (this scrubber recognises
    no container in it); malformed JPEG (bad marker, truncated length,
    overrun segment, missing EOI) returns None.
    """
    if len(data) < 2 or data[0] != 0xFF or data[1] != 0xD8:
        return data
    out = bytearray()
    i = 0
    size = len(data)
    in_scan = False
    saw_eoi = False
    while i < size and not saw_eoi:
        if not in_scan and data[i] != 0xFF:
            return None
        if i + 1 >= size:
            return None
        marker = data[i + 1]
        if marker == 0xDA:
            out += data[i:i + 2]
            i += 2
            in_scan = True
            continue
        if marker == 0xD9:
            out += data[i:]
            saw_eoi = True
            break
        if in_scan:
            out.append(data[i])
            i += 1
            continue
        if marker in (0x01, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9):
            out += data[i:i + 2]
            i += 2
            continue
        if i + 4 > size:
            return None
        length = (data[i + 2] << 8) | data[i + 3]
        end = i + 2 + length
        if end > size:
            return None
        if marker in _METADATA_MARKERS:
            i = end
            continue
        out += data[i:end]
        i = end
    if not saw_eoi:
        return None
    return bytes(out)


def scrub_png(data: bytes) -> bytes | None:
    """PNG with eXIf/tEXt/zTXt/iTXt chunks removed, rest byte-identical.

    Non-PNG input passes through unchanged; a truncated header or a
    chunk length overrunning the bytes returns None. A missing trailing
    IEND is tolerated (every chunk was still walked).
    """
    if len(data) < 8 or bytes(data[:8]) != _PNG_SIG:
        return data
    out = bytearray(_PNG_SIG)
    i = 8
    size = len(data)
    while i < size:
        if i + 8 > size:
            return None
        length = (
            (data[i] << 24) | (data[i + 1] << 16) | (data[i + 2] << 8) | data[i + 3]
        )
        if length < 0:
            return None
        chunk_type = bytes(data[i + 4:i + 8]).decode("latin-1")
        if length > size - (i + 8) - 4:
            return None
        end = i + 8 + length + 4
        if chunk_type not in _METADATA_CHUNKS:
            out += data[i:end]
        i = end
    return bytes(out)


def scrub(data: bytes) -> bytes | None:
    """Entry point: stripped JPEG/PNG copy, or None when it must not send.

    JPEG and PNG are scrubbed; HEIF/WebP/GIF/MP4/unknown return None so
    unscrubbable bytes never leave the device.
    """
    if not data:
        return None
    kind = sniff(data)
    if kind == "jpeg":
        return scrub_jpeg(data)
    if kind == "png":
        return scrub_png(data)
    return None


def fit_dimensions(width: int, height: int, max_side: int = MAX_SIDE_PX) -> tuple[int, int]:
    """Dimensions with the long side capped at ``max_side`` (aspect kept)."""
    if width <= 0 or height <= 0:
        return (0, 0)
    longest = max(width, height)
    if longest <= max_side:
        return (width, height)
    scale = max_side / float(longest)
    return (max(1, int(width * scale)), max(1, int(height * scale)))


def shrink_steps(width: int, height: int) -> list[tuple[int, int]]:
    """Successive 0.7 resizes down to the 320 px floor (fit loop shapes)."""
    steps = []
    w, h = width, height
    while max(w, h) > MIN_SIDE_PX:
        w = max(MIN_SIDE_PX, int(w * 0.7))
        h = max(MIN_SIDE_PX, int(h * 0.7))
        steps.append((w, h))
        if len(steps) > 12:
            break
    return steps


def needs_fit(data_len: int, budget: int) -> bool:
    """Whether ``data_len`` bytes exceed the image budget."""
    return data_len > budget


def _decode_dimensions(path: str | None, data: bytes | None):
    """Image dimensions via GdkPixbuf without loading full pixels twice."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path) if path else (None, 0, 0)
    if path is not None and w > 0 and h > 0:
        return (w, h)
    loader = GdkPixbuf.PixbufLoader.new()
    try:
        loader.write(data if data is not None else open(path, "rb").read())
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf is None:
            return None
        return (pixbuf.get_width(), pixbuf.get_height())
    except Exception:
        try:
            loader.close()
        except Exception:
            pass
        return None


def _reencode_jpeg(path: str | None, data: bytes | None, width: int, height: int, quality: int) -> bytes | None:
    """Load, scale to ``width``x``height`` and re-encode as JPEG."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    try:
        if path is not None:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
        else:
            loader = GdkPixbuf.PixbufLoader.new()
            loader.write(bytes(data or b""))
            loader.close()
            pixbuf = loader.get_pixbuf()
        if pixbuf is None:
            return None
        if pixbuf.get_width() != width or pixbuf.get_height() != height:
            pixbuf = pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
        if pixbuf is None:
            return None
        ok, out = pixbuf.save_to_bufferv("jpeg", ["quality"], [str(int(quality))])
        if not ok or not out:
            return None
        return bytes(out)
    except Exception:
        return None


def fit_jpeg(
    data: bytes,
    budget: int,
    dimensions: tuple[int, int] | None = None,
    reencode=None,
) -> bytes | None:
    """Shrink scrubbed JPEG ``data`` to ``budget`` (Android ``constrain``).

    ``dimensions`` skips the decoder probe in tests; ``reencode``
    replaces the GdkPixbuf step (``(width, height, quality) -> bytes``).
    Returns None when the image cannot be decoded; otherwise the
    smallest bytes the loop reached (best effort — possibly still over
    budget, but always scrubbed input, never unscrubbed bytes).
    """
    if budget <= 0 or not data:
        return None
    if len(data) <= budget:
        return data
    if dimensions is None:
        try:
            loader_probe = _decode_dimensions(None, data)
        except Exception:
            return None
        dimensions = loader_probe
    if not dimensions or dimensions[0] <= 0 or dimensions[1] <= 0:
        return None
    width, height = fit_dimensions(dimensions[0], dimensions[1])
    encode = reencode or (lambda w, h, q: _reencode_jpeg(None, data, w, h, q))
    try:
        first = encode(width, height, 75)
    except Exception:
        return None
    if not first:
        return None
    out = first
    quality = 75
    while len(out) > budget and quality > 35:
        quality -= 10
        try:
            out = encode(width, height, quality)
        except Exception:
            return None
        if not out:
            return None
    while len(out) > budget and max(width, height) > MIN_SIDE_PX:
        width = max(MIN_SIDE_PX, int(width * 0.7))
        height = max(MIN_SIDE_PX, int(height * 0.7))
        try:
            out = encode(width, height, quality)
        except Exception:
            return None
        if not out:
            return None
    if not out or len(out) == 0:
        return None
    return out if len(out) <= budget else out


def prepare_for_send(data: bytes, budget: int) -> bytes | None:
    """Scrub then fit ``data`` for the carrier budget, or None to refuse.

    JPEG: scrub, pass through under budget, else GdkPixbuf fit. PNG:
    scrub, pass through under budget (mmsd-tng accepts PNG), else
    convert through the JPEG fit path so no unscrubbed bytes send.
    Anything else (or any failure) returns None.
    """
    if not data:
        return None
    kind = sniff(data)
    if kind not in ("jpeg", "png"):
        return None
    cleaned = scrub(data)
    if cleaned is None:
        return None
    if len(cleaned) <= budget:
        return cleaned
    if kind == "jpeg":
        try:
            fitted = fit_jpeg(cleaned, budget)
        except Exception:
            return None
        return fitted
    try:
        info = _decode_dimensions(None, cleaned)
    except Exception:
        return None
    if not info or info[0] <= 0 or info[1] <= 0:
        return None
    width, height = fit_dimensions(info[0], info[1])
    try:
        out = _reencode_jpeg(None, cleaned, width, height, 75)
    except Exception:
        return None
    if not out:
        return None
    if len(out) <= budget:
        return out
    try:
        return fit_jpeg(out, budget, dimensions=(width, height))
    except Exception:
        return None


def prepare_file(path: str, budget: int) -> bytes | None:
    """Read ``path`` and :func:`prepare_for_send` it, or None to refuse."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    return prepare_for_send(data, budget)


def build_smil(image_name: str | None, text_name: str | None) -> str:
    """Minimal SMIL presentation referencing the attachment file names."""
    parts = ""
    if image_name:
        parts += f'<img src="{image_name}" region="Image"/>'
    if text_name:
        parts += f'<text src="{text_name}" region="Text"/>'
    return (
        "<smil><head><layout>"
        '<region id="Image"/><region id="Text"/>'
        f"</layout></head><body><par>{parts}</par></body></smil>"
    )
