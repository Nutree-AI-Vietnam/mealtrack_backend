"""Shared image compression — resize to max dimension, encode as JPEG."""
import logging
import re
from io import BytesIO

from PIL import Image

logger = logging.getLogger(__name__)

_MAX_DIM = 768
_MAX_BYTES = 200 * 1024


def compress_image(image_bytes: bytes, max_dim: int = _MAX_DIM) -> bytes:
    """Resize to max_dim on longest axis, encode as JPEG quality=85.

    Returns original bytes unchanged if already a small JPEG within limits.
    Never raises — falls back to original on PIL errors.
    """
    try:
        img = Image.open(BytesIO(image_bytes))
        w, h = img.size
        if img.format == "JPEG" and max(w, h) <= max_dim and len(image_bytes) < _MAX_BYTES:
            return image_bytes
        resampling = getattr(Image, "Resampling", Image).LANCZOS  # type: ignore[attr-defined]
        resized_img: Image.Image = img
        if max(w, h) > max_dim:
            ratio = max_dim / max(w, h)
            resized_img = img.resize((int(w * ratio), int(h * ratio)), resampling)
        if resized_img.mode != "RGB":
            resized_img = resized_img.convert("RGB")
        buf = BytesIO()
        resized_img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("Image compression failed, using original: %s", exc)
        return image_bytes


def to_compressed_cloudinary_url(
    image_url: str,
    max_dim: int = _MAX_DIM,
    quality: str = "auto",
) -> str:
    """Transform a Cloudinary URL to deliver an edge-resized & compressed image.

    Injects /w_{max_dim},c_limit,q_{quality},f_jpg/ into the upload path.
    If the URL is not a Cloudinary upload URL or already transformed, returns image_url.
    """
    if not image_url or "res.cloudinary.com" not in image_url or "/image/upload/" not in image_url:
        return image_url

    prefix, suffix = image_url.split("/image/upload/", 1)
    # Avoid double transformations
    if re.match(r"^[a-z]_[^/]+/", suffix):
        return image_url

    transform = f"w_{max_dim},c_limit,q_{quality},f_jpg"
    return f"{prefix}/image/upload/{transform}/{suffix}"
