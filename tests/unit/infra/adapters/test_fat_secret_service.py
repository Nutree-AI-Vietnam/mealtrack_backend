from unittest.mock import AsyncMock, Mock

import pytest

import src.infra.adapters.fat_secret_service as fat_secret_module
from src.infra.adapters.fat_secret_service import FatSecretService


class _Response:
    status_code = 200
    text = '{"ok": true}'

    def json(self):
        return {"ok": True}


class _Client:
    is_closed = False

    def __init__(self):
        self.post_calls = []

    async def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        return _Response()


@pytest.mark.unit
def test_fatsecret_provider_is_optional_when_credentials_are_missing(monkeypatch):
    monkeypatch.setattr(fat_secret_module, "_fat_secret_service", None)
    monkeypatch.setattr(fat_secret_module, "_fat_secret_service_initialized", False)
    monkeypatch.setattr(fat_secret_module.settings, "FATSECRET_CLIENT_ID", None)
    monkeypatch.setattr(fat_secret_module.settings, "FATSECRET_CLIENT_SECRET", None)
    warning = Mock()
    monkeypatch.setattr(fat_secret_module.logger, "warning", warning)

    assert fat_secret_module.get_fat_secret_service() is None
    assert fat_secret_module.get_fat_secret_service() is None
    warning.assert_called_once_with(
        "fatsecret credentials not configured; provider will be skipped"
    )


