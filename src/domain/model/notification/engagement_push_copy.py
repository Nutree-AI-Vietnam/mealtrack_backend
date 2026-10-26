"""Push copy for server-sent engagement notifications.

Account notices stay neutral: no emoji, no buddy word, no coach persona.
"""

from __future__ import annotations

from src.domain.constants.languages import DEFAULT_LANGUAGE, resolve_app_locale
from src.domain.model.notification.engagement_push import EngagementPushKind

# kind -> language -> (title, body)
_COPY: dict[EngagementPushKind, dict[str, tuple[str, str]]] = {
    EngagementPushKind.BILLING_ISSUE: {
        "en": (
            "Payment didn't go through",
            "Update your payment method to keep your Nutree plan running.",
        ),
        "vi": (
            "Thanh toán chưa thành công",
            "Cập nhật phương thức thanh toán để gói Nutree không bị gián đoạn nhé.",
        ),
        "es": (
            "El pago no se completó",
            "Actualiza tu método de pago para que tu plan de Nutree siga activo.",
        ),
        "fr": (
            "Le paiement n'a pas abouti",
            "Mets à jour ton moyen de paiement pour que ton abonnement Nutree "
            "reste actif.",
        ),
        "de": (
            "Zahlung nicht erfolgreich",
            "Aktualisiere deine Zahlungsmethode, damit dein Nutree-Abo weiterläuft.",
        ),
        "ja": (
            "お支払いが完了しませんでした",
            "Nutreeのプランを継続するには、お支払い方法を更新してください。",
        ),
        "zh": (
            "付款未成功",
            "请更新付款方式，以免你的 Nutree 订阅中断。",
        ),
    },
}


def engagement_push_copy(
    kind: EngagementPushKind, *, language: str | None
) -> tuple[str, str]:
    """Return ``(title, body)``; languages without copy fall back to English."""
    by_language = _COPY[kind]
    return by_language.get(resolve_app_locale(language), by_language[DEFAULT_LANGUAGE])
