"""Push notification copy for finished AI jobs (English and Vietnamese)."""

from __future__ import annotations

from src.domain.model.ai_jobs.enums import AiJobStatus, AiJobType

_DEFAULT_LANGUAGE = "en"

_TITLES: dict[str, dict[tuple[AiJobType, AiJobStatus], str]] = {
    "en": {
        (AiJobType.MEAL_SCAN, AiJobStatus.COMPLETED): "Your meal is ready",
        (AiJobType.MEAL_SCAN, AiJobStatus.FAILED): "We couldn't analyze your meal",
        (AiJobType.PARSE_TEXT, AiJobStatus.COMPLETED): "Your meal is ready",
        (AiJobType.PARSE_TEXT, AiJobStatus.FAILED): "We couldn't read your meal",
        (AiJobType.CHAT, AiJobStatus.COMPLETED): "Your coach replied",
        (AiJobType.CHAT, AiJobStatus.FAILED): "Your coach couldn't finish replying",
    },
    "vi": {
        (AiJobType.MEAL_SCAN, AiJobStatus.COMPLETED): "Bữa ăn của bạn đã sẵn sàng",
        (AiJobType.MEAL_SCAN, AiJobStatus.FAILED): "Chưa phân tích được bữa ăn",
        (AiJobType.PARSE_TEXT, AiJobStatus.COMPLETED): "Bữa ăn của bạn đã sẵn sàng",
        (AiJobType.PARSE_TEXT, AiJobStatus.FAILED): "Chưa đọc được bữa ăn của bạn",
        (AiJobType.CHAT, AiJobStatus.COMPLETED): "Trợ lý đã trả lời",
        (AiJobType.CHAT, AiJobStatus.FAILED): "Trợ lý chưa trả lời xong",
    },
}

_COMPLETED_BODIES: dict[str, dict[AiJobType, str]] = {
    "en": {
        AiJobType.MEAL_SCAN: "Tap to see the nutrition breakdown.",
        AiJobType.PARSE_TEXT: "Tap to review and log it.",
        AiJobType.CHAT: "Tap to read the answer.",
    },
    "vi": {
        AiJobType.MEAL_SCAN: "Chạm để xem thông tin dinh dưỡng.",
        AiJobType.PARSE_TEXT: "Chạm để xem lại và ghi nhận.",
        AiJobType.CHAT: "Chạm để đọc câu trả lời.",
    },
}

_FAILED_BODIES: dict[str, dict[bool, str]] = {
    "en": {True: "Tap to try again.", False: "Tap to see what happened."},
    "vi": {True: "Chạm để thử lại.", False: "Chạm để xem chi tiết."},
}


def ai_job_push_copy(
    job_type: AiJobType,
    status: AiJobStatus,
    *,
    language: str,
    retryable: bool = True,
) -> tuple[str, str]:
    """Return ``(title, body)``; unsupported languages fall back to English."""
    lang = (language or "").split("-")[0].lower()
    if lang not in _TITLES:
        lang = _DEFAULT_LANGUAGE
    title = _TITLES[lang][(job_type, status)]
    if status == AiJobStatus.COMPLETED:
        return title, _COMPLETED_BODIES[lang][job_type]
    return title, _FAILED_BODIES[lang][retryable]
