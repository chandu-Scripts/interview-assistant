# Step 9 — History (SQLite), search, export, deletion
Built (history/store.py): HistoryStore over sqlite3 (schema v1 via PRAGMA user_version; newer schema refused): sessions + turns,
FTS5 index (porter stemming, prefix on last word, hostile input sanitised by fts_query), thread-safe (RLock), lazy session
creation (first turn), end_session(), delete_turn/session/all, purge_older_than(days) retention (never the open session),
export_session/export_all as Markdown or JSON, runtime `enabled` switch (Step 10 will surface it). Deletion is real:
PRAGMA secure_delete + FTS5 secure-delete + VACUUM on delete_all/purge (a test scans the file bytes for a deleted marker).
Each turn stores: question, answer, source (voice/typed), status (complete/interrupted/stopped/error), category, agent, tier,
routing confidence + decision source (rules/llm/fallback - the data needed to tune Jev), route, first-token/total latency, cost, error.
Controller saves every committed question incl. interrupted/stopped (partial text), provider errors and budget blocks; fillers are
not stored; storage errors never break answering; new_interview()/shutdown end the session.
ui/history_window.py: sessions list, question list, answer view, live search with highlighted snippets, export (md/json),
delete question/session/all with confirmation, "Save history" toggle. Main window: History button, live refresh when open.
Settings: HISTORY_ENABLED, HISTORY_RETENTION_DAYS (purge at startup); DB at %APPDATA%\InterviewAssistant\history.db.
Tested: 218 tests pass (41 new: CRUD, lazy sessions, stemming/prefix/AND search, hostile queries, concurrency, secure-delete file
scan, retention, export, controller integration for all statuses, window flows with injected dialogs). Live: spoken question +
typed follow-up saved in one session with metadata, search returned both hits, window screenshots reviewed.
Known gaps: history is stored unencrypted on disk (Step 10 should offer encryption at rest or at least a lock/incognito toggle
in the main UI); export is text/JSON only (no PDF); no per-turn edit/tagging; the raw transcript of ignored filler is not kept;
retention runs only at startup; the usage.jsonl cost log is separate from history.db (could be merged later).
