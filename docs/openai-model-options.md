# Additional OpenAI choices (2026-11-06)

The selectable OpenAI catalog includes GPT-6 Astra, GPT-6.1 Sol, GPT-6.1 Terra and
GPT-6 Luna alongside the existing GPT-5.4 pair. Settings Fast/Smart presets and
session Coding/Fast selectors share the verified account catalog. Existing saved
values, defaults and the recommended pair remain unchanged. Test key refreshes a
stale account model list; new credentials still require restart before answering.

Codex and Claude independently reviewed the approach and compared proposals before
implementation. Separating selectable models from the two-item recommendation
list avoids breaking the paired recommendation controls. Simply appending IDs to
that pair was rejected. No endpoint migration is needed for this app's current
text/image chat requests, which do not use model tools.

The adapter sends reasoning_effort=low for Astra/Sol and none for Terra/Luna.
It omits sampling temperature for these exact IDs. Completion caps are unchanged;
reasoning consumes that cap, so complex answers can still be incomplete. Existing
Continue handling applies. Existing HTTP error/retry policies are unchanged.

Standard uncached input/output estimates per million tokens are Astra $10/$50,
Sol $2/$10, Terra $1/$5 and Luna $0.10/$0.50. These entries were checked on
2026-11-06. Other rates were not reverified. Aggregate usage does not itemize cache
write or long-context surcharges; displayed costs remain estimates.

## Validation

- Provider picker, adapter, cost and split-role suite: 153 passed, 1 optional skip.
- Customer BYOK, continuation and config regressions: 124 passed.
- Ruff passed; mypy passed for all 5 changed production modules.
- Native Windows synthetic selector and glass provider popup opened correctly.
  Full row-selection inspection was blocked by the UI automation helper losing
  transient popup element references; selection behavior is covered by Qt tests.
- Effective account catalog listed all 4 IDs. One synthetic live streamed request
  per model completed normally with nonempty text and usage. No resume was sent.
- First text / total seconds: Astra 3.197 / 4.545; Sol 2.300 / 3.386;
  Terra 1.504 / 1.913; Luna 1.126 / 1.473. Single-request smoke measurements are
  not a comparative benchmark or a guarantee for real interview prompts.

Claude's final read-only diff review found no blocking issues. Manually typed
snapshot IDs remain outside the four verified aliases and do not inherit their
explicit reasoning settings; no unverified snapshot compatibility is claimed.

Rollback: revert this feature commit. No storage migration, credential changes or
automatic model selection is introduced. Installer deployment is separate.

## Official references

- https://developers.openai.com/api/docs/models/gpt-6-astra
- https://developers.openai.com/api/docs/models/gpt-6.1-sol
- https://developers.openai.com/api/docs/models/gpt-6.1-terra
- https://developers.openai.com/api/docs/models/gpt-6-luna
- https://developers.openai.com/api/docs/guides/reasoning
