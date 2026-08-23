from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.commands.saved_suggestion import (
    DeleteSavedSuggestionCommand,
    SaveSuggestionCommand,
)
from src.app.handlers.command_handlers.saved_suggestion.delete_saved_suggestion_command_handler import (
    DeleteSavedSuggestionCommandHandler,
)
from src.app.handlers.command_handlers.saved_suggestion.save_suggestion_command_handler import (
    SaveSuggestionCommandHandler,
)


class FakeSavedSuggestionsRepo:
    def __init__(self, existing=None):
        self._existing = existing

    async def find_by_user_and_suggestion(self, user_id, suggestion_id):
        return self._existing

    async def save(self, **kwargs):
        return {"id": "saved-1", **kwargs}

    async def delete_by_user_and_suggestion(self, user_id, suggestion_id):
        return True


class FakeUnitOfWork:
    def __init__(self, repo):
        self.saved_suggestions = repo
        self.saved_suggestions_db = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.mark.asyncio
async def test_save_suggestion_publishes_event_when_new():
    repo = FakeSavedSuggestionsRepo(existing=None)
    uow = FakeUnitOfWork(repo)
    publisher = SimpleNamespace(publish=AsyncMock())
    handler = SaveSuggestionCommandHandler(
        uow=uow, event_publisher=publisher, environment="test"
    )

    cmd = SaveSuggestionCommand(
        user_id="user-1",
        suggestion_id="sug-1",
        meal_type="breakfast",
        portion_multiplier=1,
        suggestion_data={"name": "Oatmeal"},
    )
    result = await handler.handle(cmd)

    assert result["suggestion_id"] == "sug-1"
    publisher.publish.assert_awaited_once()
    payload = publisher.publish.await_args.args[0]
    assert payload["event_type"] == "saved_suggestion.created.v1"
    assert payload["aggregate_id"] == "sug-1"
    assert payload["data"] == {"user_id": "user-1"}


@pytest.mark.asyncio
async def test_save_suggestion_returns_existing_without_publishing():
    repo = FakeSavedSuggestionsRepo(existing={"id": "existing-1"})
    uow = FakeUnitOfWork(repo)
    publisher = SimpleNamespace(publish=AsyncMock())
    handler = SaveSuggestionCommandHandler(
        uow=uow, event_publisher=publisher, environment="test"
    )

    cmd = SaveSuggestionCommand(
        user_id="user-1",
        suggestion_id="sug-1",
        meal_type="breakfast",
        portion_multiplier=1,
        suggestion_data={"name": "Oatmeal"},
    )
    result = await handler.handle(cmd)

    assert result == {"id": "existing-1"}
    publisher.publish.assert_not_called()


@pytest.mark.asyncio
async def test_delete_saved_suggestion_publishes_event():
    repo = FakeSavedSuggestionsRepo()
    uow = FakeUnitOfWork(repo)
    publisher = SimpleNamespace(publish=AsyncMock())
    handler = DeleteSavedSuggestionCommandHandler(
        uow=uow, event_publisher=publisher, environment="test"
    )

    cmd = DeleteSavedSuggestionCommand(user_id="user-1", suggestion_id="sug-1")
    result = await handler.handle(cmd)

    assert result == {"success": True}
    publisher.publish.assert_awaited_once()
    payload = publisher.publish.await_args.args[0]
    assert payload["event_type"] == "saved_suggestion.deleted.v1"
    assert payload["aggregate_id"] == "sug-1"
    assert payload["data"] == {"user_id": "user-1"}
