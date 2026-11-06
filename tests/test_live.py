"""Opt-in, read-only smoke test against the real API.

    HORUS_LIVE_API_KEY=ak_... pytest -m live            # https://horusship.com/api/v1
    HORUS_LIVE_BASE_URL=http://localhost:3000/api/v1 ...  # another deployment

Only GET requests are made.
"""

from __future__ import annotations

import os
from typing import Iterator

import pytest

from horus import Horus
from horus.types import EventType, Me

pytestmark = pytest.mark.live


@pytest.fixture
def live() -> Iterator[Horus]:
    key = os.environ.get("HORUS_LIVE_API_KEY")
    if not key:
        pytest.skip("set HORUS_LIVE_API_KEY to run the live smoke test")
    with Horus(api_key=key, base_url=os.environ.get("HORUS_LIVE_BASE_URL")) as client:
        yield client


def test_me(live: Horus) -> None:
    assert isinstance(live.me.get(), Me)


def test_event_types_cover_the_spec(live: Horus) -> None:
    types = live.event_types.list().data  # not paginated (no cursor param)
    assert types and all(isinstance(t, EventType) for t in types)


def test_first_page_of_listings_and_jobs(live: Horus) -> None:
    listings = live.listings.list(limit=1)
    assert len(listings.data) <= 1
    jobs = live.jobs.list(limit=1)
    assert len(jobs.data) <= 1
