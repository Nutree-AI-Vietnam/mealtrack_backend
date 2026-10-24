"""MoMo one-time premium passes: catalog, price rule and MoMo result mapping.

MoMo has not enabled its subscription method for our merchant for now, so each
payment buys a fixed pass that never renews. A buyer's first pass is charged
the intro price and every later pass the regular price, which mirrors an
intro-priced subscription renewed by hand.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class MomoPlanKey(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class MomoPayMethod(StrEnum):
    """How the buyer pays on MoMo's hosted page."""

    WALLET = "wallet"
    ATM = "atm"

    @property
    def request_type(self) -> str:
        return "captureWallet" if self is MomoPayMethod.WALLET else "payWithATM"


class MomoOrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass(frozen=True)
class MomoPassPlan:
    key: MomoPlanKey
    weeks: int
    intro_amount: int
    regular_amount: int

    @property
    def duration(self) -> timedelta:
        return timedelta(weeks=self.weeks)

    def amount_for(self, has_paid_before: bool) -> int:
        return self.regular_amount if has_paid_before else self.intro_amount


MOMO_PASS_PLANS: dict[MomoPlanKey, MomoPassPlan] = {
    plan.key: plan
    for plan in (
        MomoPassPlan(
            MomoPlanKey.MONTHLY, weeks=4, intro_amount=99_000, regular_amount=198_000
        ),
        MomoPassPlan(
            MomoPlanKey.QUARTERLY,
            weeks=12,
            intro_amount=249_000,
            regular_amount=498_000,
        ),
        MomoPassPlan(
            MomoPlanKey.YEARLY, weeks=52, intro_amount=599_000, regular_amount=1_198_000
        ),
    )
}

# Every order is created with autoCapture, a one-step payment, for which MoMo
# documents 9000 (authorized) as a completed payment.
_PAID_RESULT_CODES = frozenset({0, 9000})
# MoMo is still waiting on the buyer or its own processing.
_PENDING_RESULT_CODES = frozenset({1000, 7000, 7002})
# The payment session timed out before the buyer paid.
_EXPIRED_RESULT_CODES = frozenset({1005})


def get_momo_pass_plan(key: str) -> MomoPassPlan:
    try:
        return MOMO_PASS_PLANS[MomoPlanKey(key)]
    except ValueError as error:
        raise ValueError(f"Unknown MoMo plan: {key}") from error


def order_status_for_result(result_code: int) -> MomoOrderStatus:
    """Map a MoMo resultCode onto our order status."""
    if result_code in _PAID_RESULT_CODES:
        return MomoOrderStatus.PAID
    # Codes below 1000 describe the API call (format, auth, maintenance), not
    # the buyer's payment, so they never settle an order.
    if result_code in _PENDING_RESULT_CODES or 0 < result_code < 1000:
        return MomoOrderStatus.PENDING
    if result_code in _EXPIRED_RESULT_CODES:
        return MomoOrderStatus.EXPIRED
    return MomoOrderStatus.FAILED


def next_access_window(
    now: datetime, current_end: datetime | None, duration: timedelta
) -> tuple[datetime, datetime]:
    """A new pass starts now, or when the buyer's current pass ends."""
    start = current_end if current_end and current_end > now else now
    return start, start + duration
