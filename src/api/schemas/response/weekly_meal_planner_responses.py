"""Response contracts for weekly planning, recipes, groceries, and logging."""

from datetime import date, datetime

from pydantic import BaseModel


class WeeklyRecipeSummaryResponse(BaseModel):
    id: str
    name: str
    image_url: str | None = None
    cook_time_minutes: int | None = None
    calories: int


class WeeklyMealSlotResponse(BaseModel):
    slot_id: str
    day_index: int
    slot_index: int
    slot_name: str
    recipe: WeeklyRecipeSummaryResponse | None = None
    is_logged: bool
    logged_meal_id: str | None = None


class WeeklyMealPlanResponse(BaseModel):
    id: str
    week_start_date: date
    status: str
    people: int
    preferences: dict
    plan: list[list[WeeklyMealSlotResponse]]
    total_meals_planned: int
    to_buy_count: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RecipeListItemResponse(BaseModel):
    id: str
    slug: str
    name: str
    image_url: str | None = None
    cook_time_minutes: int | None = None
    tag: str | None = None
    calories: int
    protein: float
    carbs: float
    fat: float


class RecipeListResponse(BaseModel):
    items: list[RecipeListItemResponse]
    total: int


class RecipeIngredientResponse(BaseModel):
    id: str
    name: str
    amount_per_serving: float
    unit: str
    category: str


class RecipeStepResponse(BaseModel):
    step_number: int
    title: str
    description: str


class RecipeDetailNutritionResponse(BaseModel):
    calories: float
    protein: float
    carbs: float
    fat: float
    fiber: float


class RecipeDetailResponse(BaseModel):
    id: str
    name: str
    source_name: str | None = None
    source_url: str | None = None
    image_url: str | None = None
    prep_time_minutes: int | None = None
    cook_time_minutes: int | None = None
    tag: str | None = None
    allergens: str | None = None
    summary: str | None = None
    equipment: str | None = None
    nutrition_per_serving: RecipeDetailNutritionResponse
    ingredients: list[RecipeIngredientResponse]
    steps: list[RecipeStepResponse]
    allergy_evaluated: bool = False


class WeeklyAiSlotChangeResponse(BaseModel):
    day_index: int
    slot_index: int
    previous_recipe_id: str | None
    new_recipe_id: str | None
    action: str


class WeeklyAiProposalResponse(BaseModel):
    explanation: str
    diff_summary: str
    proposed_plan: WeeklyMealPlanResponse
    slot_changes: list[WeeklyAiSlotChangeResponse]


class GroceryItemResponse(BaseModel):
    ingredient_id: int
    name: str
    total_needed: float
    unit: str
    stock_amount: float
    stock_kind: str | None = None
    status: str


class GroceryCategoryResponse(BaseModel):
    category: str
    items: list[GroceryItemResponse]


class WeeklyGroceriesResponse(BaseModel):
    total_items_count: int
    to_buy_count: int
    owned_count: int
    categories: list[GroceryCategoryResponse]


class PantryUpdateResponse(BaseModel):
    success: bool
    updated_count: int


class LogMealPlanSlotResponse(BaseModel):
    logged_meal_id: str
    slot_id: str
    calories: float
    message: str = "Meal logged to food diary successfully"
