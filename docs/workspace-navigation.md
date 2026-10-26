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

## Resume import and answer context, October 26, 2026

The effective local configuration again had Incognito enabled. Its saved-library write block
is intentional and remains in place. The Resumes page now offers **Use a resume for this
session**, which opens the existing wizard and attaches the file without saving it to the
library. Cancel/reset clears the draft's temporary text. Existing saved resumes remain
selectable. A file whose extraction started in Incognito is never added to the library even
if privacy changes before extraction finishes. History/usage privacy behavior is unchanged;
explicitly changing privacy later controls subsequent session/history persistence as before.

The wizard now parses attachments on its background worker instead of blocking Qt. It
disables advancement during loading, shows completion/errors in view, and discards cancelled
results. A failed import or a removed/renamed selected resume must be resolved by retrying or
explicitly selecting a resume (including No resume) before creation; it cannot silently use
the old/global profile. Creating/cancelling clears temporary attachments. Saved imports still
ask before replacing the same filename. Existing session snapshots are not modified.

DOCX extraction previously ignored all tables, headers and footers. Reproductions showed
that table-only resumes were rejected as empty and mixed-layout resumes lost project facts.
Extraction now preserves paragraph/table body order, includes nested tables and header/footer
text, and avoids repeating merged cells. The existing 12,000-character limit remains. Scanned
PDFs still require text recognition; empty-file errors now explain this limitation.

Disabled primary buttons now look disabled, including Import Resumes when Incognito is on.
The active session-only action uses the existing cyan styling. Native screenshots verified
one 960 x 680 logical-pixel workspace and visible upload status text after automatic scrolling.
Screenshots and test data are isolated under `tmp/resume-native-final`.

Validation: 41 distinct attachment/library/wizard tests passed (30 also run with native
Windows rendering), plus 186 broader composition, session, privacy, formatting, Q&A matching
and end-to-end tests. Ruff and mypy passed (101 source files). End-to-end mocked-provider tests
verify extracted table facts reach the actual request, replace the old profile, and remain
out of persistent library/session storage in Incognito. They cover cancellation, privacy
changes mid-import, retries, explicit selection and compact UI sizing.

Three live synthetic checks used the real DOCX parser and Jev/router: six team members,
12 pipelines, and a list-versus-tuple question all received relevant answers. No personal
resume was sent. Results are in ignored `tmp/imported-resume-live/answers.json`. This proves
the tested resume-context path, not universal answer correctness. The user's specific bad
question/answer example was requested and has not yet been supplied. Saved fictional answer
precedence and AI prompts were not changed in this fix.

These are source-preview changes; restart Chandoo - Glass Preview to load them. The installed
release and website installer were not rebuilt. No real resumes, sessions, keys or privacy
settings were changed during testing.
