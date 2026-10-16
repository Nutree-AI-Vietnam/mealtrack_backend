"""Revoke a manual entitlement grant. Grants are never deleted."""

from dataclasses import dataclass

from src.api.exceptions import ValidationException
from src.domain.events.base import Command
from src.domain.model.billing.ledger import GRANT_REASON_MAX_LENGTH


@dataclass
class RevokeEntitlementGrantCommand(Command):
    grant_id: str
    reason: str
    # Admin email from the hardened require_admin (D7); stored on the grant.
    revoked_by: str

    def __post_init__(self) -> None:
        if not self.grant_id or not self.grant_id.strip():
            raise ValidationException("grant_id is required")
        if not self.reason or not self.reason.strip():
            raise ValidationException("reason is required")
        if len(self.reason) > GRANT_REASON_MAX_LENGTH:
            raise ValidationException(
                f"reason must be at most {GRANT_REASON_MAX_LENGTH} characters"
            )
        if not self.revoked_by or not self.revoked_by.strip():
            raise ValidationException("revoked_by is required")
