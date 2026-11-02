"""What the cancel run does with the plans it was given, and what it logs."""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest
from tests.fixtures.fakes.lifetime_webhook_db import USER
from tests.fixtures.fakes.play_renewal_fakes import (
    MONTHLY_ORDER,
    PLAY_MONTHLY,
    PLAY_YEARLY,
    YEARLY_ORDER,
    play_lifetime,
    revenuecat_subscriber,
)

from src.api.routes.v1.webhook_play_renewal_cancel import cancel_play_renewals
from src.api.routes.v1.webhook_play_renewal_selection import PlayRenewal
from src.domain.ports.play_subscription_port import (
    PlayBillingUnavailableError,
    PlayCancelOutcome,
)

pytestmark = pytest.mark.asyncio

PLAY_CANCEL = "src.api.routes.v1.webhook_play_renewal_cancel"
YEARLY = PlayRenewal(USER.id, PLAY_YEARLY, YEARLY_ORDER)
MONTHLY = PlayRenewal(USER.id, PLAY_MONTHLY, MONTHLY_ORDER)


def _errors(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]


def _names_the_plan(message: str, renewal: PlayRenewal = YEARLY) -> bool:
    return all(
        part in message for part in (renewal.order_id, renewal.product_id, USER.id)
    )


# --- Switches ---


async def test_with_the_switch_off_it_warns_and_cancels_nothing(play, caplog):
    play.enabled = play.switched_on = False

    await cancel_play_renewals(play_lifetime(), [YEARLY])

    play.cancel_renewal.assert_not_awaited()
    assert "Play renewal cancel is off" in caplog.text and not _errors(caplog)


async def test_switched_on_but_unusable_logs_every_skip_at_error(play, caplog):
    play.enabled = False  # the key or package didn't load

    for _ in range(2):
        await cancel_play_renewals(play_lifetime(), [YEARLY])

    play.cancel_renewal.assert_not_awaited()
    errors = _errors(caplog)
    assert len(errors) == 2 and all(_names_the_plan(e) for e in errors)


@pytest.mark.parametrize("sandbox_enabled", [False, True])
async def test_a_sandbox_event_is_acted_on_only_when_allowed(play, sandbox_enabled):
    play.sandbox_enabled = sandbox_enabled

    await cancel_play_renewals(play_lifetime(environment="SANDBOX"), [YEARLY])

    assert play.cancel_renewal.await_count == (1 if sandbox_enabled else 0)


async def test_without_renewals_nothing_is_built(play):
    await cancel_play_renewals(play_lifetime(), [])

    play.getter.assert_not_called()


# --- RevenueCat must list the order under the event's subscriber ---


async def test_a_plan_listed_by_its_latest_renewal_order_is_cancelled(play, revenuecat):
    revenuecat.get_subscriber_info.return_value = revenuecat_subscriber(
        f"{YEARLY_ORDER}..7"
    )

    await cancel_play_renewals(play_lifetime(), [YEARLY])

    play.cancel_renewal.assert_awaited_once_with(YEARLY_ORDER, PLAY_YEARLY)
    revenuecat.get_subscriber_info.assert_awaited_once_with("firebase-uid-1")


async def test_an_order_revenuecat_does_not_list_here_is_not_cancelled(
    play, revenuecat, caplog
):
    """A transfer moved another store account's row to this buyer."""
    revenuecat.get_subscriber_info.return_value = revenuecat_subscriber(MONTHLY_ORDER)

    await cancel_play_renewals(play_lifetime(), [YEARLY, MONTHLY])

    play.cancel_renewal.assert_awaited_once_with(MONTHLY_ORDER, PLAY_MONTHLY)
    (error,) = _errors(caplog)
    assert "another subscriber" in error and _names_the_plan(error)


async def _hang(_subscriber_id):
    await asyncio.sleep(5)


@pytest.mark.parametrize(
    "answer",
    [
        {"return_value": None},
        {"return_value": {"subscriber": {}}},
        {"side_effect": RuntimeError("down")},
        {"side_effect": _hang},
    ],
    ids=["no-subscriber", "no-subscriptions", "error", "slow"],
)
async def test_without_revenuecat_confirming_nothing_is_cancelled(
    play, revenuecat, caplog, answer
):
    revenuecat.get_subscriber_info = AsyncMock(**answer)

    with patch(f"{PLAY_CANCEL}.REVENUECAT_LOOKUP_SECONDS", 0.05):
        await cancel_play_renewals(play_lifetime(), [YEARLY])

    play.cancel_renewal.assert_not_awaited()
    (error,) = _errors(caplog)
    assert "could not confirm the owner" in error and _names_the_plan(error)


# --- Outcomes and failures ---


async def test_a_plan_play_says_is_already_off_is_logged_quietly(play, caplog):
    play.cancel_renewal.return_value = PlayCancelOutcome.NOT_RENEWING

    with caplog.at_level(logging.INFO, logger=PLAY_CANCEL):
        await cancel_play_renewals(play_lifetime(), [YEARLY])

    assert "already off or ended" in caplog.text and not _errors(caplog)


async def test_a_failure_names_the_order_and_the_next_plan_is_still_tried(play, caplog):
    play.cancel_renewal.side_effect = [
        PlayBillingUnavailableError(
            "Play subscriptionsv2.cancel returned 503", status_code=503
        ),
        RuntimeError("unexpected"),
    ]

    await cancel_play_renewals(play_lifetime(), [YEARLY, MONTHLY])

    assert play.cancel_renewal.await_count == 2
    first, second = _errors(caplog)
    assert "PlayBillingUnavailableError" in first and "503" in first
    assert _names_the_plan(first)
    assert "failed unexpectedly" in second and _names_the_plan(second, MONTHLY)


async def test_a_plan_without_an_order_id_is_reported(play, caplog):
    await cancel_play_renewals(
        play_lifetime(), [PlayRenewal(USER.id, PLAY_YEARLY, None)]
    )

    play.cancel_renewal.assert_not_awaited()
    assert any("has no order id" in e for e in _errors(caplog))


async def test_a_slow_play_stops_at_the_budget_naming_what_was_left(play, caplog):
    async def hang(order_id, product_id):
        await asyncio.sleep(5)

    play.cancel_renewal.side_effect = hang
    with patch(f"{PLAY_CANCEL}.CANCEL_BUDGET_SECONDS", 0.05):
        await asyncio.wait_for(
            cancel_play_renewals(play_lifetime(), [YEARLY, MONTHLY]), timeout=2
        )

    (error,) = _errors(caplog)
    assert "ran out of time" in error
    assert _names_the_plan(error) and _names_the_plan(error, MONTHLY)


async def test_no_log_carries_a_traceback_or_the_failure_message(play, caplog):
    """Sentry would send a traceback's frame variables: settings, keys, events."""
    play.getter.side_effect = ValueError("FIREBASE_SERVICE_ACCOUNT_JSON=secret")
    await cancel_play_renewals(play_lifetime(), [YEARLY])
    play.getter.side_effect = None
    play.cancel_renewal.side_effect = RuntimeError("token=ya29.secret")
    await cancel_play_renewals(play_lifetime(), [YEARLY])

    records = [r for r in caplog.records if r.name == PLAY_CANCEL]
    assert len(records) == 2 and all(r.exc_info is None for r in records)
    assert "secret" not in caplog.text
    assert "could not start: error=ValueError" in records[0].getMessage()
