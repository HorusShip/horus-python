"""``client.jobs.wait()``: poll a desktop job until it finishes.

Desktop work (sign-ins, checkout, order scraping) answers ``202`` with a queued
:class:`~horus.types.Job`; ``wait`` polls ``GET /jobs/{id}`` until it is ``SUCCEEDED``,
``FAILED`` or ``CANCELLED``. For long-running work prefer the ``job.succeeded`` /
``job.failed`` webhooks.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional, Union

from .._exceptions import JobFailedError, JobTimeoutError
from .._types import RequestOptions
from ..types import Job

__all__ = ["SyncJobsMixin", "AsyncJobsMixin", "TERMINAL_STATUSES"]

TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
DEFAULT_TIMEOUT = 300.0
DEFAULT_INTERVAL = 2.0
MAX_INTERVAL = 10.0


def _job_id(job: Union[str, Job]) -> str:
    return job if isinstance(job, str) else job.id


def _finished(job: Job, raise_on_failure: bool) -> bool:
    if job.status not in TERMINAL_STATUSES:
        return False
    if raise_on_failure and job.status != "SUCCEEDED":
        raise JobFailedError(job)
    return True


def _next_interval(interval: float) -> float:
    return min(interval * 1.5, MAX_INTERVAL)


class SyncJobsMixin:
    def get(self, id: str, *, options: Optional[RequestOptions] = None) -> Job:
        raise NotImplementedError  # provided by the generated JobsResource

    def wait(
        self,
        job: Union[str, Job],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        poll_interval: float = DEFAULT_INTERVAL,
        raise_on_failure: bool = False,
    ) -> Job:
        """Poll until the job finishes and return it.

        Args:
          job: a job id or the ``Job`` a 202 call returned.
          timeout: seconds before :class:`~horus.JobTimeoutError` (default 300).
          poll_interval: first delay between polls; grows 1.5x up to 10 s.
          raise_on_failure: raise :class:`~horus.JobFailedError` unless it SUCCEEDED.
        """
        deadline = time.monotonic() + timeout
        interval = poll_interval
        while True:
            current = self.get(_job_id(job))
            if _finished(current, raise_on_failure):
                return current
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise JobTimeoutError(current, timeout)
            time.sleep(min(interval, remaining))
            interval = _next_interval(interval)


class AsyncJobsMixin:
    async def get(self, id: str, *, options: Optional[RequestOptions] = None) -> Job:
        raise NotImplementedError  # provided by the generated AsyncJobsResource

    async def wait(
        self,
        job: Union[str, Job],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        poll_interval: float = DEFAULT_INTERVAL,
        raise_on_failure: bool = False,
    ) -> Job:
        """Poll until the job finishes and return it (see :meth:`SyncJobsMixin.wait`)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        interval = poll_interval
        while True:
            current = await self.get(_job_id(job))
            if _finished(current, raise_on_failure):
                return current
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise JobTimeoutError(current, timeout)
            await asyncio.sleep(min(interval, remaining))
            interval = _next_interval(interval)
