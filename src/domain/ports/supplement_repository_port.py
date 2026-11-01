"""Persistence port for supplement products, lists and doses."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import date, datetime

from src.domain.model.supplement import (
    SupplementIntake,
    SupplementProduct,
    UserSupplement,
)


class SupplementRepositoryPort(ABC):
    """Writes run in the caller's transaction; nothing here commits.

    Every read that takes a product id, a barcode or a search term returns
    only products the user may see: shared and approved, or their own.
    """

    @abstractmethod
    async def schema_ready(self) -> bool:
        """Whether the supplement tables are migrated."""

    @abstractmethod
    async def get_product(
        self, product_id: str, user_id: str
    ) -> SupplementProduct | None: ...

    @abstractmethod
    async def find_by_barcode(
        self, barcode: str, user_id: str
    ) -> SupplementProduct | None:
        """The shared product first, else the user's own; any GTIN form matches."""

    @abstractmethod
    async def search_products(
        self, query: str, user_id: str, limit: int = 20
    ) -> list[SupplementProduct]:
        """Products whose brand or name hold every word.

        Approved shared rows come first (those sold in Vietnam ahead), then
        the user's own; at most 50.
        """

    @abstractmethod
    async def products_for(
        self, user_id: str, product_ids: Iterable[str]
    ) -> dict[str, SupplementProduct]:
        """Products by id the user may see, plus those behind their own doses
        and list rows, so history keeps its names if a shared row is withdrawn."""

    @abstractmethod
    async def add_product(self, product: SupplementProduct) -> tuple[str, ...]:
        """Insert the product; returns the barcodes stored with it.

        A barcode already on another shared product, or on another of the
        owner's products, is skipped rather than failing the write.
        """

    @abstractmethod
    async def list_supplements(self, user_id: str) -> list[UserSupplement]: ...

    @abstractmethod
    async def save_list_entry(self, entry: UserSupplement) -> UserSupplement:
        """Add the product to the list, or update the row already there.

        Raises ``SupplementProductNotFoundError`` when the user may not see it.
        """

    @abstractmethod
    async def update_list_entry(
        self,
        user_id: str,
        entry_id: str,
        *,
        units_per_dose: float | None = None,
        active: bool | None = None,
        sort_order: int | None = None,
    ) -> UserSupplement | None:
        """Change the given fields of the user's own row; None when it is not theirs."""

    @abstractmethod
    async def remove_list_entry(self, user_id: str, entry_id: str) -> bool: ...

    @abstractmethod
    async def add_intake(self, intake: SupplementIntake) -> bool:
        """Insert the dose; False when its id is already taken.

        Raises ``SupplementProductNotFoundError`` when the user may not see the product.
        """

    @abstractmethod
    async def intakes_between(
        self, user_id: str, start: date, end: date
    ) -> list[SupplementIntake]:
        """Doses not deleted whose local day falls in ``start``..``end``, inclusive."""

    @abstractmethod
    async def soft_delete_intake(
        self, user_id: str, intake_id: str, deleted_at: datetime
    ) -> SupplementIntake | None:
        """Mark the user's own dose deleted; returns it, or None when not theirs."""

    @abstractmethod
    async def log_days(self, user_id: str) -> list[tuple[date, bool]]:
        """Each local day with a dose, and whether one was logged on the day itself."""

    @abstractmethod
    async def delete_user_data(self, user_id: str) -> int:
        """Remove the user's doses, list and own products; returns rows removed.

        Products promoted to the catalog have no owner by then and stay.
        """
