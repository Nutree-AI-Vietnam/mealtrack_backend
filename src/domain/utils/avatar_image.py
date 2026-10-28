"""Re-encode an uploaded avatar photo so no metadata reaches public storage.

Avatar URLs are public, and phones can copy GPS tags into a picked photo, so
the server keeps only the pixels: upright, RGB, at most 1024 px, as a JPEG.
"""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

AVATAR_MAX_PIXELS = 40_000_000
AVATAR_MAX_SIDE_PX = 1024
AVATAR_JPEG_QUALITY = 85
AVATAR_IMAGE_CONTENT_TYPE = "image/jpeg"
# Only these decoders may run on an upload; Pillow's others stay unreachable.
_ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP")
_WHITE = (255, 255, 255)


class AvatarImageRejectedError(ValueError):
    """The upload cannot become an avatar."""

    error_code = "INVALID_IMAGE"


class UnsupportedAvatarImageError(AvatarImageRejectedError):
    """Not a JPEG, PNG or WebP image that can be read."""

    error_code = "UNSUPPORTED_IMAGE_TYPE"


class AvatarImageTooLargeError(AvatarImageRejectedError):
    """More pixels than an avatar may decode."""

    error_code = "IMAGE_TOO_LARGE"


def normalize_avatar_image(data: bytes) -> bytes:
    """Return the upload as a metadata-free JPEG, at most 1024 px on its long side.

    The format and pixel count are checked from the header before any pixel
    data is decoded. CPU-bound: call it through ``asyncio.to_thread``.
    """
    try:
        image = Image.open(BytesIO(data), formats=_ALLOWED_FORMATS)
    except Image.DecompressionBombError as exc:
        raise AvatarImageTooLargeError("Image has too many pixels") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise UnsupportedAvatarImageError("Image format is not supported") from exc

    with image:
        width, height = image.size
        if width * height > AVATAR_MAX_PIXELS:
            raise AvatarImageTooLargeError("Image has too many pixels")
        try:
            upright = ImageOps.exif_transpose(image)
            clean = _to_rgb(upright)
            clean.thumbnail(
                (AVATAR_MAX_SIDE_PX, AVATAR_MAX_SIDE_PX), Image.Resampling.LANCZOS
            )
            # The JPEG writer falls back to info for the comment; keep nothing.
            clean.info = {}
            output = BytesIO()
            clean.save(output, format="JPEG", quality=AVATAR_JPEG_QUALITY)
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
            raise UnsupportedAvatarImageError("Image could not be decoded") from exc
    return output.getvalue()


def _to_rgb(image: Image.Image) -> Image.Image:
    """Flatten transparency onto white so see-through areas do not turn black."""
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if not has_alpha:
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    flattened = Image.new("RGB", rgba.size, _WHITE)
    flattened.paste(rgba, mask=rgba.getchannel("A"))
    return flattened
