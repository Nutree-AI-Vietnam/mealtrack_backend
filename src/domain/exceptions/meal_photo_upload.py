"""Failures while accepting a meal-photo upload from the current app."""


class MealPhotoUploadError(Exception):
    def __init__(self, message: str, error_code: str) -> None:
        self.message = message
        self.error_code = error_code
        super().__init__(message)
