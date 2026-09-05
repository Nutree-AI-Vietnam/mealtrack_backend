"""Runtime capability declarations for mobile contract rollouts."""

from fastapi import APIRouter, HTTPException, status

from src.domain.model.chat import (
    CHAT_CONTEXT_VERSION,
    CHAT_DAILY_TURN_BUDGET,
    CHAT_DEFAULT_MODEL,
    CHAT_ERROR_CODES,
    CHAT_GENERATION_LEASE_SECONDS,
    CHAT_INTENTS,
    CHAT_MAX_USER_MESSAGE_CHARS,
    CHAT_PROMPT_VERSION,
    CHAT_SUPPORTED_LOCALES,
)
from src.infra.services.chat_schema import chat_schema_is_ready
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
    try:
        if not await chat_schema_is_ready():
            raise RuntimeError("chat schema is incomplete")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "CHAT_UNAVAILABLE",
                "message": "Chat storage is not available",
            },
        ) from exc

    return {
        "chat": True,
        "thread_model": "single",
        "read_only": True,
        "default_model": CHAT_DEFAULT_MODEL,
        "escalation_enabled": False,
        "sse": True,
        "header": "Idempotency-Key",
        "exact_replay": True,
        "locales": sorted(CHAT_SUPPORTED_LOCALES),
        "intents": list(CHAT_INTENTS),
        "error_codes": list(CHAT_ERROR_CODES),
        "max_user_message_chars": CHAT_MAX_USER_MESSAGE_CHARS,
        "daily_turn_budget": CHAT_DAILY_TURN_BUDGET,
        "generation_lease_seconds": CHAT_GENERATION_LEASE_SECONDS,
        "prompt_version": CHAT_PROMPT_VERSION,
        "context_version": CHAT_CONTEXT_VERSION,
    }
