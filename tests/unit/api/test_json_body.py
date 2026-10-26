import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from src.api.utils.json_body import json_body_openapi, read_json_body


class _Item(BaseModel):
    name: str
    count: int = Field(ge=0)
    tags: dict[str, int] = Field(default_factory=dict)


@pytest.fixture
def client() -> TestClient:
    """The same model as a body parameter and as a body the endpoint reads."""
    app = FastAPI()

    @app.post("/parameter")
    async def parameter(item: _Item) -> _Item:
        return item

    @app.post("/read", openapi_extra=json_body_openapi(_Item))
    async def read(request: Request) -> _Item:
        return await read_json_body(request, _Item)

    return TestClient(app)


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        (b'{"name": "rice", "count": 2, "tags": {"a": 1}}', "application/json"),
        (b'{"name": "rice", "count": 2}', "application/json; charset=utf-8"),
        (b'{"name": "rice", "count": 2}', "application/vnd.api+json"),
        (b'{"name": "rice", "count": -1}', "application/json"),
        (b'{"name": "rice", "tags": {"a": "x"}}', "application/json"),
        (b"", "application/json"),
    ],
)
def test_read_body_answers_like_a_body_parameter(client, content, content_type):
    headers = {"content-type": content_type} if content_type else {}

    expected = client.post("/parameter", content=content, headers=headers)
    response = client.post("/read", content=content, headers=headers)

    assert response.status_code == expected.status_code
    assert response.json() == expected.json()


@pytest.mark.parametrize(
    ("content", "content_type", "error_type"),
    [
        (b'{"name": "rice", "count": 2}', "text/plain", "model_attributes_type"),
        (b'{"name": "rice", "count": 2}', None, "model_attributes_type"),
        (b"\xff\xfe", "text/plain", "model_attributes_type"),
        (b'{"name": ', "application/json", "json_invalid"),
        (b'{"name": "\xff", "count": 2}', "application/json", "json_invalid"),
    ],
)
def test_errors_about_the_raw_body_leave_it_out(
    client, content, content_type, error_type
):
    headers = {"content-type": content_type} if content_type else {}

    response = client.post("/read", content=content, headers=headers)

    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert (error["type"], error["loc"], error["input"]) == (error_type, ["body"], {})


@pytest.mark.parametrize(
    ("content", "error_type", "loc"),
    [
        (b'["rice"]', "model_type", ["body"]),
        (b'{"name": "rice", "count": 2, "tags": []}', "dict_type", ["body", "tags"]),
    ],
)
def test_json_mode_errors_still_get_422(client, content, error_type, loc):
    # Validating JSON directly words some errors differently from json.loads
    # followed by Python validation, so these check the error, not its message.
    headers = {"content-type": "application/json"}

    expected = client.post("/parameter", content=content, headers=headers)
    response = client.post("/read", content=content, headers=headers)

    assert response.status_code == expected.status_code == 422
    [error] = response.json()["detail"]
    assert (error["type"], error["loc"]) == (error_type, loc)


def test_openapi_documents_the_body_a_parameter_would():
    openapi = FastAPI()

    @openapi.post("/read", openapi_extra=json_body_openapi(_Item))
    async def read(request: Request) -> None:
        return None

    body = openapi.openapi()["paths"]["/read"]["post"]["requestBody"]

    assert body["required"] is True
    assert body["content"]["application/json"]["schema"] == _Item.model_json_schema()
