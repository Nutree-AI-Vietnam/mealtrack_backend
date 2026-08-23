from src.app.services.serving_label import (
    apply_serving_labels,
    leftover_serving_phrases,
    needs_serving_label,
    overlay_serving_labels,
    serving_phrase_key,
)


def test_metric_units_do_not_need_labels():
    assert needs_serving_label("g", "vi") is False
    assert needs_serving_label("ml", "vi") is False


def test_exact_fatsecret_phrase_is_leftover():
    options = [{"unit": "cup, cooked, diced", "description": "1 cup cooked, diced"}]
    assert leftover_serving_phrases(options, "vi") == ["cup, cooked, diced"]


def test_english_cached_display_description_is_still_leftover():
    options = [
        {
            "unit": 'thin slice (approx 2" x 1-1/2" x 1/8")',
            "display_description": 'thin slice (approx 2" x 1-1/2" x 1/8")',
        }
    ]
    assert leftover_serving_phrases(options, "vi") == [options[0]["unit"]]


def test_apply_serving_labels_does_not_rewrite_unit():
    options = [
        {
            "unit": "cup, cooked, diced",
            "gram_weight": 158.0,
            "description": "1 cup cooked, diced",
        }
    ]
    labeled = apply_serving_labels(
        options,
        {serving_phrase_key("cup, cooked, diced"): "cốc, đã nấu, thái hạt lựu"},
        "vi",
    )
    assert labeled[0]["unit"] == "cup, cooked, diced"
    assert labeled[0]["description"] == "1 cup cooked, diced"
    assert labeled[0]["display_description"] == "cốc, đã nấu, thái hạt lựu"


def test_vietnamese_canonical_serving_label_overrides_stale_translation():
    labeled = apply_serving_labels(
        [{"unit": "serving", "gram_weight": 98.0, "description": "1 serving"}],
        {serving_phrase_key("serving"): "Phần ăn"},
        "vi",
    )

    assert labeled[0]["unit"] == "serving"
    assert labeled[0]["description"] == "1 serving"
    assert labeled[0]["display_description"] == "Khẩu phần"


def test_vietnamese_canonical_label_handles_verbose_provider_phrase():
    labeled = apply_serving_labels(
        [
            {
                "unit": 'thin slice (approx 2" x 1-1/2" x 1/8")',
                "gram_weight": 12.0,
                "description": '1 thin slice (approx 2" x 1-1/2" x 1/8")',
            }
        ],
        {},
        "vi",
    )

    assert labeled[0]["display_description"] == "Lát mỏng"


def test_overlay_serving_labels_uses_catalog_name_vi():
    options = [{"unit": "slice", "gram_weight": 30.0, "description": "1 slice"}]
    overlay = overlay_serving_labels(options, {serving_phrase_key("slice"): "lát"})
    assert overlay[0]["display_description"] == "lát"
    assert overlay[0]["unit"] == "slice"


def test_overlay_replaces_stale_vietnamese_canonical_serving_label():
    overlay = overlay_serving_labels(
        [
            {
                "unit": "serving",
                "gram_weight": 98.0,
                "description": "1 serving",
                "display_description": "Phần ăn",
            }
        ],
        {serving_phrase_key("serving"): "Phần ăn"},
        language="vi",
    )

    assert overlay[0]["display_description"] == "Khẩu phần"
