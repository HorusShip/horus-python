"""Cursor pagination: ``{"object": "list", "data", "has_more", "next_cursor"}``.

Sync::

    page = client.listings.list(status="LISTED")   # first page, fetched now
    page.data, page.has_more, page.next_cursor
    for listing in page: ...                         # every item, fetching pages as needed
    for p in page.pages(): ...                       # page by page

Async::

    async for listing in client.listings.list(): ... # every item
    page = await client.listings.list()              # first page
    async for p in page.pages(): ...
"""

from __future__ import annotations

from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Generator,
    Generic,
    Iterator,
    List,
    Optional,
    TypeVar,
)

T = TypeVar("T")

__all__ = ["SyncPage", "AsyncPage", "AsyncPaginator"]


class _PageBase(Generic[T]):
    def __init__(self, *, data: List[T], has_more: bool, next_cursor: Optional[str]) -> None:
        self.data = data
        self.has_more = has_more
        self.next_cursor = next_cursor

    def has_next_page(self) -> bool:
        return self.has_more and bool(self.next_cursor)

    def __len__(self) -> int:
        """Items on this page (not the whole collection)."""
        return len(self.data)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(items={len(self.data)}, has_more={self.has_more})"


class SyncPage(_PageBase[T]):
    """One page of a list. Iterating it walks every item of every following page."""

    def __init__(
        self,
        *,
        data: List[T],
        has_more: bool,
        next_cursor: Optional[str],
        fetch: Callable[[str], SyncPage[T]],
    ) -> None:
        super().__init__(data=data, has_more=has_more, next_cursor=next_cursor)
        self._fetch = fetch

    def next_page(self) -> Optional[SyncPage[T]]:
        """The following page, or ``None`` on the last one."""
        if not self.next_cursor or not self.has_more:
            return None
        return self._fetch(self.next_cursor)

    def pages(self) -> Iterator[SyncPage[T]]:
        """This page, then each following page."""
        page: Optional[SyncPage[T]] = self
        while page is not None:
            yield page
            page = page.next_page()

    def __iter__(self) -> Iterator[T]:
        for page in self.pages():
            yield from page.data


class AsyncPage(_PageBase[T]):
    """One page of a list (async). ``async for`` walks every item of every following page."""

    def __init__(
        self,
        *,
        data: List[T],
        has_more: bool,
        next_cursor: Optional[str],
        fetch: Callable[[str], Awaitable[AsyncPage[T]]],
    ) -> None:
        super().__init__(data=data, has_more=has_more, next_cursor=next_cursor)
        self._fetch = fetch

    async def next_page(self) -> Optional[AsyncPage[T]]:
        if not self.next_cursor or not self.has_more:
            return None
        return await self._fetch(self.next_cursor)

    async def pages(self) -> AsyncIterator[AsyncPage[T]]:
        page: Optional[AsyncPage[T]] = self
        while page is not None:
            yield page
            page = await page.next_page()

    async def __aiter__(self) -> AsyncIterator[T]:
        async for page in self.pages():
            for item in page.data:
                yield item


class AsyncPaginator(Generic[T]):
    """What async list methods return: ``await`` it for the first page, ``async for`` it for every item."""

    def __init__(self, first: Callable[[], Awaitable[AsyncPage[T]]]) -> None:
        self._first = first

    def __await__(self) -> Generator[Any, None, AsyncPage[T]]:
        return self._first().__await__()

    async def pages(self) -> AsyncIterator[AsyncPage[T]]:
        page = await self._first()
        async for p in page.pages():
            yield p

    async def __aiter__(self) -> AsyncIterator[T]:
        page = await self._first()
        async for item in page:
            yield item
