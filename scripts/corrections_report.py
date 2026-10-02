"""Summarize "wrong specialist?" corrections from history.db, to guide rule tuning (rules.py).

Usage: python scripts/corrections_report.py"""

import sqlite3
from collections import Counter

from interview_assistant.config.user_settings import default_data_dir

db = default_data_dir() / "history.db"
if not db.exists():
    raise SystemExit(f"no history database at {db}")

conn = sqlite3.connect(db)
rows = conn.execute(
    "SELECT question, corrected_from, category, decision_source "
    "FROM turns WHERE corrected_from != '' ORDER BY ts DESC"
).fetchall()

if not rows:
    print("No corrections recorded yet.")
    raise SystemExit(0)

print(f"{len(rows)} correction(s):\n")
pairs = Counter((frm, to) for _q, frm, to, _src in rows)
for (frm, to), n in pairs.most_common():
    print(f"  {n:>3}x  {frm} -> {to}")

print("\nMost recent examples:")
for question, frm, to, _src in rows[:15]:
    print(f"  [{frm} -> {to}] {question[:80]!r}")
