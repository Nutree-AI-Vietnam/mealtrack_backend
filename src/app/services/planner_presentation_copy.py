"""Finite planner fallback copy; presentation never waits for a translator."""

from src.domain.constants.languages import resolve_app_locale

_COPY = {
    "en": (
        "Prepared a reviewable weekly plan proposal from your request.",
        "{count} meals changed",
        " Confirming saves these preferences for this week; your profile preferences are unchanged.",
    ),
    "vi": (
        "Đã chuẩn bị đề xuất thực đơn để bạn xem trước.",
        "Đã thay đổi {count} bữa ăn",
        " Xác nhận sẽ lưu lựa chọn cho tuần này; hồ sơ của bạn giữ nguyên.",
    ),
    "es": (
        "La propuesta semanal está lista para revisar.",
        "{count} comidas modificadas",
        " Al confirmar se guardan las preferencias para esta semana; tu perfil no cambia.",
    ),
    "fr": (
        "La proposition de menu est prête à être examinée.",
        "{count} repas modifiés",
        " La confirmation enregistre les préférences pour cette semaine ; votre profil reste inchangé.",
    ),
    "de": (
        "Der Wochenplanvorschlag ist zur Prüfung bereit.",
        "{count} Mahlzeiten geändert",
        " Die Bestätigung speichert diese Einstellungen für diese Woche; Ihr Profil bleibt unverändert.",
    ),
    "ja": (
        "週間献立の提案を確認できます。",
        "{count}件の食事を変更",
        " 確認すると今週の設定が保存されます。プロフィールの設定は変更されません。",
    ),
    "zh": (
        "每周食谱建议已准备好，请确认。",
        "已更改{count}餐",
        " 确认后将保存本周偏好；个人资料中的偏好保持不变。",
    ),
}


def planner_copy(language, count=0):
    explanation, diff, notice = _COPY[resolve_app_locale(language)]
    return explanation, diff.format(count=count), notice
