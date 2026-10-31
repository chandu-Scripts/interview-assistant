# B1: preserve direction when selecting a saved answer

## Problem and scope

The saved-answer shortcut compared unordered token sets. A saved answer for migrating
data from SQL Server to Snowflake could therefore answer the opposite migration. The
semantic fallback checked intent and environment but did not check entity roles, so
removing the shortcut alone did not resolve the problem.

This change is limited to saved-answer retrieval. It does not edit saved questions or
answers, change the answer prompt, add a network request, or modify normal AI routing.

## Decision

Use ordered normalized content for the strong shortcut, retaining the existing aliases
and stop-word handling. Apply the same relation compatibility guard before both strong
matching and semantic ranking. Explicit `from X to Y` and `to Y from X` constructions
compare source and destination roles, including `into`, `onto`, and `towards` variants.
Expanded endpoint names can match, but a shared fragment alone does not prove identity.
Outside that syntax, changed order of shared content abstains; question and quantity
scaffolding can move without causing a rejection.

Raising the embedding threshold was rejected: reversed relations can receive very high
similarity. A paid model disambiguation call was rejected because it adds latency and
cost to local lookup. Literal per-question exceptions were rejected because the failure
is structural and can occur with arbitrary entity names.

Ordinary normalized exact lookup remains unchanged. When either raw question contains
a directional arrow, exact candidates also need compatible source/destination roles.
ASCII and Unicode arrows share this check. Query cache keys preserve the arrow direction,
preventing a cached forward answer from leaking into a reverse-arrow query whose other
words are identical. Multi-hop arrows abstain unless the operator-preserving question
matches exactly. Valid saved results still return the original text verbatim. Failed
compatibility returns to the existing AI route without selecting a saved answer.
Incompatible candidates are removed before the semantic ambiguity margin.

## Validation

Executed from the isolated feature worktree, using the existing local ONNX assets through
a junction. Tests use temporary synthetic Q&A banks; no private records or paid providers.

- 100 tests passed: `test_practice_direction.py`, `test_practice_matcher.py`,
  `test_semantic_matching.py`, and `test_practice_import.py`.
- Regression cases cover equal token bags with reversed migrations, shared Azure name
  fragments, subject/object triggers, preferred tools, temporal ordering, changed
  endpoints, cached abstentions, and correct selection when both directions are saved.
- Arrow tests cover exact-normalization collisions, equivalent left/right notation,
  ordinary from/to wording, Unicode variants, and semantic cache separation.
- A fake encoder assigns maximum similarity to every candidate, proving that semantic
  fallback cannot bypass the role guard. Real ONNX tests cover reversed and equivalent
  migrations and all existing quantity/scope paraphrases.
- Exact and strong saved answers remain verbatim; a normal stop-word paraphrase continues
  to return without loading the model. Existing JSON import validation remains passing.
- Ruff check and format check, matcher mypy, and `git diff --check` passed.

## Limits and rollback

This is a conservative structural guard, not a complete English parser. Some valid
active/passive rephrasings, disjoint aliases, or heavily reordered explanations can
abstain and use AI. Saved-question identity and import duplicate detection are unchanged:
two entries differing only in arrow direction are still rejected as duplicates, but an
opposite-direction query no longer returns the saved answer. Saving both is possible
using explicit from/to wording. No universal semantic accuracy claim is made.

Rollback consists of reverting this feature's commit. There is no storage migration and
no saved-data rewrite. The branch has not been merged, pushed, installed, or deployed.
