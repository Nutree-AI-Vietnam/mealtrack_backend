"""JSON request bodies read by the endpoint instead of by FastAPI.

FastAPI reads and decodes a body parameter before any dependency runs, so a
route whose caller must pass a dependency first, such as authentication, takes
the Request and reads its body here, after its dependencies have run.
"""

from __future__ import annotations

from email.message import Message
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails


async def read_json_body[ModelT: BaseModel](
    request: Request, model: type[ModelT]
) -> ModelT:
    """Validate the request body as ``model``, answering 422 as a body parameter would."""
    body = await request.body()
    if not body:
        raise RequestValidationError(
            [
                {
                    "type": "missing",
                    "loc": ("body",),
                    "msg": "Field required",
                    "input": None,
                }
            ]
        )
    try:
        if _is_json(request.headers.get("content-type")):
            return model.model_validate_json(body)
        # Like FastAPI, validate a body of another content type as raw bytes,
        # which no model accepts.
        return model.model_validate(body, from_attributes=True)
    except ValidationError as error:
        raise RequestValidationError(
            [_body_error(detail) for detail in error.errors(include_url=False)],
            body=body,
        ) from None


def _body_error(detail: ErrorDetails) -> dict[str, Any]:
    error: dict[str, Any] = {**detail, "loc": ("body", *detail["loc"])}
    if isinstance(error["input"], bytes):
        # The error is about the raw body. Leave the body out, as FastAPI does
        # for a JSON decode error: it can run to megabytes, and bytes that are
        # not UTF-8 would fail the 422 response's own encoding.
        error["input"] = {}
    return error


def json_body_openapi(model: type[BaseModel]) -> dict[str, Any]:
    """Route ``openapi_extra`` that documents ``model`` as the required JSON body."""
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        }
    }


def _is_json(content_type: str | None) -> bool:
    # The content types FastAPI decodes as JSON for a body parameter.
    if not content_type:
        return False
    message = Message()
    message["content-type"] = content_type
    subtype = message.get_content_subtype()
    return message.get_content_maintype() == "application" and (
        subtype == "json" or subtype.endswith("+json")
    )
