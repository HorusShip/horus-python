# Horus Python SDK

The official Python client for the [Horus](https://horusship.com) API (`/api/v1`): every
operation in the OpenAPI spec, typed models, sync and async clients, automatic retries with
idempotency keys, cursor pagination, desktop-job polling and webhook verification.

```sh
pip install horus-sdk          # import name: horus
```

Python 3.9+. Depends on `httpx` and `pydantic` v2.

## Quickstart

```python
from horus import Horus

client = Horus()  # reads HORUS_API_KEY (an ak_… key from Settings → API)

me = client.me.get()

# Lists auto-paginate: iterating walks every page.
for listing in client.listings.list(status="LISTED"):
    print(listing.id, listing.title)

# Actions are POST /<resource>/{id}/<verb>.
client.listings.end("lst_123")
client.sales.ship("sale_123", tracking_number="1Z999AA10123456784", carrier="UPS")
client.messages.mark_read("msg_123")
```

Method names mirror the spec's `operationId`s, snake_cased:
`accounts.jobs.create` → `client.accounts.jobs.create(...)`,
`messages.markRead` → `client.messages.mark_read(...)`,
`ebayOrders.fulfill` → `client.ebay_orders.fulfill(...)`.
Path parameters are positional; everything else is keyword-only. Arguments you leave out are
not sent; passing `None` sends JSON `null` (that's how a nullable field is cleared on a PATCH).
Every method's docstring lists its HTTP route and required scopes.

### Async

```python
import asyncio
from horus import AsyncHorus

async def main() -> None:
    async with AsyncHorus() as client:
        async for sale in client.sales.list(status="PENDING"):
            print(sale.id)
        first_page = await client.listings.list(limit=50)

asyncio.run(main())
```

## Authentication

| Credential | How |
|---|---|
| API key (`ak_…`, legacy `horus_pat_…`) | `Horus(api_key="ak_…")` or `HORUS_API_KEY` |
| OAuth access token (no refresh) | `Horus(access_token="eyJ…")` |
| OAuth with refresh | `Horus(oauth=OAuthCredentials(...))` |

```python
from horus import Horus, OAuthCredentials

def save(creds: OAuthCredentials) -> None:
    keychain.store(creds.refresh_token)   # Horus rotates refresh tokens on every use

client = Horus(oauth=OAuthCredentials(
    access_token=tokens["access_token"],
    refresh_token=tokens["refresh_token"],
    client_id="client_…",
    on_refresh=save,
))
```

The access token is refreshed shortly before it expires (from `expires_at` or the JWT `exp`)
and once after a `401`. The token endpoint comes from `token_endpoint=` or is discovered
from the token's `iss` claim (`<iss>/.well-known/openid-configuration`); no issuer is
hard-coded. `HORUS_BASE_URL` overrides the API base URL (default `https://horusship.com/api/v1`).

## Desktop jobs

Work that runs on your Horus Desktop (sign-ins, checkout, order scraping) answers `202` with a
`Job`. Wait for it:

```python
job = client.accounts.verify("acc_123")
job = client.jobs.wait(job, timeout=180)          # polls GET /jobs/{id}
if job.status == "SUCCEEDED":
    print(job.result)

client.jobs.wait(job, raise_on_failure=True)      # JobFailedError unless SUCCEEDED
```

An offline desktop raises `DesktopOfflineError` (503 `desktop_offline`). For long-running work,
subscribe to the `job.succeeded` / `job.failed` webhooks instead of polling.

## Pagination

```python
page = client.sales.list(limit=100)               # first page only
page.data, page.has_more, page.next_cursor
for p in page.pages(): ...                        # page by page
for sale in page: ...                             # every item, fetching as needed
nxt = page.next_page()                            # None on the last page

# async
page = await client.sales.list()
async for p in page.pages(): ...
async for sale in client.sales.list(): ...
```

## Errors, retries, timeouts

```python
import horus

try:
    client.listings.end("lst_123")
except horus.ConflictError as e:            # 409
    print(e.code, e.message, e.request_id)
except horus.RateLimitError as e:           # 429
    print("retry in", e.retry_after)
except horus.APIStatusError as e:           # any other 4xx/5xx
    print(e.status_code, e.type, e.code, e.details)
except horus.APIConnectionError:            # network / timeout (APITimeoutError)
    ...
```

| Status | Exception |
|---|---|
| 400 | `BadRequestError` |
| 401 | `AuthenticationError` |
| 403 | `PermissionDeniedError` (`insufficient_scope`: `e.details["required"]`) |
| 404 | `NotFoundError` |
| 409 | `ConflictError` |
| 422 | `UnprocessableEntityError` (`e.details["issues"]`) |
| 429 | `RateLimitError` (`e.retry_after`) |
| 503 | `ServiceUnavailableError`, `DesktopOfflineError` |
| other 5xx | `InternalServerError` |

All inherit from `horus.HorusError`.

- **Retries**: connection errors, timeouts, 408, 429, 500, 502, 503, 504 and
  `409 idempotency_in_progress` are retried twice with exponential backoff and jitter
  (honouring `Retry-After`). `503 desktop_offline` and other 4xx are not retried.
- **Idempotency**: every write that the API supports it on gets an `Idempotency-Key`, reused
  across that call's retries, so a retried write never runs twice. Pass your own with
  `idempotency_key="…"` to make retries across processes safe too.
- **Timeouts**: 60 s by default (10 s to connect). `Horus(timeout=…, max_retries=…)`.

Per-call overrides go in `options`:

```python
client.listings.list(options={"timeout": 5, "max_retries": 0, "headers": {"X-Trace": "1"}})
```

Fields the SDK doesn't know yet: `options={"extra_body": {...}, "extra_query": {...}}`, or call any
route directly with `client.request("post", "/listings/lst_1/end", body={...})`.

## Models

Responses are pydantic v2 models (`horus.types.Listing`, `Sale`, `Job`, …). Fields the SDK
doesn't know yet are kept (`model.model_extra`), and a response that doesn't match the models
(a new enum value, say) still parses instead of failing the call. `model.to_dict()` /
`model.to_json()` give the API's JSON back. Nested request objects are `TypedDict`s in
`horus.types.params`.

## Webhooks

```python
from horus import webhooks
from horus.types import SaleShippedEvent

@app.post("/horus")
def horus_webhook():
    try:
        event = webhooks.verify(request.get_data(), request.headers, os.environ["HORUS_WEBHOOK_SECRET"])
    except webhooks.WebhookVerificationError:
        return "", 400
    if isinstance(event, SaleShippedEvent):       # or compare event.type == "sale.shipped"
        print(event.data.sale.tracking_number)
    return "", 204
```

- Verifies [Standard Webhooks](https://www.standardwebhooks.com) signatures (`webhook-id`,
  `webhook-timestamp`, `webhook-signature`), rejecting timestamps more than 5 minutes off.
- Pass the **raw** body (bytes), before any JSON parsing.
- `secret` may be a list (old and new secret while rotating).
- Endpoints created before webhooks v2 are also accepted by their `X-Horus-Signature`
  header (`allow_legacy=False` to refuse), and their v1 body is normalised to the same event shape
  (`api_version == "legacy"`).
- Returns the typed event for its `type` (`horus.types.EVENT_TYPES`); unknown types parse as `Event`.
- Delivery is at-least-once: dedupe on `event.id`.
- `webhooks.sign(body, secret, msg_id=…, timestamp=…)` produces a signature for testing your handler.

## Development

```sh
cd python
uv venv && uv pip install -e ".[dev]"
python scripts/generate.py          # regenerate from ../openapi/openapi.json
python scripts/generate.py --spec https://horusship.com/api/v1/openapi.json
pytest                              # mocked tests (respx)
HORUS_LIVE_API_KEY=ak_… pytest -m live   # opt-in read-only smoke test
mypy                                # strict
ruff check src tests scripts
```

Generated (do not edit): `src/horus/types/_models.py` (datamodel-code-generator),
`types/_events.py`, `types/params.py`, `types/__init__.py`, `resources/*.py` (except
`_jobs.py`, `_webhooks.py`). `tests/test_coverage.py` fails when the committed code is stale or
an operation in the spec isn't callable.
