"""Domain models for the single-thread Nutree chat coach."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ChatMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessageStatus(StrEnum):
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class ChatClaimKind(StrEnum):
    NEW = "new"
    REPLAY = "replay"


class ChatIntent(StrEnum):
    REMAINING_BUDGET = "remaining_budget"
    NEXT_MEAL = "next_meal"
    DAY_PROGRESS = "day_progress"
    LIMITS = "limits"


CHAT_PROMPT_VERSION = "chat_prompt_v3"
CHAT_CONTEXT_VERSION = "chat_context_v1"
CHAT_RETRIEVAL_VERSION = "chat_retrieval_v1"
CHAT_EVAL_VERSION = "chat_eval_v1"
CHAT_DEFAULT_MODEL = "gpt-5.6-luna"

CHAT_HISTORY_LIMIT = 20
CHAT_RECENT_MEAL_DAYS = 3
CHAT_RECENT_MEAL_LIMIT = 24
CHAT_MAX_USER_MESSAGE_CHARS = 4000
CHAT_MAX_OUTPUT_TOKENS = 900
CHAT_DAILY_TURN_BUDGET = 40
CHAT_GENERATION_LEASE_SECONDS = 90
CHAT_RETRIEVAL_MIN_CHUNKS = 3
CHAT_RETRIEVAL_MAX_CHUNKS = 5
CHAT_SUPPORTED_LOCALES = frozenset({"en", "vi"})
CHAT_INTENTS = tuple(intent.value for intent in ChatIntent)
CHAT_ERROR_CODES = (
    "CHAT_BUSY",
    "CHAT_IDEMPOTENCY_CONFLICT",
    "CHAT_DAILY_LIMIT",
    "CHAT_RATE_LIMITED",
    "CHAT_PROVIDER_UNAVAILABLE",
    "CHAT_TURN_FAILED",
    "CHAT_UNAVAILABLE",
    "IDEMPOTENCY_KEY_REQUIRED",
    "INVALID_IDEMPOTENCY_KEY",
)


@dataclass(frozen=True, slots=True)
class ChatThread:
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime
    summary: str | None = None
    summary_through_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChatMessage:
    id: str
    thread_id: str
    role: ChatMessageRole
    status: ChatMessageStatus
    created_at: datetime
    updated_at: datetime
    content: str | None = None
    idempotency_key: str | None = None
    request_fingerprint: str | None = None
    in_reply_to_id: str | None = None
    model: str | None = None
    provider_response_id: str | None = None
    prompt_version: str | None = None
    context_version: str | None = None
    citation_source_keys: tuple[str, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    generation_lease_expires_at: datetime | None = None
    error_code: str | None = None
    completed_at: datetime | None = None
    reply_payload: dict[str, Any] | None = None

    def suggestions(self) -> list[dict[str, Any]]:
        return _payload_list(self.reply_payload, "suggestions")

    def follow_ups(self) -> list[dict[str, Any]]:
        return _payload_list(self.reply_payload, "follow_ups")

    def discover_session_id(self) -> str | None:
        if not self.reply_payload:
            return None
        value = self.reply_payload.get("discover_session_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def intent(self) -> str | None:
        if not self.reply_payload:
            return None
        value = self.reply_payload.get("intent")
        if value in CHAT_INTENTS:
            return str(value)
        return None


@dataclass(frozen=True, slots=True)
class ChatCitation:
    label: str
    source_key: str
    title: str
    canonical_uri: str | None = None
    score: float | None = None


@dataclass(frozen=True, slots=True)
class ChatUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    model: str = ""


@dataclass(frozen=True, slots=True)
class RetrievedKnowledgeChunk:
    chunk_id: str
    document_id: str
    source_key: str
    title: str
    content: str
    locale: str
    canonical_uri: str | None
    label: str
    vector_score: float | None = None
    fts_rank: float | None = None
    fused_score: float = 0.0
    safety_tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatMealSummary:
    meal_id: str
    local_date: str
    dish_name: str | None
    meal_type: str | None
    calories: float | None
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None
    status: str | None


@dataclass(frozen=True, slots=True)
class ChatHistoryTurn:
    role: ChatMessageRole
    content: str


@dataclass(frozen=True, slots=True)
class ChatUserContext:
    """Versioned, bounded snapshot of authoritative Nutree facts for one turn."""

    context_version: str
    as_of: str
    locale: str
    timezone: str
    allergies: list[str] | None
    health_conditions: list[str] | None
    dietary_preferences: list[str] | None
    goal: str | None
    tdee: float | None
    target_calories: float | None
    target_protein_g: float | None
    target_carbs_g: float | None
    target_fat_g: float | None
    consumed_calories: float | None
    consumed_protein_g: float | None
    consumed_carbs_g: float | None
    consumed_fat_g: float | None
    remaining_calories: float | None
    remaining_protein_g: float | None
    remaining_carbs_g: float | None
    remaining_fat_g: float | None
    remaining_days: int | None
    local_hour: int | None = None
    local_minute: int | None = None
    suggested_meal_slot: str | None = None
    recent_meals: tuple[ChatMealSummary, ...] = ()
    missing: tuple[str, ...] = ()

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "context_version": self.context_version,
            "as_of": self.as_of,
            "locale": self.locale,
            "timezone": self.timezone,
            "safety": {
                "allergies": self.allergies,
                "health_conditions": self.health_conditions,
                "dietary_preferences": self.dietary_preferences,
            },
            "targets": {
                "goal": self.goal,
                "tdee": self.tdee,
                "calories": self.target_calories,
                "protein_g": self.target_protein_g,
                "carbs_g": self.target_carbs_g,
                "fat_g": self.target_fat_g,
            },
            "today": {
                "consumed_calories": self.consumed_calories,
                "consumed_protein_g": self.consumed_protein_g,
                "consumed_carbs_g": self.consumed_carbs_g,
                "consumed_fat_g": self.consumed_fat_g,
                "remaining_calories": self.remaining_calories,
                "remaining_protein_g": self.remaining_protein_g,
                "remaining_carbs_g": self.remaining_carbs_g,
                "remaining_fat_g": self.remaining_fat_g,
                "remaining_days": self.remaining_days,
                "local_hour": self.local_hour,
                "local_minute": self.local_minute,
                "suggested_meal_slot": self.suggested_meal_slot,
            },
            "recent_meals": [
                {
                    "meal_id": meal.meal_id,
                    "local_date": meal.local_date,
                    "dish_name": meal.dish_name,
                    "meal_type": meal.meal_type,
                    "calories": meal.calories,
                    "protein_g": meal.protein_g,
                    "carbs_g": meal.carbs_g,
                    "fat_g": meal.fat_g,
                    "status": meal.status,
                }
                for meal in self.recent_meals
            ],
            "missing": list(self.missing),
        }


@dataclass(frozen=True, slots=True)
class ChatTurnClaim:
    kind: ChatClaimKind
    thread: ChatThread
    user_message: ChatMessage
    assistant_message: ChatMessage


@dataclass(frozen=True, slots=True)
class ChatCompletionDelta:
    text: str
    provider_response_id: str | None = None
    usage: ChatUsage | None = None
    done: bool = False


@dataclass
class ChatSseEvent:
    event: str
    data: dict[str, Any] = field(default_factory=dict)


def empty_reply_payload() -> dict[str, list[dict[str, Any]]]:
    return {"suggestions": [], "follow_ups": []}


def reply_sidecar(message: ChatMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "suggestions": message.suggestions(),
        "follow_ups": message.follow_ups(),
    }
    session_id = message.discover_session_id()
    if session_id:
        payload["discover_session_id"] = session_id
    intent = message.intent()
    if intent:
        payload["intent"] = intent
    return payload


def _payload_list(payload: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not payload:
        return []
    raw = payload.get(key)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]
