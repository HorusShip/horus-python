"""Official Python SDK for the Horus API (https://horusship.com/api/v1).

::

    from horus import Horus

    client = Horus()  # HORUS_API_KEY
    job = client.accounts.verify("acc_123")
    job = client.jobs.wait(job, timeout=120)
"""

from . import types, webhooks
from ._auth import OAuthCredentials, TokenRefreshError
from ._base_client import DEFAULT_BASE_URL, DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT
from ._client import AsyncHorus, Horus
from ._exceptions import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    DesktopOfflineError,
    HorusError,
    InternalServerError,
    JobFailedError,
    JobTimeoutError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    UnprocessableEntityError,
    WebhookVerificationError,
)
from ._pagination import AsyncPage, AsyncPaginator, SyncPage
from ._types import NOT_GIVEN, NotGiven, RequestOptions
from ._version import __version__

__all__ = [
    "Horus",
    "AsyncHorus",
    "OAuthCredentials",
    "TokenRefreshError",
    "SyncPage",
    "AsyncPage",
    "AsyncPaginator",
    "RequestOptions",
    "NOT_GIVEN",
    "NotGiven",
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT",
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
    "types",
    "webhooks",
    "__version__",
]
