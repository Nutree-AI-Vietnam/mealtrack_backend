"""Cache keys for food search results."""

from src.app.services.food_search_cache_key import food_search_cache_key


def test_case_and_spacing_do_not_change_the_key():
    assert food_search_cache_key(
        "  Phở   Bò ", "VI", mode="search", limit=20
    ) == food_search_cache_key("phở bò", "vi", mode="search", limit=20)


def test_accents_still_change_the_key():
    assert food_search_cache_key(
        "phở bò", "vi", mode="search", limit=20
    ) != food_search_cache_key("pho bo", "vi", mode="search", limit=20)


def test_mode_limit_and_language_each_change_the_key():
    base = food_search_cache_key("rice", "en", mode="search", limit=20)

    assert food_search_cache_key("rice", "en", mode="autocomplete", limit=20) != base
    assert food_search_cache_key("rice", "en", mode="search", limit=10) != base
    assert food_search_cache_key("rice", "vi", mode="search", limit=20) != base


def test_long_queries_and_languages_fit_the_cache_key_length():
    key = food_search_cache_key(
        "grilled chicken breast with steamed rice " * 10,
        "  Vietnamese ",
        mode="autocomplete",
        limit=100,
    )

    assert key.startswith("v4:vietname:autocomplete:100:")
    assert len(key) <= 64
