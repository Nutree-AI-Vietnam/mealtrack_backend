"""Read and store a Nutree user's Open Wearables id.

Each call runs in its own unit of work, so no database connection is held
while the route waits on Open Wearables.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort


class OpenWearablesAccountLinks:
    def __init__(
        self,
        uow_factory: Callable[[], AsyncUnitOfWorkPort],
        read_only_uow_factory: Callable[[], AsyncUnitOfWorkPort],
    ) -> None:
        self._uow_factory = uow_factory
        self._read_only_uow_factory = read_only_uow_factory

    async def linked_user_id(self, user_id: str) -> str | None:
        async with self._read_only_uow_factory() as uow:
            return await uow.users.find_open_wearables_user_id(UUID(user_id))

    async def link(self, user_id: str, open_wearables_user_id: str) -> str:
        """Store the id and return the stored one; a concurrent link keeps the first."""
        async with self._uow_factory() as uow:
            return await uow.users.link_open_wearables_user_id(
                UUID(user_id), open_wearables_user_id
            )
