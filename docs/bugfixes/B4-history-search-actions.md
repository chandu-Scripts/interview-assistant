# History search actions

Global search selects the session that owns the highlighted result. View transcript,
export and deletion resolve their target through the same accessor. With no result
selected, session actions are disabled even if the left list still has a selection.
Clearing search opens the session most recently selected through a result.

Delete confirmation identifies the session title/company and date. Original stored
data and schema are unchanged. Tests use only in-memory history and temporary export
files, covering cross-session results, selection changes, no results, export,
cancelled/confirmed deletion and clearing search.

Rollback: revert this branch's UI/test change; no data migration is required.
