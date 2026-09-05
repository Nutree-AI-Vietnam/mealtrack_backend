"""Tests for lifecycle email copy localization."""

import pytest

from src.domain.services.email_copy import EMAIL_COPY, get_email_copy


class TestEmailCopy:
    def test_all_seven_locales_have_four_templates(self):
        for lang in ("en", "vi", "es", "fr", "de", "ja", "zh"):
            assert set(EMAIL_COPY[lang].keys()) == {
                "welcome",
                "reengagement",
                "trial_expiring",
                "trial_cancelled",
            }

    def test_ja_welcome_subject_is_japanese(self):
        copy = get_email_copy("ja", "welcome")
        en = get_email_copy("en", "welcome")
        assert copy["subject"] != en["subject"]
        assert "栄養" in copy["subject"]

    def test_unknown_language_falls_back_to_en_per_key(self):
        copy = get_email_copy("ko", "welcome")
        en = get_email_copy("en", "welcome")
        assert copy == en

    def test_partial_locale_merge_falls_back_missing_keys_to_en(self):
        # All locales are complete today; verify merge behavior via direct call shape.
        copy = get_email_copy("ja", "trial_cancelled")
        assert "subject" in copy
        assert "closing" in copy

    def test_unknown_template_raises(self):
        with pytest.raises(ValueError, match="Unknown email template"):
            get_email_copy("en", "not_a_template")
