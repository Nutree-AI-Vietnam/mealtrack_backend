"""Failures of a recipe import, in terms the app can show."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class RecipeImportFailure(StrEnum):
    """The value is the client-facing error code."""

    # The request named no usable source (bad link, empty text).
    INVALID_SOURCE = "INVALID_RECIPE_SOURCE"
    # The link points somewhere the server must not fetch.
    URL_NOT_ALLOWED = "RECIPE_URL_NOT_ALLOWED"
    # The site or network failed; trying again later may work.
    SOURCE_UNREACHABLE = "RECIPE_SOURCE_UNREACHABLE"
    # The site answered but refused or sent something unusable.
    SOURCE_REJECTED = "RECIPE_SOURCE_REJECTED"
    # The video is private, needs a login, or the platform blocked us.
    VIDEO_UNAVAILABLE = "RECIPE_VIDEO_UNAVAILABLE"
    # The source was read, but it holds no recipe.
    NOT_A_RECIPE = "NOT_A_RECIPE"


class RecipeImportError(Exception):
    """Expected import failure; the API maps ``failure`` to a status code."""

    def __init__(
        self,
        failure: RecipeImportFailure,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.failure = failure
        self.message = message
        self.details = details or {}

    @property
    def code(self) -> str:
        return self.failure.value
