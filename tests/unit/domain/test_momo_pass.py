"""MoMo one-time pass catalog, result mapping, windows and signatures."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import pytest

from src.domain.model.payments.momo_pass import (
    MomoOrderStatus,
    MomoPayMethod,
    get_momo_pass_plan,
    next_access_window,
    order_status_for_result,
)
from src.domain.model.payments.momo_signature import (
    CREATE_FIELDS,
    is_valid_result_signature,
    momo_signature,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("key", "weeks", "first", "later"),
    [
        ("monthly", 4, 99_000, 198_000),
        ("quarterly", 12, 249_000, 498_000),
        ("yearly", 52, 599_000, 1_198_000),
    ],
)
def test_first_pass_uses_intro_price_and_later_passes_the_regular_price(
    key, weeks, first, later
):
    plan = get_momo_pass_plan(key)

    assert plan.weeks == weeks
    assert plan.duration == timedelta(weeks=weeks)
    assert plan.amount_for(has_paid_before=False) == first
    assert plan.amount_for(has_paid_before=True) == later


def test_pay_methods_map_to_momo_one_time_apis():
    assert MomoPayMethod("wallet").request_type == "captureWallet"
    assert MomoPayMethod("atm").request_type == "payWithATM"


def test_unknown_plan_is_rejected():
    with pytest.raises(ValueError):
        get_momo_pass_plan("weekly")


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (0, MomoOrderStatus.PAID),
        (1000, MomoOrderStatus.PENDING),
        (7000, MomoOrderStatus.PENDING),
        (7002, MomoOrderStatus.PENDING),
        (9000, MomoOrderStatus.PENDING),
        (99, MomoOrderStatus.PENDING),
        (11, MomoOrderStatus.PENDING),
        (1005, MomoOrderStatus.EXPIRED),
        (1006, MomoOrderStatus.FAILED),
        (1001, MomoOrderStatus.FAILED),
        (4100, MomoOrderStatus.FAILED),
    ],
)
def test_result_codes_settle_orders_only_on_payment_outcomes(code, status):
    assert order_status_for_result(code) == status


def test_pass_starts_now_without_an_active_pass():
    assert next_access_window(NOW, None, timedelta(weeks=4)) == (
        NOW,
        NOW + timedelta(weeks=4),
    )
    expired = NOW - timedelta(days=1)
    assert next_access_window(NOW, expired, timedelta(weeks=4))[0] == NOW


def test_pass_bought_during_an_active_pass_starts_when_it_ends():
    current_end = NOW + timedelta(days=10)

    start, end = next_access_window(NOW, current_end, timedelta(weeks=12))

    assert start == current_end
    assert end == current_end + timedelta(weeks=12)


def test_create_signature_signs_fields_in_momo_order():
    values = {
        "accessKey": "ak",
        "amount": 99000,
        "extraData": "",
        "ipnUrl": "https://api/ipn",
        "orderId": "o1",
        "orderInfo": "Nutree Premium 4 tuần",
        "partnerCode": "P",
        "redirectUrl": "https://quiz/momo/return",
        "requestId": "r1",
        "requestType": "payWithMethod",
    }
    raw = (
        "accessKey=ak&amount=99000&extraData=&ipnUrl=https://api/ipn&orderId=o1"
        "&orderInfo=Nutree Premium 4 tuần&partnerCode=P"
        "&redirectUrl=https://quiz/momo/return&requestId=r1&requestType=payWithMethod"
    )

    assert momo_signature("secret", values, CREATE_FIELDS) == (
        hmac.new(b"secret", raw.encode(), hashlib.sha256).hexdigest()
    )


def _signed_result(**overrides):
    payload = {
        "partnerCode": "P",
        "orderId": "o1",
        "requestId": "r1",
        "amount": 99000,
        "orderInfo": "Nutree Premium 4 tuần",
        "orderType": "momo_wallet",
        "transId": 4088878653,
        "resultCode": 0,
        "message": "Thành công.",
        "payType": "napas",
        "responseTime": 1792682356885,
        "extraData": "",
    }
    raw = (
        "accessKey=ak&amount=99000&extraData=&message=Thành công.&orderId=o1"
        "&orderInfo=Nutree Premium 4 tuần&orderType=momo_wallet&partnerCode=P"
        "&payType=napas&requestId=r1&responseTime=1792682356885&resultCode=0"
        "&transId=4088878653"
    )
    payload["signature"] = hmac.new(b"secret", raw.encode(), hashlib.sha256).hexdigest()
    payload.update(overrides)
    return payload


def test_result_signature_accepts_momo_body():
    assert is_valid_result_signature("secret", "ak", _signed_result())


@pytest.mark.parametrize(
    "tampered",
    [{"amount": 1000}, {"resultCode": 1006}, {"signature": ""}, {"signature": None}],
)
def test_result_signature_rejects_tampered_or_unsigned_body(tampered):
    assert not is_valid_result_signature("secret", "ak", _signed_result(**tampered))


def test_result_signature_rejects_wrong_secret():
    assert not is_valid_result_signature("other", "ak", _signed_result())
