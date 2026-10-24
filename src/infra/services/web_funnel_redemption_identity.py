"""Mint Firebase custom tokens for web-funnel silent login. Do not reuse resolve().

A redemption link proves the purchase, not ownership of the checkout email, so
a silent login may only open the account this redemption created itself.
"""

from __future__ import annotations

import asyncio
import hashlib

from firebase_admin import auth  # type: ignore[import-untyped]

SILENT_LOGIN_CLAIM = "wf_silent_login"
SILENT_LOGIN_UID_PREFIX = "wfr_"


class RedemptionIdentityUnavailable(Exception):
    """Map to generic 404. Do not distinguish miss vs conflict."""


def silent_login_uid(lead_id: str) -> str:
    """Firebase UID reserved for the account a lead's silent login may create.

    Only the Admin SDK can create a record with a chosen UID, so a record under
    this UID was created by this lead's redemption, never by a pre-existing owner.
    """
    digest = hashlib.sha256(f"redemption-session:{lead_id}".encode()).hexdigest()
    return f"{SILENT_LOGIN_UID_PREFIX}{digest[:40]}"


class WebFunnelRedemptionIdentityService:
    async def mint_for_new_buyer(self, email: str, *, uid: str) -> str:
        """Mint for the redemption's own record; never adopt an existing account."""
        email = email.strip().lower()
        record = await self._own_record(uid, email)
        if (
            record.uid != uid
            or record.disabled
            or (record.email or "").lower() != email
            # Created records have no provider. Any linked one (Google, Apple,
            # email link as "password") means the mailbox owner signed in here.
            or record.provider_data
        ):
            raise RedemptionIdentityUnavailable()
        token = await asyncio.to_thread(
            auth.create_custom_token,
            uid,
            {SILENT_LOGIN_CLAIM: 1},
        )
        return token.decode("utf-8") if isinstance(token, bytes) else token

    async def verified_email_for_uid(self, uid: str) -> str | None:
        try:
            record = await asyncio.to_thread(auth.get_user, uid)
        except auth.UserNotFoundError:
            return None
        if not record.email_verified or record.disabled or not record.email:
            return None
        return record.email.lower()

    async def _own_record(self, uid: str, email: str):
        try:
            return await asyncio.to_thread(auth.get_user, uid)
        except auth.UserNotFoundError:
            pass
        try:
            # email_verified only on a record created here; existing records are
            # never updated, so an unverified email cannot be promoted.
            return await asyncio.to_thread(
                auth.create_user, uid=uid, email=email, email_verified=True
            )
        except auth.EmailAlreadyExistsError:
            raise RedemptionIdentityUnavailable() from None
        except auth.UidAlreadyExistsError:
            return await asyncio.to_thread(auth.get_user, uid)
