"""HTTP client for the MoMo payment gateway (API v2, one-time payments)."""

from dataclasses import dataclass

import httpx

from src.domain.model.payments.momo_signature import (
    CREATE_FIELDS,
    QUERY_FIELDS,
    momo_signature,
)

_BASE_URLS = {
    "sandbox": "https://test-payment.momo.vn",
    "production": "https://payment.momo.vn",
}
# MoMo asks partners to wait up to 30 s for create and query responses.
_TIMEOUT_SECONDS = 30.0


class MomoGatewayError(Exception):
    """MoMo refused the request or could not be reached."""


@dataclass(frozen=True)
class MomoCredentials:
    env: str
    partner_code: str
    access_key: str
    secret_key: str

    @property
    def base_url(self) -> str:
        return _BASE_URLS["production" if self.env == "production" else "sandbox"]

    @property
    def missing(self) -> list[str]:
        values = {
            "MOMO_PARTNER_CODE": self.partner_code,
            "MOMO_ACCESS_KEY": self.access_key,
            "MOMO_SECRET_KEY": self.secret_key,
        }
        return [name for name, value in values.items() if not value]


class MomoGatewayAdapter:
    def __init__(self, credentials: MomoCredentials):
        self.credentials = credentials

    async def create_payment(
        self,
        *,
        order_id: str,
        request_id: str,
        amount: int,
        order_info: str,
        redirect_url: str,
        ipn_url: str,
        request_type: str,
        lang: str,
    ) -> dict:
        """Open a MoMo payment session; returns payUrl and, for wallets, deeplink."""
        body = {
            "partnerCode": self.credentials.partner_code,
            "partnerName": "Nutree",
            "storeId": "Nutree",
            "requestId": request_id,
            "amount": amount,
            "orderId": order_id,
            "orderInfo": order_info,
            "redirectUrl": redirect_url,
            "ipnUrl": ipn_url,
            "requestType": request_type,
            "extraData": "",
            "lang": lang,
            "autoCapture": True,
        }
        body["signature"] = self._sign(body, CREATE_FIELDS)
        payload = await self._post("/v2/gateway/api/create", body)
        if payload.get("resultCode") != 0 or not payload.get("payUrl"):
            raise MomoGatewayError(
                f"MoMo create failed: {payload.get('resultCode')} {payload.get('message')}"
            )
        return payload

    async def query_payment(self, *, order_id: str, request_id: str) -> dict:
        """Ask MoMo for an order's current result (no signature on the reply)."""
        body = {
            "partnerCode": self.credentials.partner_code,
            "requestId": request_id,
            "orderId": order_id,
            "lang": "vi",
        }
        body["signature"] = self._sign(body, QUERY_FIELDS)
        payload = await self._post("/v2/gateway/api/query", body)
        if payload.get("orderId") != order_id or not isinstance(
            payload.get("resultCode"), int
        ):
            raise MomoGatewayError("MoMo query returned an unexpected body")
        return payload

    def _sign(self, body: dict, fields: tuple[str, ...]) -> str:
        values = {**body, "accessKey": self.credentials.access_key}
        return momo_signature(self.credentials.secret_key, values, fields)

    async def _post(self, path: str, body: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    self.credentials.base_url + path, json=body
                )
        except httpx.HTTPError as error:
            raise MomoGatewayError("MoMo is unreachable") from error
        try:
            payload = response.json()
        except ValueError as error:
            raise MomoGatewayError(
                f"MoMo returned HTTP {response.status_code} without JSON"
            ) from error
        if not isinstance(payload, dict):
            raise MomoGatewayError("MoMo returned a non-object body")
        return payload
