"""
OpenFoodFacts API HTTP client.
Provides product lookup by barcode for packaged foods.
"""

import re
from typing import Any

import httpx


class OpenFoodFactsService:
    """HTTP client for OpenFoodFacts API."""

    BASE_URL = "https://world.openfoodfacts.org/api/v2"

    # Barcode validation pattern (8-14 digits)
    BARCODE_PATTERN = re.compile(r"^\d{8,14}$")

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def get_product(self, barcode: str) -> dict[str, Any] | None:
        """
        Fetch product by barcode from OpenFoodFacts.

        Args:
            barcode: Product barcode (EAN-13, EAN-8, UPC-A, UPC-E)

        Returns:
            Product dict with name, brand, nutrition per 100g, serving size, image URL
            Returns None if product not found or API error.
        """
        # Validate barcode format
        if not self.BARCODE_PATTERN.match(barcode):
            return None

        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.BASE_URL}/product/{barcode}.json",
                headers={"User-Agent": "Nutree/1.0"},
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") != 1:
                return None

            product = data.get("product", {})
            if not product:
                return None

            return self._map_product(product)
        except httpx.HTTPError:
            return None

    def _map_product(self, product: dict[str, Any]) -> dict[str, Any]:
        """Map OpenFoodFacts response to clean dict."""
        nutriments = product.get("nutriments", {})

        # Get serving size from product
        serving_size = product.get("serving_size")
        if not serving_size:
            # Try to get from selected portion
            selected_portion = product.get("selected_portion")
            if selected_portion:
                serving_size = selected_portion.get("label")

        # Get image URL
        image_url = (
            product.get("image_front_url")
            or product.get("image_url")
            or product.get("image_front_small_url")
        )

        english_name = product.get("product_name_en")
        source_food_id = str(product.get("code") or "").strip() or None
        return {
            "name": english_name or product.get("product_name"),
            # product_name may be localized; only mark explicit English data.
            "source_language": "en" if english_name else None,
            "brand": product.get("brands"),
            "barcode": product.get("code"),
            "source_namespace": "openfoodfacts",
            "source_food_id": source_food_id,
            "calories_100g": self._safe_float(nutriments.get("energy-kcal_100g")),
            "protein_100g": self._safe_float(nutriments.get("proteins_100g")),
            "carbs_100g": self._safe_float(nutriments.get("carbohydrates_100g")),
            "fat_100g": self._safe_float(nutriments.get("fat_100g")),
            "fiber_100g": self._safe_float(nutriments.get("fiber_100g")),
            "sugar_100g": self._safe_float(nutriments.get("sugars_100g")),
            "serving_size": serving_size,
            "serving_options": self._serving_options(serving_size),
            "image_url": image_url,
        }

    def _serving_options(self, serving_size: str | None) -> list[dict[str, Any]]:
        units = [{"unit": "g", "gram_weight": 1.0, "description": "1 g"}]
        grams = self._serving_grams(serving_size)
        if grams:
            units.append(
                {
                    "unit": "serving",
                    "gram_weight": grams,
                    "description": f"1 serving ({serving_size or f'{grams:g} g'})",
                }
            )
        return units

    def _serving_grams(self, serving_size: str | None) -> float | None:
        if not serving_size:
            return None
        match = re.search(
            r"(\d+(?:[.,]\d+)?)\s*(g|gram|grams|ml|milliliter)",
            serving_size,
            re.I,
        )
        if not match:
            return None
        return self._safe_float(match.group(1).replace(",", "."))

    def _safe_float(self, value: Any) -> float | None:
        """Safely convert value to float."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None


# Singleton instance
_open_food_facts_service: OpenFoodFactsService | None = None


def get_open_food_facts_service() -> OpenFoodFactsService:
    """Get singleton instance of OpenFoodFactsService."""
    global _open_food_facts_service
    if _open_food_facts_service is None:
        _open_food_facts_service = OpenFoodFactsService()
    return _open_food_facts_service
