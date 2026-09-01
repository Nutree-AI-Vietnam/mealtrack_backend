"""Runtime capability declarations for mobile contract rollouts."""

from fastapi import APIRouter, HTTPException, status

from src.domain.model.chat import CHAT_CONTEXT_VERSION, CHAT_PROMPT_VERSION
from src.infra.services.durable_write_service import (
    RETENTION_DAYS,
    durable_write_schema_is_ready,
)

router = APIRouter(prefix="/v1/capabilities", tags=["capabilities"])


@router.get("/durable-writes")
async def durable_write_capabilities() -> dict[str, object]:
    """Advertise v2 writes only when their durable storage is migrated."""
    try:
        if not await durable_write_schema_is_ready():
            raise RuntimeError("durable write schema is incomplete")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "DURABLE_WRITES_UNAVAILABLE",
                "message": "Durable write storage is not available",
            },
        ) from exc

    return {
        "durable_writes": True,
        "nutrition_contract_version": 2,
        "operations": ["create_manual_meal", "edit_meal"],
        # Keep the legacy capability shape for clients that still use the
        # claim-before-create replay store while v2 clients use the fields
        # above and the meal_write_operation table.
        "retention_days": RETENTION_DAYS,
        "actions": {
            "manual_meal_create": {
                "supported": True,
                "header": "Idempotency-Key",
                "exact_replay": True,
            },
            "barcode_meal_create": {
                "supported": True,
                "header": "Idempotency-Key",
                "exact_replay": True,
            },
            "meal_edit": {
                "supported": True,
                "header": "Idempotency-Key",
                "exact_replay": True,
            },
            "weight_sync": {
                "supported": False,
                "reason": "client_entry_id_mapping_pending",
            },
        },
    }


@router.get("/chat")
async def chat_capabilities() -> dict[str, object]:
    """Advertise the read-only single-thread Nutree coach contract."""
    from src.infra.config.settings import settings

    return {
        "chat": True,
        "thread_model": "single",
        "read_only": True,
        "default_model": settings.CHAT_MODEL,
        "escalation_enabled": bool(settings.CHAT_ESCALATION_MODEL),
        "sse": True,
        "header": "Idempotency-Key",
        "exact_replay": True,
        "locales": ["en", "vi"],
        "prompt_version": CHAT_PROMPT_VERSION,
        "context_version": CHAT_CONTEXT_VERSION,
    }
