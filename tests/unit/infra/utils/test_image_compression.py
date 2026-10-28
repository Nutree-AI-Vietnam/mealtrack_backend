"""Unit tests for the image_compression utility."""

from io import BytesIO

import pytest
from PIL import Image, ImageChops, ImageOps, ImageStat

from src.domain.utils.image_compression import (
    _MAX_BYTES,
    _MAX_DIM,
    compress_image,
    compress_vision_image,
)


def _make_jpeg(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _make_png(width: int, height: int) -> bytes:
    img = Image.new("RGBA", (width, height), color=(100, 150, 200, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _image_size(data: bytes) -> tuple[int, int]:
    return Image.open(BytesIO(data)).size


class TestCompressImage:
    def test_large_image_is_resized(self):
        raw = _make_jpeg(2000, 1500)
        result = compress_image(raw)
        w, h = _image_size(result)
        assert max(w, h) <= _MAX_DIM

    def test_small_jpeg_within_limits_returned_unchanged(self):
        raw = _make_jpeg(400, 300)
        # Ensure the raw bytes are genuinely small
        assert len(raw) < _MAX_BYTES
        result = compress_image(raw)
        assert result is raw  # same object — no recompression

    def test_png_converted_to_jpeg(self):
        raw = _make_png(200, 200)
        result = compress_image(raw)
        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"

    def test_corrupt_bytes_falls_back_to_original(self):
        corrupt = b"not an image at all"
        result = compress_image(corrupt)
        assert result is corrupt  # unchanged fallback


@pytest.mark.parametrize("orientation", range(2, 9))
@pytest.mark.parametrize("size", [(120, 80), (2400, 1600)])
def test_scan_normalizes_exif_pixels_before_resizing(orientation, size):
    image = Image.new("RGB", size, "white")
    image.paste("red", (0, 0, size[0] // 2, size[1] // 2))
    image.paste("blue", (size[0] // 2, 0, size[0], size[1] // 2))
    exif = image.getexif()
    exif[274] = orientation
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=95, exif=exif)
    raw = buf.getvalue()
    expected = ImageOps.exif_transpose(Image.open(BytesIO(raw)))
    expected.thumbnail((2048, 2048), Image.Resampling.LANCZOS)

    encoded = compress_vision_image(raw)
    actual = Image.open(BytesIO(encoded))

    assert actual.size == expected.size
    assert actual.getexif().get(274, 1) == 1
    difference = ImageStat.Stat(ImageChops.difference(actual, expected))
    assert max(difference.mean) < 3
    # Sending the same scan through acquisition and vision must not re-encode it.
    assert compress_vision_image(encoded) is encoded


def test_scan_preserves_detail_that_thumbnail_compression_discards():
    raw = _make_jpeg(4000, 3000)
    assert _image_size(compress_vision_image(raw)) == (2048, 1536)
    assert _image_size(compress_image(raw)) == (768, 576)


def test_scan_does_not_upscale_or_reencode_an_already_suitable_jpeg():
    raw = _make_jpeg(1600, 1200)
    assert compress_vision_image(raw) is raw


def test_extremely_narrow_image_keeps_nonzero_dimension():
    raw = _make_png(1, 4000)
    assert _image_size(compress_vision_image(raw)) == (1, 2048)
