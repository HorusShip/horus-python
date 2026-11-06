# Changelog

## 0.1.0 (unreleased)

First release, generated from the Horus API spec version `2026-11-06` (176 operations,
35 webhook events).

- `Horus` and `AsyncHorus` clients (httpx), every operation as a typed method under
  namespaces mirroring the operationIds.
- Auth: API keys (`HORUS_API_KEY`), OAuth access tokens, and refreshable `OAuthCredentials`
  (rotating refresh tokens, `on_refresh` callback, issuer discovery from the token).
- Pydantic v2 response models; forward-compatible parsing (unknown fields kept, unknown enum
  values tolerated). `TypedDict` request types for nested bodies.
- Typed exceptions from the API error shape; `DesktopOfflineError`, `RateLimitError.retry_after`.
- Automatic retries with backoff + jitter (connection errors, 408/429/5xx,
  `idempotency_in_progress`), honouring `Retry-After`; automatic `Idempotency-Key` on writes.
- Cursor auto-pagination (`for x in client.listings.list()`, `async for`), `.pages()`, `.next_page()`.
- `client.jobs.wait()` for 202 desktop jobs.
- `horus.webhooks.verify()` for Standard Webhooks and legacy `X-Horus-Signature`, returning
  typed events.
- `client.request()` raw escape hatch and per-call `options`.
