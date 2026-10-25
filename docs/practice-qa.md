# Fictional / Realistic Q&A

One workspace section for locally saved practice questions and answers. Supports adding,
editing, keyword search across question text, and confirmed deletion. The optional
Add fictional examples button adds four hypothetical entries (catalogs, pipelines, data
volume and team size); repeated clicks do not duplicate unchanged example questions.
Example counts describe an invented scenario, not the user's experience.

Data lives in `practice_qa.json` beside the local resume library. Saves are atomic; malformed
files are preserved and reported. Incognito blocks mutations, displays a notice, and provides
an in-workspace Open Privacy action. Nothing is written merely by opening the section.
The bank is not inserted into candidate profiles. Text answering now checks the bank first,
as described in the local matching follow-up below.

Validation: 51 focused practice/composition/start-screen tests passed; Ruff and mypy passed
(89 source files). Native Windows preview verifies seven sections retain one visible window
at 960 by 680 logical pixels, using isolated fictional data and fake providers.

Available in the source preview after quitting and reopening Chandoo - Glass Preview.
The downloadable installer has not been rebuilt or published for this change.

## Question-first UI follow-up

The initial view now contains question rows only, search and New question. Clicking a row
(or pressing Enter on it) opens its read-only answer. All questions returns to the list.
New/Edit opens a separate form inside the same workspace; no additional window is created.
Saving returns to the question list. Empty or whitespace-only answers show an explicit
compulsory-answer warning and never write data. Questions are also required. Cancelling
changed form contents requests confirmation before discarding them.

Rows have hover feedback; view changes use a 150 ms eased opacity transition. Rapid
navigation stops/restarts the same animation, and hiding the page restores full opacity.
Validation: 35 practice/composition tests passed, including click/keyboard opening,
missing-answer rejection for new and existing entries, discard confirmation and rapid
navigation. Ruff and mypy passed. Isolated native Windows screenshots verify list, answer
and editor views at the existing 960 by 680 logical size with one visible window.

## JSON import

Manual New question remains available beside Import JSON. Import accepts pasted text or a
UTF-8 JSON file (optional BOM), shaped as `{"Question?": "Answer"}`. Validate reports how
many entries are new or already saved before Import questions commits the batch. All pairs
must have non-empty question/answer strings, with the same length limits as manual entry.
Any invalid pair rejects the entire batch. Repeated keys (including case/outer-whitespace
variants) within the input are rejected. Existing saved questions are skipped rather than
overwritten, using case-insensitive, outer-whitespace-trimmed matching.

Imports allow up to 1,000 pairs and 1 MB per batch. Saving uses one atomic replacement;
errors and Incognito preserve existing data. Editing JSON invalidates the previous check.
Returning to the question list preserves an unsaved JSON draft during this app session;
successful import clears it. Import never writes to or changes the source JSON file.

Validation: 53 import/practice/composition tests passed; Ruff and mypy passed (90 source
files). Native Windows preview verifies the import view and both entry buttons at 960 by
680 logical pixels with one visible window. Available in the source preview only.

Import-button follow-up: Import JSON now opens even in Incognito, allowing draft input and
validation without writing data. The final Import questions action remains blocked, with
an inline explanation and tooltip. Turning Incognito off restores import for an already
validated draft. The latest real startup log showed Incognito enabled; previously it disabled
the entry button entirely. Validation: 26 import/page tests passed, plus Ruff and mypy.

## Local matching before AI

Text questions, including transcribed questions once submitted for answering, check the local
bank before classification or any provider request. Normalized exact matches and conservative
equivalent-word matches return the stored text unchanged. The overlay labels them **Saved
fictional Q&A**, reports zero provider cost, and preserves that provenance in its history and
the persistent turn's route. These answers are rendered as plain text and are not added to
Jev's conversation memory or candidate profile. Screenshot requests bypass this text lookup.

Exact/equivalent and uniquely strong similar matches display the saved answer immediately,
without a question picker or Use saved button. Non-literal questions now use a local sentence
embedding model, with general question-intent, count-target, and negation checks. See
`semantic-matching.md` for model setup, thresholds, cache behavior, validation and limitations.
Ambiguous matches, no match, or an unreadable bank use normal AI
routing without requesting selection. AI output has its own **AI-generated** label.
Incognito still permits reading saved
answers but prevents saving history or modifying the bank.

The matcher caches its question index by file modification time and size, uses a lock across
audio sources, and performs disk work away from the UI/async loop. Similarity is deliberately
conservative: not every paraphrase is recognized; uncertain matches fall back to AI.

Validation: 324 app/controller/cost/hardening/end-to-end/history/privacy/matcher/composition
tests passed. Tests cover exact and equivalent phrasing, uncertain choices, unrelated AI
fallback, stale-choice cancellation, literal answer preservation, Incognito, cache refresh and
conflicting saved questions. A new-interview cancellation race was fixed by advancing the
history generation before cancelling previous work. Ruff and mypy (91 source files) passed.
Native Windows preview confirmed the saved-answer and choice UI without configured AI
providers, using fictional test data. Cached exact lookup averaged 0.041 ms over 100 searches
with 1,004 entries on this machine; this excludes transcription, worker scheduling and rendering.

Restart Chandoo - Glass Preview to load this change. Downloadable installers remain unchanged.

Automatic-answer follow-up: selection UI and pending-choice handling were removed. Native
Windows preview verified that "How many ADF pipelines did you build?" displays the saved
pipeline answer directly, with the fictional-source label and no configured AI providers.
The earlier selection-based tests were replaced with direct-answer and automatic-fallback
coverage, including competing saved questions, presentation cancellation during lookup,
and consecutive saved questions. Source-only update; no user library changes.

Duplicate-entry follow-up: manual saves and renames now reject a question already in the bank,
ignoring case/spacing/presentation punctuation. JSON validation, its preview counts and import
use the same rule; existing answers are preserved, not overwritten. Technical names such as C++
and C# remain distinct. Legacy identical duplicates can still return their agreed saved answer;
conflicting duplicates continue to use normal AI routing. Matching now preserves environment
scope and ranks only compatible answers; details are in `semantic-matching.md`. Your saved bank
was read for verification but not modified.
