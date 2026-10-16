"""Admin entitlement grant commands (NM-680, D5)."""

from src.app.commands.entitlements.create_entitlement_grant_command import (
    CreateEntitlementGrantCommand,
)
from src.app.commands.entitlements.revoke_entitlement_grant_command import (
    RevokeEntitlementGrantCommand,
)

__all__ = ["CreateEntitlementGrantCommand", "RevokeEntitlementGrantCommand"]
