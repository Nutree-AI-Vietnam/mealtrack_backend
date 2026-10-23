"""Port for the person's account on the wearable data platform (Open Wearables)."""

from __future__ import annotations

from typing import Protocol


class WearableAccountPort(Protocol):
    """Manage the wearable-platform user linked to a Nutree account."""

    async def delete_user(self, user_id: str) -> bool:
        """Delete the user with their synced health data and provider tokens.

        Returns False when the user was already gone. Raises when the platform
        could not confirm the deletion.
        """
