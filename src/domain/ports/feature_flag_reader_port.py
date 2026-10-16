"""Port for server-side reads of the DB-backed ``feature_flags`` table."""

from abc import ABC, abstractmethod


class FeatureFlagReaderPort(ABC):
    """Read boolean kill switches such as ``ent_ledger_write``.

    Implementations cache briefly and never raise: on a read error they log and
    return ``default``, so a flag outage falls back to legacy behaviour.
    """

    @abstractmethod
    async def is_enabled(self, name: str, default: bool = False) -> bool:
        """Return the flag value, or ``default`` when the row is missing or unreadable."""
