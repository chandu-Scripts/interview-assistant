# Interview Assistant Relay

The backend for sharing the desktop app with friends under your own OpenAI billing: friends
request access from their copy of the app, you approve them from `/admin`, and their questions are
proxied through this server using your key - which never reaches their PC. See
`..\.claude\plans\zippy-wandering-penguin.md` for the full design.

**Status: deployed and live** at **https://interview-assistant-relay.fly.dev**. Requests, admin
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
49 tests: full request -> approve/deny -> token -> revoke/reissue lifecycle (`tests/test_db.py`,
`tests/test_app.py`), plus the proxy (`tests/test_proxy.py`) against a mocked upstream
(`httpx.MockTransport`, same pattern as the main project's `tests/test_llm.py`) - auth rejection,
budget rejection (402, before any upstream call), tier/model validation, `max_tokens` clamping, and
a successful proxied stream with cost recorded exactly matching `PriceBook.llm_cost`. All against
an in-memory database, no network calls.

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
  `402` (budget reached for the current day/month), or `502`/`503` (upstream/relay trouble).

## Admin (`/admin`, HTTP Basic auth, username `admin`)
Approve/deny pending requests (sets each friend's budget on approval), see spend-so-far per
friend, revoke access, reissue a lost token.

## Design notes
- Metadata only: this server never sees or stores a prompt or an answer - only who was approved
  and how much of their budget they've used (same rule as the desktop app's own usage log).
- Tokens are stored as a SHA-256 hash, never in a form recoverable after their one-time delivery.
- A friend's real OpenAI model and `max_tokens` are decided here, not by their app - a modified
  client can't request a different model or an unbounded answer (`interview_relay/proxy.py`).
- Budget is enforced from a `spends` log (not a single counter), so "day"/"month" scopes reset
  correctly - the same logic as the desktop app's `cost.tracker._start_of()`. Billing uses OpenAI's
  own reported token usage; if a stream dies before that arrives, a short char-count estimate is
  billed instead (never $0) so an interrupted request still counts against budget.
