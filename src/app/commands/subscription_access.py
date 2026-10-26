"""Commands for the authenticated user's subscription access."""

from dataclasses import dataclass

from src.app.events.base import Command


@dataclass
class RefreshMyEntitlementsCommand(Command):
    """Re-read the user's provider customer into their access rows."""

    user_id: str
