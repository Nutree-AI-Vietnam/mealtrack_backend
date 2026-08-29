"""Command to update user language preference."""

from dataclasses import dataclass
from uuid import UUID

from src.app.events.base import Command
from src.domain.constants.languages import ENABLED_APP_LOCALES

SUPPORTED_LANGUAGES = ENABLED_APP_LOCALES


@dataclass
class UpdateLanguageCommand(Command):
    """Command to update user's preferred language."""

    user_id: UUID
    language_code: str  # ISO 639-1 code
