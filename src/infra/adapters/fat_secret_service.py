"""
fatsecret API HTTP client.
Provides product lookup by barcode and food search using OAuth 2.0.
"""

import asyncio
import base64
import logging
import math
import re
import time
from typing import Any

import httpx

from src.domain.constants.fatsecret_locale import LANGUAGE_TO_REGION
from src.domain.services.fatsecret_description_nutrition import (
    description_macros_as_100g,
)
from src.domain.services.nutrition_integrity_policy import (
    NutritionIntegrityPolicy,
    normalize_serving_options,
)
from src.infra.config.settings import settings

logger = logging.getLogger(__name__)

# fatsecret API endpoints
FATSECRET_TOKEN_URL = "https://oauth.fatsecret.com/connect/token"
FATSECRET_API_BASE = "https://platform.fatsecret.com/rest/server.api"

# Barcode validation pattern (8-14 digits)
BARCODE_PATTERN = re.compile(r"^\d{8,14}$")


def _has_search_macros(food: dict[str, Any]) -> bool:
    return all(
        food.get(field) is not None
        for field in ("protein_100g", "carbs_100g", "fat_100g")
    )


class FatSecretService:
    """HTTP client for fatsecret API with OAuth 2.0."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        integrity_policy: NutritionIntegrityPolicy | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token: str | None = None
        self._token_expires_at: float = 0
        self._client: httpx.AsyncClient | None = None
        self._integrity_policy = integrity_policy or NutritionIntegrityPolicy()

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _get_access_token(self) -> str | None:
        """Get OAuth 2.0 access token, refreshing if needed."""
        # Check if token is still valid (with 60s buffer)
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        # Get new token
        try:
            credentials = f"{self.client_id}:{self.client_secret}"
            b64_credentials = base64.b64encode(credentials.encode()).decode()

            headers = {
                "Authorization": f"Basic {b64_credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            }

            data = {"grant_type": "client_credentials"}

            client = await self._get_client()
            response = await client.post(
                FATSECRET_TOKEN_URL, headers=headers, data=data
            )

            if response.status_code != 200:
                logger.warning(
                    "fatsecret token request failed: status=%s",
                    response.status_code,
                )
                return None

            token_data = response.json()
            self._access_token = token_data.get("access_token")
            expires_in = token_data.get("expires_in", 3600)
            self._token_expires_at = time.time() + expires_in

            return self._access_token
        except Exception as exc:
            logger.warning("fatsecret OAuth error: %s", type(exc).__name__)
            return None

    async def _api_request(
        self,
        method: str,
        endpoint: str = "",
        params: dict | None = None,
        base_url: str = FATSECRET_API_BASE,
    ) -> dict | None:
        """Make authenticated API request."""
        token = await self._get_access_token()
        if not token:
            return None

        url = base_url if not endpoint else f"{base_url}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        try:
            client = await self._get_client()
            if method.upper() == "GET":
                response = await client.get(url, headers=headers, params=params)
            else:
                response = await client.post(url, headers=headers, data=params)

            if response.status_code != 200:
                logger.warning(
                    "fatsecret API error: status=%s",
                    response.status_code,
                )
                return None

            try:
                return response.json()
            except ValueError:
                logger.warning(
                    "fatsecret API returned non-JSON response",
                )
                return None
        except httpx.HTTPError as exc:
            logger.warning("fatsecret request error: %s", type(exc).__name__)
            return None

    async def get_product(
        self,
        barcode: str,
        region: str = "US",
        language: str = "en",
    ) -> dict[str, Any] | None:
        """Fetch product by barcode from fatsecret."""
        # Validate barcode format
        if not BARCODE_PATTERN.match(barcode):
            logger.warning("Invalid barcode format: type=%s", type(barcode).__name__)
            return None

        try:
            normalized_barcode = barcode.zfill(13)
            params = {
                "method": "food.find_id_for_barcode",
                "barcode": normalized_barcode,
                "format": "json",
                "region": region,
                "language": language,
            }
            result = await self._api_request("POST", params=params)
            if not result:
                return None

            food_id = result.get("food_id")
            if not food_id:
                return None

            # Get food details
            detail_params = {
                "method": "food.get.v5",
                "food_id": food_id,
                "format": "json",
                "region": region,
                "language": language,
                "flag_default_serving": "true",
            }
            food_details = await self._api_request("POST", params=detail_params)
            if not food_details:
                return None

            food_data = food_details.get("food", food_details)
            return self._map_product(
                food_data,
                normalized_barcode,
                source_food_id=str(food_id),
            )
        except Exception as e:
            logger.warning(
                "fatsecret API error for barcode lookup: %s", type(e).__name__
            )
            return None

    async def search_foods(
        self,
        query: str,
        max_results: int = 10,
        region: str = "US",
        language: str = "en",
    ) -> list[dict[str, Any]]:
        """Search foods by query string with nutrition data."""
        try:
            foods = await self.search_food_candidates(
                query,
                max_results=max_results,
                region=region,
                language=language,
            )

            # Process each food, fetching detailed nutrition concurrently to
            # avoid N+1 sequential round trips (one food.get per search result).
            async def _process(food: dict) -> dict:
                food_id = food.get("food_id")
                mapped = dict(food)
                if food_id and not _has_search_macros(mapped):
                    try:
                        details = await self.get_food_details(
                            food_id,
                            region=region,
                            language=language,
                        )
                        if details:
                            mapped.update(details)
                    except Exception:
                        pass

                return mapped

            # gather preserves input order
            processed = await asyncio.gather(*[_process(food) for food in foods])
            return list(processed)
        except Exception as e:
            logger.warning("fatsecret search error: %s", type(e).__name__)
            return []

    async def search_food_candidates(
        self,
        query: str,
        max_results: int = 10,
        region: str = "US",
        language: str = "en",
    ) -> list[dict[str, Any]]:
        """Search foods without fetching detail nutrition for every result."""
        params = {
            "method": "foods.search.v5",
            "search_expression": query,
            "max_results": max_results,
            "page_number": 0,
            "format": "json",
            "region": region,
            "language": language,
            "flag_default_serving": "true",
        }
        result = await self._api_request("POST", params=params)
        if not result:
            return []

        foods = self._extract_foods_search_results(result)
        if not foods:
            error = result.get("error")
            if error:
                error_code = (
                    error.get("code")
                    if isinstance(error, dict)
                    else type(error).__name__
                )
                logger.warning(
                    "fatsecret API error response received: code=%s", error_code
                )
            else:
                logger.warning(
                    "fatsecret returned no foods: response_keys=%s",
                    sorted(str(key) for key in result.keys()),
                )
            return []

        return [self._map_search_result(food) for food in foods]

    async def get_food_details(
        self,
        food_id: str,
        region: str = "US",
        language: str = "en",
    ) -> dict[str, Any] | None:
        """Fetch detailed nutrition for one selected fatsecret food candidate."""
        detail_params = {
            "method": "food.get.v5",
            "food_id": food_id,
            "format": "json",
            "region": region,
            "language": language,
            "flag_default_serving": "true",
        }
        details = await self._api_request("POST", params=detail_params)
        if not details:
            return None

        food_data = details.get("food", details)
        mapped = self._map_search_result(food_data)
        mapped.update(self._extract_nutrition_from_details(food_data))
        return mapped

    def _extract_foods_search_results(self, result: dict[str, Any]) -> list[dict]:
        """Extract search result foods across fatsecret response shapes."""
        foods = result.get("foods", {}).get("food", [])
        if not foods:
            foods = result.get("foods_search", {}).get("results", {}).get("food", [])
        if isinstance(foods, dict):
            foods = [foods]
        return foods if isinstance(foods, list) else []

    def _extract_serving_units(self, food: dict) -> list[dict]:
        """Extract all serving units from fatsecret food details."""
        servings = food.get("servings", {}).get("serving", [])
        if not servings:
            return self._default_allowed_units()

        if isinstance(servings, dict):
            servings = [servings]

        units = []
        seen = set()
        for s in servings:
            unit = str(s.get("measurement_description") or "").strip()
            description = str(s.get("serving_description") or "").strip()
            if any(ord(char) < 32 for char in f"{unit}{description}"):
                continue
            gram_weight = self._safe_float(s.get("metric_serving_amount"))
            if unit and len(unit) <= 100 and gram_weight and gram_weight > 0:
                unit_key = unit.lower().strip()
                if unit_key in seen:
                    continue
                seen.add(unit_key)
                units.append(
                    {
                        "unit": unit,
                        "gram_weight": gram_weight,
                        "description": description[:100],
                    }
                )
            if len(units) >= 12:
                break

        return (
            normalize_serving_options(units, provider_100g_label=True)
            or self._default_allowed_units()
        )

    def _select_per_100g_serving(self, food: dict[str, Any]) -> dict | None:
        servings = food.get("servings", {}).get("serving", [])
        if isinstance(servings, dict):
            servings = [servings]
        if not isinstance(servings, list) or not servings:
            return None

        for serving in servings:
            metric_amount = self._safe_float(serving.get("metric_serving_amount"))
            measurement = str(serving.get("measurement_description") or "").lower()
            if measurement == "g" and metric_amount == 100:
                return serving
        return servings[0] if isinstance(servings[0], dict) else None

    def _extract_nutrition_from_details(self, food: dict[str, Any]) -> dict[str, Any]:
        """Extract per-100g nutrition from fatsecret food details."""
        serving = self._select_per_100g_serving(food)

        if not isinstance(serving, dict):
            return {"allowed_units": self._default_allowed_units()}

        # Get metric serving amount for per-100g calculation
        metric_amount = self._safe_float(serving.get("metric_serving_amount"))
        if metric_amount is None or metric_amount <= 0:
            return {
                "metric_serving_amount": None,
                "calories_100g": None,
                "protein_100g": None,
                "carbs_100g": None,
                "fat_100g": None,
                "fiber_100g": None,
                "sugar_100g": None,
                "allowed_units": self._extract_serving_units(food),
            }

        return self._apply_integrity_policy(
            {
                "metric_serving_amount": metric_amount,
                "calories_100g": self._calc_per_100g(
                    serving.get("calories"), metric_amount
                ),
                "protein_100g": self._calc_per_100g(
                    serving.get("protein"), metric_amount
                ),
                "carbs_100g": self._calc_per_100g(
                    serving.get("carbohydrate"), metric_amount
                ),
                "fat_100g": self._calc_per_100g(serving.get("fat"), metric_amount),
                "fiber_100g": self._calc_per_100g(serving.get("fiber"), metric_amount),
                "sugar_100g": self._calc_per_100g(serving.get("sugar"), metric_amount),
                "serving_description": serving.get("serving_description"),
                "allowed_units": self._extract_serving_units(food),
            },
            require_metric_basis=True,
        )

    def _default_allowed_units(self) -> list[dict]:
        """Return default allowed units when none are provided."""
        return normalize_serving_options(
            [{"unit": "g", "gram_weight": 100.0, "description": "100 g"}],
            provider_100g_label=True,
        ) or [{"unit": "g", "gram_weight": 1.0, "description": "1 g"}]

    def _map_product(
        self,
        food: dict[str, Any],
        barcode: str,
        source_food_id: str | None = None,
    ) -> dict[str, Any]:
        """Map fatsecret response to clean dict."""
        resolved_source_food_id = str(
            source_food_id or food.get("food_id") or ""
        ).strip()
        provider_identity: dict[str, str] = {}
        if resolved_source_food_id:
            provider_identity = {
                "origin": "provider",
                "source_namespace": "fatsecret",
                "source_food_id": resolved_source_food_id,
            }
        serving = self._select_per_100g_serving(food)
        if not isinstance(serving, dict):
            return {
                "name": food.get("food_name", ""),
                "brand": food.get("brand_name"),
                "barcode": barcode,
                **provider_identity,
                "calories_100g": None,
                "protein_100g": None,
                "carbs_100g": None,
                "fat_100g": None,
                "serving_size": None,
                "image_url": None,
                "allowed_units": self._default_allowed_units(),
            }
        # Use metric_serving_amount for accurate per-100g calculation
        metric_amount = self._safe_float(serving.get("metric_serving_amount")) or 100
        return self._apply_integrity_policy(
            {
                "name": food.get("food_name", ""),
                "brand": food.get("brand_name"),
                "barcode": barcode,
                **provider_identity,
                "calories_100g": self._calc_per_100g(
                    serving.get("calories"), metric_amount
                ),
                "protein_100g": self._calc_per_100g(
                    serving.get("protein"), metric_amount
                ),
                "carbs_100g": self._calc_per_100g(
                    serving.get("carbohydrate"), metric_amount
                ),
                "fat_100g": self._calc_per_100g(serving.get("fat"), metric_amount),
                "serving_size": serving.get("serving_description"),
                "image_url": food.get("food_url"),
                "allowed_units": self._extract_serving_units(food),
                "metric_serving_amount": metric_amount,
            },
            require_metric_basis=True,
        )

    def _apply_integrity_policy(
        self,
        payload: dict[str, Any],
        *,
        require_metric_basis: bool,
    ) -> dict[str, Any]:
        result = self._integrity_policy.evaluate(
            payload,
            require_energy=True,
            require_metric_basis=require_metric_basis,
            provider_100g_label=True,
        )
        payload["allowed_units"] = list(result.serving_options)
        if not result.accepted:
            payload["nutrition_integrity_reason"] = result.reason_code
            for field in (
                "calories_100g",
                "protein_100g",
                "carbs_100g",
                "fat_100g",
                "fiber_100g",
                "sugar_100g",
            ):
                if field in payload:
                    payload[field] = None
            return payload
        payload.update(
            {
                "calories_100g": result.derived_calories_100g,
                "protein_100g": result.protein_100g,
                "carbs_100g": result.carbs_100g,
                "fat_100g": result.fat_100g,
                "fiber_100g": result.fiber_100g,
                "sugar_100g": result.sugar_100g,
            }
        )
        return payload

    def _calc_per_100g(self, value: Any, metric_amount: float) -> float | None:
        """Calculate nutrition value per 100g using metric_serving_amount."""
        if value is None:
            return None
        raw_value = self._safe_float(value)
        if raw_value is None:
            return None
        if metric_amount <= 0:
            return None
        return (raw_value / metric_amount) * 100

    def _map_search_result(self, food: dict[str, Any]) -> dict[str, Any]:
        """Map fatsecret search result to clean dict."""
        food_id = food.get("food_id")
        mapped: dict[str, Any] = {
            "description": food.get("food_name", ""),
            "brand": food.get("brand_name"),
            "food_description": food.get("food_description", ""),
            "source": "fatsecret",
            "food_id": food_id,
            "origin": "provider",
            "source_namespace": "fatsecret",
            "source_food_id": str(food_id) if food_id else None,
            "allowed_units": self._default_allowed_units(),
        }
        if food.get("servings"):
            mapped.update(self._extract_nutrition_from_details(food))
        elif not _has_search_macros(mapped):
            # List-quality macros from search description only (no food.get.v5).
            # Does not set metric_serving_amount — search adoption stays gated.
            mapped.update(description_macros_as_100g(food))
        return mapped

    def _safe_float(self, value: Any) -> float | None:
        """Safely convert value to float."""
        if value is None:
            return None
        try:
            converted = float(value)
        except (ValueError, TypeError):
            return None
        return converted if math.isfinite(converted) else None


_fat_secret_service: FatSecretService | None = None
_fat_secret_service_initialized = False


def get_fat_secret_service() -> FatSecretService | None:
    """Get the optional FatSecret service when credentials are configured."""
    global _fat_secret_service, _fat_secret_service_initialized
    if _fat_secret_service_initialized:
        return _fat_secret_service

    client_id = settings.FATSECRET_CLIENT_ID
    client_secret = settings.FATSECRET_CLIENT_SECRET

    if not client_id or not client_secret:
        logger.warning("fatsecret credentials not configured; provider will be skipped")
        _fat_secret_service_initialized = True
        return None

    _fat_secret_service = FatSecretService(
        client_id=client_id,
        client_secret=client_secret,
    )
    _fat_secret_service_initialized = True
    return _fat_secret_service
