# Customers use their own AI provider keys

Date: 2026-11-01. The owner explicitly chose customer-owned API keys when approving
the tested bug-fix merge.

## Decision

Customers configure their own provider credentials in the app. Generation is billed
by the selected provider to that customer's provider account. The app owner does not
include or fund AI usage as part of app access. Paid cloud transcription likewise
needs a customer credential; an available local transcription option needs no paid
speech key. Customers need only the providers they choose to use.

Provider credentials remain in the account-scoped Windows Credential Manager and
are excluded from settings files, history, exports, backups and logs. The existing
customer foundation ignores personal environment/.env provider credentials and does
not automatically adopt the owner's saved credentials.

App access and AI provider billing are separate. App prices, merchant provider,
currency, refund policy and any subscription terms still require owner decisions.
This approval does not activate a checkout, subscription, server deployment or paid
provider call. The existing personal/friends relay is separate from the commercial
product and must not automatically fund or enroll customers.

## Implementation boundary

This change records the product decision; it does not claim that all commercial
onboarding and relay restrictions have been implemented. Before a customer release,
review onboarding to explain provider billing, confirm customer mode cannot silently
use owner-funded access, and test missing, invalid, replaced and removed customer
keys with isolated fixtures. Preserve normal provider fallback only among the
customer's configured providers.

Server access grants can restrict the official app. They cannot fully prevent a
modified client from using keys owned by the customer. Hosted account acceptance,
commercial access controls and signed release preparation remain separate work.

## Current merge scope

The current approval covers the six tested bug-fix branches and their required
account-login foundation, plus work reports and this decision. Admin, entitlements,
cloud backup, local transfer, hosted email and release features remain on their
existing feature branches. GitHub push requires separate approval.