@pytest.mark.unit
def test_fatsecret_serving_units_preserve_fatsecret_order():
    service = FatSecretService("client", "secret")
    food = {
        "servings": {
            "serving": [
                {
                    "measurement_description": "serving",
                    "metric_serving_amount": "30.000",
                    "serving_description": "1 cup",
                },
                {
                    "measurement_description": "g",
                    "metric_serving_amount": "100.000",
                    "serving_description": "100 g",
                },
            ]
        }
    }

    units = service._extract_serving_units(food)

    assert units == [
        {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
        {"unit": "serving", "gram_weight": 30.0, "description": "1 cup"},
        {"unit": "serving", "gram_weight": 100.0, "description": "100 g"},
    ]


@pytest.mark.unit
def test_fatsecret_nutrition_prefers_100g_serving():
    service = FatSecretService("client", "secret")
    food = {
        "servings": {
            "serving": [
                {
                    "measurement_description": "serving",
                    "metric_serving_amount": "30.000",
                    "serving_description": "1 cup",
                    "calories": "100",
                    "protein": "3",
                    "carbohydrate": "20",
                    "fat": "2",
                },
                {
                    "measurement_description": "g",
                    "metric_serving_amount": "100.000",
                    "serving_description": "100 g",
                    "calories": "333",
                    "protein": "10",
                    "carbohydrate": "66.67",
                    "fat": "6.67",
                },
            ]
        }
    }

    nutrition = service._extract_nutrition_from_details(food)

    assert nutrition["calories_100g"] == pytest.approx(366.71)
    assert nutrition["protein_100g"] == 10.0
    assert nutrition["carbs_100g"] == 66.67
    assert nutrition["fat_100g"] == 6.67
    assert nutrition["allowed_units"][0] == {
        "unit": "g",
        "gram_weight": 1.0,
        "description": "1 g",
    }
    assert nutrition["allowed_units"][1]["description"] == "1 cup"


@pytest.mark.unit
def test_fatsecret_nutrition_rejects_missing_metric_basis():
    service = FatSecretService("client", "secret")
    nutrition = service._extract_nutrition_from_details(
        {
            "servings": {
                "serving": {
                    "measurement_description": "serving",
                    "calories": "100",
                    "protein": "3",
                    "carbohydrate": "20",
                    "fat": "2",
                }
            }
        }
    )

    assert nutrition["metric_serving_amount"] is None
    assert nutrition["protein_100g"] is None


@pytest.mark.unit
def test_fatsecret_nutrition_rejects_missing_metric_basis():
    service = FatSecretService("client", "secret")
    nutrition = service._extract_nutrition_from_details(
        {
            "servings": {
                "serving": {
                    "measurement_description": "serving",
                    "calories": "100",
                    "protein": "3",
                    "carbohydrate": "20",
                    "fat": "2",
                }
            }
        }
    )

    assert nutrition["metric_serving_amount"] is None
    assert nutrition["protein_100g"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fatsecret_api_request_posts_form_params():
    service = FatSecretService("client", "secret")
    service._get_access_token = AsyncMock(return_value="token")
    client = _Client()
    service._client = client

    result = await service._api_request(
        "POST",
        params={"method": "foods.search.v5", "format": "json"},
    )

    assert result == {"ok": True}
    _, kwargs = client.post_calls[0]
    assert kwargs["data"] == {"method": "foods.search.v5", "format": "json"}
    assert "json" not in kwargs
    assert kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fatsecret_search_uses_v5_methods():
    service = FatSecretService("client", "secret")
    service._api_request = AsyncMock(
        side_effect=[
            {
                "foods_search": {
                    "results": {
                        "food": [
                            {
                                "food_id": "50953",
                                "food_name": "Whole Grain Cheerios",
                                "brand_name": "General Mills",
                            }
                        ]
                    }
                }
            },
            {
                "food": {
                    "food_id": "50953",
                    "food_name": "Whole Grain Cheerios",
                    "servings": {
                        "serving": {
                            "measurement_description": "g",
                            "metric_serving_amount": "100.000",
                            "serving_description": "100 g",
                            "calories": "333",
                            "protein": "10",
                            "carbohydrate": "66.67",
                            "fat": "6.67",
                        }
                    },
                }
            },
        ]
    )

    results = await service.search_foods("cheerios", max_results=1)

    search_call = service._api_request.call_args_list[0]
    detail_call = service._api_request.call_args_list[1]
    search_params = search_call.kwargs["params"]
    detail_params = detail_call.kwargs["params"]
    assert search_call.args[0] == "POST"
    assert detail_call.args[0] == "POST"
    assert search_params["method"] == "foods.search.v5"
    assert search_params["flag_default_serving"] == "true"
    assert detail_params["method"] == "food.get.v5"
    assert results[0]["allowed_units"][0]["gram_weight"] == 1.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fatsecret_search_candidates_does_not_fetch_details():
    service = FatSecretService("client", "secret")
    service._api_request = AsyncMock(
        return_value={
            "foods_search": {
                "results": {
                    "food": [
                        {
                            "food_id": "50953",
                            "food_name": "Whole Grain Cheerios",
                            "brand_name": "General Mills",
                        }
                    ]
                }
            }
        }
    )

    results = await service.search_food_candidates("cheerios", max_results=1)

    service._api_request.assert_awaited_once()
    assert (
        service._api_request.await_args.kwargs["params"]["method"] == "foods.search.v5"
    )
    assert results == [
        {
            "description": "Whole Grain Cheerios",
            "brand": "General Mills",
            "food_description": "",
            "source": "fatsecret",
            "food_id": "50953",
            "origin": "provider",
            "source_namespace": "fatsecret",
            "source_food_id": "50953",
            "allowed_units": [
                {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
                {"unit": "serving", "gram_weight": 100.0, "description": "100 g"},
            ],
        }
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fatsecret_search_candidates_logs_error_code(caplog):
    service = FatSecretService("client", "secret")
    service._api_request = AsyncMock(
        return_value={"error": {"code": 21, "message": "Invalid OAuth signature"}}
    )

    with caplog.at_level("WARNING"):
        results = await service.search_food_candidates("grilled pork ribs")

    assert results == []
    assert any("code=21" in record.message for record in caplog.records)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fatsecret_get_food_details_fetches_one_selected_candidate():
    service = FatSecretService("client", "secret")
    service._api_request = AsyncMock(
        return_value={
            "food": {
                "food_id": "50953",
                "food_name": "Whole Grain Cheerios",
                "brand_name": "General Mills",
                "servings": {
                    "serving": {
                        "measurement_description": "g",
                        "metric_serving_amount": "100.000",
                        "serving_description": "100 g",
                        "calories": "333",
                        "protein": "10",
                        "carbohydrate": "66.67",
                        "fat": "6.67",
                    }
                },
            }
        }
    )

    result = await service.get_food_details("50953")

    service._api_request.assert_awaited_once()
    params = service._api_request.await_args.kwargs["params"]
    assert params["method"] == "food.get.v5"
    assert params["food_id"] == "50953"
    assert result["protein_100g"] == 10.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fatsecret_search_skips_details_when_search_includes_servings():
    service = FatSecretService("client", "secret")
    service._api_request = AsyncMock(
        return_value={
            "foods_search": {
                "results": {
                    "food": [
                        {
                            "food_id": "50953",
                            "food_name": "Whole Grain Cheerios",
                            "brand_name": "General Mills",
                            "servings": {
                                "serving": {
                                    "measurement_description": "g",
                                    "metric_serving_amount": "100.000",
                                    "serving_description": "100 g",
                                    "calories": "333",
                                    "protein": "10",
                                    "carbohydrate": "66.67",
                                    "fat": "6.67",
                                }
                            },
                        }
                    ]
                }
            }
        }
    )

    results = await service.search_foods("cheerios", max_results=1)

    service._api_request.assert_awaited_once()
    assert (
        service._api_request.await_args.kwargs["params"]["method"] == "foods.search.v5"
    )
    assert results[0]["protein_100g"] == 10.0
    assert results[0]["carbs_100g"] == pytest.approx(66.67)
    assert results[0]["fat_100g"] == pytest.approx(6.67)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fatsecret_barcode_lookup_uses_method_based_endpoint():
    service = FatSecretService("client", "secret")
    service._api_request = AsyncMock(
        side_effect=[
            {"food_id": "50953"},
            {
                "food": {
                    "food_id": "50953",
                    "food_name": "Whole Grain Cheerios",
                    "servings": {
                        "serving": {
                            "measurement_description": "g",
                            "metric_serving_amount": "100.000",
                            "serving_description": "100 g",
                            "calories": "333",
                            "protein": "10",
                            "carbohydrate": "66.67",
                            "fat": "6.67",
                        }
                    },
                }
            },
        ]
    )

    result = await service.get_product("12345678")

    barcode_call = service._api_request.call_args_list[0]
    detail_call = service._api_request.call_args_list[1]
    detail_params = detail_call.kwargs["params"]
    assert barcode_call.args[0] == "POST"
    assert barcode_call.kwargs["params"]["method"] == "food.find_id_for_barcode"
    assert detail_call.args[0] == "POST"
    assert detail_params["method"] == "food.get.v5"
    assert result["name"] == "Whole Grain Cheerios"
    assert result["origin"] == "provider"
    assert result["source_namespace"] == "fatsecret"
    assert result["source_food_id"] == "50953"
    assert result["allowed_units"][0] == {
        "unit": "g",
        "gram_weight": 1.0,
        "description": "1 g",
    }


@pytest.mark.unit
def test_map_search_result_parses_per_100g_description():
    service = FatSecretService("client", "secret")
    mapped = service._map_search_result(
        {
            "food_id": "1",
            "food_name": "Egg",
            "food_description": (
                "Per 100g - Calories: 155kcal | Fat: 11g | Carbs: 1.1g | Protein: 13g"
            ),
        }
    )
    assert mapped["protein_100g"] == 13.0
    assert mapped["carbs_100g"] == 1.1
    assert mapped["fat_100g"] == 11.0
    assert mapped["calories_100g"] == 155.0
    assert mapped.get("metric_serving_amount") is None


@pytest.mark.unit
def test_map_search_result_ignores_per_serving_description_as_100g():
    service = FatSecretService("client", "secret")
    mapped = service._map_search_result(
        {
            "food_id": "1",
            "food_name": "Bar",
            "food_description": (
                "Per Serving - Calories: 200kcal | Fat: 8g | Carbs: 20g | Protein: 10g"
            ),
        }
    )
    assert mapped.get("protein_100g") is None
    assert mapped.get("calories_100g") is None
