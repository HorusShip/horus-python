"""Base class for the generated response models, and forgiving response parsing.

Responses are validated into pydantic models. If the server sends something the
models don't know yet (a new enum value, a new nullable field), the SDK still hands
back a model built from the raw data instead of failing the call: forward
compatibility beats strictness for an API client.
"""

from __future__ import annotations

import logging
import sys
import types
from typing import Any, Dict, Mapping, Type, TypeVar, Union, get_args, get_origin

import pydantic
from pydantic import ConfigDict

__all__ = ["HorusModel", "parse_model"]

log = logging.getLogger("horus")

M = TypeVar("M", bound=pydantic.BaseModel)

_UNION_TYPES: tuple[object, ...] = (Union, types.UnionType) if sys.version_info >= (3, 10) else (Union,)


class HorusModel(pydantic.BaseModel):
    """Every API object. Unknown fields are kept (``model.model_extra``), never dropped."""

    model_config = ConfigDict(extra="allow", populate_by_name=True, protected_namespaces=())

    def to_dict(self) -> Dict[str, Any]:
        """The object as JSON-ready ``dict`` (API field names, unset fields omitted)."""
        return self.model_dump(mode="json", by_alias=True, exclude_unset=True)

    def to_json(self, *, indent: int | None = 2) -> str:
        """The object as JSON text."""
        return self.model_dump_json(by_alias=True, exclude_unset=True, indent=indent)


def parse_model(model: Type[M], data: object) -> M:
    """Validate ``data`` into ``model``; on mismatch, build it without validation."""
    try:
        return model.model_validate(data)
    except pydantic.ValidationError as exc:
        if not isinstance(data, Mapping):
            raise
        log.debug("horus: %s did not validate (%s); using unvalidated data", model.__name__, exc.error_count())
        return _construct(model, data)


def _construct(model: Type[M], data: Mapping[str, object]) -> M:
    """Recursive ``model_construct``: nested objects become models where they fit."""
    values: Dict[str, Any] = {}
    by_alias = {(f.alias or name): name for name, f in model.model_fields.items()}
    for key, raw in data.items():
        name = by_alias.get(key, key)
        field = model.model_fields.get(name)
        values[name] = _coerce(field.annotation, raw) if field is not None else raw
    return model.model_construct(_fields_set=set(values), **values)


def _coerce(annotation: object, raw: object) -> object:
    """Best-effort conversion of ``raw`` to ``annotation`` (models and lists of models only)."""
    target = _model_in(annotation)
    if isinstance(raw, Mapping) and target is not None:
        return parse_model(target, raw)
    if isinstance(raw, list):
        item = _list_item(annotation)
        if item is not None:
            return [_coerce(item, v) for v in raw]
    return raw


def _model_in(annotation: object) -> Type[pydantic.BaseModel] | None:
    """The pydantic model inside ``annotation`` (``X``, ``Optional[X]``, ``Annotated[X, ...]``)."""
    if isinstance(annotation, type) and issubclass(annotation, pydantic.BaseModel):
        return annotation
    origin = get_origin(annotation)
    if origin in _UNION_TYPES or _is_annotated(annotation):
        for arg in get_args(annotation):
            found = _model_in(arg)
            if found is not None:
                return found
    return None


def _list_item(annotation: object) -> object | None:
    """The item type of ``List[X]`` / ``Optional[List[X]]``."""
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        return args[0] if args else None
    if origin in _UNION_TYPES or _is_annotated(annotation):
        for arg in get_args(annotation):
            item = _list_item(arg)
            if item is not None:
                return item
    return None


def _is_annotated(annotation: object) -> bool:
    return hasattr(annotation, "__metadata__")
