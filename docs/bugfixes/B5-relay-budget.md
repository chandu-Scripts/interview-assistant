# B5: atomic relay budget reservations

## Failure and decision

Concurrent requests previously checked the same remaining balance before either request
recorded spend. Both could call the paid provider, exceeding a friend's configured budget.
Length-based fallback billing also underestimated interrupted requests, and unsupported
model prices could silently bill zero.

The fix books the full supported input/output upper bound in a durable spend row before
opening the upstream connection. A SQLite `BEGIN IMMEDIATE` transaction re-reads the friend,
budget, token credential version, current-period spend and concurrent reservations. This
works across separate database connections on one shared SQLite file. A process-local
mutex alone was rejected because another worker can otherwise admit the same money.

The normal spend log includes reservations, so there is one accounting source of truth.
A unique reservation ID and guarded terminal states make settlement idempotent. Valid
upstream usage adjusts the existing spend row; it never creates a duplicate charge.
Request-start timestamps keep late settlement in the original UTC day/month period.
Lowering a budget or revoking access blocks new admissions without deleting existing costs.

Token reissue increments a nonsecret credential version. The transaction rejects a stale
authenticated snapshot after rotation. Approval and one-time token consumption also use
the transaction helper to prevent duplicate grants/delivery across worker connections.

## Bounded request and pricing contract

- Request bodies are capped at 8 MiB before JSON parsing; text is capped at 64 KiB of UTF-8,
  with at most 128 messages and 16 parts per message. Every forwarded field is validated.
- Text reservations count UTF-8 bytes plus conservative message/part framing, not chars/4.
  Output is capped by the server, with a supported ceiling of 1 to 8192 tokens.
- Inline PNG screenshots remain supported, with the bytes and selected detail preserved.
  Base64, PNG chunks/CRC, dimensions and file sizes are checked without decoding pixels.
  Limits are four images, 4 MiB per PNG, 8192 pixels per side and 16,777,216 pixels overall.
  Remote URLs, animated PNG, tools, multiple completions and unpriced options are rejected.
- Only exact aliases `gpt-4o`, `gpt-4o-mini`, and `gpt-5.4-mini` currently have a supported
  contract. Unknown model strings return 503 before any provider call. Rates are captured
  from the existing PriceBook for each admitted request; pricing changes need maintenance.
- Image reservations use the maximum supported detail cost, avoiding quality changes:
  1445 tokens for gpt-4o, 48169 for gpt-4o-mini, and 12001 for gpt-5.4-mini. The latter includes
  one token of rounding allowance. These maxima were checked against the official
  [vision tokenization documentation](https://developers.openai.com/api/docs/guides/images-vision)
  on 2026-11-01. They are deliberately conservative, especially for small/low-detail images.

These are quota bounds for the supported contract, not a universal guarantee against
future provider pricing/tokenization changes. Actual reported cost above a reservation
is recorded fully and logged as a bound violation; it is never clamped away.

## Failure, cancellation and recovery

The reservation is already durable at the moment a send may begin. A timeout, HTTP 5xx,
mid-stream disconnect, missing/malformed usage, or process crash cannot make incurred
work free. Those requests retain the full reserved amount as an estimated quota charge.
Only a proven unsent connection or explicit trusted API 4xx can release the reservation.
No prompt, answer, image, provider key or friend token is added to the reservation ledger.

A response owner handles cleanup even if response-header delivery fails before its async
iterator starts. A 120-second deadline/timer also closes abandoned responses. Settlement
commits before cancellable network cleanup; database-write failure leaves the original
charge in place. Socket cleanup is cancellation-shielded and bounded.

The durable concurrency lease expires after 180 seconds. The next admission converts old
active rows to estimated, freeing their slots but retaining every reserved dollar.
The admin page lists the latest 100 unresolved reservations and labels totals as spent
plus reserved. Estimated amounts are not presented as verified provider invoice amounts.

For reconciliation, an operator must obtain trusted per-request provider cost evidence,
identify the corresponding reservation, and call `RelayDB.settle(reservation_id, actual_usd)`
in an authorized maintenance session. Estimated/active rows can be reconciled once;
settled/released rows cannot be silently charged twice. Never set costs to zero merely
because a lease expired or the client disconnected. Automatic invoice reconciliation and
a refund/edit UI are outside this fix.

## Validation

- 111 server tests pass, including all 49 pre-existing tests.
- Separate-connection tests cover competing admissions, settlement races, duplicate
  approvals/token delivery, stale budget/revocation, credential rotation, budget reductions,
  period rollover, persisted crash reservations, lease expiry and schema-1 migration.
- Fake HTTP/ASGI tests cover cancellation awaiting headers, disconnect before iteration,
  abandoned-response expiry, usage observed before yield, accounting-write failure,
  explicit 4xx versus ambiguous 5xx/timeouts, malformed usage/body and unknown prices.
- Screenshot tests verify unchanged PNG forwarding and reservation before the fake provider
  is contacted. UTF-8 and request-size regressions cover the bounded input contract.
- Ruff, formatting, mypy for all seven server modules and `git diff --check` pass.

All tests use synthetic data and mocked HTTP. No real relay database, API credentials,
provider billing, hosted deployment or customer records were accessed. The installed
Starlette emits one existing httpx TestClient deprecation warning; tests still pass.

## Release and rollback

Not deployed by this change. Use one Fly machine with one durable SQLite volume; independent
machine copies cannot enforce a common balance. WAL and a five-second busy timeout support
worker connections sharing that file. Take a consistent backup before the transactional
schema-1-to-2 upgrade. Older servers refuse schema 2. Rollback must preserve newly incurred
charges; restoring an old backup blindly would erase accounting and is not a safe rollback.
