"""``client.webhooks.verify()``: the module-level :func:`horus.webhooks.verify` on the client."""

from __future__ import annotations

from typing import Mapping, Optional

from .. import webhooks as _webhooks
from ..types import WebhookEvent

__all__ = ["SyncWebhooksMixin", "AsyncWebhooksMixin"]


class _VerifyMixin:
    def verify(
        self,
        payload: _webhooks.Payload,
        headers: Mapping[str, str],
        secret: _webhooks.Secrets,
        *,
        tolerance: int = _webhooks.DEFAULT_TOLERANCE_SECONDS,
        now: Optional[float] = None,
        allow_legacy: bool = True,
    ) -> WebhookEvent:
        """Verify a delivery's signature and return the typed event (see :func:`horus.webhooks.verify`)."""
        return _webhooks.verify(payload, headers, secret, tolerance=tolerance, now=now, allow_legacy=allow_legacy)


class SyncWebhooksMixin(_VerifyMixin):
    pass


class AsyncWebhooksMixin(_VerifyMixin):
    pass
