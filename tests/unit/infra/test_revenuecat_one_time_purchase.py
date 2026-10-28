"""RevenueCat lookup of one consumable purchase by its store transaction."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.domain.ports.subscription_service_port import OneTimePurchaseLookup
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter


def _subscriber(product_key="nutree_streak_restore_3", sandbox=False):
    return {
        "subscriber": {
            "non_subscriptions": {
                product_key: [
                    {
                        "id": "rc-1",
                        "store": "APP_STORE",
                        "store_transaction_id": "2000000812345678",
                        "is_sandbox": sandbox,
                    }
                ]
            }
        }
    }


async def _find(response=None, error=None, product_id="nutree_streak_restore_3"):
    with patch("httpx.AsyncClient") as client:
        get = (
            AsyncMock(side_effect=error) if error else AsyncMock(return_value=response)
        )
        client.return_value.__aenter__.return_value.get = get
        return await RevenueCatAdapter(api_key="key").find_one_time_purchase(
            "firebase-uid", product_id, "2000000812345678", timeout_seconds=3.0
        )


def _response(status=200, body=None):
    response = MagicMock(status_code=status)
    response.json.return_value = body
    response.raise_for_status = MagicMock()
    return response


@pytest.mark.asyncio
@pytest.mark.parametrize("sandbox", [False, True])
async def test_a_listed_transaction_is_found_with_its_store(sandbox):
    found = await _find(_response(body=_subscriber(sandbox=sandbox)))
    assert found.status is OneTimePurchaseLookup.FOUND
    assert found.store == "app_store"
    assert found.store_transaction_id == "2000000812345678"
    assert found.is_sandbox is sandbox


@pytest.mark.asyncio
async def test_a_play_base_plan_suffix_matches_the_plain_id():
    body = _subscriber(product_key="nutree_streak_restore_3:buy")
    found = await _find(_response(body=body))
    assert found.status is OneTimePurchaseLookup.FOUND


@pytest.mark.asyncio
async def test_another_transaction_is_not_found():
    body = _subscriber()
    body["subscriber"]["non_subscriptions"]["nutree_streak_restore_3"][0][
        "store_transaction_id"
    ] = "other"
    found = await _find(_response(body=body))
    assert found.status is OneTimePurchaseLookup.NOT_FOUND


@pytest.mark.asyncio
async def test_another_product_is_not_found():
    found = await _find(
        _response(body=_subscriber()), product_id="nutree_streak_restore_7"
    )
    assert found.status is OneTimePurchaseLookup.NOT_FOUND


@pytest.mark.asyncio
async def test_an_unknown_subscriber_is_not_found():
    found = await _find(_response(status=404))
    assert found.status is OneTimePurchaseLookup.NOT_FOUND


@pytest.mark.asyncio
async def test_a_timeout_is_unavailable_not_missing():
    found = await _find(error=httpx.ReadTimeout("slow"))
    assert found.status is OneTimePurchaseLookup.UNAVAILABLE
