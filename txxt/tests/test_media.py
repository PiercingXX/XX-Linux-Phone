"""L5 media tests: EXIF/metadata scrub (fail closed) + fit math.

Ports the Android scrubber contracts (``ImageMetadataScrubber``,
``PngMetadataScrubber``, ``MetadataScrubber``, ``MmsImageFit``):
scrubbed JPEG/PNG bytes carry no metadata segments, unknown formats
(HEIC/WebP/GIF/MP4/random) are refused, and malformed input returns
None — never unscrubbed bytes. Fit math is pure; the GdkPixbuf
re-encode path is exercised only when the loader is available.
"""

import struct

import pytest

from txxt import media


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


def _jpeg(*segments: bytes, scan: bytes = b"\x11\x22\xff\x00\x33") -> bytes:
    return b"\xff\xd8" + b"".join(segments) + b"\xff\xda" + scan + b"\xff\xd9"


def _chunk(chunk_type: str, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type.encode("latin-1")
        + data
        + b"\xde\xad\xbe\xef"
    )


def _png(*chunks: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def _ihdr() -> bytes:
    return _chunk("IHDR", bytes((0, 0, 0, 1, 0, 0, 0, 1, 8, 0, 0, 0)))


def _idat() -> bytes:
    return _chunk("IDAT", b"\x78\x9c\x63\x60\x60\x60\x00\x00\x00\x04\x00\x01")


def _iend() -> bytes:
    return _chunk("IEND", b"")


EXIF_PAYLOAD = b"Exif\x00\x00GPS\x00lat-40.7-lon-74.0"


def jpeg_with_gps() -> bytes:
    return _jpeg(
        _segment(0xE0, b"JFIF\x00" + b"\x00" * 9),
        _segment(0xE1, EXIF_PAYLOAD),
        _segment(0xFE, b"camera-comment"),
        _segment(0xDB, b"\x00" * 8),
    )


class TestSniff:
    def test_jpeg_png_detected(self):
        assert media.sniff(jpeg_with_gps()) == "jpeg"
        assert media.sniff(_png(_ihdr(), _idat(), _iend())) == "png"

    def test_heif_webp_gif_mp4_detected(self):
        assert media.sniff(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 8) == "heif"
        assert media.sniff(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 4) == "webp"
        assert media.sniff(b"GIF89a" + b"\x00" * 8) == "gif"
        assert media.sniff(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8) == "mp4"

    def test_random_bytes_are_unknown(self):
        assert media.sniff(b"hello world, not an image") is None
        assert media.sniff(b"") is None


class TestScrubJpeg:
    def test_gps_exif_and_comment_segments_are_gone(self):
        out = media.scrub_jpeg(jpeg_with_gps())
        assert out is not None
        assert b"GPS" not in out
        assert b"Exif" not in out
        assert b"camera-comment" not in out
        assert out[:2] == b"\xff\xd8"
        assert out[-2:] == b"\xff\xd9"

    def test_jfif_and_image_segments_survive(self):
        out = media.scrub_jpeg(jpeg_with_gps())
        assert out is not None
        assert b"JFIF" in out

    def test_clean_jpeg_passes_through(self):
        clean = _jpeg(_segment(0xE0, b"JFIF\x00" + b"\x00" * 9))
        assert media.scrub_jpeg(clean) == clean

    def test_non_jpeg_passes_through_this_scrubber(self):
        assert media.scrub_jpeg(b"not a jpeg") == b"not a jpeg"

    def test_truncated_segment_fails_closed(self):
        bad = b"\xff\xd8\xff\xe1\x00\x10ABC"
        assert media.scrub_jpeg(bad) is None

    def test_missing_eoi_fails_closed(self):
        bad = b"\xff\xd8" + _segment(0xE0, b"JFIF\x00" + b"\x00" * 9)
        assert media.scrub_jpeg(bad) is None

    def test_stray_byte_outside_scan_fails_closed(self):
        bad = b"\xff\xd8\x00\xff\xd9"
        assert media.scrub_jpeg(bad) is None


class TestScrubPng:
    def _metadata_png(self) -> bytes:
        return _png(
            _ihdr(),
            _chunk("eXIf", b"MM\x00GPS"),
            _chunk("tEXt", b"Comment\x00Taken at home"),
            _chunk("zTXt", b"Location\x00compressed-gps"),
            _chunk("iTXt", b"XML:com.adobe.xmp\x00\x00gps"),
            _idat(),
            _iend(),
        )

    def test_exif_and_text_chunks_are_gone(self):
        out = media.scrub_png(self._metadata_png())
        assert out == _png(_ihdr(), _idat(), _iend())

    def test_clean_png_is_unchanged(self):
        clean = _png(_ihdr(), _idat(), _iend())
        assert media.scrub_png(clean) == clean

    def test_non_png_passes_through_this_scrubber(self):
        assert media.scrub_png(b"nope") == b"nope"

    def test_truncated_chunk_fails_closed(self):
        bad = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 1024) + b"IDAT\x01\x02\x03"
        assert media.scrub_png(bad) is None


