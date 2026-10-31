# Interview Assistant Relay

The backend for sharing the desktop app with friends under your own OpenAI billing: friends
request access from their copy of the app, you approve them from `/admin`, and their questions are
proxied through this server using your key - which never reaches their PC. See
`..\.claude\plans\zippy-wandering-penguin.md` for the full design.

**Existing deployment:** **https://interview-assistant-relay.fly.dev**. The budget-reservation
changes documented below have only been tested locally and are not yet deployed. Requests, admin
approve/deny, token issuance/revocation, the `/v1/chat/completions` proxy with per-friend day/month
budget enforcement and real-usage billing, and the desktop app's "Shared access" settings tab are
all built, tested, and verified end-to-end against the real public deployment and real OpenAI. The
relay URL above is what goes in a friend's "Shared access" tab. See `DEPLOY.md` for how to redeploy
after a code change (`fly deploy` from the repo root) or set up from scratch elsewhere.

## Run locally
```
py -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
copy .env.example .env      # then fill in OPENAI_API_KEY and ADMIN_TOKEN
set OPENAI_API_KEY=sk-... & set ADMIN_TOKEN=pick-something-long & uvicorn interview_relay.app:create_app --factory --reload
```
Visit `http://127.0.0.1:8000/admin` (username `admin`, password = your `ADMIN_TOKEN`).

## Test
```
pytest
```
111 tests: full request -> approve/deny -> token -> revoke/reissue lifecycle (`tests/test_db.py`,
`tests/test_app.py`), plus the proxy (`tests/test_proxy.py`) against a mocked upstream
(`httpx.MockTransport`, same pattern as the main project's `tests/test_llm.py`) - auth rejection,
budget rejection (402, before any upstream call), tier/model validation, `max_tokens` clamping, and
a successful proxied stream with cost recorded exactly matching `PriceBook.llm_cost`. All against
synthetic databases and fake HTTP transports, no network calls. Reservation tests also use
separate SQLite connections against a temporary file to verify concurrency and migration.

## API (friend-facing)
- `POST /requests {"name": str, "note"?: str}` -> `{"public_id": str}`
- `GET /requests/{public_id}` -> `{"status": "pending"|"approved"|"denied", "token": str|null, "budget_usd": float|null, "budget_scope": str|null}`.
  `token` is non-null exactly once - the first poll after approval. Store it immediately; if it's
  lost, ask the owner to reissue from `/admin`.
- `POST /v1/chat/completions` (header `Authorization: Bearer <friend's token>`), body
  `{"model": "fast"|"smart", "messages": [...], "max_completion_tokens"?: int}` -> an OpenAI-shaped
  SSE stream. `model` must be the literal tier name (never a real model id); the server maps it to
  the configured `MODEL_FAST`/`MODEL_SMART` and clamps `max_completion_tokens` to
  `MAX_TOKENS_CEILING`. Returns `401` (bad/revoked token), `400` (bad tier or empty messages),
  `402` (insufficient budget for the maximum request reservation), `413` (body over 8 MiB),
  `429` (per-friend concurrency cap), or `502`/`503` (upstream/relay trouble).

## Admin (`/admin`, HTTP Basic auth, username `admin`)
Approve/deny pending requests (sets each friend's budget on approval), see spend-so-far per
friend, revoke access, reissue a lost token.

## Design notes
- Metadata-only persistence: the relay processes prompts and answers in memory while proxying
  them, but does not store them in its database or log them.
- Tokens are stored as a SHA-256 hash, never in a form recoverable after their one-time delivery.
- A friend's real OpenAI model and `max_tokens` are decided here, not by their app - a modified
  client can't request a different model or an unbounded answer (`interview_relay/proxy.py`).
- Admission atomically books a maximum-cost reservation with `BEGIN IMMEDIATE`, including
  existing spend and other reservations. UTC day/month periods follow request start. The check
  re-reads budget, revocation and credential version; rotating a token invalidates an old auth
  snapshot. `MAX_INFLIGHT_PER_FRIEND` defaults to 2 (supported range 1 to 8).
- Valid provider usage reconciles that reservation exactly once. Missing or malformed usage,
  ambiguous network failure and crashes retain the entire reservation as an estimated quota
  charge, not a claimed provider invoice. A proven unsent connection or explicit API 4xx may
  release it. HTTP 5xx does not. Actual reported overages are recorded without clamping.
- Requests have a 120-second deadline and 180-second durable concurrency lease. Expired leases
  become estimated on the next admission, freeing slots without refunding money. The owner
  dashboard shows unresolved reservations; trusted provider records can reconcile them later.
- The bounded input contract accepts text and inline PNG images, preserving image detail and
  bytes. It rejects remote images, tools, multiple completions and unpriced request options.
  Supported model aliases are `gpt-4o-mini`, `gpt-4o`, and `gpt-5.4-mini`; other model strings
  fail closed until their pricing/input contract is added. See
  [the B5 report](../docs/bugfixes/B5-relay-budget.md) for bounds, tests and operational limits.
