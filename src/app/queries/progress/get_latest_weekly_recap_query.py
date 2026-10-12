"""Read the user's newest weekly recap ("Weekly Wrapped")."""

from dataclasses import dataclass

from src.app.events.base import Query
from src.domain.services.progress_recap_i18n import recap_locale


@dataclass
class GetLatestWeeklyRecapQuery(Query):
    user_id: str
    header_timezone: str | None = None
    locale: str = "en"

    def __post_init__(self) -> None:
        self.locale = recap_locale(self.locale)
