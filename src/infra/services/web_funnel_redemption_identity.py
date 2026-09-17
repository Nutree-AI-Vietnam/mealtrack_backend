"""Mint Firebase custom tokens for web-funnel silent login. Do not reuse resolve()."""

from __future__ import annotations

import asyncio

from firebase_admin import auth  # type: ignore[import-untyped]

SILENT_LOGIN_CLAIM = "wf_silent_login"


class RedemptionIdentityUnavailable(Exception):
    """Map to generic 404. Do not distinguish miss vs conflict."""


class WebFunnelRedemptionIdentityService:
    async def mint_for_email(self, email: str, *, mealtrack_uid: str | None) -> str:
        record = await self._user_for(email.strip().lower(), mealtrack_uid)
        if (
            record.disabled
            or (record.uid or "").startswith("wf_")
            or (record.email or "").lower() != email.strip().lower()
        ):
            raise RedemptionIdentityUnavailable()
        await asyncio.to_thread(
            auth.update_user,
            record.uid,
            email=email.strip().lower(),
            email_verified=True,
        )
        token = await asyncio.to_thread(
            auth.create_custom_token,
            record.uid,
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

    async def _user_for(self, email: str, mealtrack_uid: str | None):
        if mealtrack_uid:
            if mealtrack_uid.startswith("wf_"):
                raise RedemptionIdentityUnavailable()
            try:
                return await asyncio.to_thread(auth.get_user, mealtrack_uid)
            except auth.UserNotFoundError:
                try:
                    return await asyncio.to_thread(
                        auth.create_user,
                        uid=mealtrack_uid,
                        email=email,
                        email_verified=True,
                    )
                except auth.UidAlreadyExistsError:
                    return await asyncio.to_thread(auth.get_user, mealtrack_uid)
                except auth.EmailAlreadyExistsError:
                    record = await self._get_by_email(email)
                    if record.uid != mealtrack_uid:
                        raise RedemptionIdentityUnavailable() from None
                    return record
        try:
            return await self._get_by_email(email)
        except auth.UserNotFoundError:
            try:
                return await asyncio.to_thread(
                    auth.create_user, email=email, email_verified=True
                )
            except auth.EmailAlreadyExistsError:
                return await self._get_by_email(email)

    async def _get_by_email(self, email: str):
        return await asyncio.to_thread(auth.get_user_by_email, email)
