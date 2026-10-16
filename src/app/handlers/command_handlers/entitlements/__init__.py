"""Admin entitlement grant handlers (NM-680, D5)."""

from src.app.handlers.command_handlers.entitlements.create_entitlement_grant_command_handler import (
    CreateEntitlementGrantCommandHandler,
)
from src.app.handlers.command_handlers.entitlements.revoke_entitlement_grant_command_handler import (
    RevokeEntitlementGrantCommandHandler,
)

__all__ = [
    "CreateEntitlementGrantCommandHandler",
    "RevokeEntitlementGrantCommandHandler",
]
