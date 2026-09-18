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
        if (
            img.format == "JPEG"
            and max(w, h) <= max_dim
            and len(image_bytes) < _MAX_BYTES
        ):
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


def to_compressed_image_url(
    image_url: str,
    max_dim: int = _MAX_DIM,
    quality: str = "auto",
) -> str:
    """Transform an image URL (Cloudflare Images or Cloudinary) to deliver an edge-resized & compressed image.

    For Cloudflare Images (imagedelivery.net/<hash>/<id>/<variant>):
        Replaces the variant with edge resize transform `w={max_dim},fit=scale-down,f=auto`.
    For Cloudinary (res.cloudinary.com/.../image/upload/...):
        Injects `/w_{max_dim},c_limit,q_{quality},f_jpg/` into the upload path.
    If the URL is already transformed or unsupported, returns image_url unchanged.
    """
    if not image_url:
        return image_url

    # Cloudflare Images delivery URL
    if "imagedelivery.net" in image_url:
        parts = image_url.split("/")
        # Format: https://imagedelivery.net/<account_hash>/<image_id>/<variant>
        if len(parts) >= 5:
            current_variant = parts[-1]
            if current_variant.startswith("w=") or current_variant.startswith("width="):
                return image_url
            transform = (
                f"w={max_dim},fit=scale-down,q={quality},f=auto"
                if quality != "auto"
                else f"w={max_dim},fit=scale-down,f=auto"
            )
            return "/".join(parts[:-1]) + f"/{transform}"
        return image_url

    # Cloudinary delivery URL
    if "res.cloudinary.com" in image_url and "/image/upload/" in image_url:
        prefix, suffix = image_url.split("/image/upload/", 1)
        # Avoid double transformations
        if re.match(r"^[a-z]_[^/]+/", suffix):
            return image_url

        transform = f"w_{max_dim},c_limit,q_{quality},f_jpg"
        return f"{prefix}/image/upload/{transform}/{suffix}"

    return image_url


# Backward-compatibility alias
to_compressed_cloudinary_url = to_compressed_image_url
