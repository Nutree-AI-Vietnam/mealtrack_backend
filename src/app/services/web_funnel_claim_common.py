"""Shared, token-safe helpers for the dark-launched web claim aggregate."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from hmac import compare_digest

from fastapi import HTTPException, status

CLAIM_TTL = timedelta(hours=24)
RESERVATION_TTL = timedelta(minutes=10)
EXCHANGE_TTL = timedelta(minutes=5)
RESEND_COOLDOWN = timedelta(minutes=2)


def utcnow() -> datetime:
    return datetime.now(UTC)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_secret() -> str:
    """Generate an opaque URL-safe secret; callers must never persist its value."""
    return secrets.token_urlsafe(48)


def token_matches(value: str, digest: str | None) -> bool:
    if digest is None:
        return False
    return compare_digest(hash_secret(value), digest)


def claim_not_found() -> HTTPException:
    """One generic response prevents claim, email, and identity enumeration."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def claim_conflict() -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Claim unavailable")


def is_active_standard(subscriber: dict | None) -> bool:
    """Accept only fetched RevenueCat standard entitlement state."""
    entitlements = (subscriber or {}).get("subscriber", {}).get("entitlements", {})
    standard = entitlements.get("standard")
    if not isinstance(standard, dict):
        return False
    expires = standard.get("expires_date")
    if expires is None:
        return True
    try:
        return datetime.fromisoformat(str(expires).replace("Z", "+00:00")) > utcnow()
    except ValueError:
        return False


def standard_expires_at(subscriber: dict | None) -> datetime | None:
    """Parse RevenueCat standard entitlement expiry; None means lifetime."""
    entitlements = (subscriber or {}).get("subscriber", {}).get("entitlements", {})
    standard = entitlements.get("standard")
    if not isinstance(standard, dict):
        return None
    expires = standard.get("expires_date")
    if expires is None:
        return None
    try:
        return datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    except ValueError:
        return None
