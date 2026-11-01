"""Errors for external food-catalog lookups."""


class FoodNotFoundError(Exception):
    """The catalog has no food for this id.

    The message stays free of provider URLs. Callers map this to the API
    not-found response.
    """

    def __init__(self, fdc_id: int) -> None:
        self.fdc_id = fdc_id
        super().__init__("Food not found")