class TestScrubEntry:
    def test_jpeg_with_gps_leaves_no_gps(self):
        out = media.scrub(jpeg_with_gps())
        assert out is not None and b"GPS" not in out

    def test_png_metadata_is_stripped(self):
        dirty = _png(_ihdr(), _chunk("tEXt", b"k\x00v"), _idat(), _iend())
        assert media.scrub(dirty) == _png(_ihdr(), _idat(), _iend())

    @pytest.mark.parametrize("blob", [
        b"\x00\x00\x00\x18ftypheic" + b"\x00" * 8,
        b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 4,
        b"GIF89a" + b"\x00" * 8,
        b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8,
        b"just some text",
        b"",
    ])
    def test_unscrubbable_formats_are_refused(self, blob):
        assert media.scrub(blob) is None


class TestBudgetAndFitMath:
    def test_default_budget_is_carrier_cap_minus_allowance(self):
        assert media.max_image_bytes(0) == 300_000 - 24_000
        assert media.max_image_bytes(1000) == 300_000 - 24_000

    def test_large_cap_minus_allowance(self):
        assert media.max_image_bytes(600_000) == 600_000 - 24_000

    def test_floor_holds(self):
        assert media.max_image_bytes(90_000) == 80_000

    def test_fit_dimensions_caps_long_side(self):
        assert media.fit_dimensions(4000, 2000) == (1600, 800)
        assert media.fit_dimensions(1000, 2000) == (800, 1600)
        assert media.fit_dimensions(800, 600) == (800, 600)
        assert media.fit_dimensions(0, 600) == (0, 0)

    def test_shrink_steps_descend_to_floor(self):
        steps = media.shrink_steps(1600, 1200)
        assert steps and steps[0] == (1120, 840)
        assert all(max(w, h) >= media.MIN_SIDE_PX for w, h in steps)

    def test_needs_fit(self):
        assert media.needs_fit(300_000, 276_000) is True
        assert media.needs_fit(100, 276_000) is False

    def test_fit_jpeg_passes_through_under_budget(self):
        data = b"\xff\xd8" + b"\x00" * 50 + b"\xff\xd9"
        assert media.fit_jpeg(data, 1000, dimensions=(800, 600)) is data

    def test_fit_jpeg_steps_quality_then_size(self):
        calls = []

        def _encode(width, height, quality):
            calls.append((width, height, quality))
            return b"J" * 500

        out = media.fit_jpeg(b"J" * 5000, 400, dimensions=(1600, 1200), reencode=_encode)
        assert out == b"J" * 500
        assert calls[0] == (1600, 1200, 75)
        qualities = [q for _, _, q in calls]
        assert qualities == sorted(qualities, reverse=True)

    def test_fit_jpeg_returns_none_when_undecodable(self):
        assert media.fit_jpeg(b"J" * 5000, 10, dimensions=(0, 0)) is None
        assert media.fit_jpeg(b"", 10, dimensions=(100, 100)) is None


class TestPrepareForSend:
    def test_small_jpeg_sends_scrubbed(self):
        out = media.prepare_for_send(jpeg_with_gps(), 300_000)
        assert out is not None
        assert b"GPS" not in out
        assert out[:2] == b"\xff\xd8"

    def test_small_png_sends_scrubbed(self):
        dirty = _png(_ihdr(), _chunk("tEXt", b"k\x00v"), _idat(), _iend())
        out = media.prepare_for_send(dirty, 300_000)
        assert out == _png(_ihdr(), _idat(), _iend())

    @pytest.mark.parametrize("blob", [
        b"\x00\x00\x00\x18ftypheic" + b"\x00" * 8,
        b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 4,
        b"GIF89a" + b"\x00" * 8,
        b"not an image at all",
        b"",
    ])
    def test_refused_formats_never_send(self, blob):
        assert media.prepare_for_send(blob, 300_000) is None

    def test_malformed_jpeg_never_sends_unscrubbed(self):
        bad = b"\xff\xd8\xff\xe1\x00\x10ABC"
        assert media.prepare_for_send(bad, 300_000) is None

    def test_real_jpeg_fit_through_pixbuf(self):
        try:
            import gi

            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf
        except Exception:
            pytest.skip("GdkPixbuf is unavailable")
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 64, 48)
        try:
            pixbuf.fill(0xFF0000FF)
        except Exception:
            pytest.skip("pixbuf fill unavailable")
        ok, raw = pixbuf.save_to_bufferv("jpeg", ["quality"], ["90"])
        if not ok or not raw:
            pytest.skip("jpeg encoder unavailable")
        out = media.prepare_for_send(bytes(raw), 300_000)
        assert out is not None and bytes(out)[:2] == b"\xff\xd8"
        tiny = media.prepare_for_send(bytes(raw), 80_000 if len(raw) > 80_000 else len(raw) - 1)
        assert tiny is None or len(bytes(tiny)) > 0

    def test_smil_shapes(self):
        smil = media.build_smil("image_0.jpg", "text_0.txt")
        assert 'src="image_0.jpg"' in smil and 'src="text_0.txt"' in smil
        assert "<smil>" in smil
