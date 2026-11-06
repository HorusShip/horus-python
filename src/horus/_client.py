"""The public clients: :class:`Horus` (blocking) and :class:`AsyncHorus` (asyncio)."""

from __future__ import annotations

from typing import Mapping, Optional

import httpx

from ._auth import OAuthCredentials
from ._base_client import DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT, AsyncAPIClient, ClientConfig, SyncAPIClient
from ._types import Timeout
from .resources import AsyncResources, SyncResources

__all__ = ["Horus", "AsyncHorus"]


class Horus(SyncAPIClient, SyncResources):
    """Blocking client for the Horus API.

    ::

        from horus import Horus

        client = Horus()                       # reads HORUS_API_KEY
        for listing in client.listings.list(status="LISTED"):
            print(listing.id, listing.title)

    Args:
      api_key: a Horus API key (``ak_…``) or legacy ``horus_pat_…``. Defaults to ``$HORUS_API_KEY``.
      access_token: an OAuth access token (no automatic refresh).
      oauth: :class:`OAuthCredentials` with a refresh token (refreshed automatically).
      base_url: defaults to ``$HORUS_BASE_URL`` or ``https://horusship.com/api/v1``.
      timeout: seconds or an ``httpx.Timeout`` (default 60 s, 10 s to connect).
      max_retries: retries on connection errors, 408/429/5xx (default 2).
      default_headers: sent with every request.
      http_client: bring your own ``httpx.Client`` (proxies, transports); you close it.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        access_token: Optional[str] = None,
        oauth: Optional[OAuthCredentials] = None,
        base_url: Optional[str] = None,
        timeout: Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        config = ClientConfig(api_key, access_token, oauth, base_url, timeout, max_retries, default_headers)
        super().__init__(config, http_client)
        self._init_resources(self)

    def __repr__(self) -> str:
        return f"Horus(base_url={self.base_url!r})"


class AsyncHorus(AsyncAPIClient, AsyncResources):
    """asyncio client for the Horus API (same arguments as :class:`Horus`).

    ::

        async with AsyncHorus() as client:
            async for sale in client.sales.list(status="PENDING"):
                ...
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        access_token: Optional[str] = None,
        oauth: Optional[OAuthCredentials] = None,
        base_url: Optional[str] = None,
        timeout: Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        config = ClientConfig(api_key, access_token, oauth, base_url, timeout, max_retries, default_headers)
        super().__init__(config, http_client)
        self._init_resources(self)

    def __repr__(self) -> str:
        return f"AsyncHorus(base_url={self.base_url!r})"
