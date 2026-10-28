"""Commands for subscription access: the user's own refresh, support's resync."""

from dataclasses import dataclass

from src.app.events.base import Command


@dataclass
class RefreshMyEntitlementsCommand(Command):
    """Re-read the user's provider customer into their access rows."""

    user_id: str


@dataclass
class ResyncSubscriptionAccessCommand(Command):
    """Support: re-read one user's RevenueCat customer into their rows.

    The same read and sync as the user's own refresh, for a target user an
    admin names; answered with the access decision before and after.
    """

    user_id: str
