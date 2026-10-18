import logging
from dataclasses import dataclass

import pytest

from src.api.exceptions import ResourceNotFoundException
from src.app.commands.meal_planner.update_weekly_meal_plan_command import (
    UpdateWeeklyMealPlanCommand,
)
from src.domain.events.base import EventHandler, Query
from src.domain.exceptions.ai_exceptions import (
    AIOutputValidationError,
    AIUnavailableError,
)
from src.domain.exceptions.weekly_meal_planner_exceptions import (
    WeeklyMealPlanConflictError,
)
from src.infra.event_bus.pymediator_event_bus import PyMediatorEventBus


@dataclass
class _MissingMealQuery(Query):
    meal_id: str


@dataclass
class _CrashingQuery(Query):
    name: str


@dataclass
class _AIUnavailableQuery(Query):
    purpose: str


class _MissingMealHandler(EventHandler[_MissingMealQuery, None]):
    async def handle(self, event: _MissingMealQuery) -> None:
        raise ResourceNotFoundException(f"Meal with ID {event.meal_id} not found")


class _AIUnavailableHandler(EventHandler[_AIUnavailableQuery, None]):
    async def handle(self, event: _AIUnavailableQuery) -> None:
        raise AIUnavailableError(
            f"All vision models failed for {event.purpose}",
            attempted_models=["gpt-5.4-mini-2026-03-17", "gpt-5.4-mini-2026-03-17"],
            last_error="504 DEADLINE_EXCEEDED",
        )


class _CrashingHandler(EventHandler[_CrashingQuery, None]):
    async def handle(self, event: _CrashingQuery) -> None:
        raise RuntimeError(f"Unexpected failure for {event.name}")


@pytest.mark.asyncio
async def test_expected_application_exception_is_not_logged_as_error(caplog):
    bus = PyMediatorEventBus()
    bus.register_handler(_MissingMealQuery, _MissingMealHandler())

    with caplog.at_level(
        logging.DEBUG,
        logger="src.infra.event_bus.pymediator_event_bus",
    ):
        with pytest.raises(ResourceNotFoundException):
            await bus.send(_MissingMealQuery(meal_id="meal-1"))

    assert not [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and "Error handling" in record.message
    ]
    assert any(
        record.levelno == logging.DEBUG
        and "Application exception handling _MissingMealQuery" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_ai_unavailable_exception_is_not_logged_as_error(caplog):
    bus = PyMediatorEventBus()
    bus.register_handler(_AIUnavailableQuery, _AIUnavailableHandler())

    with caplog.at_level(
        logging.DEBUG,
        logger="src.infra.event_bus.pymediator_event_bus",
    ):
        with pytest.raises(AIUnavailableError):
            await bus.send(_AIUnavailableQuery(purpose="meal_scan"))

    assert not [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and "Error handling" in record.message
    ]
    assert any(
        record.levelno == logging.DEBUG
        and "Application exception handling _AIUnavailableQuery" in record.message
        for record in caplog.records
    )


class _AIOutputInvalidHandler(EventHandler[_AIUnavailableQuery, None]):
    async def handle(self, event: _AIUnavailableQuery) -> None:
        raise AIOutputValidationError(
            "AI nutrition fallback failed physical validation",
            purpose=event.purpose,
            attempt_count=1,
            validation_details=["unsafe_density_fallback"],
        )


@pytest.mark.asyncio
async def test_ai_output_validation_exception_is_not_logged_as_error(caplog):
    bus = PyMediatorEventBus()
    bus.register_handler(_AIUnavailableQuery, _AIOutputInvalidHandler())

    with caplog.at_level(
        logging.DEBUG,
        logger="src.infra.event_bus.pymediator_event_bus",
    ):
        with pytest.raises(AIOutputValidationError):
            await bus.send(_AIUnavailableQuery(purpose="parse_text"))

    assert not [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and "Error handling" in record.message
    ]


class _LoggedSlotConflictHandler(EventHandler[UpdateWeeklyMealPlanCommand, None]):
    async def handle(self, event: UpdateWeeklyMealPlanCommand) -> None:
        raise WeeklyMealPlanConflictError("Logged weekly meal slots cannot be replaced")


@pytest.mark.asyncio
async def test_weekly_meal_plan_conflict_is_logged_at_warning_not_error(caplog):
    """PYTHON-JR: replacing a logged slot is a business rule, not a failure.

    The repository raises WeeklyMealPlanConflictError and the event bus used to
    log it at ERROR with exc_info. Sentry's LoggingIntegration (event_level=ERROR)
    turned that record into PYTHON-JR even though the route returns 409.
    """
    bus = PyMediatorEventBus()
    bus.register_handler(UpdateWeeklyMealPlanCommand, _LoggedSlotConflictHandler())
    command = UpdateWeeklyMealPlanCommand(
        user_id="8e3bf7dd-bf85-49de-a961-0134299a22ab",
        plan_id="a6221ce5-8f33-463b-9f88-31e2cbe416f7",
        idempotency_key="patch-logged-slot",
        slots={(4, 0): "3a43de9a-7e56-450d-b6ae-5a4b6dd02bfc"},
        expected_revision=5,
    )

    with caplog.at_level(
        logging.DEBUG,
        logger="src.infra.event_bus.pymediator_event_bus",
    ):
        with pytest.raises(
            WeeklyMealPlanConflictError,
            match="Logged weekly meal slots cannot be replaced",
        ):
            await bus.send(command)

    assert not [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and "Error handling" in record.message
    ]
    assert any(
        record.levelno == logging.WARNING
        and record.exc_info is None
        and "Weekly meal plan conflict handling UpdateWeeklyMealPlanCommand"
        in record.message
        and "Logged weekly meal slots cannot be replaced" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_unexpected_exception_is_logged_as_error(caplog):
    bus = PyMediatorEventBus()
    bus.register_handler(_CrashingQuery, _CrashingHandler())

    with caplog.at_level(
        logging.ERROR,
        logger="src.infra.event_bus.pymediator_event_bus",
    ):
        with pytest.raises(RuntimeError):
            await bus.send(_CrashingQuery(name="test"))

    assert any(
        record.levelno == logging.ERROR
        and "Error handling _CrashingQuery" in record.message
        for record in caplog.records
    )
