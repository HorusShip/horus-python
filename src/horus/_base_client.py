"""HTTP plumbing shared by :class:`horus.Horus` and :class:`horus.AsyncHorus`.

``BaseClient`` holds configuration and the pure parts (headers, parsing, retry
decisions). ``SyncAPIClient`` / ``AsyncAPIClient`` only add the I/O loop.
"""

from __future__ import annotations

import asyncio
import os
import platform
import time
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Type, TypeVar, Union

import httpx
import pydantic

from ._auth import Auth, OAuthCredentials, is_expired_token_error
from ._exceptions import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    HorusError,
    RateLimitError,
)
from ._models import parse_model
from ._operation import Operation, PreparedRequest, prepare_request
from ._pagination import AsyncPage, AsyncPaginator, SyncPage
from ._retry import error_from_response, retry_delay, should_retry
from ._types import RequestOptions, Timeout
from ._version import __version__

__all__ = ["ClientConfig", "BaseClient", "SyncAPIClient", "AsyncAPIClient", "DEFAULT_BASE_URL"]

DEFAULT_BASE_URL = "https://horusship.com/api/v1"
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
DEFAULT_MAX_RETRIES = 2

M = TypeVar("M", bound=pydantic.BaseModel)
JSONObject = Dict[str, object]


@dataclass(frozen=True)
class ClientConfig:
    """Constructor arguments shared by both clients."""

    api_key: Optional[str]
    access_token: Optional[str]
    oauth: Optional[OAuthCredentials]
    base_url: Optional[str]
    timeout: Timeout
    max_retries: int
    default_headers: Optional[Mapping[str, str]]


