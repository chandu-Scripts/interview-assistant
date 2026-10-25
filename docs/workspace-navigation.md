# Single-window workspace and batch resume import

Implemented after the desktop glass preview feedback on 2026-10-25.

- Resumes → Import Resumes accepts multiple PDF, DOCX, TXT and MD files. Extraction runs on
  one background worker, continues after unreadable files, and reports successes and errors.
- Existing resumes are never overwritten by library imports. Filename collisions, including
  case-only collisions, receive a numeric suffix. Each saved resume is immediately available
  in the interview wizard. Incognito still prevents library writes.
- Settings, Privacy, History and Audio/transcript controls are hosted in the existing workspace.
  Settings tabs scroll independently, with Save/Cancel visible. History's Create Session action
  uses the existing interview wizard. Tray and second-launch actions raise the workspace.
- Presentation mode minimizes the workspace, hides the live overlay and pauses capture/answers.
  Embedded navigation cannot reopen it during presentation. Recording and incognito status are
  visible above the workspace pages.
- The approved 960 × 680 logical-pixel default is unchanged. Native Windows preview validates
  exactly one visible top-level window for each of six navigation sections.

Storage remains local: SQLite for session/history records and atomic JSON saves for the resume
library. No cloud database or credentials are required for these changes. The download website
distributes the app; it does not synchronize resumes, settings or history across computers.

Validation tooling: `scripts/preview_workspace.py`, `tests/test_resume_page.py`, and
`tests/test_composition.py`. Screenshots contain fictional data in `tmp/workspace-preview`.

Validation results: initial focused run 102 passed; full regression run 663 passed, 2 skipped,
and one old tab-parent assertion failed. After updating that assertion for the scroll container,
all 104 affected configuration/composition/resume tests passed. Ruff and mypy (87 source files)
passed. Native Windows preview confirmed one visible 960 × 680 window for all six sections.

These changes are in the local source preview. Previously built v0.1.1 artifacts predate this
follow-up and were not rebuilt or published; the signing blocker described in `glass-ui.md`
still applies. Quit the running app and reopen **Chandoo - Glass Preview** to load the changes.

Follow-up: the running app's startup log showed Incognito enabled, which prevents all saved
resume mutations. The Resumes page now displays that restriction before any picker or
confirmation opens, disables import/rename/delete, and provides an Open Privacy button in
the same workspace. Explicitly turning Incognito off immediately restores those controls;
the app does not change privacy preferences automatically. Regression coverage verifies
import and confirmed deletion after that toggle, with the original file preserved.
Validation: 84 resume/composition/privacy tests passed; Ruff and mypy passed.
