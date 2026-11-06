"""Operation descriptors (one per OpenAPI operation) and request building.

The generated resource methods hand an :class:`Operation` plus their arguments to the
client; everything that turns those into an HTTP request lives here, as pure functions
shared by the sync and async clients.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Tuple
from urllib.parse import quote

import pydantic

from ._types import NotGiven, RequestOptions

__all__ = ["Operation", "PreparedRequest", "prepare_request", "to_json_value", "new_idempotency_key"]

WRITE_METHODS = frozenset({"post", "put", "patch", "delete"})


@dataclass(frozen=True)
class Operation:
    """What the SDK knows about one API operation (from the OpenAPI spec)."""

    id: str
    """The spec's operationId, e.g. ``"listings.end"``."""
    method: str
    path: str
    """Path template relative to the base URL, e.g. ``"/listings/{id}/end"``."""
    path_params: Tuple[str, ...] = ()
    query_params: Tuple[str, ...] = ()
    body_params: Tuple[str, ...] = ()
    has_body: bool = False
    idempotent: bool = False
    """Sends an ``Idempotency-Key`` (generated when not given)."""
    paginated: bool = False
    requires_desktop: bool = False
    scopes: Tuple[str, ...] = field(default=())

    @property
    def is_write(self) -> bool:
        return self.method in WRITE_METHODS


@dataclass(frozen=True)
class PreparedRequest:
    """An HTTP request ready to send (minus auth, which is added per attempt)."""

    method: str
    path: str
    query: Dict[str, str]
    json: Optional[Dict[str, object]]
    headers: Dict[str, str]


def prepare_request(
    op: Operation,
    *,
    path: Mapping[str, object],
    params: Mapping[str, object],
    idempotency_key: Optional[str],
    options: Optional[RequestOptions],
) -> PreparedRequest:
    """Build the request for ``op`` from the method's arguments."""
    opts: RequestOptions = options or {}
    given = {k: v for k, v in params.items() if not isinstance(v, NotGiven)}
    return PreparedRequest(
        method=op.method.upper(),
        path=_render_path(op, path),
        query=_build_query(op, given, opts),
        json=_build_body(op, given, opts),
        headers=_build_headers(op, idempotency_key, opts),
    )


def _render_path(op: Operation, values: Mapping[str, object]) -> str:
    rendered = op.path
    for name in op.path_params:
        value = values.get(name)
        text = "" if value is None else str(value)
        if not text:
            raise ValueError(f"{op.id}: path parameter {name!r} must be a non-empty string")
        rendered = rendered.replace("{" + name + "}", quote(text, safe=""))
    return rendered


def _build_query(op: Operation, given: Mapping[str, object], opts: RequestOptions) -> Dict[str, str]:
    query = {name: _query_value(given[name]) for name in op.query_params if name in given and given[name] is not None}
    query.update(opts.get("extra_query", {}))
    return query


def _query_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return ",".join(_query_value(v) for v in value)
    return str(value)


def _build_body(op: Operation, given: Mapping[str, object], opts: RequestOptions) -> Optional[Dict[str, object]]:
    extra = opts.get("extra_body")
    if not op.has_body and not extra:
        return None
    body: Dict[str, object] = {name: to_json_value(given[name]) for name in op.body_params if name in given}
    if extra:
        body.update(extra)
    return body


def _build_headers(op: Operation, idempotency_key: Optional[str], opts: RequestOptions) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if op.idempotent:
        headers["Idempotency-Key"] = idempotency_key or new_idempotency_key()
    headers.update(opts.get("headers", {}))
    return headers


def new_idempotency_key() -> str:
    """A fresh key; the SDK reuses it across the retries of one call."""
    return f"horus-py-{uuid.uuid4()}"


def to_json_value(value: object) -> object:
    """Convert models, datetimes and containers into plain JSON values."""
    if isinstance(value, pydantic.BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_unset=True)
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): to_json_value(v) for k, v in value.items() if not isinstance(v, NotGiven)}
    if isinstance(value, (list, tuple)):
        return [to_json_value(v) for v in value]
    return value
