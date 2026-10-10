"""Locally signed meal-photo upload tickets.

The phone posts the file to our API. Nothing in this module calls the
Cloudflare Images API.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

_MAX_TTL_SECONDS = 3600


def issue_upload_ticket(
    *,
    image_id: str,
    ttl: int,
    api_public_base_url: str,
    signing_secret: str,
) -> dict:
    expires_at = int(time.time()) + max(1, ttl)
    signature = upload_signature(signing_secret, image_id, expires_at)
    upload_url = (
        f"{api_public_base_url.rstrip('/')}/v1/meals/direct-upload/"
        f"{quote(image_id, safe='')}"
        f"?exp={expires_at}&sig={signature}"
    )
    return {
        "image_id": image_id,
        "upload_url": upload_url,
        "provider": "r2",
        "cloud_name": "",
        "api_key": "",
        "timestamp": int(time.time()),
        "signature": signature,
        "folder": "mealtrack",
        "public_id": f"mealtrack/{image_id}",
    }


def upload_signature(secret: str, image_id: str, expires_at: int) -> str:
    message = f"{image_id}.{expires_at}".encode()
    key = secret.strip().encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def ticket_is_valid(
    secret: str, image_id: str, expires_at: int, signature: str
) -> bool:
    now = int(time.time())
    if expires_at < now or expires_at > now + _MAX_TTL_SECONDS:
        return False
    expected = upload_signature(secret, image_id, expires_at)
    return hmac.compare_digest(expected, signature)
