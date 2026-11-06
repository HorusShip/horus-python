from __future__ import annotations

import json

import httpx
import pytest
import respx

from horus import Horus, HorusError, __version__
from horus.types import Job, Sale

from .conftest import API_KEY, BASE_URL, job


def test_sends_bearer_key_and_sdk_headers(client: Horus, mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/jobs/job_1").respond(json=job())
    result = client.jobs.get("job_1")
    assert isinstance(result, Job)
    assert result.status == "QUEUED"
    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["user-agent"].startswith(f"horus-sdk-python/{__version__}")
    assert "idempotency-key" not in request.headers


def test_reads_api_key_and_base_url_from_env(monkeypatch: pytest.MonkeyPatch, mock_api: respx.MockRouter) -> None:
    monkeypatch.setenv("HORUS_API_KEY", "ak_env_key")
    monkeypatch.setenv("HORUS_BASE_URL", BASE_URL)
    route = mock_api.get("/jobs/job_1").respond(json=job())
    with Horus() as c:
        c.jobs.get("job_1")
    assert route.calls.last.request.headers["authorization"] == "Bearer ak_env_key"


def test_missing_credentials_is_a_clear_error() -> None:
    with pytest.raises(HorusError, match="HORUS_API_KEY"):
        Horus()


def test_rejects_two_credentials() -> None:
    with pytest.raises(HorusError, match="only one"):
        Horus(api_key="ak_a", access_token="tok")


def test_query_params_skip_not_given_and_serialise_bools(client: Horus, mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/listings").respond(
        json={"object": "list", "data": [], "has_more": False, "next_cursor": None}
    )
    client.listings.list(status="LISTED", is_bundle=True, limit=5)
    params = route.calls.last.request.url.params
    assert dict(params) == {"status": "LISTED", "is_bundle": "true", "limit": "5"}


def test_body_sends_only_given_fields_and_explicit_none(client: Horus, mock_api: respx.MockRouter) -> None:
    route = mock_api.post("/webhooks").respond(201, json={"id": "wh_1", "object": "webhook"})
    client.webhooks.create(url="https://example.com/h", description=None)
    body = json.loads(route.calls.last.request.content)
    assert body == {"url": "https://example.com/h", "description": None}


def test_path_params_are_escaped(client: Horus, mock_api: respx.MockRouter) -> None:
    route = mock_api.get(url__regex=r".*/listings/prescans/AMAZON/B0%2F1$").respond(json={"object": "prescan"})
    client.listings.prescans.get("AMAZON", "B0/1")
    assert route.called


def test_empty_path_param_is_rejected(client: Horus) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        client.sales.get("")


def test_writes_get_an_idempotency_key(client: Horus, mock_api: respx.MockRouter) -> None:
    route = mock_api.post("/sales/s_1/ship").respond(json={"object": "sale", "id": "s_1"})
    client.sales.ship("s_1", tracking_number="1Z999", carrier="UPS")
    request = route.calls.last.request
    assert request.headers["idempotency-key"].startswith("horus-py-")
    assert json.loads(request.content) == {"tracking_number": "1Z999", "carrier": "UPS"}


def test_explicit_idempotency_key_is_used(client: Horus, mock_api: respx.MockRouter) -> None:
    route = mock_api.post("/sales/s_1/ship").respond(json={"object": "sale", "id": "s_1"})
    client.sales.ship("s_1", idempotency_key="my-key")
    assert route.calls.last.request.headers["idempotency-key"] == "my-key"


def test_delete_with_body(client: Horus, mock_api: respx.MockRouter) -> None:
    route = mock_api.delete("/account").respond(json={"object": "profile_deleted"})
    client.account.delete(confirm_email="me@example.com")
    assert json.loads(route.calls.last.request.content) == {"confirm_email": "me@example.com"}


def test_request_options(client: Horus, mock_api: respx.MockRouter) -> None:
    route = mock_api.post("/sales/s_1/ship").respond(json={"object": "sale", "id": "s_1"})
    client.sales.ship(
        "s_1",
        options={
            "headers": {"X-Trace": "t1"},
            "extra_query": {"debug": "1"},
            "extra_body": {"beta": True},
            "timeout": 5,
        },
    )
    request = route.calls.last.request
    assert request.headers["x-trace"] == "t1"
    assert request.url.params["debug"] == "1"
    assert json.loads(request.content) == {"beta": True}
    assert request.extensions["timeout"]["read"] == 5


def test_raw_request_escape_hatch(client: Horus, mock_api: respx.MockRouter) -> None:
    route = mock_api.post("/listings/l_1/end").respond(json={"ok": True})
    assert client.request("POST", "listings/l_1/end", body={"x": 1}) == {"ok": True}
    request = route.calls.last.request
    assert json.loads(request.content) == {"x": 1}
    assert "idempotency-key" in request.headers


def test_unknown_fields_and_enum_values_do_not_break_parsing(client: Horus, mock_api: respx.MockRouter) -> None:
    mock_api.get("/jobs/job_1").respond(json=job(status="PAUSED", brand_new_field=1))
    result = client.jobs.get("job_1")
    assert result.to_dict()["status"] == "PAUSED"
    assert result.model_extra == {"brand_new_field": 1}


def test_nested_models_are_typed(client: Horus, mock_api: respx.MockRouter) -> None:
    sale = {"object": "sale", "id": "s_1", "nested_unknown": {"a": 1}}
    mock_api.post("/sales/s_1/ship").respond(json=sale)
    result = client.sales.ship("s_1")
    assert isinstance(result, Sale)
    assert result.id == "s_1"
    assert result.to_dict()["nested_unknown"] == {"a": 1}


def test_custom_http_client_is_not_closed() -> None:
    http = httpx.Client()
    with Horus(api_key="ak_x", http_client=http):
        pass
    assert not http.is_closed
    http.close()