class BaseClient:
    """Configuration and the transport-independent parts of a request."""

    def __init__(self, config: ClientConfig) -> None:
        self._auth = _resolve_auth(config.api_key, config.access_token, config.oauth)
        self.base_url = (config.base_url or os.environ.get("HORUS_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout: Timeout = config.timeout
        self.max_retries = config.max_retries
        self._default_headers = dict(config.default_headers or {})

    @property
    def oauth(self) -> Optional[OAuthCredentials]:
        """The OAuth credentials in use (updated in place on refresh), if any."""
        return self._auth.oauth

    def _build_request(self, http: Union[httpx.Client, httpx.AsyncClient], prepared: PreparedRequest, options: Optional[RequestOptions]) -> httpx.Request:
        timeout = (options or {}).get("timeout", self.timeout)
        return http.build_request(
            prepared.method,
            self.base_url + prepared.path,
            params=prepared.query or None,
            json=prepared.json,
            headers=self._headers(prepared),
            timeout=timeout,
        )

    def _headers(self, prepared: PreparedRequest) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": _user_agent(),
            "X-Horus-SDK": f"python/{__version__}",
            **self._default_headers,
            "Authorization": self._auth.header,
        }
        headers.update(prepared.headers)
        return headers

    def _retries_for(self, options: Optional[RequestOptions]) -> int:
        return max(0, (options or {}).get("max_retries", self.max_retries))

    def _should_refresh(self, error: APIError, already_refreshed: bool) -> bool:
        return (
            isinstance(error, AuthenticationError)
            and is_expired_token_error(error)
            and self._auth.refreshable
            and not already_refreshed
        )

    @staticmethod
    def _delay_for(error: APIError, attempt: int, retries: int) -> Optional[float]:
        """Seconds before the next attempt, or ``None`` to give up and raise."""
        if attempt >= retries:
            return None
        if isinstance(error, APIStatusError):
            if not should_retry(error):
                return None
            retry_after = error.retry_after if isinstance(error, RateLimitError) else None
            return retry_delay(attempt, retry_after)
        return retry_delay(attempt) if isinstance(error, APIConnectionError) else None

    @staticmethod
    def _json(response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError as exc:
            raise APIResponseValidationError(f"Expected JSON from {response.request.url}, got: {response.text[:200]!r}", response=response) from exc

    def _parse(self, cast_to: Type[M], response: httpx.Response) -> M:
        return parse_model(cast_to, self._json(response))

    def _parse_page_parts(self, item: Type[M], response: httpx.Response) -> tuple[List[M], bool, Optional[str]]:
        body = self._json(response)
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise APIResponseValidationError("Expected a list object", response=response)
        data = [parse_model(item, row) for row in body["data"]]
        cursor = body.get("next_cursor")
        return data, bool(body.get("has_more")), cursor if isinstance(cursor, str) else None


def _resolve_auth(api_key: Optional[str], access_token: Optional[str], oauth: Optional[OAuthCredentials]) -> Auth:
    given = [x for x in (api_key, access_token, oauth) if x is not None]
    if len(given) > 1:
        raise HorusError("Pass only one of api_key, access_token or oauth.")
    if oauth is not None:
        return Auth(oauth=oauth)
    token = api_key or access_token or os.environ.get("HORUS_API_KEY")
    if not token:
        raise HorusError(
            "No credentials: pass api_key=... (or access_token= / oauth=) or set the HORUS_API_KEY environment variable."
        )
    return Auth(token=token)


def _user_agent() -> str:
    return f"horus-sdk-python/{__version__} (python {platform.python_version()}; httpx {httpx.__version__})"


def _transport_error(exc: httpx.TransportError, request: httpx.Request) -> APIConnectionError:
    if isinstance(exc, httpx.TimeoutException):
        return APITimeoutError(request=request)
    return APIConnectionError(f"Connection error: {exc}", request=request)


class SyncAPIClient(BaseClient):
    """Blocking transport (``httpx.Client``)."""

    def __init__(self, config: ClientConfig, http_client: Optional[httpx.Client] = None) -> None:
        super().__init__(config)
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=self.timeout, follow_redirects=False)

    # -- public escape hatch ---------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, object]] = None,
        body: Optional[Mapping[str, object]] = None,
        idempotency_key: Optional[str] = None,
        options: Optional[RequestOptions] = None,
    ) -> object:
        """Call any endpoint directly and get the decoded JSON (``client.request("get", "/me")``)."""
        op, params = _raw_operation(method, path, query, body)
        prepared = prepare_request(op, path={}, params=params, idempotency_key=idempotency_key, options=options)
        return self._json(self._send(prepared, options))

    # -- used by the generated resources ---------------------------------------------------
    def _call(
        self,
        op: Operation,
        cast_to: Type[M],
        *,
        path: Optional[Mapping[str, object]] = None,
        params: Optional[Mapping[str, object]] = None,
        idempotency_key: Optional[str] = None,
        options: Optional[RequestOptions] = None,
    ) -> M:
        prepared = prepare_request(op, path=path or {}, params=params or {}, idempotency_key=idempotency_key, options=options)
        return self._parse(cast_to, self._send(prepared, options))

    def _call_page(
        self,
        op: Operation,
        item: Type[M],
        *,
        path: Optional[Mapping[str, object]] = None,
        params: Optional[Mapping[str, object]] = None,
        options: Optional[RequestOptions] = None,
    ) -> SyncPage[M]:
        base = dict(params or {})

        def fetch(cursor: Optional[str]) -> SyncPage[M]:
            query = {**base, "cursor": cursor} if cursor else base
            prepared = prepare_request(op, path=path or {}, params=query, idempotency_key=None, options=options)
            data, has_more, next_cursor = self._parse_page_parts(item, self._send(prepared, options))
            return SyncPage(data=data, has_more=has_more, next_cursor=next_cursor, fetch=fetch)

        return fetch(None)

    # -- transport -------------------------------------------------------------------------
    def _send(self, prepared: PreparedRequest, options: Optional[RequestOptions]) -> httpx.Response:
        retries, attempt, refreshed = self._retries_for(options), 0, False
        while True:
            outcome = self._attempt(prepared, options)
            if isinstance(outcome, httpx.Response):
                return outcome
            if self._should_refresh(outcome, refreshed):
                refreshed = True
                self._refresh_token()
                continue
            delay = self._delay_for(outcome, attempt, retries)
            if delay is None:
                raise outcome
            time.sleep(delay)
            attempt += 1

    def _attempt(self, prepared: PreparedRequest, options: Optional[RequestOptions]) -> Union[httpx.Response, APIError]:
        if self._auth.needs_refresh():
            self._refresh_token()
        request = self._build_request(self._http, prepared, options)
        try:
            response = self._http.send(request)
        except httpx.TransportError as exc:
            return _transport_error(exc, request)
        return response if response.is_success else error_from_response(response)

    def _refresh_token(self) -> None:
        with self._auth.lock:
            creds = self._auth.oauth
            if creds is None:
                return
            endpoint = creds.token_endpoint or self._auth.apply_discovery(self._http.get(self._auth.discovery_url()))
            self._auth.apply_token_response(self._http.post(endpoint, data=self._auth.refresh_form()))

    # -- lifecycle -------------------------------------------------------------------------
    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self: _SyncSelf) -> _SyncSelf:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class AsyncAPIClient(BaseClient):
    """Non-blocking transport (``httpx.AsyncClient``)."""

    def __init__(self, config: ClientConfig, http_client: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(config)
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=False)
        self._refresh_lock: Optional[asyncio.Lock] = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, object]] = None,
        body: Optional[Mapping[str, object]] = None,
        idempotency_key: Optional[str] = None,
        options: Optional[RequestOptions] = None,
    ) -> object:
        """Call any endpoint directly and get the decoded JSON (``await client.request("get", "/me")``)."""
        op, params = _raw_operation(method, path, query, body)
        prepared = prepare_request(op, path={}, params=params, idempotency_key=idempotency_key, options=options)
        return self._json(await self._send(prepared, options))

    async def _call(
        self,
        op: Operation,
        cast_to: Type[M],
        *,
        path: Optional[Mapping[str, object]] = None,
        params: Optional[Mapping[str, object]] = None,
        idempotency_key: Optional[str] = None,
        options: Optional[RequestOptions] = None,
    ) -> M:
        prepared = prepare_request(op, path=path or {}, params=params or {}, idempotency_key=idempotency_key, options=options)
        return self._parse(cast_to, await self._send(prepared, options))

    def _call_page(
        self,
        op: Operation,
        item: Type[M],
        *,
        path: Optional[Mapping[str, object]] = None,
        params: Optional[Mapping[str, object]] = None,
        options: Optional[RequestOptions] = None,
    ) -> AsyncPaginator[M]:
        base = dict(params or {})

        async def fetch(cursor: Optional[str]) -> AsyncPage[M]:
            query = {**base, "cursor": cursor} if cursor else base
            prepared = prepare_request(op, path=path or {}, params=query, idempotency_key=None, options=options)
            data, has_more, next_cursor = self._parse_page_parts(item, await self._send(prepared, options))
            return AsyncPage(data=data, has_more=has_more, next_cursor=next_cursor, fetch=fetch)

        return AsyncPaginator(lambda: fetch(None))

    async def _send(self, prepared: PreparedRequest, options: Optional[RequestOptions]) -> httpx.Response:
        retries, attempt, refreshed = self._retries_for(options), 0, False
        while True:
            outcome = await self._attempt(prepared, options)
            if isinstance(outcome, httpx.Response):
                return outcome
            if self._should_refresh(outcome, refreshed):
                refreshed = True
                await self._refresh_token()
                continue
            delay = self._delay_for(outcome, attempt, retries)
            if delay is None:
                raise outcome
            await asyncio.sleep(delay)
            attempt += 1

    async def _attempt(self, prepared: PreparedRequest, options: Optional[RequestOptions]) -> Union[httpx.Response, APIError]:
        if self._auth.needs_refresh():
            await self._refresh_token()
        request = self._build_request(self._http, prepared, options)
        try:
            response = await self._http.send(request)
        except httpx.TransportError as exc:
            return _transport_error(exc, request)
        return response if response.is_success else error_from_response(response)

    async def _refresh_token(self) -> None:
        if self._refresh_lock is None:
            self._refresh_lock = asyncio.Lock()
        async with self._refresh_lock:
            creds = self._auth.oauth
            if creds is None:
                return
            endpoint = creds.token_endpoint or self._auth.apply_discovery(await self._http.get(self._auth.discovery_url()))
            self._auth.apply_token_response(await self._http.post(endpoint, data=self._auth.refresh_form()))

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self: _AsyncSelf) -> _AsyncSelf:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


_SyncSelf = TypeVar("_SyncSelf", bound=SyncAPIClient)
_AsyncSelf = TypeVar("_AsyncSelf", bound=AsyncAPIClient)


def _raw_operation(
    method: str, path: str, query: Optional[Mapping[str, object]], body: Optional[Mapping[str, object]]
) -> tuple[Operation, Dict[str, object]]:
    """An ad-hoc :class:`Operation` for :meth:`request`."""
    m = method.lower()
    query_keys = tuple(query or {})
    body_keys = tuple(body or {})
    op = Operation(
        id=f"raw.{m}",
        method=m,
        path="/" + path.lstrip("/"),
        query_params=query_keys,
        body_params=body_keys,
        has_body=body is not None,
        idempotent=m != "get",
    )
    return op, {**(query or {}), **(body or {})}
