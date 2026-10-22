"""MoMo one-time pass checkout and result handling against fake I/O."""

from datetime import timedelta

import pytest

from src.domain.model.payments.momo_pass import MomoPayMethod, get_momo_pass_plan
from src.domain.model.payments.momo_signature import (
    RESULT_FIELDS,
    is_valid_result_signature,
    momo_signature,
)
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
    def __init__(
        self, credentials=CREDENTIALS, fail=False, query_code=1000, query_fails=False
    ):
        self.credentials = credentials
        self.fail, self.query_code, self.query_fails = fail, query_code, query_fails
        self.created, self.queried = [], []

    async def create_payment(self, **kwargs):
        if self.fail:
            raise MomoGatewayError("MoMo create failed: 22 amount")
        self.created.append(kwargs)
        return {"payUrl": "https://test-payment.momo.vn/pay", "deeplink": "momo://app"}

    async def query_payment(self, *, order_id, request_id):
        self.queried.append(order_id)
        if self.query_fails:
            raise MomoGatewayError("MoMo is unreachable")
        return {"orderId": order_id, "resultCode": self.query_code, "amount": 99000}


class FakeEntitlements:
    """Records credits and syncs; the locked steps are covered on Postgres."""

    def __init__(self, orders):
        self.orders, self.credits, self.syncs, self.activations = orders, [], [], []

    async def credit(self, _db, order_id, user_id):
        self.credits.append((order_id, user_id))
        self.orders[order_id].granted_user_id = user_id

    async def sync(self, _db, user_id):
        self.syncs.append(user_id)
        for order in self.orders.values():
            if order.granted_user_id == user_id:
                order.granted_at = utc_now()
        return True

    async def activate_for_user(self, _db, user_id, verified_email):
        paid = sorted(o for o, row in self.orders.items() if row.status == "paid")
        self.activations.append((user_id, verified_email, paid))

    async def has_active_pass(self, _db, _user_id):
        return bool(self.activations and self.activations[-1][2])


@pytest.fixture
def store(monkeypatch):
    """Orders by id plus records of lead updates, behind the order helpers."""
    state = {
        "orders": {},
        "paid_before": False,
        "paid_before_lookups": [],
        "leads_paid": [],
        "leads_started": [],
        "pending_lookups": [],
    }

    async def find_order(_db, order_id):
        return state["orders"].get(order_id)

    async def claim_query_slot(_db, order_id, interval):
        order = state["orders"][order_id]
        now = utc_now()
        if order.status != "pending" or (
            order.last_checked_at and order.last_checked_at >= now - interval
        ):
            return False
        order.last_checked_at = now
        return True

    async def paid_before(_db, **owners):
        state["paid_before_lookups"].append(owners)
        return state["paid_before"]

    async def recent_pending(_db, user_id, email, *, since, limit):
        state["pending_lookups"].append((user_id, email, limit))
        pending = [o for o, row in state["orders"].items() if row.status == "pending"]
        return pending[:limit]

    async def lead_paid(_db, lead_id):
        state["leads_paid"].append(lead_id)

    async def lead_started(_db, lead_id):
        state["leads_started"].append(lead_id)

    monkeypatch.setattr(momo_pass_orders, "find_order", find_order)
    monkeypatch.setattr(momo_pass_orders, "lock_order", find_order)
    monkeypatch.setattr(momo_pass_orders, "claim_query_slot", claim_query_slot)
    monkeypatch.setattr(momo_pass_orders, "has_paid_before", paid_before)
    monkeypatch.setattr(momo_pass_orders, "recent_pending_order_ids", recent_pending)
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


async def test_production_api_refuses_sandbox_momo_checkout(store):
    credentials = MomoCredentials(
        "sandbox", "NUTREE", "access", "secret", require_production=True
    )
    service, session = _service(store, FakeGateway(credentials))

    with pytest.raises(MomoNotConfigured) as error:
        await service.start_checkout(
            MomoBuyer(channel="app", user_id="user-1"),
            get_momo_pass_plan("monthly"),
            lang="vi",
        )

    assert error.value.missing == ["MOMO_ENV"]
    assert session.added == []


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
    assert service.entitlements.credits == [("o1", "user-1")]
    assert service.entitlements.syncs == ["user-1"]


async def test_replayed_ipn_retries_a_quiz_pass_credited_at_sign_in(store):
    store["orders"]["w1"] = _order(
        order_id="w1",
        channel="web_funnel",
        user_id=None,
        status="paid",
        granted_user_id="user-9",
    )
    service, _session = _service(store)

    assert await service.handle_ipn(_ipn(order_id="w1"))

    assert service.entitlements.syncs == ["user-9"]
    assert store["orders"]["w1"].granted_at is not None


async def test_web_order_paid_marks_the_lead_and_waits_for_sign_in(store):
    store["orders"]["o1"] = _order(channel="web_funnel", user_id=None, lead_id="lead-1")
    service, _session = _service(store)

    assert await service.handle_ipn(_ipn())

    assert store["orders"]["o1"].status == "paid"
    assert store["leads_paid"] == ["lead-1"]
    assert service.entitlements.credits == []


async def test_replayed_ipn_does_not_grant_twice(store):
    store["orders"]["o1"] = _order()
    service, _session = _service(store)

    await service.handle_ipn(_ipn())
    await service.handle_ipn(_ipn(result_code=1006))

    assert store["orders"]["o1"].status == "paid"
    assert len(service.entitlements.credits) == 1


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
    assert service.entitlements.credits == []


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
    assert service.entitlements.credits == [("o1", "user-1")]


