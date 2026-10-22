"""MoMo one-time pass checkout and result handling against fake I/O."""

import hashlib
import hmac
from datetime import timedelta

import pytest

from src.domain.model.payments.momo_pass import MomoPayMethod, get_momo_pass_plan
from src.domain.model.payments.momo_signature import RESULT_FIELDS, momo_signature
from src.domain.utils.timezone_utils import utc_now
from src.infra.adapters.momo_gateway_adapter import MomoCredentials, MomoGatewayError
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.services import momo_pass_orders
from src.infra.services.momo_pass_service import (
    MomoBuyer,
    MomoNotConfigured,
    MomoPassService,
    MomoUrls,
)

CREDENTIALS = MomoCredentials("sandbox", "NUTREE", "access", "secret")
URLS = MomoUrls(
    ipn_url="https://api.example/v1/webhooks/momo/ipn",
    web_redirect_url="https://quiz.example/momo/return",
    app_redirect_url="nutree://momo-return",
)

pytestmark = pytest.mark.asyncio


class FakeSession:
    def __init__(self):
        self.added, self.commits, self.rollbacks = [], 0, 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakeGateway:
    def __init__(self, credentials=CREDENTIALS, fail=False, query_code=1000):
        self.credentials = credentials
        self.fail, self.query_code = fail, query_code
        self.created, self.queried = [], []

    async def create_payment(self, **kwargs):
        if self.fail:
            raise MomoGatewayError("MoMo create failed: 22 amount")
        self.created.append(kwargs)
        return {"payUrl": "https://test-payment.momo.vn/pay", "deeplink": "momo://app"}

    async def query_payment(self, *, order_id, request_id):
        self.queried.append(order_id)
        return {"orderId": order_id, "resultCode": self.query_code, "amount": 99000}


class FakeEntitlements:
    """Marks the order granted, like the real grant does under its lock."""

    def __init__(self, orders):
        self.orders, self.grants = orders, []

    async def grant(self, _db, order_id, user_id):
        self.grants.append((order_id, user_id))
        self.orders[order_id].granted_at = utc_now()
        return True


@pytest.fixture
def store(monkeypatch):
    """Orders by id plus records of lead updates, behind the order helpers."""
    state = {"orders": {}, "paid_before": False, "leads_paid": [], "leads_started": []}

    async def find_order(_db, order_id):
        return state["orders"].get(order_id)

    async def paid_before(_db, _user_id, _email):
        return state["paid_before"]

    async def lead_paid(_db, lead_id):
        state["leads_paid"].append(lead_id)

    async def lead_started(_db, lead_id):
        state["leads_started"].append(lead_id)

    monkeypatch.setattr(momo_pass_orders, "find_order", find_order)
    monkeypatch.setattr(momo_pass_orders, "lock_order", find_order)
    monkeypatch.setattr(momo_pass_orders, "has_paid_before", paid_before)
    monkeypatch.setattr(momo_pass_orders, "mark_lead_paid", lead_paid)
    monkeypatch.setattr(momo_pass_orders, "mark_lead_checkout_started", lead_started)
    return state


def _service(store, gateway=None, urls=URLS):
    session = FakeSession()
    service = MomoPassService(
        lambda: session,
        gateway or FakeGateway(),
        FakeEntitlements(store["orders"]),
        urls,
    )
    return service, session


def _order(**values):
    defaults = {
        "order_id": "o1",
        "request_id": "r1",
        "channel": "app",
        "request_type": "captureWallet",
        "plan": "monthly",
        "weeks": 4,
        "amount": 99000,
        "user_id": "user-1",
        "lead_id": None,
        "status": "pending",
        "last_checked_at": None,
        "granted_at": None,
    }
    return MomoPaymentOrder(**{**defaults, **values})


def _ipn(order_id="o1", result_code=0, amount=99000, secret="secret", **extra):
    body = {
        "partnerCode": "NUTREE",
        "orderId": order_id,
        "requestId": "r1",
        "amount": amount,
        "orderInfo": "Nutree Premium 4 tuần",
        "orderType": "momo_wallet",
        "transId": 4088878653,
        "resultCode": result_code,
        "message": "ok",
        "payType": "qr",
        "responseTime": 1792682356885,
        "extraData": "",
        **extra,
    }
    body["signature"] = momo_signature(
        secret, {**body, "accessKey": "access"}, RESULT_FIELDS
    )
    return body


async def test_web_checkout_defaults_to_the_momo_wallet_back_to_the_quiz(store):
    service, session = _service(store)
    buyer = MomoBuyer(
        channel="web_funnel", lead_id="lead-1", email=" Buyer@Example.com "
    )

    result = await service.start_checkout(
        buyer, get_momo_pass_plan("quarterly"), lang="vi"
    )

    order = session.added[0]
    created = service.gateway.created[0]
    assert result["order_id"] == order.order_id and result["amount"] == 249_000
    assert (order.request_type, order.intro_price, order.buyer_email) == (
        "captureWallet",
        True,
        "buyer@example.com",
    )
    assert created["redirect_url"] == URLS.web_redirect_url
    assert created["order_info"] == "Nutree Premium 12 tuần"
    assert store["leads_started"] == ["lead-1"]


