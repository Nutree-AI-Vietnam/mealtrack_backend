"""Text clean-up, durations, servings and schema.org recipes."""

import pytest

from src.domain.model.recipe_import.schema_org import find_schema_recipe
from src.domain.model.recipe_import.text import (
    clean_text,
    parse_duration_minutes,
    parse_servings,
)


@pytest.mark.parametrize(
    ("value", "minutes"),
    [
        ("PT1H30M", 90),
        ("P0DT0H20M", 20),
        ("PT90M", 90),
        ("pt45m", 45),
        ("PT30S", 1),
        ("about 20 min", 20),
        ("1 h 15 phút", 75),
        ("1.5 hours", 90),
        (45, 45),
        ("PT0S", None),
        ("P30D", None),
        ("soon", None),
        (None, None),
        (True, None),
    ],
)
def test_duration_minutes(value, minutes):
    assert parse_duration_minutes(value) == minutes


@pytest.mark.parametrize(
    ("value", "servings"),
    [
        ("Serves 4-6", 4),
        (["", "8 servings"], 8),
        (6.0, 6),
        ("Makes 24 cookies", 24),
        ("4 phần", 4),
        (0, None),
        ("500", None),
        (float("nan"), None),
        (None, None),
    ],
)
def test_servings(value, servings):
    assert parse_servings(value) == servings


def test_clean_text_unescapes_strips_tags_and_collapses_space():
    raw = "<p>Fish &amp;amp; <b>chips</b>\r\n\r\n\r\n second\x07 line</p>"

    assert (
        clean_text(raw, max_chars=100, multiline=True) == "Fish & chips\n\nsecond line"
    )
    assert clean_text(raw, max_chars=100) == "Fish & chips second line"


def test_clean_text_truncates_on_a_word_boundary():
    text = clean_text("word " * 50, max_chars=42)

    assert text is not None and text.endswith("…") and len(text) <= 43


def test_clean_text_ignores_non_text_values():
    assert clean_text({"a": 1}, max_chars=10) is None
    assert clean_text("   ", max_chars=10) is None


class TestSchemaOrg:
    def test_finds_a_recipe_inside_a_graph_and_flattens_it(self):
        blocks = [
            {
                "@context": "https://schema.org",
                "@graph": [
                    {"@type": "WebPage", "name": "Page"},
                    {
                        "@type": ["Recipe", "NewsArticle"],
                        "name": "Ph&#7903; b&ograve;",
                        "recipeYield": ["4", "4 servings"],
                        "prepTime": "PT20M",
                        "cookTime": "PT3H",
                        "recipeIngredient": ["1 kg beef bones", "<b>2</b> onions", ""],
                        "recipeInstructions": [
                            {
                                "@type": "HowToSection",
                                "name": "Broth",
                                "itemListElement": [
                                    {"@type": "HowToStep", "text": "Char the onions."},
                                    {"@type": "HowToStep", "text": "Simmer 3 hours."},
                                ],
                            },
                            "Serve hot.",
                        ],
                        "image": [
                            {"@type": "ImageObject", "url": "https://x.com/a.jpg"}
                        ],
                        "author": [{"@type": "Person", "name": "Chef A"}],
                        "recipeCuisine": ["Vietnamese"],
                    },
                ],
            }
        ]

        recipe = find_schema_recipe(blocks)

        assert recipe is not None
        assert recipe.name == "Phở bò"
        assert recipe.ingredients == ("1 kg beef bones", "2 onions")
        assert recipe.instructions == (
            "[Broth]",
            "Char the onions.",
            "Simmer 3 hours.",
            "Serve hot.",
        )
        assert (recipe.servings, recipe.recipe_yield) == (4, "4")
        assert (recipe.prep_time_minutes, recipe.cook_time_minutes) == (20, 180)
        assert recipe.image_url == "https://x.com/a.jpg"
        assert (recipe.author, recipe.cuisine) == ("Chef A", "Vietnamese")
        assert recipe.is_complete

    def test_instruction_text_blocks_split_into_lines(self):
        recipe = find_schema_recipe(
            [{"@type": "http://schema.org/Recipe", "recipeInstructions": "Mix.\nBake."}]
        )

        assert recipe is not None and recipe.instructions == ("Mix.", "Bake.")
        assert not recipe.is_complete

    def test_main_entity_is_searched(self):
        recipe = find_schema_recipe(
            [{"@type": "WebPage", "mainEntity": {"@type": "Recipe", "name": "Soup"}}]
        )

        assert recipe is not None and recipe.name == "Soup"

    def test_no_recipe_returns_none(self):
        assert find_schema_recipe([{"@type": "Article"}, "junk", 3]) is None

    def test_non_http_images_are_ignored(self):
        recipe = find_schema_recipe(
            [{"@type": "Recipe", "image": "data:image/png;base64,AAA"}]
        )

        assert recipe is not None and recipe.image_url is None