async def test_status_poll_does_not_hammer_momo(store):
    store["orders"]["o1"] = _order(last_checked_at=utc_now() - timedelta(seconds=1))
    service, _session = _service(store)

    await service.app_order("o1", "user-1")

    assert service.gateway.queried == []


async def test_failing_momo_query_is_still_throttled(store):
    store["orders"]["o1"] = _order()
    service, _session = _service(store, FakeGateway(query_fails=True))

    first = await service.app_order("o1", "user-1")
    await service.app_order("o1", "user-1")

    assert first["status"] == "pending"
    assert service.gateway.queried == ["o1"]


async def test_status_poll_ends_the_read_before_asking_momo(store):
    store["orders"]["o1"] = _order()
    service, session = _service(store)

    async def query_payment(*, order_id, request_id):
        assert session.rollbacks == 1
        return {"orderId": order_id, "resultCode": 1000}

    service.gateway.query_payment = query_payment

    assert await service.app_order("o1", "user-1")


async def test_unconfigured_momo_is_never_queried(store):
    store["orders"]["o1"] = _order()
    production_with_sandbox = MomoCredentials(
        "sandbox", "NUTREE", "access", "secret", require_production=True
    )
    service, _session = _service(store, FakeGateway(production_with_sandbox))

    await service.app_order("o1", "user-1")

    assert service.gateway.queried == []


async def test_ipn_signed_with_an_empty_key_is_rejected_when_unconfigured(store):
    store["orders"]["o1"] = _order()
    service, _session = _service(
        store, FakeGateway(credentials=MomoCredentials("sandbox", "", "", ""))
    )
    body = _ipn(partnerCode="")
    body["signature"] = momo_signature("", {**body, "accessKey": ""}, RESULT_FIELDS)
    assert is_valid_result_signature("", "", body)

    assert not await service.handle_ipn(body)
    assert store["orders"]["o1"].status == "pending"


async def test_ipn_is_rejected_when_production_runs_sandbox_keys(store):
    store["orders"]["o1"] = _order()
    credentials = MomoCredentials(
        "sandbox", "NUTREE", "access", "secret", require_production=True
    )
    service, _session = _service(store, FakeGateway(credentials))

    assert not await service.handle_ipn(_ipn())
    assert store["orders"]["o1"].status == "pending"


async def test_sign_in_settles_a_paid_order_whose_ipn_was_missed(store):
    store["orders"]["w1"] = _order(
        order_id="w1", channel="web_funnel", user_id=None, buyer_email="b@x.vn"
    )
    service, _session = _service(store, FakeGateway(query_code=0))

    assert await service.activate("user-1", "b@x.vn")

    assert service.gateway.queried == ["w1"]
    assert store["orders"]["w1"].status == "paid"
    # Granting runs after the order settled, so the new payment is included.
    assert service.entitlements.activations == [("user-1", "b@x.vn", ["w1"])]


async def test_sign_in_asks_momo_about_each_pending_order_once(store):
    for order_id in ("o1", "o2"):
        store["orders"][order_id] = _order(order_id=order_id)
    service, _session = _service(store, FakeGateway(query_code=1000))

    assert not await service.activate("user-1", None)

    assert sorted(service.gateway.queried) == ["o1", "o2"]
    assert store["pending_lookups"] == [("user-1", None, 3)]
    assert service.entitlements.activations == [("user-1", None, [])]


async def test_sign_in_with_unconfigured_momo_still_grants_paid_orders(store):
    store["orders"]["o1"] = _order(status="paid")
    store["orders"]["o2"] = _order(order_id="o2")
    unconfigured = MomoCredentials("sandbox", "", "", "")
    service, _session = _service(store, FakeGateway(unconfigured))

    assert await service.activate("user-1", None)

    assert service.gateway.queried == [] and store["pending_lookups"] == []
    assert service.entitlements.activations == [("user-1", None, ["o1"])]


async def test_sign_in_still_credits_paid_orders_when_a_settle_fails(store):
    store["orders"]["o1"] = _order()
    store["orders"]["o2"] = _order(order_id="o2", status="paid")
    gateway = FakeGateway()

    async def broken_query(*, order_id, request_id):
        raise RuntimeError("connection reset")

    gateway.query_payment = broken_query
    service, _session = _service(store, gateway)

    assert await service.activate("user-1", None)

    assert service.entitlements.activations == [("user-1", None, ["o2"])]


async def test_quiz_price_depends_only_on_the_leads_own_orders(store):
    service, _session = _service(store)
    buyer = MomoBuyer(channel="web_funnel", lead_id="lead-1", email="b@x.vn")

    await service.plans_for(buyer)
    await service.start_checkout(buyer, get_momo_pass_plan("monthly"), lang="vi")

    assert store["paid_before_lookups"] == [{"lead_id": "lead-1"}] * 2


async def test_app_price_depends_on_the_account_and_its_proven_email(store):
    service, _session = _service(store)

    await service.plans_for(MomoBuyer(channel="app", user_id="user-1", email="b@x.vn"))

    assert store["paid_before_lookups"] == [{"user_id": "user-1", "email": "b@x.vn"}]


async def test_status_of_someone_elses_order_is_hidden(store):
    store["orders"]["o1"] = _order()
    service, _session = _service(store)

    assert await service.app_order("o1", "user-2") is None
