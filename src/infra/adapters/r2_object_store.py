"""Store meal photos in Cloudflare R2.

R2 uses the S3 API. Those calls are not part of the Cloudflare REST API
quota of 1,200 requests per 5 minutes.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime
from urllib.parse import quote, urlencode

import httpx

logger = logging.getLogger(__name__)


class R2UploadError(Exception):
    """R2 rejected or could not store an object."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("Meal photo storage is temporarily unavailable")


class R2ObjectStore:
    def __init__(
        self,
        *,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        public_base_url: str,
    ) -> None:
        self._account_id = account_id.strip()
        self._access_key_id = access_key_id.strip()
        self._secret_access_key = secret_access_key.strip()
        self._bucket = bucket.strip()
        self._public_base_url = public_base_url.strip().rstrip("/")

    def public_url(self, key: str) -> str:
        return f"{self._public_base_url}/{quote(key, safe='/')}"

    def presigned_put_url(self, key: str, expires_in: int) -> str:
        return presigned_put_url(
            account_id=self._account_id,
            access_key_id=self._access_key_id,
            secret_access_key=self._secret_access_key,
            bucket=self._bucket,
            key=key,
            expires_in=expires_in,
            now=datetime.now(UTC),
        )

    async def put(self, key: str, body: bytes, content_type: str) -> str:
        url, headers = signed_put_request(
            account_id=self._account_id,
            access_key_id=self._access_key_id,
            secret_access_key=self._secret_access_key,
            bucket=self._bucket,
            key=key,
            body=body,
            content_type=content_type,
            now=datetime.now(UTC),
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(url, content=body, headers=headers)
        if response.status_code not in (200, 201):
            logger.warning("R2 put failed status=%s", response.status_code)
            raise R2UploadError(response.status_code)
        return self.public_url(key)


def signed_put_request(
    *,
    account_id: str,
    access_key_id: str,
    secret_access_key: str,
    bucket: str,
    key: str,
    body: bytes,
    content_type: str,
    now: datetime,
) -> tuple[str, dict[str, str]]:
    """Return a SigV4-signed PUT for one R2 object."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = amz_date[:8]
    payload_hash = hashlib.sha256(body).hexdigest()
    host = f"{account_id}.r2.cloudflarestorage.com"
    canonical_uri = f"/{quote(bucket, safe='')}/{quote(key, safe='/')}"
    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [
            "PUT",
            canonical_uri,
            "",
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{datestamp}/auto/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    signing_key = _signing_key(secret_access_key, datestamp)
    signature = hmac.new(
        signing_key, string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return f"https://{host}{canonical_uri}", {
        "Authorization": authorization,
        "Content-Type": content_type,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
    }


def presigned_put_url(
    *,
    account_id: str,
    access_key_id: str,
    secret_access_key: str,
    bucket: str,
    key: str,
    expires_in: int,
    now: datetime,
) -> str:
    """Return a URL the phone can PUT to without calling our API again."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = amz_date[:8]
    host = f"{account_id.strip()}.r2.cloudflarestorage.com"
    canonical_uri = f"/{quote(bucket.strip(), safe='')}/{quote(key, safe='/')}"
    credential_scope = f"{datestamp}/auto/s3/aws4_request"
    signed_headers = "host"
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key_id.strip()}/{credential_scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires_in),
        "X-Amz-SignedHeaders": signed_headers,
    }
    canonical_query = urlencode(sorted(query.items()), quote_via=quote)
    canonical_request = "\n".join(
        [
            "PUT",
            canonical_uri,
            canonical_query,
            f"host:{host}\n",
            signed_headers,
            "UNSIGNED-PAYLOAD",
        ]
    )
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    signature = hmac.new(
        _signing_key(secret_access_key.strip(), datestamp),
        string_to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()
    query["X-Amz-Signature"] = signature
    return f"https://{host}{canonical_uri}?{urlencode(sorted(query.items()), quote_via=quote)}"


def _signing_key(secret_access_key: str, datestamp: str) -> bytes:
    def _sign(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode(), hashlib.sha256).digest()

    date_key = _sign(f"AWS4{secret_access_key}".encode(), datestamp)
    region_key = _sign(date_key, "auto")
    service_key = _sign(region_key, "s3")
    return _sign(service_key, "aws4_request")
