"""Rate limiting middleware using slowapi."""

from __future__ import annotations

import base64
import ipaddress
import json

from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# Published Cloudflare proxy ranges.
# Source: https://www.cloudflare.com/ips-v4 and https://www.cloudflare.com/ips-v6
# Vendored 2026-10-21. Do not fetch these at runtime.
_CLOUDFLARE_IP_RANGES: tuple[str, ...] = (
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
)

_CLOUDFLARE_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in _CLOUDFLARE_IP_RANGES
)


def _public_ip(value: str) -> str | None:
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    return str(ip) if ip.is_global else None


def _is_cloudflare_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return any(ip in network for network in _CLOUDFLARE_NETWORKS)


def _parsed_ip(value: str) -> str | None:
    """Canonical IP text, or None when the value is not an IP."""
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _rightmost_public_or_peer(request) -> str:
    """#761 selection: right-most public X-Forwarded-For hop, else the socket peer.

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


def get_client_ip(request) -> str:
    """Client IP for rate-limit keys and request logs.

    Starts from the #761 hop. When that hop is inside Cloudflare's published
    ranges, the visitor IP is CF-Connecting-IP. Trust that header only in that
    case, and only when it parses as an IP. Otherwise keep the #761 hop, which
    leaves direct Render traffic unchanged.
    """
    hop = _rightmost_public_or_peer(request)
    if _is_cloudflare_ip(hop):
        connecting = _parsed_ip(request.headers.get("CF-Connecting-IP") or "")
        if connecting is not None:
            return connecting
    return hop


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
