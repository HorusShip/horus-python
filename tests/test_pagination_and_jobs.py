from __future__ import annotations

import time
from typing import Dict, List, Optional

import httpx
import pytest
import respx

from horus import AsyncHorus, AsyncPage, Horus, JobFailedError, JobTimeoutError, SyncPage
from horus.types import Listing

from .conftest import job


def _page(ids: List[str], next_cursor: Optional[str]) -> Dict[str, object]:
    return {
        "object": "list",
        "data": [{"object": "listing", "id": i} for i in ids],
        "has_more": next_cursor is not None,
        "next_cursor": next_cursor,
    }


def _paged_listings(mock_api: respx.MockRouter) -> respx.Route:
    pages = {None: _page(["l1", "l2"], "c1"), "c1": _page(["l3"], "c2"), "c2": _page(["l4"], None)}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.params.get("cursor")])

    return mock_api.get("/listings").mock(side_effect=handler)


def test_sync_auto_pagination(client: Horus, mock_api: respx.MockRouter) -> None:
    route = _paged_listings(mock_api)
    page = client.listings.list(status="LISTED", limit=2)
    assert isinstance(page, SyncPage)
    assert [x.id for x in page.data] == ["l1", "l2"] and page.has_more and page.next_cursor == "c1"
    assert route.call_count == 1  # first page only, until iterated
    items = list(page)
    assert all(isinstance(x, Listing) for x in items)
    assert [x.id for x in items] == ["l1", "l2", "l3", "l4"]
    assert all(c.request.url.params["status"] == "LISTED" for c in route.calls)


def test_sync_pages_and_next_page(client: Horus, mock_api: respx.MockRouter) -> None:
    _paged_listings(mock_api)
    page = client.listings.list()
    assert [len(p) for p in page.pages()] == [2, 1, 1]
    last = page.next_page()
    assert last is not None
    final = last.next_page()
    assert final is not None and final.next_page() is None and not final.has_next_page()


async def test_async_auto_pagination(async_client: AsyncHorus, mock_api: respx.MockRouter) -> None:
    _paged_listings(mock_api)
    ids = [x.id async for x in async_client.listings.list()]
    assert ids == ["l1", "l2", "l3", "l4"]


async def test_async_first_page_and_pages(async_client: AsyncHorus, mock_api: respx.MockRouter) -> None:
    _paged_listings(mock_api)
    first = await async_client.listings.list()
    assert isinstance(first, AsyncPage) and [x.id for x in first.data] == ["l1", "l2"]
    sizes = [len(p) async for p in async_client.listings.list().pages()]
    assert sizes == [2, 1, 1]
    items = [x.id async for x in first]
    assert items == ["l1", "l2", "l3", "l4"]


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)


def _job_sequence(mock_api: respx.MockRouter, statuses: List[str]) -> respx.Route:
    responses = [httpx.Response(200, json=job(status=s)) for s in statuses]
    return mock_api.get("/jobs/job_1").mock(side_effect=responses)


@pytest.mark.usefixtures("no_sleep")
def test_wait_polls_until_finished(client: Horus, mock_api: respx.MockRouter) -> None:
    route = _job_sequence(mock_api, ["QUEUED", "RUNNING", "SUCCEEDED"])
    done = client.jobs.wait("job_1", timeout=60)
    assert done.status == "SUCCEEDED" and route.call_count == 3


@pytest.mark.usefixtures("no_sleep")
def test_wait_accepts_a_job_and_raises_on_failure(client: Horus, mock_api: respx.MockRouter) -> None:
    mock_api.post("/accounts/acc_1/verify").respond(202, json=job())
    _job_sequence(mock_api, ["RUNNING", "FAILED"])
    queued = client.accounts.verify("acc_1")
    with pytest.raises(JobFailedError) as info:
        client.jobs.wait(queued, raise_on_failure=True)
    assert info.value.job.status == "FAILED"


def test_wait_times_out(client: Horus, mock_api: respx.MockRouter) -> None:
    mock_api.get("/jobs/job_1").respond(json=job(status="RUNNING"))
    with pytest.raises(JobTimeoutError) as info:
        client.jobs.wait("job_1", timeout=0.05, poll_interval=0.01)
    assert info.value.job.status == "RUNNING"


async def test_async_wait(async_client: AsyncHorus, mock_api: respx.MockRouter) -> None:
    _job_sequence(mock_api, ["CLAIMED", "CANCELLED"])
    done = await async_client.jobs.wait("job_1", poll_interval=0.001)
    assert done.status == "CANCELLED"
