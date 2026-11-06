"""Verify and parse Horus webhooks.

Horus signs deliveries with `Standard Webhooks <https://www.standardwebhooks.com>`_::

    webhook-id:        evt_…
    webhook-timestamp: 1762516800
    webhook-signature: v1,<base64 HMAC-SHA256("{id}.{timestamp}.{raw body}")>  (space-separated during rotation)

keyed with the base64-decoded part of the secret after ``whsec_``. Endpoints created
before webhooks v2 also get ``X-Horus-Signature: sha256=<hex HMAC-SHA256(body)>`` keyed with
the raw secret string; it is accepted when the Standard Webhooks headers are absent.

Usage (Flask)::

    from horus import webhooks

    @app.post("/horus")
    def horus_hook():
        try:
            event = webhooks.verify(request.get_data(), request.headers, os.environ["HORUS_WEBHOOK_SECRET"])
        except webhooks.WebhookVerificationError:
            return "", 400
        queue.put(event)            # dedupe on event.id (delivery is at-least-once)
        return "", 204
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from typing import Dict, Mapping, Optional, Sequence, Union

from ._exceptions import WebhookVerificationError
from ._models import parse_model
from .types import EVENT_TYPES, Event, WebhookEvent

__all__ = [
    "verify",
    "verify_signature",
    "construct_event",
    "sign",
    "WebhookVerificationError",
    "WebhookEvent",
    "DEFAULT_TOLERANCE_SECONDS",
]

DEFAULT_TOLERANCE_SECONDS = 300
SECRET_PREFIX = "whsec_"
LEGACY_HEADER = "x-horus-signature"
_BASE64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")

Payload = Union[bytes, bytearray, str]
Secrets = Union[str, Sequence[str]]


def verify(
    payload: Payload,
    headers: Mapping[str, str],
    secret: Secrets,
    *,
    tolerance: int = DEFAULT_TOLERANCE_SECONDS,
    now: Optional[float] = None,
    allow_legacy: bool = True,
) -> WebhookEvent:
    """Check the signature and return the typed event (e.g. :class:`~horus.types.SaleShippedEvent`).

    ``payload`` must be the **raw** request body, before any JSON parsing. ``secret`` may be a
    list (old + new secret while rotating). Raises :class:`WebhookVerificationError`.
    """
    verify_signature(payload, headers, secret, tolerance=tolerance, now=now, allow_legacy=allow_legacy)
    return construct_event(payload)


def verify_signature(
    payload: Payload,
    headers: Mapping[str, str],
    secret: Secrets,
    *,
    tolerance: int = DEFAULT_TOLERANCE_SECONDS,
    now: Optional[float] = None,
    allow_legacy: bool = True,
) -> None:
    """Like :func:`verify` but only checks the signature."""
    body = _as_bytes(payload)
    secrets = [secret] if isinstance(secret, str) else list(secret)
    if not secrets:
        raise WebhookVerificationError("No webhook secret given.")
    h = _lower(headers)
    if "webhook-signature" in h:
        _verify_standard(body, h, secrets, tolerance, time.time() if now is None else now)
    elif allow_legacy and LEGACY_HEADER in h:
        _verify_legacy(body, h[LEGACY_HEADER], secrets)
    else:
        raise WebhookVerificationError("Missing webhook-signature header.")


def construct_event(payload: Payload) -> WebhookEvent:
    """Parse a (verified) body into its typed event. Legacy v1 bodies are normalised."""
    try:
        data = json.loads(_as_bytes(payload))
    except ValueError as exc:
        raise WebhookVerificationError("Webhook body is not JSON.") from exc
    if not isinstance(data, dict):
        raise WebhookVerificationError("Webhook body is not a JSON object.")
    envelope = data if "type" in data else _from_legacy(data)
    model = EVENT_TYPES.get(str(envelope.get("type")), Event)
    return parse_model(model, envelope)


def sign(payload: Payload, secret: str, *, msg_id: str, timestamp: int) -> str:
    """The ``webhook-signature`` value Horus would send (handy for testing your handler)."""
    return "v1," + _standard_digest(_key(secret), msg_id, str(timestamp), _as_bytes(payload))


# -- internals -------------------------------------------------------------------------------
def _verify_standard(body: bytes, h: Mapping[str, str], secrets: Sequence[str], tolerance: int, now: float) -> None:
    msg_id, timestamp = h.get("webhook-id", ""), h.get("webhook-timestamp", "")
    if not msg_id or not timestamp:
        raise WebhookVerificationError("Missing webhook-id or webhook-timestamp header.")
    _check_timestamp(timestamp, tolerance, now)
    expected = {_standard_digest(_key(s), msg_id, timestamp, body) for s in secrets}
    if not any(hmac.compare_digest(sig, exp) for sig in _v1_signatures(h["webhook-signature"]) for exp in expected):
        raise WebhookVerificationError("No matching webhook signature.")


def _verify_legacy(body: bytes, header: str, secrets: Sequence[str]) -> None:
    given = header.strip()
    given = given[len("sha256=") :] if given.startswith("sha256=") else given
    for s in secrets:
        expected = hmac.new(s.encode("utf-8"), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(given.lower(), expected):
            return
    raise WebhookVerificationError("No matching X-Horus-Signature.")


def _check_timestamp(timestamp: str, tolerance: int, now: float) -> None:
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise WebhookVerificationError("Invalid webhook-timestamp header.") from exc
    if abs(now - ts) > tolerance:
        raise WebhookVerificationError("Webhook timestamp is outside the tolerance window (replayed or clock skew).")


def _v1_signatures(header: str) -> list[str]:
    out = []
    for part in header.split():
        version, _, sig = part.partition(",")
        if version == "v1" and sig:
            out.append(sig)
    return out


def _standard_digest(key: bytes, msg_id: str, timestamp: str, body: bytes) -> str:
    signed = f"{msg_id}.{timestamp}.".encode() + body
    return base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


def _key(secret: str) -> bytes:
    """``whsec_<base64>`` -> decoded bytes; anything else -> its UTF-8 bytes (as the server does)."""
    if secret.startswith(SECRET_PREFIX):
        b64 = secret[len(SECRET_PREFIX) :]
        if _BASE64.match(b64) and len(b64) % 4 == 0:
            try:
                return base64.b64decode(b64, validate=True)
            except binascii.Error:
                pass
    return secret.encode("utf-8")


def _from_legacy(data: Mapping[str, object]) -> Dict[str, object]:
    """v1 body ``{id, event, data, createdAt}`` -> the v2 envelope shape."""
    return {
        "id": data.get("id"),
        "object": "event",
        "type": data.get("event"),
        "created": data.get("createdAt"),
        "api_version": "legacy",
        "data": data.get("data") if isinstance(data.get("data"), dict) else {},
    }


def _lower(headers: Mapping[str, str]) -> Dict[str, str]:
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _as_bytes(payload: Payload) -> bytes:
    return payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
