"""Exceptions raised by the SDK.

Every API error carries the public error shape
``{"error": {"type", "code", "message", "details"?, "request_id"?}}``::

    HorusError
    ├── APIError
    │   ├── APIConnectionError
    │   │   └── APITimeoutError
    │   ├── APIResponseValidationError
    │   └── APIStatusError                 (status, type, code, details, request_id)
    │       ├── BadRequestError            400
    │       ├── AuthenticationError        401
    │       ├── PermissionDeniedError      403
    │       ├── NotFoundError              404
    │       ├── ConflictError              409
    │       ├── UnprocessableEntityError   422
    │       ├── RateLimitError             429 (retry_after)
    │       ├── InternalServerError        5xx
    │       └── ServiceUnavailableError    503
    │           └── DesktopOfflineError    503 desktop_offline
    ├── JobTimeoutError / JobFailedError
    └── WebhookVerificationError
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional, Type

import httpx

if TYPE_CHECKING:
    from .types import Job

__all__ = [
    "HorusError",
    "APIError",
    "APIConnectionError",
    "APITimeoutError",
    "APIResponseValidationError",
    "APIStatusError",
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ConflictError",
    "UnprocessableEntityError",
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailableError",
    "DesktopOfflineError",
    "JobTimeoutError",
    "JobFailedError",
    "WebhookVerificationError",
]


class HorusError(Exception):
    """Base class of every exception the SDK raises."""


class APIError(HorusError):
    """Something went wrong talking to the API."""

    def __init__(self, message: str, *, request: Optional[httpx.Request] = None) -> None:
        super().__init__(message)
        self.message = message
        self.request = request


class APIConnectionError(APIError):
    """The request never got a response (DNS, refused connection, reset...)."""

    def __init__(self, message: str = "Connection error.", *, request: Optional[httpx.Request] = None) -> None:
        super().__init__(message, request=request)


class APITimeoutError(APIConnectionError):
    """The request timed out."""

    def __init__(self, *, request: Optional[httpx.Request] = None) -> None:
        super().__init__("Request timed out.", request=request)


class APIResponseValidationError(APIError):
    """The API answered 2xx with a body that isn't JSON."""

    def __init__(self, message: str, *, response: httpx.Response) -> None:
        super().__init__(message, request=response.request)
        self.response = response
        self.status_code = response.status_code


class APIStatusError(APIError):
    """The API answered with a 4xx/5xx status."""

    status_code: int
    type: str
    code: str
    details: object
    request_id: Optional[str]
    response: httpx.Response

    def __init__(
        self,
        message: str,
        *,
        response: httpx.Response,
        type: str,
        code: str,
        details: object = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message, request=response.request)
        self.response = response
        self.status_code = response.status_code
        self.type = type
        self.code = code
        self.details = details
        self.request_id = request_id

    def __str__(self) -> str:
        rid = f" (request {self.request_id})" if self.request_id else ""
        return f"{self.status_code} {self.code}: {self.message}{rid}"


class BadRequestError(APIStatusError):
    """400: malformed request."""


class AuthenticationError(APIStatusError):
    """401: missing, unknown, expired or revoked credential."""


class PermissionDeniedError(APIStatusError):
    """403: e.g. ``insufficient_scope`` (``details["required"]`` lists the scopes)."""


class NotFoundError(APIStatusError):
    """404: no such resource (someone else's resource is a 404 too)."""


class ConflictError(APIStatusError):
    """409: state conflict (``listing_not_ended``, ``idempotency_conflict``...)."""


class UnprocessableEntityError(APIStatusError):
    """422: validation failed (``details["issues"]`` lists the fields)."""


class RateLimitError(APIStatusError):
    """429: too many requests. ``retry_after`` is seconds to wait, when known."""

    retry_after: Optional[float]

    def __init__(
        self,
        message: str,
        *,
        response: httpx.Response,
        type: str,
        code: str,
        details: object = None,
        request_id: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message, response=response, type=type, code=code, details=details, request_id=request_id)
        self.retry_after = retry_after


class InternalServerError(APIStatusError):
    """5xx: something broke on Horus's side. Retried automatically."""


class ServiceUnavailableError(APIStatusError):
    """503: a dependency (Horus Desktop, eBay) is not reachable."""


class DesktopOfflineError(ServiceUnavailableError):
    """503 ``desktop_offline``: the work needs your Horus Desktop app online."""


class JobTimeoutError(HorusError):
    """``jobs.wait()`` gave up before the job finished. ``job`` is its last known state."""

    def __init__(self, job: Job, timeout: float) -> None:
        super().__init__(f"Job {job.id} still {job.status} after {timeout:g}s.")
        self.job = job
        self.timeout = timeout


class JobFailedError(HorusError):
    """``jobs.wait(raise_on_failure=True)`` saw the job end FAILED or CANCELLED."""

    def __init__(self, job: Job) -> None:
        detail = f": {job.error}" if job.error else ""
        super().__init__(f"Job {job.id} ended {job.status}{detail}")
        self.job = job


class WebhookVerificationError(HorusError):
    """A webhook's signature, timestamp or body didn't check out. Answer 400."""


_BY_STATUS: Dict[int, Type[APIStatusError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: UnprocessableEntityError,
    429: RateLimitError,
    503: ServiceUnavailableError,
}


def error_class_for(status: int, code: str) -> Type[APIStatusError]:
    """The exception class for an HTTP status and error ``code``."""
    if status == 503 and code == "desktop_offline":
        return DesktopOfflineError
    if status in _BY_STATUS:
        return _BY_STATUS[status]
    return InternalServerError if status >= 500 else APIStatusError
