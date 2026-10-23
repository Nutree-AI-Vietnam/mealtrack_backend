"""Delete a deleted account's Open Wearables user, then drop the link.

Runs after the account deletion has committed. Until Open Wearables confirms
the deletion, `users.open_wearables_user_id` stays on the deleted row, so
`is_active = false AND open_wearables_user_id IS NOT NULL` lists the work still
owed. `scripts/retry_open_wearables_deletions.py` retries those rows.
"""

from __future__ import annotations

import logging
from uuid import UUID

from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.wearable_account_port import WearableAccountPort

logger = logging.getLogger(__name__)


async def delete_open_wearables_user(
    wearable_accounts: WearableAccountPort | None,
    uow: AsyncUnitOfWorkPort,
    user_id: UUID,
    ow_user_id: str,
) -> bool:
    """Return True once Open Wearables confirmed it and the link is cleared.

    Never raises: the account is already deleted, and a failure only leaves
    the link in place for the retry script.
    """
    if wearable_accounts is None:
        logger.warning(
            "Open Wearables is not configured; kept the link for cleanup user_id=%s",
            user_id,
        )
        return False

    try:
        deleted = await wearable_accounts.delete_user(ow_user_id)
    except Exception:
        logger.error(
            "Open Wearables did not confirm the user deletion; kept the link "
            "for retry user_id=%s",
            user_id,
            exc_info=True,
        )
        return False

    if not deleted:
        logger.info("Open Wearables user was already deleted user_id=%s", user_id)

    try:
        async with uow:
            await uow.users.clear_open_wearables_user_id(user_id)
    except Exception:
        logger.error(
            "Open Wearables user is deleted but the link was not cleared user_id=%s",
            user_id,
            exc_info=True,
        )
        return False
    return True
