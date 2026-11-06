from __future__ import annotations

import base64
import json
import time
from typing import List, Optional

import httpx
import pytest
import respx

from horus import AsyncHorus, AuthenticationError, Horus, OAuthCredentials, TokenRefreshError

from .conftest import BASE_URL, error

ISSUER = "https://auth.example.test"
TOKEN_URL = f"{ISSUER}/oauth2/token"


def jwt(**claims: object) -> str:
    def part(obj: object) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.sig"


def token_response(access: str, refresh: str = "rt_2") -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": access, "refresh_token": refresh, "expires_in": 3600, "scope": "listings:read"}
    )


def creds(
    seen: List[OAuthCredentials], *, access_token: Optional[str] = None, token_endpoint: Optional[str] = None
) -> OAuthCredentials:
    return OAuthCredentials(
        access_token=access_token or jwt(iss=ISSUER, exp=time.time() + 3600),
        refresh_token="rt_1",
        client_id="client_cli",
        token_endpoint=token_endpoint,
        on_refresh=seen.append,
    )


@pytest.mark.usefixtures("delays")
def test_expiring_token_is_refreshed_before_the_call(mock_api: respx.MockRouter) -> None:
    seen: List[OAuthCredentials] = []
    oauth = creds(seen, access_token=jwt(iss=ISSUER, exp=time.time() + 10), token_endpoint=TOKEN_URL)
    token = mock_api.post(TOKEN_URL).mock(return_value=token_response("at_new"))
    me = mock_api.get("/me").respond(json={"object": "me"})
    with Horus(oauth=oauth, base_url=BASE_URL) as client:
        client.me.get()
    form = dict(httpx.QueryParams(token.calls.last.request.content.decode()))
    assert form == {"grant_type": "refresh_token", "refresh_token": "rt_1", "client_id": "client_cli"}
    assert me.calls.last.request.headers["authorization"] == "Bearer at_new"
    assert oauth.refresh_token == "rt_2" and seen == [oauth]
    assert oauth.expires_at is not None and oauth.expires_at > time.time() + 3000


@pytest.mark.usefixtures("delays")
def test_401_invalid_access_token_refreshes_once_via_discovery(mock_api: respx.MockRouter) -> None:
    seen: List[OAuthCredentials] = []
    oauth = creds(seen)
    discovery = mock_api.get(f"{ISSUER}/.well-known/openid-configuration").respond(json={"token_endpoint": TOKEN_URL})
    mock_api.post(TOKEN_URL).mock(return_value=token_response("at_new"))
    me = mock_api.get("/me").mock(
        side_effect=[
            httpx.Response(401, json=error("authentication", "invalid_access_token")),
            httpx.Response(200, json={"object": "me"}),
        ]
    )
    with Horus(oauth=oauth, base_url=BASE_URL) as client:
        client.me.get()
    assert discovery.called and oauth.token_endpoint == TOKEN_URL
    assert [c.request.headers["authorization"] for c in me.calls][1] == "Bearer at_new"


@pytest.mark.usefixtures("delays")
def test_second_401_after_refresh_is_raised(mock_api: respx.MockRouter) -> None:
    oauth = creds([], token_endpoint=TOKEN_URL)
    mock_api.post(TOKEN_URL).mock(return_value=token_response("at_new"))
    mock_api.get("/me").respond(401, json=error("authentication", "invalid_access_token"))
    with Horus(oauth=oauth, base_url=BASE_URL) as client, pytest.raises(AuthenticationError):
        client.me.get()


@pytest.mark.usefixtures("delays")
def test_refresh_failure_raises(mock_api: respx.MockRouter) -> None:
    oauth = creds([], access_token=jwt(iss=ISSUER, exp=time.time() - 5), token_endpoint=TOKEN_URL)
    mock_api.post(TOKEN_URL).respond(400, json={"error": "invalid_grant"})
    with Horus(oauth=oauth, base_url=BASE_URL) as client, pytest.raises(TokenRefreshError, match="invalid_grant"):
        client.me.get()


def test_access_token_without_refresh_is_sent_as_is(mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/me").respond(json={"object": "me"})
    with Horus(access_token="opaque", base_url=BASE_URL) as client:
        client.me.get()
    assert route.calls.last.request.headers["authorization"] == "Bearer opaque"


@pytest.mark.usefixtures("delays")
async def test_async_refresh(mock_api: respx.MockRouter) -> None:
    oauth = creds([], access_token=jwt(iss=ISSUER, exp=time.time()), token_endpoint=TOKEN_URL)
    mock_api.post(TOKEN_URL).mock(return_value=token_response("at_async"))
    route = mock_api.get("/me").respond(json={"object": "me"})
    async with AsyncHorus(oauth=oauth, base_url=BASE_URL) as client:
        await client.me.get()
    assert route.calls.last.request.headers["authorization"] == "Bearer at_async"
