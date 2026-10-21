"""Rate limiting middleware using slowapi."""

from __future__ import annotations

import base64
import ipaddress
import json

from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def _public_ip(value: str) -> str | None:
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    return str(ip) if ip.is_global else None


def get_client_ip(request) -> str:
    """Real client IP behind Render's proxy.

    The socket peer is Render's internal proxy (10.x), shared by every caller.
    Render appends the address it saw to X-Forwarded-For but keeps whatever the
    client sent, so the leftmost entries can be faked. Reading from the right,
    the first public address is the one Render's edge recorded; a client can
    only add entries to its left. Without a public entry, the peer address is used.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    for entry in reversed(forwarded.split(",")):
        ip = _public_ip(entry)
        if ip:
            return ip
    return get_remote_address(request)


def get_user_id_or_ip(request):
    """Extract user ID from JWT payload for per-user limits, fallback to IP.

    Uses lightweight base64 decode of JWT payload (no verification).
    Auth is already enforced by verify_firebase_token dependency —
    this only needs a stable key for rate limiting.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            # Decode JWT payload without verification (just for rate limit key)
            payload_b64 = auth[7:].split(".")[1]
            # Add padding for base64
            padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded))
            uid = payload.get("sub") or payload.get("uid")
            if uid:
                return uid
        except Exception:
            pass
    return get_client_ip(request)


def get_ip_only_rate_limit_key(request):
    """Ignore Authorization. Unauthenticated routes must not key on JWT `sub`."""
    return get_client_ip(request)


limiter = Limiter(key_func=get_user_id_or_ip)


async def rate_limit_exceeded_handler(request, exc: RateLimitExceeded):
    """Stable public 429 envelope for clients that need deterministic backoff."""
    response = JSONResponse(
        status_code=429,
        content={"error": "rate_limit_exceeded", "code": "preview_rate_limited"},
    )
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    else:
        response.headers["Retry-After"] = "60"
    return response
