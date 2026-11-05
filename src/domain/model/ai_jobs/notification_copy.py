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
        (AiJobType.RECIPE_IMPORT, AiJobStatus.COMPLETED): "Your recipe is ready",
        (AiJobType.RECIPE_IMPORT, AiJobStatus.FAILED): "We couldn't import your recipe",
        (AiJobType.WEEKLY_PLAN, AiJobStatus.COMPLETED): "Your week is planned",
        (AiJobType.WEEKLY_PLAN, AiJobStatus.FAILED): "We couldn't plan your week",
        (AiJobType.DESCRIBE_LOG, AiJobStatus.COMPLETED): "Quick log saved",
        (AiJobType.DESCRIBE_LOG, AiJobStatus.FAILED): "We couldn't log that",
        (AiJobType.DESCRIBE_MOVEMENT, AiJobStatus.COMPLETED): "Your activity is logged",
        (
            AiJobType.DESCRIBE_MOVEMENT,
            AiJobStatus.FAILED,
        ): "We couldn't log your activity",
        (AiJobType.DESCRIBE_HYDRATION, AiJobStatus.COMPLETED): "Your drink is logged",
        (
            AiJobType.DESCRIBE_HYDRATION,
            AiJobStatus.FAILED,
        ): "We couldn't log your drink",
    },
    "vi": {
        (AiJobType.MEAL_SCAN, AiJobStatus.COMPLETED): "Bữa ăn của bạn đã sẵn sàng",
        (AiJobType.MEAL_SCAN, AiJobStatus.FAILED): "Chưa phân tích được bữa ăn",
        (AiJobType.PARSE_TEXT, AiJobStatus.COMPLETED): "Bữa ăn của bạn đã sẵn sàng",
        (AiJobType.PARSE_TEXT, AiJobStatus.FAILED): "Chưa đọc được bữa ăn của bạn",
        (AiJobType.CHAT, AiJobStatus.COMPLETED): "Trợ lý đã trả lời",
        (AiJobType.CHAT, AiJobStatus.FAILED): "Trợ lý chưa trả lời xong",
        (
            AiJobType.RECIPE_IMPORT,
            AiJobStatus.COMPLETED,
        ): "Công thức của bạn đã sẵn sàng",
        (AiJobType.RECIPE_IMPORT, AiJobStatus.FAILED): "Chưa nhập được công thức",
        (
            AiJobType.WEEKLY_PLAN,
            AiJobStatus.COMPLETED,
        ): "Thực đơn tuần của bạn đã xong",
        (AiJobType.WEEKLY_PLAN, AiJobStatus.FAILED): "Chưa lên được thực đơn tuần",
        (AiJobType.DESCRIBE_LOG, AiJobStatus.COMPLETED): "Ghi nhanh đã lưu",
        (AiJobType.DESCRIBE_LOG, AiJobStatus.FAILED): "Chưa ghi nhận được",
        (
            AiJobType.DESCRIBE_MOVEMENT,
            AiJobStatus.COMPLETED,
        ): "Đã ghi nhận hoạt động",
        (
            AiJobType.DESCRIBE_MOVEMENT,
            AiJobStatus.FAILED,
        ): "Chưa ghi nhận được hoạt động",
        (AiJobType.DESCRIBE_HYDRATION, AiJobStatus.COMPLETED): "Đã ghi nhận đồ uống",
        (
            AiJobType.DESCRIBE_HYDRATION,
            AiJobStatus.FAILED,
        ): "Chưa ghi nhận được đồ uống",
    },
}

_COMPLETED_BODIES: dict[str, dict[AiJobType, str]] = {
    "en": {
        AiJobType.MEAL_SCAN: "Tap to see the nutrition breakdown.",
        AiJobType.PARSE_TEXT: "Tap to review and log it.",
        AiJobType.CHAT: "Tap to read the answer.",
        AiJobType.RECIPE_IMPORT: "Tap to review and save it.",
        AiJobType.WEEKLY_PLAN: "Tap to see what you are eating this week.",
        AiJobType.DESCRIBE_LOG: "Tap to see what was added.",
        AiJobType.DESCRIBE_MOVEMENT: "Tap to see what was added.",
        AiJobType.DESCRIBE_HYDRATION: "Tap to see what was added.",
    },
    "vi": {
        AiJobType.MEAL_SCAN: "Chạm để xem thông tin dinh dưỡng.",
        AiJobType.PARSE_TEXT: "Chạm để xem lại và ghi nhận.",
        AiJobType.CHAT: "Chạm để đọc câu trả lời.",
        AiJobType.RECIPE_IMPORT: "Chạm để xem lại và lưu.",
        AiJobType.WEEKLY_PLAN: "Chạm để xem tuần này ăn gì.",
        AiJobType.DESCRIBE_LOG: "Chạm để xem những gì đã thêm.",
        AiJobType.DESCRIBE_MOVEMENT: "Chạm để xem những gì đã thêm.",
        AiJobType.DESCRIBE_HYDRATION: "Chạm để xem những gì đã thêm.",
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
