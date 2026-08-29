"""Tests for shared locale constants."""

import pytest

from src.api.schemas.request.notification_requests import (
    NotificationPreferencesUpdateRequest,
)
from src.domain.constants.languages import (
    ENABLED_APP_LOCALES,
    SUPPORTED_TRANSLATION_LANGUAGES,
    resolve_app_locale,
)


class TestLanguageConstants:
    def test_enabled_app_locales_matches_translation_set(self):
        assert ENABLED_APP_LOCALES == SUPPORTED_TRANSLATION_LANGUAGES
        assert len(ENABLED_APP_LOCALES) == 7

    def test_supported_translation_languages_has_seven_locales(self):
        assert SUPPORTED_TRANSLATION_LANGUAGES == frozenset(
            {"en", "vi", "es", "fr", "de", "ja", "zh"}
        )

    def test_notification_languages_match_translation_set(self):
        assert SUPPORTED_TRANSLATION_LANGUAGES == frozenset(
            {"en", "vi", "es", "fr", "de", "ja", "zh"}
        )

    def test_preferences_validator_uses_enabled_app_locales(self):
        req = NotificationPreferencesUpdateRequest(language="fr")
        assert req.language == "fr"

        with pytest.raises(ValueError):
            NotificationPreferencesUpdateRequest(language="ko")

    def test_resolve_app_locale_clamps_unknown_codes(self):
        assert resolve_app_locale("ja") == "ja"
        assert resolve_app_locale("zh-Hans") == "zh"
        assert resolve_app_locale("ko") == "en"
        assert resolve_app_locale(None) == "en"
