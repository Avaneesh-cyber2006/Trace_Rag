# Module 10 — Increment 2: Offline Privacy and Citation Validation

## Scope

Add provider-neutral privacy projection, strict answer schema, and citation
validation to the internal Module 10 package. No Gemini SDK/network calls,
API, frontend, repository rereads, or changes to Modules 1–9.

## Trust boundaries

- `EvidenceBundle` is raw and always `provider_safe=False`.
- `redact_evidence(bundle)` builds a separate immutable `RedactedEvidenceBundle`.
  It drops private-key-bearing source records and dependent references, masks
  common credentials and email patterns, and preserves full safe records
  rather than truncating code. It rejects sensitive user queries instead of
  silently rewriting the question. It never exports repository paths or raw
  source hashes, and never modifies the original registry.
- **Redaction is heuristic, not a proof of confidentiality.** New or uncommon
  credential/PII formats can evade pattern matching. Consequently even the
  redacted bundle has `provider_safe=False`. A later orchestration increment
  must require explicit external-processing authorization and an approved
  repository privacy policy before transferring evidence to Gemini.
- `parse_answer` refuses malformed, duplicate-key, oversized or nonfinite JSON,
  unknown fields, invented source-line intervals, numeric confidence fields,
  invalid claim categories, unbounded text, unsafe HTML and other invalid
  records. No Python SDK or generation is needed for this step.
- `validate_answer` recomputes the redacted view from the trusted **local** raw
  registry and rejects mismatches. Labels must be admitted and category-typed:
  `indexed_fact` -> `S`, `graph_relation` -> `G`, `reported_history` -> `H`,
  `reported_runtime` -> `R`. Quotes must occur literally in an admitted `S`
  source's **redacted** content; masked secrets cannot be quoted. A hypothesis
  is explicitly possible and can reference several evidence types, but an
  `answered` response needs at least one source/graph citation.
- A verified ID and exact quote **do not prove the natural-language explanation
  or causality**. Semantic and factual checking remains a separate requirement.
  Unverified history/runtime context cannot on its own establish an indexed
  answer. Do not grant a model permission to act on retrieved code instructions.

## Limits

The output schema permits up to 6 claims, 3 hypotheses, 8 limitations, 8 IDs per
claim, and 32 KiB encoded JSON. Redacted context is 65,536 UTF-8 bytes total
by default, with at most 16,384 bytes per item; the question is at most 4,096
UTF-8 bytes. The original Increment 1 item limits also apply.

This is a **pre-model** security layer, not permission to transmit private code,
not a UI-safe renderer, and not a claim of universal secret detection.

## Verification

Run `tests/test_module10_redaction.py` and `tests/test_module10_citations.py`,
plus prior Module 10 and Module 9 regression tests. Before any commit, run the
full offline project suite, review the staged diff, and preserve `main`.
