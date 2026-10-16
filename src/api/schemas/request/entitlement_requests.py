"""Request bodies for the entitlement endpoints (NM-680)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.domain.model.billing.constants import ENTITLEMENT_STANDARD


class EntitlementClientHintRequest(BaseModel):
    """What the app just did. Logged and used to pick the RC lane; never state."""

    # Unknown fields from newer apps are ignored rather than failing the sync.
    model_config = ConfigDict(extra="ignore")

    kind: str = Field(..., max_length=32, description="purchase | restore | redeem")
    product_id: str | None = Field(default=None, max_length=255)
    transaction_id: str | None = Field(default=None, max_length=255)


class EntitlementSyncRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    client_hint: EntitlementClientHintRequest | None = None


class CreateEntitlementGrantRequest(BaseModel):
    """Manual grant from our ledger (D5): at most 366 days, reason required."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(..., min_length=1, max_length=36)
    entitlement_key: str = Field(
        default=ENTITLEMENT_STANDARD, min_length=1, max_length=64
    )
    source: Literal["admin", "support", "qa", "migration"]
    reason: str = Field(..., min_length=1, max_length=255)
    starts_at: datetime | None = Field(
        default=None, description="Timezone-aware; defaults to now"
    )
    ends_at: datetime = Field(..., description="Timezone-aware")
    source_ref: str | None = Field(
        default=None,
        max_length=255,
        description="Idempotency key: a retry with the same source_ref returns "
        "the existing grant",
    )


class RevokeEntitlementGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=255)
