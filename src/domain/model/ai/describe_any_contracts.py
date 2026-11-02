"""AI contract for the + panel's Ghi nhanh: one sentence, every kind of log.

Loose for the same reason as ``describe_log_contracts``: OpenAI structured
outputs drop min/max/length from the schema, so the handler trims, clamps or
drops items in code instead of rejecting the whole sentence. Drinks and
activities reuse the water and activity contracts as they are.
"""

from pydantic import BaseModel, ConfigDict, Field

from src.domain.model.ai.describe_log_contracts import ActivityLogItem, DrinkLogItem
from src.domain.model.ai.supplement_label_contract import (
    LabelForm,
    ProductKind,
    TrackedNutrientKey,
)


class SupplementNutrientEstimate(BaseModel):
    """One nutrient of a typical unit, as a label would print it."""

    model_config = ConfigDict(extra="ignore")

    nutrient: TrackedNutrientKey
    amount: float = Field(..., description="Amount in ONE unit (one capsule, one drop)")
    unit: str = Field(..., description="mg, mcg or IU, as labels print it")


class SupplementEstimate(BaseModel):
    """A typical product of this kind, used only when the person has not added it."""

    model_config = ConfigDict(extra="ignore")

    form: LabelForm
    nutrients: list[SupplementNutrientEstimate] = Field(
        default_factory=list,
        description="Tracked vitamins and minerals in one unit; empty when none",
    )


class SupplementLogItem(BaseModel):
    """One supplement read from the sentence."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., description="The product as the person said it")
    words: str = Field(
        ..., description="The words of this item in the sentence, amount included"
    )
    units: float | None = Field(
        None,
        description="How many viên, giọt, muỗng or gói, only when the sentence says",
    )
    list_ref: str | None = Field(
        None, description="The ref of the person's own supplement it names, else null"
    )
    catalog_query: str | None = Field(
        None,
        description=(
            "Brand and product name to look up, only when a brand or branded "
            "product is named; null for a bare nutrient such as vitamin D3"
        ),
    )
    product_kind: ProductKind = "supplement"
    estimate: SupplementEstimate | None = Field(
        None, description="A typical unit, only when list_ref is null"
    )


class DescribeAnyParse(BaseModel):
    """Everything logged in the sentence, split by kind, in sentence order."""

    model_config = ConfigDict(extra="ignore")

    food: str | None = Field(
        None,
        description=(
            "Everything eaten, in the person's words with amounts, for the meal "
            "parser; null when nothing was eaten"
        ),
    )
    drinks: list[DrinkLogItem] = Field(default_factory=list)
    activities: list[ActivityLogItem] = Field(default_factory=list)
    supplements: list[SupplementLogItem] = Field(default_factory=list)
