"""Tests for notification message localization."""

import pytest

from src.api.schemas.request.notification_requests import (
    NotificationPreferencesUpdateRequest,
)
from src.domain.services.notification_messages import NOTIFICATION_MESSAGES, get_messages

class TestNotificationMessages:
    def test_all_seven_locales_present(self):
        assert set(NOTIFICATION_MESSAGES.keys()) == {
            "en",
            "vi",
            "es",
            "fr",
            "de",
            "ja",
            "zh",
        }

    @pytest.mark.parametrize("locale", ["es", "fr", "de", "ja", "zh"])
    def test_new_locale_matches_en_structure(self, locale):
        en_male = NOTIFICATION_MESSAGES["en"]["male"]
        locale_male = NOTIFICATION_MESSAGES[locale]["male"]
        assert set(locale_male.keys()) == set(en_male.keys())
        for category, en_blocks in en_male.items():
            assert set(locale_male[category].keys()) == set(en_blocks.keys())

    def test_ja_female_is_not_english(self):
        ja = get_messages("ja", "female")
        en = get_messages("en", "female")
        assert ja["meal_reminder"]["breakfast"]["body"] != en["meal_reminder"]["breakfast"]["body"]
        assert ja["subscription_hook"]["title"] != en["subscription_hook"]["title"]

    def test_unknown_language_falls_back_to_en(self):
        ko = get_messages("ko", "female")
        en = get_messages("en", "female")
        assert ko == en

    def test_es_male_and_female_identical(self):
        es_male = get_messages("es", "male")
        es_female = get_messages("es", "female")
        assert es_male == es_female

    def test_preferences_api_accepts_ja(self):
        req = NotificationPreferencesUpdateRequest(language="ja")
        assert req.language == "ja"

    def test_preferences_api_rejects_unknown_language(self):
        with pytest.raises(ValueError, match="Unsupported notification language"):
            NotificationPreferencesUpdateRequest(language="ko")
