from src.domain.constants.languages import SUPPORTED_TRANSLATION_LANGUAGES
from src.domain.services.progress_recap_i18n import recap_locale, recap_text
from src.domain.services.progress_recap_i18n_en_vi import EN, VI
from src.domain.services.progress_recap_i18n_es_fr_de import DE, ES, FR
from src.domain.services.progress_recap_i18n_ja_zh import JA, ZH

_TABLES = {"en": EN, "vi": VI, "es": ES, "fr": FR, "de": DE, "ja": JA, "zh": ZH}


def test_recap_locale_uses_app_language() -> None:
    assert recap_locale("vi-VN,vi;q=0.9") == "vi"
    assert recap_locale("en-US") == "en"
    assert recap_locale("ko") == "en"


def test_fallback_tables_cover_every_app_locale() -> None:
    assert set(_TABLES) == set(SUPPORTED_TRANSLATION_LANGUAGES)
    for table in _TABLES.values():
        assert set(table) == set(EN)


def test_vietnamese_protein_line_is_not_english() -> None:
    text = recap_text("vi", "protein_low", gap=40)
    assert "Đạm" in text
    assert "Protein is short" not in text
