"""High-value claim endpoints require a recently minted Firebase bearer."""

from datetime import timedelta

import pytest
from fastapi import HTTPException

from src.api.routes.v1.web_funnel import _require_fresh_token
from src.app.services.web_funnel_claim_common import utcnow


def test_fresh_claim_bearer_is_accepted():
    _require_fresh_token({"iat": int(utcnow().timestamp())})


# Offsets, not timestamps: parametrize values are built at collection time, so
# an absolute "+1 minute" would be in the past once the run is a minute old.
@pytest.mark.parametrize(
    "offset",
    [timedelta(minutes=-11), timedelta(minutes=1)],
    ids=["stale", "future"],
)
def test_stale_or_future_claim_bearer_is_rejected(offset):
    issued_at = int((utcnow() + offset).timestamp())
    with pytest.raises(HTTPException) as error:
        _require_fresh_token({"iat": issued_at})
    assert error.value.status_code == 401
