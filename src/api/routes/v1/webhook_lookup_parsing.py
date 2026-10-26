"""RevenueCat webhook user lookup and payload parsing helpers."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from src.infra.database.models.user.user import User

logger = logging.getLogger(__name__)


def candidate_revenuecat_ids(event: dict) -> list[str]:
    """Return RevenueCat IDs worth trying for user/subscription lookup."""
    candidates = [
        event.get("app_user_id"),
        event.get("original_app_user_id"),
        *(event.get("aliases") or []),
        *(event.get("redeemed_by") or []),
        *(event.get("transferred_to") or []),
        *(event.get("transferred_from") or []),
    ]
    seen: set[str] = set()
    return [
        candidate
        for candidate in candidates
        if isinstance(candidate, str)
        and candidate
        and not (candidate in seen or seen.add(candidate))
    ]


def is_anonymous_event(event: dict) -> bool:
    """Whether all available RevenueCat identities are anonymous placeholders."""
    candidates = candidate_revenuecat_ids(event)
    return bool(candidates) and all(
        candidate.startswith("$RCAnonymousID:") for candidate in candidates
    )


async def find_user_for_revenuecat_event(uow, event: dict) -> User | None:
    """Find a user from any RevenueCat identifier present in a webhook payload."""
    for candidate in candidate_revenuecat_ids(event):
        result = await uow.session.execute(
            select(User).where(User.firebase_uid == candidate)
        )
        user = result.scalars().first()
        if user:
            return user

        # User.id stores UUID values as strings; skip anonymous/non-UUID
        # RevenueCat IDs here so only valid internal IDs hit this fallback.
        try:
            candidate_uuid = uuid.UUID(candidate)
        except (ValueError, AttributeError, TypeError):
            candidate_uuid = None
        if candidate_uuid is not None:
            result = await uow.session.execute(
                select(User).where(User.id == str(candidate_uuid))
            )
            user = result.scalars().first()
            if user:
                return user

        subscription = await uow.subscriptions.find_by_revenuecat_id(candidate)
        if subscription:
            result = await uow.session.execute(
                select(User).where(User.id == subscription.user_id)
            )
            user = result.scalars().first()
            if user:
                logger.info(
                    "RevenueCat webhook: found user via subscription record — "
                    "revenuecat_id=%s, user_id=%s",
                    candidate,
                    user.id,
                )
                return user
    return None


@dataclass(frozen=True)
class TransferParty:
    """A local user named on either side of a RevenueCat TRANSFER."""

    user: User
    # Named only in transferred_from: RevenueCat moved purchases away from it.
    is_source_only: bool

    @property
    def user_id(self) -> str:
        return str(self.user.id)


async def find_transfer_parties(uow, event: dict) -> list[TransferParty]:
    """Resolve every local user behind a TRANSFER's source and target ids."""
    found: dict[str, tuple[User, set[str]]] = {}
    for side in ("transferred_from", "transferred_to"):
        for identity in event.get(side) or []:
            if not isinstance(identity, str) or not identity:
                continue
            user = await find_user_for_revenuecat_event(uow, {"app_user_id": identity})
            if user is None:
                continue
            found.setdefault(str(user.id), (user, set()))[1].add(side)
    return [
        TransferParty(user, sides == {"transferred_from"})
        for user, sides in found.values()
    ]


async def get_subscription_by_revenuecat_id(
    uow, revenuecat_id: str, user_id: str | None = None
):
    """Get the user's RevenueCat-provided row for a RevenueCat subscriber ID."""
    return await uow.subscriptions.find_by_revenuecat_id(revenuecat_id, user_id=user_id)


def parse_platform(store: str) -> str:
    """Parse store name to platform."""
    if not store:
        return "ios"

    store_upper = store.upper()
    store_map = {
        "APP_STORE": "ios",
        "PLAY_STORE": "android",
        "PADDLE": "web",
        "STRIPE": "web",
        "MAC_APP_STORE": "ios",
    }
    return store_map.get(store_upper, "ios")


def parse_timestamp(ms: int | None) -> datetime | None:
    """Parse millisecond timestamp to datetime."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC)
    except Exception as e:
        logger.error(f"Error parsing timestamp {ms}: {e}")
        return None
