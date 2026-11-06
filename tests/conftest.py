from __future__ import annotations

from typing import AsyncIterator, Dict, Iterator, List, Optional

import httpx
import pytest
import respx

from horus import AsyncHorus, Horus
from horus._retry import retry_delay

BASE_URL = "https://api.horus.test/api/v1"
API_KEY = "ak_0123456789abcdef_secret"
# Building an SSL context per client is slow on Windows; requests are mocked anyway.
SSL_CONTEXT = httpx.create_ssl_context()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HORUS_API_KEY", raising=False)
    monkeypatch.delenv("HORUS_BASE_URL", raising=False)


@pytest.fixture
def delays(monkeypatch: pytest.MonkeyPatch) -> List[float]:
    """Retry delays the client asked for (no real sleeping)."""
    seen: List[float] = []

    def fake(attempt: int, retry_after: Optional[float] = None) -> float:
        seen.append(retry_delay(attempt, retry_after))
        return 0.0

    monkeypatch.setattr("horus._base_client.retry_delay", fake)
    return seen


@pytest.fixture
def mock_api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
def client(delays: List[float]) -> Iterator[Horus]:
    http = httpx.Client(verify=SSL_CONTEXT)
    with Horus(api_key=API_KEY, base_url=BASE_URL, http_client=http) as c:
        yield c
    http.close()


@pytest.fixture
async def async_client(delays: List[float]) -> AsyncIterator[AsyncHorus]:
    http = httpx.AsyncClient(verify=SSL_CONTEXT)
    async with AsyncHorus(api_key=API_KEY, base_url=BASE_URL, http_client=http) as c:
        yield c
    await http.aclose()


def job(status: str = "QUEUED", **extra: object) -> Dict[str, object]:
    return {
        "object": "job",
        "id": "job_1",
        "account_id": "acc_1",
        "kind": "VERIFY_SESSION",
        "status": status,
        "attempts": 0,
        "result": None,
        "error": None,
        "requested_at": "2026-11-06T00:00:00.000Z",
        "claimed_at": None,
        "finished_at": None,
        **extra,
    }


def error(status_type: str, code: str, message: str = "nope", **extra: object) -> Dict[str, object]:
    return {"error": {"type": status_type, "code": code, "message": message, "request_id": "req_1", **extra}}
