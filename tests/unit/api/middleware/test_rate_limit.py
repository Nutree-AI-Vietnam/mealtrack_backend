import base64
import json

import pytest

from src.api.middleware.rate_limit import (
    _rightmost_public_or_peer,
    get_client_ip,
    get_ip_only_rate_limit_key,
    get_user_id_or_ip,
    rate_limit_exceeded_handler,
)


class _Req:
    def __init__(
        self,
        auth: str | None,
        ip: str,
        forwarded: str | None = None,
        cf_connecting_ip: str | None = None,
    ):
        self.headers = {}
        if auth is not None:
            self.headers["Authorization"] = auth
        if forwarded is not None:
            self.headers["X-Forwarded-For"] = forwarded
        if cf_connecting_ip is not None:
            self.headers["CF-Connecting-IP"] = cf_connecting_ip
        self.client = type("C", (), {"host": ip})()


def _jwt_with_payload(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.sig"


def test_get_user_id_or_ip_returns_uid_from_sub():
    req = _Req(auth=f"Bearer {_jwt_with_payload({'sub': 'user_1'})}", ip="1.2.3.4")
    assert get_user_id_or_ip(req) == "user_1"


def test_get_user_id_or_ip_returns_uid_from_uid_field():
    req = _Req(auth=f"Bearer {_jwt_with_payload({'uid': 'user_2'})}", ip="1.2.3.4")
    assert get_user_id_or_ip(req) == "user_2"


def test_get_user_id_or_ip_falls_back_to_ip_on_bad_token():
    req = _Req(auth="Bearer not.a.jwt", ip="5.6.7.8")
    assert get_user_id_or_ip(req) == "5.6.7.8"


def test_get_user_id_or_ip_falls_back_to_ip_when_missing_auth():
    req = _Req(auth=None, ip="9.9.9.9")
    assert get_user_id_or_ip(req) == "9.9.9.9"


def test_ip_only_key_ignores_rotating_unsigned_sub():
    first = _Req(
        auth=f"Bearer {_jwt_with_payload({'sub': 'attacker-a'})}", ip="10.0.0.1"
    )
    second = _Req(
        auth=f"Bearer {_jwt_with_payload({'sub': 'attacker-b'})}", ip="10.0.0.1"
    )
    assert get_ip_only_rate_limit_key(first) == get_ip_only_rate_limit_key(second)
    assert get_ip_only_rate_limit_key(first) == "10.0.0.1"


@pytest.mark.parametrize(
    ("forwarded", "expected"),
    [
        ("93.184.216.9", "93.184.216.9"),
        # Render keeps a client-sent header and appends what it saw.
        ("1.1.1.1, 93.184.216.9", "93.184.216.9"),
        ("1.1.1.1, 93.184.216.9, 10.20.30.40", "93.184.216.9"),
        ("2402:800:629d::1, 10.0.0.2", "2402:800:629d::1"),
        ("not-an-ip, 93.184.216.9", "93.184.216.9"),
        ("10.0.0.2, 192.168.1.1", "10.9.9.9"),
        ("", "10.9.9.9"),
    ],
)
def test_client_ip_reads_the_rightmost_public_forwarded_address(forwarded, expected):
    assert get_client_ip(_Req(None, "10.9.9.9", forwarded)) == expected


def test_client_ip_ignores_a_spoofed_leftmost_address():
    real = _Req(None, "10.9.9.9", "93.184.216.9")
    spoofed = _Req(None, "10.9.9.9", "45.33.32.77, 93.184.216.9")
    assert get_client_ip(spoofed) == get_client_ip(real)


def test_ip_keys_use_forwarded_client_not_the_proxy_peer():
    first = _Req(None, "10.0.0.1", "93.184.216.1")
    second = _Req(None, "10.0.0.1", "93.184.216.2")
    assert get_ip_only_rate_limit_key(first) != get_ip_only_rate_limit_key(second)
    assert get_user_id_or_ip(first) == "93.184.216.1"


def test_spoofed_cf_connecting_ip_from_a_non_cloudflare_hop_is_ignored():
    # A client can send CF-Connecting-IP. It counts only when the hop #761
    # already trusts is a Cloudflare address. A CF-looking entry on the left
    # does not make the right-most public hop Cloudflare.
    spoofed = _Req(
        None,
        "10.9.9.9",
        "104.16.0.1, 93.184.216.9",
        cf_connecting_ip="8.8.8.8",
    )
    assert get_client_ip(spoofed) == "93.184.216.9"
    assert _rightmost_public_or_peer(spoofed) == "93.184.216.9"

    # No public XFF hop: the socket peer is Render's proxy, not Cloudflare.
    peer_only = _Req(None, "10.9.9.9", cf_connecting_ip="8.8.8.8")
    assert get_client_ip(peer_only) == "10.9.9.9"


def test_request_via_cloudflare_edge_uses_cf_connecting_ip():
    edge = "104.18.1.1"  # inside vendored 104.16.0.0/13
    visitor = "93.184.216.9"
    request = _Req(
        None,
        "10.9.9.9",
        f"{visitor}, {edge}",
        cf_connecting_ip=visitor,
    )
    assert get_client_ip(request) == visitor
    assert get_ip_only_rate_limit_key(request) == visitor


def test_761_rightmost_public_on_the_cloudflare_chain_is_the_edge():
    """client -> Cloudflare -> Render -> uvicorn.

    Cloudflare appends the client IP to X-Forwarded-For and sets
    CF-Connecting-IP. Render's proxy appends the Cloudflare edge IP.
    #761's right-most public hop is that edge, so every visitor on the edge
    would share one bucket. The key uses CF-Connecting-IP instead.
    """
    visitor = "93.184.216.9"
    edge = "104.18.1.1"
    request = _Req(
        None,
        "10.20.0.8",
        f"45.33.32.77, {visitor}, {edge}",
        cf_connecting_ip=visitor,
    )

    assert _rightmost_public_or_peer(request) == edge
    assert get_client_ip(request) == visitor
    assert get_ip_only_rate_limit_key(request) == visitor


def test_direct_onrender_traffic_is_unchanged_from_761():
    # Render recorded the visitor. There is no Cloudflare hop, so a
    # CF-Connecting-IP header does not replace the #761 address.
    forwarded = "1.1.1.1, 93.184.216.9"
    direct = _Req(None, "10.9.9.9", forwarded)
    with_header = _Req(None, "10.9.9.9", forwarded, cf_connecting_ip="8.8.8.8")
    assert _rightmost_public_or_peer(direct) == "93.184.216.9"
    assert get_client_ip(direct) == "93.184.216.9"
    assert get_client_ip(with_header) == "93.184.216.9"


def test_ipv6_cloudflare_range_uses_cf_connecting_ip():
    edge = "2606:4700::1"  # inside vendored 2606:4700::/32
    visitor = "2402:800:629d::1"
    request = _Req(
        None,
        "10.9.9.9",
        f"{visitor}, {edge}",
        cf_connecting_ip=visitor,
    )
    assert _rightmost_public_or_peer(request) == edge
    assert get_client_ip(request) == visitor


def test_cloudflare_socket_peer_uses_cf_connecting_ip_when_xff_has_no_public_hop():
    request = _Req(None, "104.16.0.1", "", cf_connecting_ip="93.184.216.9")
    assert _rightmost_public_or_peer(request) == "104.16.0.1"
    assert get_client_ip(request) == "93.184.216.9"


def test_missing_cf_connecting_ip_on_a_cloudflare_hop_falls_back_to_761():
    edge = "104.18.1.1"
    request = _Req(None, "10.9.9.9", f"93.184.216.9, {edge}")
    assert get_client_ip(request) == edge


@pytest.mark.parametrize("header", ["not-an-ip", "", "999.1.1.1"])
def test_malformed_cf_connecting_ip_falls_back_to_761(header):
    edge = "172.64.1.1"  # inside vendored 172.64.0.0/13
    request = _Req(
        None,
        "10.9.9.9",
        f"93.184.216.9, {edge}",
        cf_connecting_ip=header,
    )
    assert _rightmost_public_or_peer(request) == edge
    assert get_client_ip(request) == edge


@pytest.mark.asyncio
async def test_registered_preview_rate_limit_envelope_has_retry_after():
    response = await rate_limit_exceeded_handler(
        _Req(None, "1.2.3.4"), type("Limit", (), {"retry_after": 23})()
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "23"
    assert (
        response.body
        == b'{"error":"rate_limit_exceeded","code":"preview_rate_limited"}'
    )
