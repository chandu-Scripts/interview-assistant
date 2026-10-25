# Conversational answer prompt

The shared specialist prompt now requests the actual main answer in paragraph one, supporting
details in separate closed text blocks, and 3–4 meaningful conversational markers per prose
paragraph. It explains the roles of “so”, “mainly”, “basically”, “I mean”, “like”, and “also”,
limits “yes” to supported affirmative answers, and reserves “kind of” for approximation.
A two-paragraph cadence example and silent per-paragraph count reminder reinforce the rule.
Code, code comments, identifiers and output tables are exempt. No programmatic filler insertion
or additional model pass was added. Saved fictional Q&A remains verbatim.

## Live validation, October 25, 2026

Three rounds used GPT-4o mini with the app's actual specialist prompts and three synthetic
questions: SQL optimization, Python list versus tuple, and a small Python addition function.
No resume, audio or user data was sent. The first run missed the style frequently; examples
and a clearer count instruction improved some paragraphs but did not establish reliable
3–4 marker compliance. In the third SQL answer the marker counts were 4, 2 and 1. The
list/tuple answer still included an unfenced opening; the app's existing local final-format
repair handles wrapping, but cannot improve wording. Some SQL advice was too general,
and the tuple answer omitted that its contents must be hashable for dictionary-key use.

Therefore the prompt change is implemented, but exact filler counts and naturalness are not
guaranteed by GPT-4o mini. No claim of full style acceptance is made. The opt-in reproducible
script is `scripts/test_spoken_style_live.py`; synthetic responses are in
`tmp/spoken-style-live.json`. Its marker counter is a diagnostic only: it does not determine
whether each occurrence serves a natural conversational purpose.

The style applies to all specialist prompts. Existing answer formatting/copy-card and routing
tests are run separately from live prompt-quality checks. Restart the source preview to load
the prompt. Model selection, installed release, saved answers and user settings are unchanged.
