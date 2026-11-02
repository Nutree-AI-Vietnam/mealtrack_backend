import httpx

from src.infra.monitoring.secret_redaction import (
    redact_sensitive_text,
    scrub_exception_text,
)


def test_redact_sensitive_text_filters_query_secrets():
    secret = "synthetic-fdc-key-not-real"
    text = (
        "Client error '404 Not Found' for url "
        f"'https://api.nal.usda.gov/fdc/v1/food/12345?api_key={secret}&page=1'"
    )

    redacted = redact_sensitive_text(text)

    assert secret not in redacted
    assert "api_key=[Filtered]" in redacted
    assert "page=1" in redacted
    assert redact_sensitive_text(f"api_key={secret}") == "api_key=[Filtered]"
    assert (
        redact_sensitive_text(f"https://example.test/x?token={secret}")
        == "https://example.test/x?token=[Filtered]"
    )


def test_scrub_exception_text_removes_raise_for_status_api_key():
    secret = "synthetic-fdc-key-not-real"
    request = httpx.Request(
        "GET",
        "https://api.nal.usda.gov/fdc/v1/food/12345",
        params={"api_key": secret},
    )
    response = httpx.Response(404, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        error = exc

    scrub_exception_text(error)

    assert secret not in str(error)
    assert secret not in repr(error.request.url)
    assert secret not in repr(error.response.request.url)
    assert "api_key=[Filtered]" in str(error)
