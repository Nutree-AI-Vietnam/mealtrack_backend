"""Localized copy for server-sent engagement pushes."""

import unicodedata

import pytest

from src.domain.constants.languages import ENABLED_APP_LOCALES
from src.domain.model.notification import EngagementPushKind, engagement_push_copy

BILLING = EngagementPushKind.BILLING_ISSUE
EN = (
    "Payment didn't go through",
    "Update your payment method to keep your Nutree plan running.",
)
VI = (
    "Thanh toán chưa thành công",
    "Cập nhật phương thức thanh toán để gói Nutree không bị gián đoạn nhé.",
)


@pytest.mark.unit
class TestEngagementPushCopy:
    def test_english(self):
        assert engagement_push_copy(BILLING, language="en") == EN

    def test_vietnamese(self):
        assert engagement_push_copy(BILLING, language="vi") == VI

    @pytest.mark.parametrize("language", sorted(ENABLED_APP_LOCALES))
    def test_every_app_locale_has_its_own_copy(self, language):
        title, body = engagement_push_copy(BILLING, language=language)

        assert title.strip() and body.strip()
        if language != "en":
            assert (title, body) != EN

    @pytest.mark.parametrize("language", ["ko", "pt", None, "", "xx-YY"])
    def test_other_languages_fall_back_to_english(self, language):
        assert engagement_push_copy(BILLING, language=language) == EN

    @pytest.mark.parametrize(
        ("language", "expected"), [("vi-VN", VI), ("VI", VI), ("en_GB", EN)]
    )
    def test_region_tags_and_case_are_ignored(self, language, expected):
        assert engagement_push_copy(BILLING, language=language) == expected

    @pytest.mark.parametrize("kind", list(EngagementPushKind))
    @pytest.mark.parametrize("language", sorted(ENABLED_APP_LOCALES))
    def test_copy_is_neutral(self, kind, language):
        text = " ".join(engagement_push_copy(kind, language=language))

        # No emoji or other pictographs.
        assert not [ch for ch in text if unicodedata.category(ch) == "So"]
        # No buddy word or coach persona.
        lowered = text.lower()
        for word in ("bro", "mate", "bạn ơi", "coach"):
            assert word not in lowered
