from __future__ import annotations

import json

import pytest

from horus import Horus, webhooks
from horus.types import Event, SaleShippedEvent, SaleShippedEventData, WebhookSale

# Published Standard Webhooks test vector (standard-webhooks/standard-webhooks, libraries/python tests).
SW_SECRET = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
SW_ID = "msg_p5jXN8AQM9LWM0D4loKWxJek"
SW_TS = 1614265330
SW_PAYLOAD = '{"test": 2432232314}'
SW_SIGNATURE = "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="

# Signed by the Horus API with two
# secrets, as during a rotation: [SW_SECRET, "legacy-plain-secret"].
HORUS_BODY = (
    '{"id":"evt_1","object":"event","type":"sale.shipped","created":"2026-11-07T12:00:00.000Z",'
    '"api_version":"2026-11-06","data":{"sale":{"object":"sale","id":"s1"}}}'
)
HORUS_HEADERS = {
    "webhook-id": "evt_1",
    "webhook-timestamp": "1762516800",
    "webhook-signature": (
        "v1,Wb+K5cs8PSBsLYBg+44H2ArxBsLg/Z8qkBe4A2Ye4yQ= v1,46nRCKhM6xxBktdZONwlFpkHzOtmUKr4Mh6VlZmg7pI="
    ),
    "X-Horus-Signature": "sha256=e214311a488eb1ad54b6d58801511dd27efb3d7fad6ee827c5a4aad3841c2245",
}
HORUS_NOW = 1762516800 + 30


def sw_headers(signature: str = SW_SIGNATURE, ts: int = SW_TS) -> dict[str, str]:
    return {"webhook-id": SW_ID, "webhook-timestamp": str(ts), "webhook-signature": signature}


def test_standard_webhooks_test_vector() -> None:
    assert webhooks.sign(SW_PAYLOAD, SW_SECRET, msg_id=SW_ID, timestamp=SW_TS) == SW_SIGNATURE
    webhooks.verify_signature(SW_PAYLOAD, sw_headers(), SW_SECRET, now=SW_TS)


def test_vector_rejects_tampering() -> None:
    with pytest.raises(webhooks.WebhookVerificationError, match="signature"):
        webhooks.verify_signature('{"test": 2432232315}', sw_headers(), SW_SECRET, now=SW_TS)
    with pytest.raises(webhooks.WebhookVerificationError):
        webhooks.verify_signature(SW_PAYLOAD, sw_headers(), "whsec_" + "A" * 32, now=SW_TS)
    with pytest.raises(webhooks.WebhookVerificationError):
        webhooks.verify_signature(SW_PAYLOAD, sw_headers(signature="v2," + SW_SIGNATURE[3:]), SW_SECRET, now=SW_TS)


def test_timestamp_tolerance() -> None:
    with pytest.raises(webhooks.WebhookVerificationError, match="tolerance"):
        webhooks.verify_signature(SW_PAYLOAD, sw_headers(), SW_SECRET, now=SW_TS + 301)
    webhooks.verify_signature(SW_PAYLOAD, sw_headers(), SW_SECRET, now=SW_TS + 3000, tolerance=3600)
    with pytest.raises(webhooks.WebhookVerificationError, match="timestamp"):
        webhooks.verify_signature(SW_PAYLOAD, {**sw_headers(), "webhook-timestamp": "soon"}, SW_SECRET, now=SW_TS)


def test_missing_headers() -> None:
    with pytest.raises(webhooks.WebhookVerificationError, match="Missing webhook-signature"):
        webhooks.verify_signature(SW_PAYLOAD, {}, SW_SECRET)
    with pytest.raises(webhooks.WebhookVerificationError, match="webhook-id"):
        webhooks.verify_signature(SW_PAYLOAD, {"webhook-signature": SW_SIGNATURE}, SW_SECRET)


def test_horus_backend_signature_and_typed_event() -> None:
    event = webhooks.verify(HORUS_BODY.encode(), HORUS_HEADERS, SW_SECRET, now=HORUS_NOW)
    assert isinstance(event, SaleShippedEvent)
    assert isinstance(event.data, SaleShippedEventData) and isinstance(event.data.sale, WebhookSale)
    assert event.data.sale.id == "s1" and event.type == "sale.shipped"


def test_rotation_second_secret_and_raw_secret_key() -> None:
    # The second v1 entry was signed with a non-whsec secret, keyed by its UTF-8 bytes.
    webhooks.verify_signature(HORUS_BODY, HORUS_HEADERS, "legacy-plain-secret", now=HORUS_NOW)
    webhooks.verify_signature(HORUS_BODY, HORUS_HEADERS, ["whsec_" + "B" * 44, SW_SECRET], now=HORUS_NOW)


def test_headers_are_case_insensitive() -> None:
    upper = {k.upper(): v for k, v in HORUS_HEADERS.items()}
    webhooks.verify_signature(HORUS_BODY, upper, SW_SECRET, now=HORUS_NOW)


def test_legacy_x_horus_signature() -> None:
    legacy_only = {"X-Horus-Signature": HORUS_HEADERS["X-Horus-Signature"]}
    webhooks.verify_signature(HORUS_BODY, legacy_only, SW_SECRET)  # keyed with the raw secret string
    with pytest.raises(webhooks.WebhookVerificationError):
        webhooks.verify_signature(HORUS_BODY, legacy_only, "wrong")
    with pytest.raises(webhooks.WebhookVerificationError):
        webhooks.verify_signature(HORUS_BODY, legacy_only, SW_SECRET, allow_legacy=False)


def test_legacy_body_is_normalised() -> None:
    body = json.dumps(
        {"id": "evt_9", "event": "job.succeeded", "data": {"jobId": "job_1"}, "createdAt": "2026-01-01T00:00:00Z"}
    )
    event = webhooks.construct_event(body)
    assert event.type == "job.succeeded" and event.id == "evt_9"
    assert event.api_version == "legacy" and event.created == "2026-01-01T00:00:00Z"


def test_unknown_event_type_parses_as_base_event() -> None:
    body = json.dumps(
        {"id": "e", "object": "event", "type": "brand.new", "created": "x", "api_version": "v", "data": {"k": 1}}
    )
    event = webhooks.construct_event(body)
    assert type(event) is Event and event.data == {"k": 1}


def test_non_json_body() -> None:
    with pytest.raises(webhooks.WebhookVerificationError, match="JSON"):
        webhooks.construct_event(b"not json")


def test_client_webhooks_verify_delegates(client: Horus) -> None:
    event = client.webhooks.verify(HORUS_BODY, HORUS_HEADERS, SW_SECRET, now=HORUS_NOW)
    assert isinstance(event, SaleShippedEvent)
