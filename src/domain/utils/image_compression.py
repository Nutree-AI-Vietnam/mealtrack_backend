"""Shared image compression — resize to max dimension, encode as JPEG."""

import logging
import re
from io import BytesIO
from urllib.parse import urlparse, urlunparse

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
    custom_domain: str | None = None,
) -> str:
    """Transform an image URL (Cloudflare Images or Cloudinary) to deliver an edge-resized & compressed image.

    For Cloudflare Images:
        - Default: https://imagedelivery.net/<account_hash>/<image_id>/<variant>
        - Custom domain: https://<custom_domain>/<image_id>/<variant>
        Replaces the variant with edge resize transform `w={max_dim},fit=scale-down,f=auto`.
        Preserves image ID and rejects malformed URLs missing a variant.
    For Cloudinary (res.cloudinary.com/.../image/upload/...):
        Injects `/w_{max_dim},c_limit,q_{quality},f_jpg/` into the upload path.
    If the URL is already transformed or unsupported, returns image_url unchanged.
    """
    if not image_url:
        return image_url

    try:
        parsed = urlparse(image_url)
    except Exception:
        return image_url

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return image_url

    # Cloudflare Images delivery URL (default or custom domain)
    is_cf_default = hostname == "imagedelivery.net" or hostname.endswith(
        ".imagedelivery.net"
    )
    clean_custom_domain = (
        custom_domain.strip()
        .lower()
        .replace("https://", "")
        .replace("http://", "")
        .split("/")[0]
        if custom_domain
        else ""
    )
    is_cf_custom = bool(clean_custom_domain and hostname == clean_custom_domain)

    if is_cf_default or is_cf_custom:
        path_segments = [s for s in parsed.path.strip("/").split("/") if s]
        # Format:
        # Default: /<account_hash>/<image_id>/<variant> -> requires at least 3 segments
        # Custom domain: /<image_id>/<variant> -> requires at least 2 segments
        has_variant = (is_cf_default and len(path_segments) >= 3) or (
            is_cf_custom and len(path_segments) >= 2
        )
        if not has_variant:
            return image_url

        current_variant = path_segments[-1]
        if current_variant.startswith("w=") or current_variant.startswith("width="):
            return image_url

        transform = (
            f"w={max_dim},fit=scale-down,q={quality},f=auto"
            if quality != "auto"
            else f"w={max_dim},fit=scale-down,f=auto"
        )
        new_path = "/" + "/".join(path_segments[:-1] + [transform])
        return urlunparse(parsed._replace(path=new_path))

    # Cloudinary delivery URL
    if hostname == "res.cloudinary.com" and "/image/upload/" in parsed.path:
        prefix, suffix = image_url.split("/image/upload/", 1)
        # Avoid double transformations
        if re.match(r"^[a-z]_[^/]+/", suffix):
            return image_url

        transform = f"w_{max_dim},c_limit,q_{quality},f_jpg"
        return f"{prefix}/image/upload/{transform}/{suffix}"

    return image_url


# Backward-compatibility alias
to_compressed_cloudinary_url = to_compressed_image_url
