# Verified credential removal

Provider and shared-access credential removal now distinguishes an unavailable vault
from a missing credential. Deletion checks the credential before and after the operation
using strict backend reads. Read failures, delete failures and no-op deletes cannot
produce a success notice. Backend exception strings are not exposed.

Normal runtime credential loading retains its existing tolerant behavior and saved-key
precedence. Removal failures leave provider verification and pending relay state intact.
An already absent credential is safe to remove repeatedly. Windows account-scoped
credential namespaces and logout mutation fences remain in place.

Regression tests inject the entire keyring backend; no personal credentials are read
or changed. Rollback needs only the code revert, with no data migration.
# Follow-up independent review

Purge now performs strict deletion for every provider before removing the local
inventory and includes the legacy relay token. A tolerant runtime read cannot
silently skip vault cleanup. The UI also preserves an approved relay request if
its token is missing or cannot be saved, and reports a failed key-save rollback
explicitly. Synthetic tests cover these failures without accessing real credentials.