async def test_app_checkout_by_atm_card_returns_to_the_app(store):
    store["paid_before"] = True
    service, session = _service(store)

    result = await service.start_checkout(
        MomoBuyer(channel="app", user_id="user-1"),
        get_momo_pass_plan("monthly"),
        lang="en",
        method=MomoPayMethod.ATM,
    )

    order = session.added[0]
    assert result["pay_url"].startswith("https://")
    assert (order.request_type, order.amount, order.intro_price) == (
        "payWithATM",
        198_000,
        False,
    )
    assert service.gateway.created[0]["redirect_url"] == "nutree://momo-return"


async def test_checkout_without_keys_names_what_is_missing(store):
    gateway = FakeGateway(credentials=MomoCredentials("sandbox", "", "", ""))
    service, _session = _service(
        store, gateway, MomoUrls("", "", "nutree://momo-return")
    )

    with pytest.raises(MomoNotConfigured) as error:
        await service.start_checkout(
            MomoBuyer(channel="web_funnel", lead_id="lead-1"),
            get_momo_pass_plan("monthly"),
            lang="vi",
        )

    assert error.value.missing == [
        "MOMO_PARTNER_CODE",
        "MOMO_ACCESS_KEY",
        "MOMO_SECRET_KEY",
        "MOMO_IPN_URL",
        "MOMO_REDIRECT_URL",
    ]


async def test_rejected_create_marks_the_order_failed(store):
    service, session = _service(store, FakeGateway(fail=True))

    with pytest.raises(MomoGatewayError):
        await service.start_checkout(
            MomoBuyer(channel="app", user_id="user-1"),
            get_momo_pass_plan("monthly"),
            lang="vi",
        )

    assert session.added[0].status == "failed"


async def test_signed_paid_ipn_settles_the_order_and_grants_the_pass(store):
    store["orders"]["o1"] = _order()
    service, _session = _service(store)

    assert await service.handle_ipn(_ipn())

    order = store["orders"]["o1"]
    assert (order.status, order.trans_id, order.pay_type) == ("paid", 4088878653, "qr")
    assert service.entitlements.grants == [("o1", "user-1")]


async def test_web_order_paid_marks_the_lead_and_waits_for_sign_in(store):
    store["orders"]["o1"] = _order(channel="web_funnel", user_id=None, lead_id="lead-1")
    service, _session = _service(store)

    assert await service.handle_ipn(_ipn())

    assert store["orders"]["o1"].status == "paid"
    assert store["leads_paid"] == ["lead-1"]
    assert service.entitlements.grants == []


async def test_replayed_ipn_does_not_grant_twice(store):
    store["orders"]["o1"] = _order()
    service, _session = _service(store)

    await service.handle_ipn(_ipn())
    await service.handle_ipn(_ipn(result_code=1006))

    assert store["orders"]["o1"].status == "paid"
    assert len(service.entitlements.grants) == 1


@pytest.mark.parametrize(
    "body",
    [
        _ipn(secret="forged"),
        _ipn(partnerCode="OTHER"),
        {**_ipn(), "amount": 1000},
    ],
)
async def test_untrusted_ipn_changes_nothing(store, body):
    store["orders"]["o1"] = _order()
    service, _session = _service(store)

    assert not await service.handle_ipn(body)
    assert store["orders"]["o1"].status == "pending"


async def test_paid_result_with_another_amount_is_not_trusted(store):
    store["orders"]["o1"] = _order()
    service, _session = _service(store)

    assert await service.handle_ipn(_ipn(amount=1000))

    assert store["orders"]["o1"].status == "pending"
    assert service.entitlements.grants == []


async def test_back_button_result_fails_the_order(store):
    store["orders"]["o1"] = _order()
    service, _session = _service(store)

    await service.handle_ipn(_ipn(result_code=1006))

    assert store["orders"]["o1"].status == "failed"
    assert store["orders"]["o1"].result_code == 1006


async def test_status_poll_asks_momo_while_pending(store):
    store["orders"]["o1"] = _order()
    service, _session = _service(store, FakeGateway(query_code=0))

    result = await service.app_order("o1", "user-1")

    assert service.gateway.queried == ["o1"]
    assert result["status"] == "paid"
    assert service.entitlements.grants == [("o1", "user-1")]


async def test_status_poll_does_not_hammer_momo(store):
    store["orders"]["o1"] = _order(last_checked_at=utc_now() - timedelta(seconds=1))
    service, _session = _service(store)

    await service.app_order("o1", "user-1")

    assert service.gateway.queried == []


async def test_status_of_someone_elses_order_is_hidden(store):
    store["orders"]["o1"] = _order()
    service, _session = _service(store)

    assert await service.app_order("o1", "user-2") is None


def test_signature_helper_matches_a_hand_built_raw_string():
    raw = "accessKey=access&amount=1"
    assert momo_signature(
        "secret", {"accessKey": "access", "amount": 1}, ("accessKey", "amount")
    ) == (hmac.new(b"secret", raw.encode(), hashlib.sha256).hexdigest())
