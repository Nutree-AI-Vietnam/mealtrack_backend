from tests.fixtures.fakes.fake_meal_repository import FakeMealRepository
from tests.fixtures.fakes.fake_meal_suggestion_repository import (
    FakeMealSuggestionRepository,
)
from tests.fixtures.fakes.fake_notification_repository import FakeNotificationRepository
from tests.fixtures.fakes.fake_subscription_repository import FakeSubscriptionRepository
from tests.fixtures.fakes.fake_user_repository import FakeUserRepository

from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort


class FakeUnitOfWork(AsyncUnitOfWorkPort):
    """Fake UnitOfWork that supports both sync (legacy tests) and async (new handlers)."""

    def __init__(self):
        self.users = FakeUserRepository()
        self.notifications = FakeNotificationRepository()
        self.subscriptions = FakeSubscriptionRepository()
        self.meals = FakeMealRepository()
        self.meal_suggestions = FakeMealSuggestionRepository()
        self.committed = False
        self.rolled_back = False

    # ---- Sync context manager (kept for legacy tests) ----
    def __enter__(self) -> "FakeUnitOfWork":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type:
            self._sync_rollback()
        else:
            self._sync_commit()

    def _sync_commit(self) -> None:
        self.committed = True

    def _sync_rollback(self) -> None:
        self.rolled_back = True

    # ---- Async context manager (for async handlers) ----
    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type:
            await self.rollback()
        else:
            await self.commit()

    async def commit(self) -> None:  # type: ignore[override]
        self.committed = True

    async def rollback(self) -> None:  # type: ignore[override]
        self.rolled_back = True

    def refresh(self, obj) -> None:
        pass
