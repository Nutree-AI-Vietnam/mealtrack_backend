"""Authenticated load profile for the meal catalog."""

from __future__ import annotations

import os

from locust import HttpUser, between, events, task

TOKEN_ENV = "MEALTRACK_LOAD_TEST_TOKEN"


@events.init.add_listener
def require_load_test_token(environment, **kwargs) -> None:
    if not os.getenv(TOKEN_ENV):
        raise RuntimeError(f"{TOKEN_ENV} is required for meal catalog load tests")


class MealCatalogUser(HttpUser):
    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        self.catalog_id: str | None = None
        self.client.headers.update(
            {
                "Authorization": f"Bearer {os.environ[TOKEN_ENV]}",
                "Accept-Language": "en",
                "X-Timezone": "Asia/Ho_Chi_Minh",
            }
        )
        self._load_catalog_page()

    @task(8)
    def food_search(self) -> None:
        self.client.get(
            "/v1/foods/search",
            params={"query": "rice", "limit": 10, "language": "en"},
            name="/v1/foods/search",
        )

    @task(6)
    def catalog_page(self) -> None:
        self._load_catalog_page()

    @task(4)
    def catalog_search(self) -> None:
        self.client.get(
            "/v1/meal-catalog",
            params={"q": "rice", "limit": 20},
            name="/v1/meal-catalog?q",
        )

    @task(6)
    def catalog_detail(self) -> None:
        if not self.catalog_id:
            self._load_catalog_page()
        if not self.catalog_id:
            return
        self.client.get(
            f"/v1/meal-catalog/{self.catalog_id}",
            name="/v1/meal-catalog/:catalog_id",
        )

    def _load_catalog_page(self) -> None:
        with self.client.get(
            "/v1/meal-catalog",
            params={"feed": "popular", "limit": 20},
            name="/v1/meal-catalog",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                return
            items = response.json().get("items") or []
            if items:
                self.catalog_id = items[0].get("id")
