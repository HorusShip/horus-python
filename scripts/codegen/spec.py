"""Reads the OpenAPI document into small, typed descriptions of each operation."""

from __future__ import annotations

import json
import keyword
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Parsed JSON is dynamic by nature; the generator treats it as such.
Schema = Dict[str, Any]

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
RESERVED_ARGS = frozenset({"self", "options", "idempotency_key"})


@dataclass(frozen=True)
class Param:
    """One argument of an SDK method (path, query or body field)."""

    name: str
    """Wire name."""
    py_name: str
    schema: Schema
    required: bool
    location: str
    description: Optional[str] = None


@dataclass(frozen=True)
class OperationInfo:
    op_id: str
    method: str
    path: str
    summary: str
    description: Optional[str]
    namespace: Tuple[str, ...]
    """snake_case attribute chain, e.g. ``("accounts", "jobs")``."""
    py_method: str
    path_params: List[Param]
    query_params: List[Param]
    body_params: List[Param]
    has_body: bool
    idempotent: bool
    status: str
    response_model: str
    page_item: Optional[str]
    requires_desktop: bool
    scopes: Tuple[str, ...]
    body_ref: Optional[str] = field(default=None)

    @property
    def const_name(self) -> str:
        return "_" + "_".join((*self.namespace, self.py_method)).upper()


def snake(name: str) -> str:
    """``bulkDelete`` -> ``bulk_delete``, ``ebayOrders`` -> ``ebay_orders``."""
    out = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    return re.sub(r"[^0-9a-zA-Z_]", "_", out).lower()


def pascal(name: str) -> str:
    """``ebay_orders`` / ``ebayOrders`` / ``sale.shipped`` -> ``EbayOrders`` / ``SaleShipped``."""
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"[^0-9a-zA-Z]+", snake(name)) if part)


def py_ident(name: str) -> str:
    ident = snake(name)
    return ident + "_" if keyword.iskeyword(ident) or ident in RESERVED_ARGS else ident


def ref_name(schema: Mapping[str, Any]) -> Optional[str]:
    ref = schema.get("$ref")
    return ref.rsplit("/", 1)[-1] if isinstance(ref, str) else None


class Spec:
    def __init__(self, document: Schema) -> None:
        self.doc = document
        components = document.get("components")
        self.schemas: Dict[str, Schema] = dict(components.get("schemas", {})) if isinstance(components, dict) else {}

    @classmethod
    def load(cls, path: Path) -> Spec:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def resolve(self, schema: Mapping[str, Any]) -> Schema:
        name = ref_name(schema)
        return self.schemas[name] if name else dict(schema)

    def raw_operations(self) -> List[Tuple[str, str, Schema]]:
        paths = self.doc.get("paths")
        assert isinstance(paths, dict)
        out: List[Tuple[str, str, Schema]] = []
        for path, item in paths.items():
            for method in HTTP_METHODS:
                if method in item:
                    out.append((path, method, item[method]))
        return out

    def operations(self) -> List[OperationInfo]:
        ops = [self._operation(path, method, op) for path, method, op in self.raw_operations()]
        return sorted(ops, key=lambda o: o.op_id)

    def webhook_events(self) -> List[Tuple[str, str]]:
        """``(event type, payload schema name)`` for every webhook in the spec."""
        hooks = self.doc.get("webhooks")
        out: List[Tuple[str, str]] = []
        for name, item in (hooks or {}).items() if isinstance(hooks, dict) else []:
            schema = item["post"]["requestBody"]["content"]["application/json"]["schema"]
            ref = ref_name(schema)
            assert ref, f"webhook {name} payload must be a $ref"
            out.append((name, ref))
        return out

    # -- operation parsing ---------------------------------------------------------------
    def _operation(self, path: str, method: str, op: Schema) -> OperationInfo:
        op_id = str(op["operationId"])
        parts = op_id.split(".")
        params = op.get("parameters", [])
        assert isinstance(params, list)
        status, response_schema = self._success(op)
        body_schema, body_ref = self._body(op)
        return OperationInfo(
            op_id=op_id,
            method=method,
            path=path,
            summary=str(op.get("summary") or ""),
            description=op.get("description") if isinstance(op.get("description"), str) else None,
            namespace=tuple(snake(p) for p in parts[:-1]),
            py_method=py_ident(parts[-1]),
            path_params=self._params(params, "path"),
            query_params=self._params(params, "query"),
            body_params=self._body_params(body_schema),
            has_body=body_schema is not None,
            idempotent=any(isinstance(p, dict) and p.get("name") == "Idempotency-Key" for p in params),
            status=status,
            response_model=self.response_model_name(op_id, response_schema),
            page_item=self._page_item(response_schema, params),
            requires_desktop=bool(op.get("x-horus-requires-desktop")),
            scopes=tuple(str(s) for s in op.get("x-horus-scopes", []) or []),
            body_ref=body_ref,
        )

    def _params(self, params: List[Schema], location: str) -> List[Param]:
        out = []
        for p in params:
            assert isinstance(p, dict)
            if p.get("in") != location:
                continue
            out.append(
                Param(
                    str(p["name"]),
                    py_ident(str(p["name"])),
                    p.get("schema", {}),
                    bool(p.get("required")),
                    location,
                    p.get("description"),
                )
            )
        return out

    def _body(self, op: Schema) -> Tuple[Optional[Schema], Optional[str]]:
        body = op.get("requestBody")
        if not isinstance(body, dict):
            return None, None
        schema = body["content"]["application/json"]["schema"]
        return self.resolve(schema), ref_name(schema)

    def _body_params(self, schema: Optional[Schema]) -> List[Param]:
        if schema is None:
            return []
        props = schema.get("properties", {})
        required = set(schema.get("required", []) or [])
        assert isinstance(props, dict)
        return [
            Param(name, py_ident(name), s, name in required, "body", s.get("description")) for name, s in props.items()
        ]

    def _success(self, op: Schema) -> Tuple[str, Schema]:
        responses = op["responses"]
        assert isinstance(responses, dict)
        for code, resp in responses.items():
            if code.startswith("2"):
                return code, resp.get("content", {}).get("application/json", {}).get("schema", {})
        raise ValueError(f"{op['operationId']}: no 2xx response")

    def response_model_name(self, op_id: str, schema: Schema) -> str:
        return ref_name(schema) or f"{pascal(op_id.replace('.', '_'))}Response"

    def _page_item(self, schema: Schema, params: List[Schema]) -> Optional[str]:
        has_cursor = any(isinstance(p, dict) and p.get("in") == "query" and p.get("name") == "cursor" for p in params)
        model = self.resolve(schema) if schema else {}
        props = model.get("properties", {})
        if not has_cursor or not isinstance(props, dict) or "next_cursor" not in props:
            return None
        items = props["data"].get("items", {})
        name = ref_name(items)
        assert name, "paginated list items must be a $ref"
        return name
