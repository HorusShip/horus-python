"""Retry policy and error construction, shared by the sync and async clients.

Retried (with exponential backoff + jitter, honouring ``Retry-After``):

* connection errors and timeouts,
* ``408``, ``429``, ``500``, ``502``, ``503``, ``504``,
* ``409 idempotency_in_progress`` (the first attempt with the same key is still running).

Not retried: ``503 desktop_offline`` (the desktop won't come back within a second), and
every other 4xx. Writes carry the same ``Idempotency-Key`` on every attempt, and the API
releases a key on 5xx, so a retried write never runs twice.
"""

from __future__ import annotations

import email.utils
import json
import random
import time
from typing import Mapping, Optional

import httpx

from ._exceptions import APIStatusError, RateLimitError, error_class_for

__all__ = ["should_retry", "retry_delay", "error_from_response", "parse_retry_after"]

RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
NO_RETRY_CODES = frozenset({"desktop_offline"})
INITIAL_DELAY = 0.5
MAX_DELAY = 8.0
MAX_RETRY_AFTER = 60.0


def should_retry(error: APIStatusError) -> bool:
    """Whether a failed response is worth another attempt."""
    if error.code in NO_RETRY_CODES:
        return False
    if error.status_code == 409:
        return error.code == "idempotency_in_progress"
    return error.status_code in RETRY_STATUSES


def retry_delay(attempt: int, retry_after: Optional[float] = None) -> float:
    """Seconds to wait before retry number ``attempt`` (0-based)."""
    if retry_after is not None and 0 <= retry_after <= MAX_RETRY_AFTER:
        return retry_after
    backoff = min(INITIAL_DELAY * (2.0**attempt), MAX_DELAY)
    return backoff * (1 - 0.25 * random.random())


def parse_retry_after(headers: Mapping[str, str], now: Optional[float] = None) -> Optional[float]:
    """``Retry-After`` in seconds (delta or HTTP date); falls back to ``RateLimit-Reset``."""
    raw = headers.get("retry-after")
    if raw:
        seconds = _parse_delta_or_date(raw.strip(), time.time() if now is None else now)
        if seconds is not None:
            return seconds
    reset = headers.get("ratelimit-reset")
    if reset and reset.strip().isdigit():
        return float(reset.strip())
    return None


def _parse_delta_or_date(raw: str, now: float) -> Optional[float]:
    try:
        return max(float(raw), 0.0)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return max(when.timestamp() - now, 0.0)


def error_from_response(response: httpx.Response) -> APIStatusError:
    """The typed exception for a 4xx/5xx response."""
    err = _error_object(response)
    code = _str(err.get("code")) or f"http_{response.status_code}"
    message = _str(err.get("message")) or response.reason_phrase or "API error"
    kind = _str(err.get("type")) or ("internal" if response.status_code >= 500 else "invalid_request")
    request_id = _str(err.get("request_id")) or response.headers.get("request-id")
    details = err.get("details")
    cls = error_class_for(response.status_code, code)
    if cls is RateLimitError:
        return RateLimitError(
            message,
            response=response,
            type=kind,
            code=code,
            details=details,
            request_id=request_id,
            retry_after=parse_retry_after(response.headers),
        )
    return cls(message, response=response, type=kind, code=code, details=details, request_id=request_id)


def _error_object(response: httpx.Response) -> Mapping[str, object]:
    try:
        body = json.loads(response.content or b"null")
    except ValueError:
        return {"message": response.text[:500] or None}
    inner = body.get("error") if isinstance(body, dict) else None
    return inner if isinstance(inner, dict) else {}


def _str(value: object) -> Optional[str]:
    return value if isinstance(value, str) and value else None
