"""
Notification message templates keyed by language and gender.

Time Sensitive notifications use a title plus body field.
Bodies intentionally include one line break so iOS renders richer copy across
two lines without leaving the emoji alone on a third line.

Gender-aware buddy terms:
- EN: male="bro", female="mate"
- VI: male="bro", female="bạn ơi"

Placeholders:
- Daily summary: {percentage}, {deficit}, {excess}
Meal and hydration reminders use static copy (no live values).
"""

from src.domain.constants.languages import resolve_app_locale
from src.domain.services.notification_messages_de import NOTIFICATION_MESSAGES_DE
from src.domain.services.notification_messages_es import NOTIFICATION_MESSAGES_ES
from src.domain.services.notification_messages_fr import NOTIFICATION_MESSAGES_FR
from src.domain.services.notification_messages_ja import NOTIFICATION_MESSAGES_JA
from src.domain.services.notification_messages_zh import NOTIFICATION_MESSAGES_ZH

NOTIFICATION_MESSAGES = {
    "en": {
        "male": {
            "meal_reminder": {
                "breakfast": {
                    "body": "Morning, bro! Grab a bite or coffee\nWhen you can — log it 🌅",
                },
                "lunch": {
                    "body": "Lunch o'clock, bro!\nWhat's on the plate? Log it when you can 🥗",
                },
                "dinner": {
                    "body": "Dinner time, bro!\nLog tonight's plate when you can 🌝",
                },
            },
            "daily_summary": {
                "zero_logs": {
                    "body": "Busy day, bro? No stress\nLog one quick meal when you can 📝",
                },
                "on_target": {
                    "body_template": "Crushed it, bro! {percentage}% of your goal\nKeep that momentum going 🎉",
                },
                "under_goal": {
                    "body_template": "Almost there, bro! {deficit} cal left\nA smart snack can close it 💪",
                },
                "slightly_over": {
                    "body_template": "No stress, bro! {excess} cal over\nKeep going and stay consistent 😎",
                },
                "way_over": {
                    "body_template": "All good, bro! {excess} cal over\nTomorrow's a fresh start 🤙",
                },
            },
            "trial_expiry": {
                "2d": {
                    "title": "Nutree",
                    "body": "Heads up, bro — your monthly plan renews in 2 days\nUpgrade to annual and claim this offer",
                },
                "1d": {
                    "title": "Nutree",
                    "body": "Heads up, bro — your monthly plan renews soon\nUpgrade to annual and claim this offer",
                },
            },
            "hydration_reminder": {
                "afternoon": {
                    "body": "Water break, bro?\nA quick sip keeps you sharp 🥤",
                },
            },
            "subscription_hook": {
                "title": "Your Nutree plan is ready",
                "body": "Your personalized nutrition plan is ready, bro\nSubscribe to unlock your next step ✨",
            },
        },
        "female": {
            "meal_reminder": {
                "breakfast": {
                    "body": "Morning, mate! Grab a bite or coffee\nWhen you can — log it 🌅",
                },
                "lunch": {
                    "body": "Lunch o'clock, mate!\nWhat's on the plate? Log it when you can 🥗",
                },
                "dinner": {
                    "body": "Dinner time, mate!\nLog tonight's plate when you can 🌝",
                },
            },
            "daily_summary": {
                "zero_logs": {
                    "body": "Busy day, mate? No stress\nLog one quick meal when you can 📝",
                },
                "on_target": {
                    "body_template": "Crushed it, mate! {percentage}% of your goal\nKeep that momentum going 🎉",
                },
                "under_goal": {
                    "body_template": "Almost there, mate! {deficit} cal left\nA smart snack can close it 💪",
                },
                "slightly_over": {
                    "body_template": "No stress, mate! {excess} cal over\nKeep going and stay consistent 😎",
                },
                "way_over": {
                    "body_template": "All good, mate! {excess} cal over\nTomorrow's a fresh start 🤙",
                },
            },
            "trial_expiry": {
                "2d": {
                    "title": "Nutree",
                    "body": "Heads up, mate — your monthly plan renews in 2 days\nUpgrade to annual and claim this offer",
                },
                "1d": {
                    "title": "Nutree",
                    "body": "Heads up, mate — your monthly plan renews soon\nUpgrade to annual and claim this offer",
                },
            },
            "hydration_reminder": {
                "afternoon": {
                    "body": "Water break, mate?\nA quick sip keeps you sharp 🥤",
                },
            },
            "subscription_hook": {
                "title": "Your Nutree plan is ready",
                "body": "Your personalized nutrition plan is ready, mate\nSubscribe to unlock your next step ✨",
            },
        },
    },
    "vi": {
        "male": {
            "meal_reminder": {
                "breakfast": {
                    "body": "Sáng rồi bro! Ăn nhẹ hay cà phê đi\nNhớ ghi lại nha 🌅",
                },
                "lunch": {
                    "body": "Trưa rồi bro!\nĂn gì thì nhớ ghi lại nha 🥗",
                },
                "dinner": {
                    "body": "Tối rồi bro!\nGhi lại bữa tối khi nào tiện nha 🌝",
                },
            },
            "daily_summary": {
                "zero_logs": {
                    "body": "Bận cả ngày hả bro? Không sao\nTranh thủ ghi lại một bữa nha 📝",
                },
                "on_target": {
                    "body_template": "Đỉnh nóc bro! {percentage}% mục tiêu\nCứ giữ nhịp này nha 🎉",
                },
                "under_goal": {
                    "body_template": "Gần tới rồi bro! Còn {deficit} cal\nĂn nhẹ gì đó đi 💪",
                },
                "slightly_over": {
                    "body_template": "Thoải mái bro! Vượt {excess} cal\nVẫn ổn, tiếp tục nha 😎",
                },
                "way_over": {
                    "body_template": "Không sao bro! Vượt {excess} cal\nMai là ngày mới nha 🤙",
                },
            },
            "trial_expiry": {
                "2d": {
                    "title": "Nutree",
                    "body": "Gói tháng của bro gia hạn sau 2 ngày\nNâng lên gói năm để nhận ưu đãi này",
                },
                "1d": {
                    "title": "Nutree",
                    "body": "Gói tháng của bro sắp gia hạn\nNâng lên gói năm để nhận ưu đãi này",
                },
            },
            "hydration_reminder": {
                "afternoon": {
                    "body": "Uống nước đi bro!\nMột ngụm nhỏ cũng giúp nạp năng lượng 🥤",
                },
            },
            "subscription_hook": {
                "title": "Kế hoạch Nutree đã sẵn sàng",
                "body": "Kế hoạch dinh dưỡng của bro đã sẵn sàng\nĐăng ký để mở khóa bước tiếp theo ✨",
            },
        },
        "female": {
            "meal_reminder": {
                "breakfast": {
                    "body": "Sáng rồi bạn ơi! Ăn nhẹ hay cà phê đi\nNhớ ghi lại nha 🌅",
                },
                "lunch": {
                    "body": "Trưa rồi bạn ơi!\nĂn gì thì nhớ ghi lại nha 🥗",
                },
                "dinner": {
                    "body": "Tối rồi bạn ơi!\nGhi lại bữa tối khi nào tiện nha 🌝",
                },
            },
            "daily_summary": {
                "zero_logs": {
                    "body": "Bận cả ngày hả bạn ơi? Không sao\nTranh thủ ghi lại một bữa nha 📝",
                },
                "on_target": {
                    "body_template": "Đỉnh nóc bạn ơi! {percentage}% mục tiêu\nCứ giữ nhịp này nha 🎉",
                },
                "under_goal": {
                    "body_template": "Gần tới rồi bạn ơi! Còn {deficit} cal\nĂn nhẹ gì đó đi 💪",
                },
                "slightly_over": {
                    "body_template": "Thoải mái bạn ơi! Vượt {excess} cal\nVẫn ổn, tiếp tục nha 😎",
                },
                "way_over": {
                    "body_template": "Không sao bạn ơi! Vượt {excess} cal\nMai là ngày mới nha 🤙",
                },
            },
            "trial_expiry": {
                "2d": {
                    "title": "Nutree",
                    "body": "Gói tháng của bạn gia hạn sau 2 ngày\nNâng lên gói năm để nhận ưu đãi này",
                },
                "1d": {
                    "title": "Nutree",
                    "body": "Gói tháng của bạn sắp gia hạn\nNâng lên gói năm để nhận ưu đãi này",
                },
            },
            "hydration_reminder": {
                "afternoon": {
                    "body": "Uống nước đi bạn ơi!\nMột ngụm nhỏ cũng giúp nạp năng lượng 🥤",
                },
            },
            "subscription_hook": {
                "title": "Kế hoạch Nutree đã sẵn sàng",
                "body": "Kế hoạch dinh dưỡng của bạn đã sẵn sàng\nĐăng ký để mở khóa bước tiếp theo ✨",
            },
        },
    },
    "es": NOTIFICATION_MESSAGES_ES,
    "fr": NOTIFICATION_MESSAGES_FR,
    "de": NOTIFICATION_MESSAGES_DE,
    "ja": NOTIFICATION_MESSAGES_JA,
    "zh": NOTIFICATION_MESSAGES_ZH,
}


def get_messages(language: str, gender: str) -> dict:
    """Get notification messages for language + gender.

    Falls back to: EN male if language/gender combo not found.
    """
    locale_code = resolve_app_locale(language)
    locale = NOTIFICATION_MESSAGES.get(locale_code, NOTIFICATION_MESSAGES["en"])
    return locale.get(gender, locale["male"])
