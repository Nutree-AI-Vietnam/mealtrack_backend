"""Command to restore missed streak days, paying with held or just-bought restores."""

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class PackClaim:
    """A restore pack the app just bought, identified by its store transaction."""

    product_id: str
    transaction_id: str


@dataclass
class RestoreStreakCommand:
    user_id: str
    dates: list[date] = field(default_factory=list)
    purchase: PackClaim | None = None
    header_timezone: str | None = None
