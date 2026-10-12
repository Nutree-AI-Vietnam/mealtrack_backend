from collections import Counter
from collections.abc import Iterable
from datetime import date

from src.domain.model.weekly_recap import WeeklyRecap
from src.domain.ports.weekly_recap_repository_port import WeeklyRecapRepositoryPort


class FakeWeeklyRecapRepository(WeeklyRecapRepositoryPort):
    def __init__(self, recaps: Iterable[WeeklyRecap] = ()):
        self.recaps = {(recap.user_id, recap.week_start): recap for recap in recaps}
        self.inserted: list[WeeklyRecap] = []

    async def get(self, user_id: str, week_start: date) -> WeeklyRecap | None:
        return self.recaps.get((user_id, week_start))

    async def insert_if_absent(self, recap: WeeklyRecap) -> bool:
        key = (recap.user_id, recap.week_start)
        if key in self.recaps:
            return False
        self.recaps[key] = recap
        self.inserted.append(recap)
        return True

    async def count_cases_before(self, user_id: str, before: date) -> dict[str, int]:
        return dict(
            Counter(
                recap.case_id
                for (owner, week_start), recap in self.recaps.items()
                if owner == user_id and week_start < before
            )
        )
