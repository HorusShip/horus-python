"""Small shared types: the ``NOT_GIVEN`` sentinel and per-request options."""

from __future__ import annotations

from typing import Dict, Mapping, Union

import httpx
from typing_extensions import Literal, TypedDict, final

__all__ = ["NOT_GIVEN", "NotGiven", "RequestOptions", "Timeout", "Headers"]


@final
class NotGiven:
    """Marks a keyword argument that was not passed (as opposed to an explicit ``None``).

    Omitted arguments are not sent at all; ``None`` is sent as JSON ``null`` (which is how a
    nullable field is cleared on a ``PATCH``).
    """

    _instance: NotGiven | None = None

    def __new__(cls) -> NotGiven:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> Literal[False]:
        return False

    def __repr__(self) -> str:
        return "NOT_GIVEN"


NOT_GIVEN = NotGiven()

Timeout = Union[float, httpx.Timeout, None]
Headers = Mapping[str, str]


class RequestOptions(TypedDict, total=False):
    """Per-call overrides, passed as ``options=`` to any SDK method."""

    timeout: Timeout
    """Seconds (or an ``httpx.Timeout``) for this call only."""
    max_retries: int
    """Retries for this call only (0 disables)."""
    headers: Dict[str, str]
    """Extra request headers."""
    extra_query: Dict[str, str]
    """Extra query parameters, for fields the SDK doesn't know about yet."""
    extra_body: Dict[str, object]
    """Extra JSON body fields, for fields the SDK doesn't know about yet."""
