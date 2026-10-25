# Local semantic saved-answer matching

The source preview now compares question meanings using the locally executed
`sentence-transformers/all-MiniLM-L6-v2` ONNX model. It no longer repairs the specific
“have sed” wording. No question/answer-specific paraphrase table is used. Existing exact
and equivalent-word shortcuts remain; non-literal questions use normalized sentence vectors.
Only questions are embedded. The original saved answer is returned unchanged, with its
Saved fictional Q&A label; saved fictional answers never enter the AI profile or history context.

## Decisions

- Cosine similarity must reach 0.55, with a 0.10 margin over the next compatible, distinct answer.
  These are heuristics, not calibrated probabilities or a guarantee of correctness.
- General checks reject changed negation, numeric qualifiers, conjunctions, and timing words.
  Question intent distinguishes quantity, explanation, method and reason. A count-target check
  distinguishes counting tables from counting catalogs. These are general grammar guards;
  they do not enumerate individual saved questions.
- Conflicting equivalent entries and close competing semantic matches abstain. Normal AI
  routing handles uncertain or unrelated questions. The app does not invent a saved match.
- Questions and vectors stay in memory. There are no embedding API calls or matching charges.
  The model is loaded once per matcher, with two CPU threads. Startup warms the index away
  from Qt; updates rebuild it on the next lookup. Up to 128 query results are cached.
- File modification time plus size invalidates both index and query cache. A shared lock
  protects simultaneous audio sources. All controller lookups run in a worker thread.
- Missing/corrupt model files degrade to literal/equivalent matching and normal AI routing,
  with a content-free warning in the app log. No automatic downloads occur inside the app.

## Setup and distribution

Run `python scripts/download_semantic_model.py` once. This explicitly downloads about 91 MB
of model/tokenizer assets from pinned revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`.
Assets live in `src/interview_assistant/assets/semantic/` and are git-ignored. The model card
and revision are kept with the assets. The project declares ONNX Runtime and tokenizers as
direct dependencies. The wheel and PyInstaller specifications include the assets; PyInstaller
requires the download to have been completed. No installer was built or published here.

Model source and pooling instructions:
https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2

## Validation and limitations

Tests cover model-based paraphrases across catalog count, pipeline count, data volume and team
size; unrelated definitions; changed negation; multi-part questions; table/catalog and
schema/catalog distinctions; conflicting answers; cache refresh after edits/deletion;
missing model behavior; and the actual overlay using fake providers. All 150 relevant tests
passed; Ruff and mypy (94 source files) passed. Native Windows preview verified
“What was your catalog count?” renders the saved answer with no configured cloud providers.

Read-only testing against the user's existing bank confirmed three catalog paraphrases return
the project-specific saved entry and “What is Unity Catalog?” falls back to AI. Warm fresh
lookups measured approximately 4–5 ms on this laptop. A later synthetic benchmark measured
5–7 ms per fresh query, about 0.4 s setup, and below 1 ms for cached repeats.
These timings exclude audio capture, transcription,
worker scheduling and UI rendering, and do not promise zero end-to-end latency.

This English sentence model is approximate. Unfamiliar terminology, long questions, scope
changes and some paraphrases can still be missed or misinterpreted. The model truncates at
256 word pieces. Larger saved banks need more initial indexing time. The app deliberately
falls back to AI for low-confidence results instead of promising universal semantic matching.

Reproduce with `python scripts/benchmark_saved_matching.py`. Real-model integration tests
in `tests/test_semantic_matching.py` run entirely offline after download and skip if assets
are absent; deterministic cache/failure tests use an injected encoder. Quit/reopen the source
preview to load the change.

## Saved-answer accuracy fixes (October 25, 2026)

General and environment-specific questions no longer share automatic answers: production/prod,
development/dev, test/testing/QA, UAT and staging are compared symmetrically. The scope is taken
from the saved question, not from incidental words in its answer. A general answer mentioning
three environments therefore cannot answer a production-only count. If the bank also contains
a production question, compatible entries are ranked first, so a high-scoring generic question
cannot hide that correct scoped match. Multi-question follow-ups, contracted negation and numeric
versions have additional guards. These are general language checks, not a per-question table.

Identical answers on close paraphrases no longer create artificial ambiguity. Existing duplicate
questions with identical answers can resolve; conflicting duplicates still abstain. No existing
records are rewritten. Exact matching, manual saves and JSON preview/import share normalization
for case/spacing/punctuation, preserving technical spellings such as C++ and C#. New duplicate
manual saves/renames are rejected with an inline warning; imports skip already saved questions
and reject duplicate keys inside a batch. Editing the original answer still works.

Read-only validation of the user's bank found 8 entries, no duplicate question groups and 8/8
exact questions resolving. The misspelled catalog question returned saved Q&A; a production-only
count and a Unity Catalog definition correctly declined the generic count. Synthetic warm
semantic queries measured 3.4–9.2 ms (cached repeats below 0.1 ms), excluding UI/audio time.
Semantic matching remains approximate and cannot guarantee equivalence for arbitrary project,
ownership or time-scope changes. Stored answer text remains unchanged and never enters the AI profile.
