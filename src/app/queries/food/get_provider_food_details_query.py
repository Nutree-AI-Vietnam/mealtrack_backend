"""Query to resolve one provider food (e.g. FatSecret) after search select."""

from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class GetProviderFoodDetailsQuery(Query):
    source_namespace: str
    source_food_id: str
    language: str = "en"
    adopt: bool = True
