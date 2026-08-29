"""
Notification message templates keyed by language and gender.

Time Sensitive notifications use a title plus body field.
Bodies intentionally include one line break so iOS renders richer copy across
two lines without leaving the emoji alone on a third line.

Gender-aware buddy terms:
- EN: male="bro", female="mate"
- VI: male="bro", female="bạn ơi"

Placeholders:
- Meal reminders (lunch/dinner): {remaining} for remaining calories
- Daily summary: {percentage}, {deficit}, {excess}
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
                    "body_template": "Lunch o'clock, bro! {remaining} cal left\nWhat's going on the plate? 🥗",
                },
                "dinner": {
                    "body_template": "Dinner time, bro! {remaining} cal left\nMake it count tonight 🌙",
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
                    "body": "Heads up, bro — trial ends in 2 days\nLock in your streak ⏳",
                },
                "1d": {
                    "title": "Nutree",
                    "body": "Heads up, bro — your free trial ends soon\nKeep your progress going 🔥",
                },
            },
            "hydration_reminder": {
                "afternoon": {
                    "body_template": "Halfway there, bro! {consumed_ml}ml down, {remaining_ml}ml to go\nStay hydrated 💧",
                },
                "evening": {
                    "body_template": "Almost there, bro! {consumed_ml}ml logged today\nJust {remaining_ml}ml left to hit your goal 💧",
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
                    "body_template": "Lunch o'clock, mate! {remaining} cal left\nWhat's going on the plate? 🥗",
                },
                "dinner": {
                    "body_template": "Dinner time, mate! {remaining} cal left\nMake it count tonight 🌙",
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
                    "body": "Heads up, mate — trial ends in 2 days\nLock in your streak ⏳",
                },
                "1d": {
                    "title": "Nutree",
                    "body": "Heads up, mate — your free trial ends soon\nKeep your progress going 🔥",
                },
            },
            "hydration_reminder": {
                "afternoon": {
                    "body_template": "Halfway there, mate! {consumed_ml}ml down, {remaining_ml}ml to go\nStay hydrated 💧",
                },
                "evening": {
                    "body_template": "Almost there, mate! {consumed_ml}ml logged today\nJust {remaining_ml}ml left to hit your goal 💧",
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
                    "body_template": "Trưa rồi bro! Còn {remaining} cal\nĂn gì cho ngon đây? 🥗",
                },
                "dinner": {
                    "body_template": "Tối rồi bro! Còn {remaining} cal\nĂn gì cho đúng mục tiêu? 🌙",
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
                    "body": "Trial còn 2 ngày là hết hạn nha bro\nGiữ streak tiếp nào ⏳",
                },
                "1d": {
                    "title": "Nutree",
                    "body": "Sắp hết free trial rồi bro\nĐừng để mất tiến độ nha 🔥",
                },
            },
            "hydration_reminder": {
                "afternoon": {
                    "body_template": "Giữa ngày rồi bro! Uống thêm {remaining_ml}ml nữa nhé\nHôm nay uống được {consumed_ml}ml rồi đó 💧",
                },
                "evening": {
                    "body_template": "Chiều tà rồi bro! Uống được {consumed_ml}ml rồi, còn {remaining_ml}ml nữa là đủ nước\nCố lên nha 💧",
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
                    "body_template": "Trưa rồi bạn ơi! Còn {remaining} cal\nĂn gì cho ngon đây? 🥗",
                },
                "dinner": {
                    "body_template": "Tối rồi bạn ơi! Còn {remaining} cal\nĂn gì cho đúng mục tiêu? 🌙",
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
                    "body": "Trial còn 2 ngày là hết hạn nha bạn ơi\nGiữ streak tiếp nào ⏳",
                },
                "1d": {
                    "title": "Nutree",
                    "body": "Sắp hết free trial rồi bạn ơi\nĐừng để mất tiến độ nha 🔥",
                },
            },
            "hydration_reminder": {
                "afternoon": {
                    "body_template": "Giữa ngày rồi bạn ơi! Uống thêm {remaining_ml}ml nữa nhé\nHôm nay uống được {consumed_ml}ml rồi đó 💧",
                },
                "evening": {
                    "body_template": "Chiều tà rồi bạn ơi! Uống được {consumed_ml}ml rồi, còn {remaining_ml}ml nữa là đủ nước\nCố lên nha 💧",
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
