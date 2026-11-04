"""Engagement push composition root: flag, start, stop and wiring."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.bootstrap.engagement_push as engagement_bootstrap
from src.infra.database.uow_async import AsyncUnitOfWork

LOGGER = "src.bootstrap.engagement_push"


class FakeLoop:
    """Stands in for the dispatcher's run loop."""

    def __init__(
        self, *, ignore_stop: bool = False, error: Exception | None = None
    ) -> None:
        self.ignore_stop = ignore_stop
        self.error = error
        self.stops: list[asyncio.Event] = []
        self.finished = False
        self.cancelled = False

    async def run(self, stop: asyncio.Event) -> None:
        self.stops.append(stop)
        if self.error is not None:
            raise self.error
        try:
            if self.ignore_stop:
                await asyncio.Event().wait()
            await stop.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.finished = True


def _reset() -> None:
    engagement_bootstrap._stop = None
    engagement_bootstrap._task = None


@pytest.fixture(autouse=True)
def clean_module_state():
    _reset()
    yield
    _reset()


@pytest.fixture
def env(monkeypatch):
    env = SimpleNamespace(
        settings=SimpleNamespace(ENGAGEMENT_PUSH_ENABLED=True),
        loop=FakeLoop(),
        push_executor=object(),
    )
    env.Dispatcher = MagicMock(side_effect=lambda **_: env.loop)
    env.FcmPushSender = MagicMock()
    env.get_push_executor = MagicMock(return_value=env.push_executor)
    monkeypatch.setattr(engagement_bootstrap, "get_settings", lambda: env.settings)
    monkeypatch.setattr(
        engagement_bootstrap, "EngagementPushDispatcher", env.Dispatcher
    )
    monkeypatch.setattr(engagement_bootstrap, "FcmPushSender", env.FcmPushSender)
    monkeypatch.setattr(
        engagement_bootstrap, "get_push_executor", env.get_push_executor
    )
    return env


def test_flag_is_off_by_default():
    from src.infra.config.settings import Settings

    assert Settings.model_fields["ENGAGEMENT_PUSH_ENABLED"].default is False


@pytest.mark.parametrize("enabled", [False, True])
def test_engagement_push_enabled_reads_the_flag(env, enabled):
    env.settings.ENGAGEMENT_PUSH_ENABLED = enabled

    assert engagement_bootstrap.engagement_push_enabled() is enabled


@pytest.mark.asyncio
async def test_start_does_nothing_while_the_flag_is_off(env):
    env.settings.ENGAGEMENT_PUSH_ENABLED = False

    assert engagement_bootstrap.start_engagement_push_dispatcher() is False

    env.Dispatcher.assert_not_called()
    assert engagement_bootstrap._task is None


@pytest.mark.asyncio
async def test_start_runs_one_dispatcher_on_the_fcm_sender(env, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)

    assert engagement_bootstrap.start_engagement_push_dispatcher() is True
    assert engagement_bootstrap.start_engagement_push_dispatcher() is False
    await asyncio.sleep(0)

    env.FcmPushSender.assert_called_once_with(executor=env.push_executor)
    env.Dispatcher.assert_called_once_with(
        uow_factory=AsyncUnitOfWork,
        sender=env.FcmPushSender.return_value,
        interval_seconds=30.0,
        batch_size=20,
    )
    assert len(env.loop.stops) == 1
    assert "Engagement push dispatcher started" in caplog.messages

    await engagement_bootstrap.stop_engagement_push_dispatcher(timeout=1)

    assert env.loop.finished is True
    assert engagement_bootstrap._task is None


@pytest.mark.asyncio
async def test_stop_without_a_dispatcher_is_a_no_op(env):
    await engagement_bootstrap.stop_engagement_push_dispatcher(timeout=0.01)

    assert engagement_bootstrap._task is None


@pytest.mark.asyncio
async def test_stop_cancels_a_dispatcher_that_does_not_finish_in_time(env):
    env.loop = FakeLoop(ignore_stop=True)
    engagement_bootstrap.start_engagement_push_dispatcher()
    await asyncio.sleep(0)

    await engagement_bootstrap.stop_engagement_push_dispatcher(timeout=0.01)

    assert env.loop.cancelled is True
    assert engagement_bootstrap._task is None


@pytest.mark.asyncio
async def test_a_crashed_dispatcher_is_logged(env, caplog):
    env.loop = FakeLoop(error=RuntimeError("boom"))

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        engagement_bootstrap.start_engagement_push_dispatcher()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert "Engagement push dispatcher stopped unexpectedly" in caplog.messages
    await engagement_bootstrap.stop_engagement_push_dispatcher(timeout=0.01)
