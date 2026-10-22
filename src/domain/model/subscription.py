from dataclasses import dataclass
from datetime import datetime

from src.domain.model.base import BaseDomainModel
from src.domain.services.subscription_access_policy import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)
from src.domain.utils.timezone_utils import utc_now


@dataclass
class Subscription(BaseDomainModel):
    user_id: str
    product_id: str
    status: str
    expires_at: datetime | None = None
    platform: str | None = None
    original_transaction_id: str | None = None
    provider: str = "revenuecat"
    grace_period_expires_at: datetime | None = None
    period_type: str | None = None
    provider_snapshot_at: datetime | None = None

    def is_active(self) -> bool:
        if self.provider_snapshot_at is None:
            return self.status == "active" and (
                self.expires_at is None or self.expires_at > utc_now()
            )
        try:
            return evaluate_subscription_access([self]).has_access
        except SubscriptionAccessUnavailable:
            return False

    def is_monthly(self) -> bool:
        return "monthly" in self.product_id.lower()

    def is_yearly(self) -> bool:
        return (
            "yearly" in self.product_id.lower() or "annual" in self.product_id.lower()
        )
