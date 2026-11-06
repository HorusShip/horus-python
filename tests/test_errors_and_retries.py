from __future__ import annotations

from typing import List, Type

import httpx
import pytest
import respx

from horus import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncHorus,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    DesktopOfflineError,
    Horus,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    UnprocessableEntityError,
)
from horus._retry import parse_retry_after, retry_delay

from .conftest import error, job


@pytest.mark.parametrize(
    ("status", "kind", "code", "cls"),
    [
        (400, "invalid_request", "bad", BadRequestError),
        (401, "authentication", "invalid_api_key", AuthenticationError),
        (403, "permission", "insufficient_scope", PermissionDeniedError),
        (404, "not_found", "listing_not_found", NotFoundError),
        (409, "conflict", "listing_not_ended", ConflictError),
        (422, "invalid_request", "validation_failed", UnprocessableEntityError),
        (500, "internal", "internal_error", InternalServerError),
        (503, "unavailable", "ebay_unavailable", ServiceUnavailableError),
        (503, "unavailable", "desktop_offline", DesktopOfflineError),
    ],
)
def test_status_maps_to_exception(
    client: Horus, mock_api: respx.MockRouter, status: int, kind: str, code: str, cls: Type[APIStatusError]
) -> None:
    mock_api.get("/listings/l_1").respond(status, json=error(kind, code, details={"required": ["listings:read"]}))
    with pytest.raises(cls) as info:
        client.listings.get("l_1", options={"max_retries": 0})
    exc = info.value
    assert type(exc) is cls
    assert (exc.status_code, exc.type, exc.code, exc.request_id) == (status, kind, code, "req_1")
    assert exc.details == {"required": ["listings:read"]}
    assert code in str(exc)


def test_non_json_error_body(client: Horus, mock_api: respx.MockRouter) -> None:
    mock_api.get("/me").respond(502, text="<html>Bad gateway</html>", headers={"Request-Id": "req_h"})
    with pytest.raises(InternalServerError) as info:
        client.me.get(options={"max_retries": 0})
    assert info.value.code == "http_502"
    assert info.value.request_id == "req_h"


def test_rate_limit_error_exposes_retry_after(client: Horus, mock_api: respx.MockRouter) -> None:
    mock_api.get("/me").respond(429, json=error("rate_limited", "rate_limited"), headers={"Retry-After": "7"})
    with pytest.raises(RateLimitError) as info:
        client.me.get(options={"max_retries": 0})
    assert info.value.retry_after == 7.0


def test_retries_5xx_with_the_same_idempotency_key(
    client: Horus, mock_api: respx.MockRouter, delays: List[float]
) -> None:
    route = mock_api.post("/accounts/acc_1/verify").mock(
        side_effect=[httpx.Response(500, json=error("internal", "internal_error")), httpx.Response(202, json=job())]
    )
    result = client.accounts.verify("acc_1")
    assert result.id == "job_1"
    keys = {call.request.headers["idempotency-key"] for call in route.calls}
    assert route.call_count == 2 and len(keys) == 1
    assert len(delays) == 1


def test_retry_after_is_honoured(client: Horus, mock_api: respx.MockRouter, delays: List[float]) -> None:
    mock_api.get("/me").mock(
        side_effect=[
            httpx.Response(429, json=error("rate_limited", "rate_limited"), headers={"Retry-After": "3"}),
            httpx.Response(200, json={"object": "me", "id": "u_1"}),
        ]
    )
    client.me.get()
    assert delays == [3.0]


def test_gives_up_after_max_retries(client: Horus, mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/me").respond(503, json=error("unavailable", "busy"))
    with pytest.raises(ServiceUnavailableError):
        client.me.get()
    assert route.call_count == 3  # 1 + max_retries=2


def test_per_call_max_retries(client: Horus, mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/me").respond(503, json=error("unavailable", "busy"))
    with pytest.raises(ServiceUnavailableError):
        client.me.get(options={"max_retries": 5})
    assert route.call_count == 6


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "bad"), (404, "listing_not_found"), (409, "idempotency_conflict"), (503, "desktop_offline")],
)
def test_does_not_retry(client: Horus, mock_api: respx.MockRouter, status: int, code: str) -> None:
    route = mock_api.post("/listings/l_1/end").respond(status, json=error("x", code))
    with pytest.raises(APIStatusError):
        client.listings.end("l_1")
    assert route.call_count == 1


def test_retries_idempotency_in_progress(client: Horus, mock_api: respx.MockRouter) -> None:
    route = mock_api.post("/listings/l_1/end").mock(
        side_effect=[
            httpx.Response(409, json=error("conflict", "idempotency_in_progress")),
            httpx.Response(200, json={"object": "listing", "id": "l_1"}),
        ]
    )
    client.listings.end("l_1")
    assert route.call_count == 2


def test_connection_errors_are_retried_then_raised(client: Horus, mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/me").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(APIConnectionError):
        client.me.get()
    assert route.call_count == 3


def test_timeout_raises_api_timeout_error(client: Horus, mock_api: respx.MockRouter) -> None:
    mock_api.get("/me").mock(side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json={"object": "me"})])
    client.me.get()  # retried once, then OK
    mock_api.get("/me").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(APITimeoutError):
        client.me.get(options={"max_retries": 0})


async def test_async_retries_and_errors(async_client: AsyncHorus, mock_api: respx.MockRouter) -> None:
    mock_api.post("/accounts/acc_1/verify").mock(
        side_effect=[httpx.Response(502, text="bad"), httpx.Response(202, json=job())]
    )
    assert (await async_client.accounts.verify("acc_1")).id == "job_1"
    mock_api.get("/listings/nope").respond(404, json=error("not_found", "listing_not_found"))
    with pytest.raises(NotFoundError):
        await async_client.listings.get("nope")


def test_backoff_grows_and_is_capped() -> None:
    assert 0.375 <= retry_delay(0) <= 0.5
    assert 0.75 <= retry_delay(1) <= 1.0
    assert retry_delay(10) <= 8.0
    assert retry_delay(0, retry_after=2.5) == 2.5
    assert retry_delay(0, retry_after=3600) <= 0.5  # absurd Retry-After falls back to backoff


def test_parse_retry_after_variants() -> None:
    assert parse_retry_after(httpx.Headers({"Retry-After": "12"})) == 12.0
    assert parse_retry_after(httpx.Headers({"Retry-After": "Thu, 01 Jan 2026 00:00:10 GMT"}), now=1767225600.0) == 10.0
    assert parse_retry_after(httpx.Headers({"RateLimit-Reset": "4"})) == 4.0
    assert parse_retry_after(httpx.Headers({})) is None
