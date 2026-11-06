"""Every operation in the pinned spec is callable, on both clients, and hits the right route."""

from __future__ import annotations

import inspect
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx
import pytest
import respx

from horus import AsyncHorus, Horus, SyncPage
from horus.resources._index import OPERATIONS

ROOT = Path(__file__).resolve().parent.parent
SPEC = json.loads((ROOT.parent / "openapi" / "openapi.json").read_text(encoding="utf-8"))
METHODS = ("get", "post", "put", "patch", "delete")

SPEC_OPS: Dict[str, Tuple[str, str]] = {
    op["operationId"]: (method, path)
    for path, item in SPEC["paths"].items()
    for method, op in item.items()
    if method in METHODS
}
IDEMPOTENT = {
    op["operationId"]
    for item in SPEC["paths"].values()
    for method, op in item.items()
    if method in METHODS and any(p["name"] == "Idempotency-Key" for p in op.get("parameters", []))
}
LIST_BODY = {"object": "list", "data": [], "has_more": False, "next_cursor": None}


def test_every_spec_operation_is_indexed() -> None:
    assert set(OPERATIONS) == set(SPEC_OPS)
    assert len(OPERATIONS) == 176


def test_index_matches_names_from_the_brief() -> None:
    assert OPERATIONS["listings.list"] == (("listings",), "list")
    assert OPERATIONS["accounts.jobs.create"] == (("accounts", "jobs"), "create")
    assert OPERATIONS["sales.ship"] == (("sales",), "ship")
    assert OPERATIONS["messages.markRead"] == (("messages",), "mark_read")
    assert OPERATIONS["ebayOrders.fulfill"] == (("ebay_orders",), "fulfill")


def _resolve(client: object, op_id: str) -> Any:
    namespace, method = OPERATIONS[op_id]
    target = client
    for part in namespace:
        target = getattr(target, part)
    return getattr(target, method)


def _call_args(fn: Any) -> Tuple[List[str], Dict[str, str]]:
    positional: List[str] = []
    keywords: Dict[str, str] = {}
    for i, (name, param) in enumerate(inspect.signature(fn).parameters.items()):
        if param.kind is param.POSITIONAL_OR_KEYWORD:
            positional.append(f"p{i}")
        elif param.kind is param.KEYWORD_ONLY and param.default is param.empty:
            keywords[name] = "x"
    return positional, keywords


def _expected_path(path: str, positional: List[str]) -> str:
    values = iter(positional)
    return "/api/v1" + re.sub(r"\{[^}]+\}", lambda _m: next(values), path)


def _respond(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=LIST_BODY if request.method == "GET" else {"object": "x", "id": "x"})


@pytest.mark.parametrize("op_id", sorted(SPEC_OPS))
def test_sync_operation_hits_its_route(op_id: str, client: Horus, mock_api: respx.MockRouter) -> None:
    route = mock_api.route().mock(side_effect=_respond)
    fn = _resolve(client, op_id)
    positional, keywords = _call_args(fn)
    result = fn(*positional, **keywords)
    method, path = SPEC_OPS[op_id]
    request = route.calls.last.request
    assert (request.method, request.url.path) == (method.upper(), _expected_path(path, positional))
    if isinstance(result, SyncPage):
        assert result.data == []
    assert ("idempotency-key" in request.headers) == (op_id in IDEMPOTENT)


@pytest.mark.parametrize("op_id", sorted(SPEC_OPS))
async def test_async_operation_hits_its_route(op_id: str, async_client: AsyncHorus, mock_api: respx.MockRouter) -> None:
    route = mock_api.route().mock(side_effect=_respond)
    fn = _resolve(async_client, op_id)
    positional, keywords = _call_args(fn)
    result = fn(*positional, **keywords)
    await result
    method, path = SPEC_OPS[op_id]
    request = route.calls.last.request
    assert (request.method, request.url.path) == (method.upper(), _expected_path(path, positional))


def test_generated_code_is_up_to_date() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate.py"), "--check"], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
