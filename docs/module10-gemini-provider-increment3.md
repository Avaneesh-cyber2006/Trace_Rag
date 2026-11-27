# Module 10 — Increment 3: Optional Gemini generation adapter

## Scope

A provider-neutral, immutable `GenerationRequest` is bound to the exact
redacted evidence projection recomputed from the local `EvidenceBundle`.
`GeminiAdapter` uses the pinned Google Gen AI Python SDK (`google-genai==2.22.0`)
only at the provider edge, and only after both gates succeed:

1. `GeminiSettings(enabled=True)` is explicitly configured by the trusted server.
2. `prepare_generation_request(..., external_processing_approved=True)` is
   called **only after** the server has checked repository ownership,
   authorization, consent, and outbound data policy. A client cannot authorize
   itself by posting the flag to the API. This increment contains **no** auth
   service, so real external transmission is not approved for end users yet.

`GeminiSettings` does not auto-load environment credentials. It needs an
explicit server-side API key and generation model ID. API keys are excluded
from its `repr` and must never go into client state, prompts, responses or logs.

## Provider behavior (updated for Stage 2 reliability)

- No SDK or network access at import time; SDK imported lazily on first
  authorized `generate()` call; no external call in normal unit tests.
- One attempt by default, with a JSON-serialized redacted question/evidence
  projection (no raw source paths or SHA values). Whole-record omissions remain
  explicit. A separate system instruction treats *all* retrieved data as
  untrusted and requires a restricted answer schema.
- `response_mime_type=application/json`; configurable generation model,
  1–60 second client timeout and 128–8192 max output tokens.
  Server-side `max_attempts=2` explicitly enables one retry for HTTP
  500/502/503/504, after one second, using the same approved payload.
  SDK retries and automatic function calling are disabled. Authentication,
  quota, unknown transport, and malformed-output failures are not retried.
  Prompt capped at 128 KiB and output at 32 KiB.
- Provider errors and malformed outputs map to sanitized typed errors. Raw
  provider exception strings are never passed back to the caller.
- A request-specific JSON schema constrains evidence labels and categories.
  Text bounds are described to the model and enforced locally. Truncated or
  blocked candidate output is rejected. Logging contains static failure codes
  and parser field paths, never generated values, prompts, keys or SDK bodies.
- `parse_answer` checks the JSON schema and a preliminary local evidence-ID
  membership check runs. The returned `ReasonedAnswer` is **still only a draft**;
  it MUST be independently checked by `citations.validate_answer(raw, redacted,
  candidate)` (including excerpts, trust categories, and registry identity)
  before any content is presented to a user. Increment 4 will implement that
  orchestrator.

## Safety limits

Redaction is pattern-based, not a proof that confidential information is absent.
Neither the raw bundle nor the prepared projection is automatically authorized
for transmission. This adapter is not an autonomous tool and does not use any
retrieval APIs, load files, log source content or perform code execution. A system
instruction reduces—but cannot eliminate—prompt injection risk.

No change to Modules 1–9 or original module contracts. No SDK dependency
modifications, API, UI, live-model tests, or feature-branch commits.

## Verification

Run the new `tests/test_module10_gemini_adapter.py` with the existing
Module 10 evidence/privacy/citation tests, Module 9 integration tests,
real-Chroma tests, and older contract tests. Tests inject a fake client and
require no credentials or network. A separately guarded live test can be added
only after the repository privacy/consent flow is implemented.

## Launcher compatibility

Existing callers remain compatible: `max_attempts` defaults to 1. No schema or
database migration is required. The repository's `module10_local_demo.py` is an
offline synthetic demo, not a production launcher. There is no checked-in
production Gemini launcher in this release.

The Downloads Stage 2 launcher pins old file hashes and must be deliberately
updated after release approval to launch the revised tree. Do not remove its
integrity checks or reuse the budget-specific verification driver for production.
For a future supported launcher, explicitly bind server/repository consent,
inject secrets securely, choose the model, and set timeout/output/attempt budgets.
Keep embedding SDK retries explicitly bounded as well. End-to-end deadlines must
include retrieval and all permitted generation attempts, not just one attempt.
