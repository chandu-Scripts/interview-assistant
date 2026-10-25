# Vocabulary-hint echo suppression

The reported screenshot showed the exact configured default vocabulary hint being treated
as speech and then answered. Both cloud and local transcription adapters previously accepted
that result unchanged.

Both providers now reject long near-copies of their configured hint before emitting transcript
text. This also catches a copied list without its introductory sentence, punctuation/case
changes, and small omissions. The shared filter uses the configured hint, not hardcoded
interview questions. Short technical mentions and explicit question/instruction wording remain
valid. Empty final events release the aggregator's in-flight bookkeeping without submitting a
question, so the next real question can proceed normally. No retries, new API requests or
changes to microphone sensitivity were added.

Regression tests use mocked cloud and local providers to reproduce the screenshot, confirm
no question is emitted, and confirm recovery on the next real question. This is a conservative
text check: a person literally reading the long vocabulary hint may also be suppressed. It
does not eliminate every possible speech-recognition hallucination. No live audio capture was
started during validation; no user settings or history were modified.

Quit/reopen Chandoo - Glass Preview to load the source change. The installed release is unchanged.
