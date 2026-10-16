"""Grant an entitlement from our own ledger (D5: admin/support/qa/migration)."""

from dataclasses import dataclass
from datetime import datetime

from src.api.exceptions import ValidationException
from src.domain.events.base import Command
from src.domain.exceptions.billing_exceptions import InvalidEntitlementGrant
from src.domain.model.billing.constants import ENTITLEMENT_STANDARD
from src.domain.model.billing.ledger import validate_grant_request


@dataclass
class CreateEntitlementGrantCommand(Command):
    user_id: str
    source: str
    reason: str
    ends_at: datetime
    # Admin email from the hardened require_admin (D7).
    created_by: str
    entitlement_key: str = ENTITLEMENT_STANDARD
    # None = the handler's clock at issue time.
    starts_at: datetime | None = None
    # Idempotency key: a retry with the same (source, source_ref) returns the
    # existing grant instead of adding a second one.
    source_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.user_id or not self.user_id.strip():
            raise ValidationException("user_id is required")
        if not self.created_by or not self.created_by.strip():
            raise ValidationException("created_by is required")
        if not self.entitlement_key or not self.entitlement_key.strip():
            raise ValidationException("entitlement_key is required")
        try:
            validate_grant_request(
                source=self.source,
                reason=self.reason,
                starts_at=self.starts_at,
                ends_at=self.ends_at,
            )
        except InvalidEntitlementGrant as exc:
            raise ValidationException(str(exc)) from exc
