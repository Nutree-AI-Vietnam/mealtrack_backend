"""Response schema for subscription access checks."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class MyEntitlementsResponse(BaseModel):
    """Database access decision for the signed-in user.

    product_id, will_renew and provider describe the granting subscription
    row with the latest deadline (a lifetime grant wins). They are null when
    has_access is false.
    """

    has_access: bool
    status: Literal["none", "trial", "active", "grace_period", "expired"]
    expires_at: datetime | None
    product_id: str | None = None
    will_renew: bool | None = None
    provider: str | None = None
