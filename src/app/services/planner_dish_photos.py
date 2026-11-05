"""Photos for the dishes a model writes into a week.

A catalogue dish comes with a photo. A dish the planner asked a model for
had none, so a written week showed the empty plate in every one of its
twenty-one slots. Each dish is now photographed the moment its slot keeps
it, while the next dish is still being written, so the week waits for at
most the last few photos before it leaves 'preparing'.

A photo is decoration. One that fails, or is still being made when the
week is ready, leaves its dish on the empty plate: it never costs the week
and never holds it longer than ``SETTLE_SECONDS``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import replace
from typing import Any

from src.domain.model.weekly_meal_planner.saved_recipe import SavedRecipeSnapshot

logger = logging.getLogger(__name__)

# How long a written week still waits for photos being made. A dish is kept
# every ten to twenty seconds (one recipe call each), so by the last dinner
# nearly every photo is done; this only bounds the one or two still running.
SETTLE_SECONDS = 30.0
# One photo, from the request to a stored URL.
PHOTO_TIMEOUT_SECONDS = 60.0
# Photos made at once.
MAX_PARALLEL_PHOTOS = 4

# Takes a meal card, answers with a URL or something that carries one in
# ``url`` (ChatMealImageGenerator answers a FoodImageResult), or None.
Photographer = Callable[[dict[str, Any]], Awaitable[Any]]


class PlannerDishPhotos:
    """Start each dish's photo as it is kept; hand the week back with them."""

    def __init__(
        self,
        photographer: Photographer | None,
        *,
        plan_id: str = "",
        settle_seconds: float = SETTLE_SECONDS,
        photo_timeout_seconds: float = PHOTO_TIMEOUT_SECONDS,
        max_parallel: int = MAX_PARALLEL_PHOTOS,
    ) -> None:
        self._photographer = photographer
        self._plan_id = plan_id
        self._settle_seconds = settle_seconds
        self._photo_timeout_seconds = photo_timeout_seconds
        self._turns = asyncio.Semaphore(max_parallel)
        self._photos: dict[str, asyncio.Task[str | None]] = {}

    def start(self, dish: SavedRecipeSnapshot) -> None:
        """Begin this dish's photo, unless it has one or one is under way.

        Two dishes the photo would draw the same way share one photo.
        """
        photographer = self._photographer
        if photographer is None or dish.image_url:
            return
        key = photo_key(dish)
        if not key or key in self._photos:
            return
        task = asyncio.create_task(self._take(photographer, dish))
        task.add_done_callback(_read_outcome)
        self._photos[key] = task

    async def attach[K](
        self, dishes: Mapping[K, SavedRecipeSnapshot]
    ) -> dict[K, SavedRecipeSnapshot]:
        """The dishes, each with its photo when it was ready in time."""
        photos, self._photos = self._photos, {}
        if not photos:
            return dict(dishes)
        running = [task for task in photos.values() if not task.done()]
        if running:
            try:
                await asyncio.wait(running, timeout=self._settle_seconds)
            except BaseException:
                _stop(photos.values())
                raise

        urls: dict[str, str] = {}
        late: list[asyncio.Task[str | None]] = []
        failures: list[str] = []
        for key, task in photos.items():
            if not task.done():
                late.append(task)
            elif task.cancelled():
                failures.append("CancelledError")
            elif (error := task.exception()) is not None:
                failures.append(type(error).__name__)
            elif url := task.result():
                urls[key] = url
            else:
                failures.append("NoPhoto")
        _stop(late)
        log = logger.warning if failures or late else logger.info
        log(
            "Weekly plan %s photos: %d of %d ready, %d failed %s, %d still running",
            self._plan_id,
            len(urls),
            len(photos),
            len(failures),
            sorted(set(failures)),
            len(late),
        )
        return {slot: _with_photo(dish, urls) for slot, dish in dishes.items()}

    def cancel(self) -> None:
        """Stop every photo still being made: the week will not use them."""
        _stop(self._photos.values())
        self._photos = {}

    async def _take(
        self, photographer: Photographer, dish: SavedRecipeSnapshot
    ) -> str | None:
        async with self._turns:
            async with asyncio.timeout(self._photo_timeout_seconds):
                found = await photographer(meal_card(dish))
        url = getattr(found, "url", found)
        if isinstance(url, str) and url.strip():
            return url.strip()
        return None


def meal_card(dish: SavedRecipeSnapshot) -> dict[str, Any]:
    """What the photo is drawn from: the English names, which say what was
    cooked, and never the reader's dish name when there is an English one.
    """
    return {
        "name": dish.name,
        "english_name": dish.english_name,
        "ingredients": [
            {"name": line.name} for line in dish.ingredients if not line.optional
        ],
    }


def photo_key(dish: SavedRecipeSnapshot) -> str:
    """Dishes with the same key would be drawn alike, so they share a photo."""
    return (dish.english_name or dish.name or "").strip().casefold()


def _with_photo(
    dish: SavedRecipeSnapshot, urls: Mapping[str, str]
) -> SavedRecipeSnapshot:
    if dish.image_url:
        return dish
    url = urls.get(photo_key(dish))
    return replace(dish, image_url=url) if url else dish


def _stop(tasks: Iterable[asyncio.Task[Any]]) -> None:
    for task in tasks:
        task.cancel()


def _read_outcome(task: asyncio.Task[Any]) -> None:
    """Read a finished photo's error, so a week that never asks for it (one
    that failed, or was cancelled) does not leave asyncio logging it as lost.
    """
    if not task.cancelled():
        task.exception()
