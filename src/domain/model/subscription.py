from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.domain.model.base import BaseDomainModel
from src.domain.utils.timezone_utils import utc_now


@dataclass
class Subscription(BaseDomainModel):
    user_id: str
    product_id: str
    status: str
    expires_at: Optional[datetime] = None
    platform: Optional[str] = None
    original_transaction_id: Optional[str] = None

    def is_active(self) -> bool:
        if self.status != "active":
            return False
        if self.expires_at and self.expires_at < utc_now():
            return False
        return True

    def is_monthly(self) -> bool:
        return "monthly" in self.product_id.lower()

    def is_yearly(self) -> bool:
        return (
            "yearly" in self.product_id.lower() or "annual" in self.product_id.lower()
        )
