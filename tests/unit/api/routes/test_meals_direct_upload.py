"""The direct-upload route refuses a bad ticket before it reads the photo."""

import json
import time
import uuid
from urllib.parse import urlencode, urlsplit

import httpx
import pytest
import respx
from limits import parse

from src.api.base_dependencies import get_image_store
from src.api.main import app
from src.api.middleware.rate_limit import reset_limiter_counters
from src.api.routes.v1.meals_direct_upload import DIRECT_UPLOAD_RATE_LIMIT
from src.infra.adapters.cloudflare_image_store import (
    CloudflareImageStore,
    _upload_ticket_signature,
)

SECRET = "r2-secret"
BOUNDARY = "direct-upload-test"
PHOTO = b"\xff\xd8\xff meal photo"
R2_PUT = r"https://acct\.r2\.cloudflarestorage\.com/meal-photos/mealtrack/.*"
INVALID_TICKET = {
    "detail": {
        "error_code": "INVALID_UPLOAD_TICKET",
        "message": "Upload link is invalid or expired",
        "details": {},
    }
}


@pytest.fixture
def store():
    store = CloudflareImageStore(
        account_id="acct",
        api_token="token",
        account_hash="hash",
        custom_domain="",
        api_public_base_url="https://api.test",
        r2_access_key_id="access",
        r2_secret_access_key=SECRET,
        r2_bucket="meal-photos",
        r2_public_base_url="https://photos.example.com",
    )
    app.dependency_overrides[get_image_store] = lambda: store
    reset_limiter_counters()
    yield store
    app.dependency_overrides.pop(get_image_store, None)
    reset_limiter_counters()


class _Post:
    """One multipart photo POST; counts the body reads the app makes."""

    def __init__(self) -> None:
        self.body = (
            (
                f"--{BOUNDARY}\r\n"
                'Content-Disposition: form-data; name="file"; filename="meal.jpg"\r\n'
                "Content-Type: image/jpeg\r\n\r\n"
            ).encode()
            + PHOTO
            + f"\r\n--{BOUNDARY}--\r\n".encode()
        )
        self.reads = 0
        self.sent: list[dict] = []

    async def receive(self) -> dict:
        self.reads += 1
        if self.reads == 1:
            return {"type": "http.request", "body": self.body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    @property
    def status(self) -> int:
        return self.sent[0]["status"]

    def json(self) -> object:
        return json.loads(b"".join(m.get("body", b"") for m in self.sent[1:]))


async def _post_photo(path: str, query: str) -> _Post:
    """Send the POST through the whole app, middleware included."""
    post = _Post()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", f"multipart/form-data; boundary={BOUNDARY}".encode()),
            (b"content-length", str(len(post.body)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    await app(scope, post.receive, post.send)
    return post


def _upload_url(store: CloudflareImageStore) -> tuple[str, str]:
    """Path and query of a new upload_url, as GET /v1/meals/upload-token gives it."""
    url = urlsplit(store.generate_upload_signature(str(uuid.uuid4()))["upload_url"])
    return url.path, url.query


def _path(image_id: str) -> str:
    return f"/v1/meals/direct-upload/{image_id}"


def _ticket(signed_id: str, expires_at: int) -> str:
    signature = _upload_ticket_signature(SECRET, signed_id, expires_at)
    return urlencode({"exp": expires_at, "sig": signature})


@pytest.mark.asyncio
@respx.mock
async def test_valid_ticket_stores_the_photo(store):
    put = respx.put(url__regex=R2_PUT).mock(return_value=httpx.Response(200))
    path, query = _upload_url(store)
    image_id = path.rsplit("/", 1)[1]

    post = await _post_photo(path, query)

    photo_url = f"https://photos.example.com/mealtrack/{image_id}"
    assert post.status == 200
    assert post.json() == {
        "secure_url": photo_url,
        "result": {"id": image_id, "variants": [photo_url]},
    }
    assert put.calls.last.request.content == PHOTO
    assert post.reads == 1


@pytest.mark.asyncio
@respx.mock
async def test_expired_ticket_is_refused_before_the_photo_is_read(store):
    image_id = str(uuid.uuid4())

    post = await _post_photo(_path(image_id), _ticket(image_id, int(time.time()) - 60))

    assert post.status == 401
    assert post.json() == INVALID_TICKET
    assert post.reads == 0


@pytest.mark.asyncio
@respx.mock
async def test_bad_signature_is_refused_before_the_photo_is_read(store):
    # A signature the server made, but for another photo.
    query = _ticket(str(uuid.uuid4()), int(time.time()) + 300)

    post = await _post_photo(_path(str(uuid.uuid4())), query)

    assert post.status == 401
    assert post.json() == INVALID_TICKET
    assert post.reads == 0


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("query", ["sig=" + "0" * 64, "exp=soon&sig=" + "0" * 64])
async def test_ticket_without_a_usable_exp_is_refused_before_the_photo_is_read(
    store, query
):
    post = await _post_photo(_path(str(uuid.uuid4())), query)

    assert post.status == 401
    assert post.json() == INVALID_TICKET
    assert post.reads == 0


@pytest.mark.asyncio
@respx.mock
async def test_uploads_over_the_ip_limit_are_refused_before_the_photo_is_read(store):
    put = respx.put(url__regex=R2_PUT).mock(return_value=httpx.Response(200))
    limit = parse(DIRECT_UPLOAD_RATE_LIMIT).amount
    image_id = str(uuid.uuid4())

    # A refused ticket does not use up the limit.
    expired = await _post_photo(
        _path(image_id), _ticket(image_id, int(time.time()) - 60)
    )
    assert expired.status == 401
    for _ in range(limit):
        assert (await _post_photo(*_upload_url(store))).status == 200
    over = await _post_photo(*_upload_url(store))

    assert over.status == 429
    assert over.reads == 0
    assert put.call_count == limit
