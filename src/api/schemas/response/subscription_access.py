"""Response schema for subscription access checks."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class MyEntitlementsResponse(BaseModel):
    has_access: bool
    status: Literal["none", "trial", "active", "grace_period", "expired"]
    expires_at: datetime | None
