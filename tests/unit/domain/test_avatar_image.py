"""normalize_avatar_image keeps only upright, resized, metadata-free pixels."""

import struct
import zlib
from io import BytesIO

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngImageFile

from src.domain.utils.avatar_image import (
    AvatarImageTooLargeError,
    UnsupportedAvatarImageError,
    normalize_avatar_image,
)

GPS_IFD = 0x8825
ORIENTATION = 0x0112
COMMENT = b"home address: 12 Hidden Lane"


def _encode(image: Image.Image, image_format: str, **params) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format=image_format, **params)
    return buffer.getvalue()


def _png_header_only(width: int, height: int) -> bytes:
    """A PNG whose header claims a size; it carries no pixel data at all."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def _decoded(data: bytes) -> Image.Image:
    image = Image.open(BytesIO(data))
    image.load()
    return image


@pytest.mark.unit
class TestNormalizeAvatarImage:
    def test_gps_exif_and_comment_are_removed(self):
        exif = Image.Exif()
        gps = exif.get_ifd(GPS_IFD)
        gps[1] = "N"
        gps[2] = (10.0, 46.0, 30.0)
        gps[3] = "E"
        gps[4] = (106.0, 41.0, 2.0)
        source = _encode(
            Image.new("RGB", (64, 48), (200, 40, 40)),
            "JPEG",
            exif=exif.tobytes(),
            comment=COMMENT,
        )
        assert _decoded(source).getexif().get_ifd(GPS_IFD)
        assert COMMENT in source

        result = normalize_avatar_image(source)

        output = _decoded(result)
        assert output.format == "JPEG"
        assert len(output.getexif()) == 0
        assert "comment" not in output.info
        assert "exif" not in output.info
        assert COMMENT not in result
        assert b"Exif\x00\x00" not in result

    def test_rotated_photo_comes_out_upright(self):
        # Stored landscape with "rotate 90 clockwise" (orientation 6) in EXIF.
        exif = Image.Exif()
        exif[ORIENTATION] = 6
        source = _encode(
            Image.new("RGB", (80, 40), "blue"), "JPEG", exif=exif.tobytes()
        )

        output = _decoded(normalize_avatar_image(source))

        assert output.size == (40, 80)
        assert len(output.getexif()) == 0

    def test_png_with_alpha_becomes_an_rgb_jpeg_on_white(self):
        image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        image.paste((0, 128, 0, 255), (8, 8, 24, 24))

        output = _decoded(normalize_avatar_image(_encode(image, "PNG")))

        assert output.format == "JPEG"
        assert output.mode == "RGB"
        red, green, blue = output.getpixel((1, 1))
        assert min(red, green, blue) > 240
        assert output.getpixel((16, 16))[1] > 100

    def test_webp_is_accepted(self):
        source = _encode(Image.new("RGB", (20, 10), "orange"), "WEBP")

        assert _decoded(normalize_avatar_image(source)).format == "JPEG"

    def test_long_side_is_scaled_to_1024(self):
        source = _encode(Image.new("RGB", (3000, 1500), "gray"), "JPEG")

        output = _decoded(normalize_avatar_image(source))

        assert output.size == (1024, 512)

    def test_tiff_bytes_are_rejected(self):
        tiff = _encode(Image.new("RGB", (16, 16), "white"), "TIFF")

        with pytest.raises(UnsupportedAvatarImageError):
            normalize_avatar_image(tiff)

    def test_icns_bytes_are_rejected(self):
        icns = _encode(Image.new("RGBA", (16, 16), "white"), "ICNS")
        assert icns.startswith(b"icns")

        with pytest.raises(UnsupportedAvatarImageError):
            normalize_avatar_image(icns)

    def test_bytes_that_are_not_an_image_are_rejected(self):
        with pytest.raises(UnsupportedAvatarImageError):
            normalize_avatar_image(b"not an image at all")

    def test_truncated_jpeg_is_rejected(self):
        source = _encode(Image.new("RGB", (200, 200), "red"), "JPEG")

        with pytest.raises(UnsupportedAvatarImageError):
            normalize_avatar_image(source[: len(source) // 3])

    def test_50_megapixel_image_is_rejected_before_decode(self, monkeypatch):
        def no_decode(self):
            raise AssertionError("pixel data must not be decoded")

        monkeypatch.setattr(PngImageFile, "load", no_decode)

        with pytest.raises(AvatarImageTooLargeError):
            normalize_avatar_image(_png_header_only(10_000, 5_000))

    def test_image_past_pillows_own_bomb_limit_is_too_large(self):
        with pytest.raises(AvatarImageTooLargeError):
            normalize_avatar_image(_png_header_only(20_000, 10_000))
