"""High-value claim endpoints require a recently minted Firebase bearer."""

import pytest
from fastapi import HTTPException

from src.api.routes.v1.web_funnel import _require_fresh_token
from src.app.services.web_funnel_claim_common import utcnow

# _require_fresh_token accepts an iat only while its age is in [0, 600] seconds.
# Parametrize offsets, not timestamps: values are built at collection time, so
# an absolute "+1 minute" would fall inside the window once the run is a minute
# old. Offsets are added to utcnow() when the test runs. +60s is a small future
# skew and must be rejected; +900s is that same rejection well outside the window.
_STALE_OFFSET_SECONDS = -(600 + 60)
_FUTURE_OFFSET_SECONDS = 60
_FAR_FUTURE_OFFSET_SECONDS = 600 + 300


def test_fresh_claim_bearer_is_accepted():
    _require_fresh_token({"iat": int(utcnow().timestamp())})


@pytest.mark.parametrize(
    "offset_seconds",
    [
        _STALE_OFFSET_SECONDS,
        _FUTURE_OFFSET_SECONDS,
        _FAR_FUTURE_OFFSET_SECONDS,
    ],
    ids=["stale", "future", "far-future"],
)
def test_stale_or_future_claim_bearer_is_rejected(offset_seconds):
    issued_at = int(utcnow().timestamp()) + offset_seconds
    with pytest.raises(HTTPException) as error:
        _require_fresh_token({"iat": issued_at})
    assert error.value.status_code == 401
