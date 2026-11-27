# Stage 2 generation reliability investigation

Updated: 2026-11-26. Branch: `fix/module10-generation-reliability`.

## Status

Live API validation succeeded for two of the three approved questions. The approved repeat failed with two provider HTTP 504 timeouts.
The final backend suite passed all 4,421 tests. Stage 2 remains partially verified,
with provider availability and live browser rendering outstanding.

## Findings

The reported `ANSWER_SCHEMA_INVALID` is a lossy adapter error, not a unique root
cause. It previously combined JSON errors, field shape errors, character bounds,
unsafe markup, privacy checks, duplicate citation labels, and cross-field
consistency failures. The previous live JSON was not retained, so its exact
failure cannot be inferred from `FINISH_REASON: STOP` and the generic code.

The generation schema and local parser have a reproducible mismatch: the schema
allows arbitrary strings but the parser requires non-whitespace strings and
limits overview to 2,000 characters, claim text and hypothesis summaries to
1,000, excerpts and limitation entries to 500, and missing-observation entries
to 300. A schema-shaped answer with a 501-character excerpt reproduces the
generic failure. This proves a contract gap, not that the historical response
contained that particular defect.

Google documents that `minLength`, `maxLength`, and `uniqueItems` are ignored by
constrained decoding. Adding those keywords would not guarantee acceptance.
Supported descriptions and system instructions now state the local constraints;
local validation remains authoritative.

Reference: https://ai.google.dev/gemini-api/docs/structured-output

HTTP 503/504 errors are separate provider availability failures. They occur
before answer parsing and are not evidence of a response-schema problem.
The existing opt-in maximum of two generation attempts is preserved, with SDK
retries disabled, and only HTTP 500/502/503/504 eligible for the second attempt.
Malformed generation is never retried automatically.

## Changes

- `backend/causal_reasoner/answer_models.py`: preserve every acceptance rule;
  attach static reason codes and field paths to parser failures. JSON duplicate
  keys and nonfinite constants retain distinct diagnostics. No untrusted key or
  value appears in diagnostic metadata.
- `backend/causal_reasoner/providers/gemini.py`: preserve the user's transient
  retry and structured schema changes; describe text limits and cross-field
  rules; log safe parser categories; distinguish provider failure classes;
  reject truncated and blocked candidate output even if its JSON is parseable.
- `tests/test_module10_gemini_adapter.py`: preserve existing tests and add
  regressions for diagnostic accuracy/privacy, length limits, incomplete
  responses, malformed JSON, and provider failure categories.

No changes were made to citation validation, query redaction, authorization,
indexing, frontend implementation, or deployment configuration. Parsed answers
still pass through independent registry validation before API serialization.
Quoted excerpts must remain exact substrings of admitted indexed evidence.

## Final verification (2026-11-26)

- Final targeted regressions: **129 passed**.
- Frontend: **21 passed** across two files; TypeScript and production build passed.
- Earlier full backend run: **4,407 passed, 5 skipped, 2 deselected**. This run
  preceded the final 14 diagnostic tests. The fresh final run completed: **4,421 passed, 5 skipped, 2 deselected**,
  with two dependency deprecation warnings, in 531.63 seconds. These are the
  Starlette/httpx and Chroma legacy embedding configuration warnings. No failures.
- Independent production-code review: no actionable correctness or security
  regressions. `git diff --check` passed.
- Working branch remains `fix/module10-generation-reliability`, HEAD
  `21711c789381c3c44be53d38a7fb8474a9fcab38`. No commits, merges, pushes or deployment.

## Live results and cumulative budget

The original allowance was three query embeddings and six generation calls,
including retries, with `top_k=3` against the existing 12-chunk namespace
`tracerag/stage2-targeted-21711c7`. No indexing was performed.

| Investigation | Result | Embeddings | Generations |
| --- | --- | ---: | ---: |
| How does TraceRAG validate evidence citations? | HTTP 200; 3 claims, 3 citations; independently verified | 1 | 1 |
| When does TraceRAG reject a generated answer? (initial) | Failed after transient retry; final category unavailable in interrupted log | 1 | 2 |
| How does TraceRAG verify quoted source excerpts? | HTTP 200; 2 claims, 2 citations; independently verified | 1 | 2 |
| Second question, explicitly approved repeat | HTTP 504 twice; SERVICE_ERROR; local API HTTP 502 | 1 | 2 |

Before the repeat, cumulative consumption was exactly **8 calls: 3 embeddings
and 5 generation attempts**. The user explicitly approved one bounded repeat,
raising the cumulative maximum to **11 calls: 4 embeddings and 7 generations**.
The repeat consumed all three additional calls. Final cumulative consumption is
**11 calls: 4 embeddings and 7 generation attempts**. No budget remains.
The runner carried the prior counts forward and executed only the failed question.
It does not repeat the successful investigations or retry malformed output.

The first generation attempt in both two-attempt runs necessarily satisfied the
adapter's transient HTTP retry condition (500/502/503/504). The original second
investigation's final failure category cannot be recovered from the retained log;
it must not be described as a confirmed schema failure or a specific HTTP status.

### What was verified

Actual loopback HTTP requests exercised the real FastAPI endpoints, not an
in-process test client. `/api/v1/repositories` returned 401 without a bearer token
and 200 with the privately generated token. `/api/v1/answer` retained server,
repository and per-request external-processing approval. Query privacy was
checked before embedding. Successful API JSON passed `AnswerEnvelope` validation
and independent `validate_answer` against evidence assembled and redacted from
the actual retrieved chunks. Returned citation IDs and content matched the
registry exactly; any model-provided excerpts remained subject to exact
substring checks. This checks reference grounding, not semantic truth or runtime
causation.

### Browser versus API verification

The supported Codex browser loaded Evidence Studio without console errors. The
example-question button populated the question, the source selector selected
three hits, and analysis remained disabled without authentication. Frontend
integration tests passed. Live authenticated answer rendering and citation-click
navigation were **not** verified in a browser; successful live results above are
API-level verification. No synthetic success was substituted for live output.

## Verification driver and security

Operational files remain outside the repository in
`%LOCALAPPDATA%\TraceRAG\stage2\verification-20261125`:
`serve.py`, `start.ps1`, `server-first-run.log`, `server-resumed.log`, and
`server-approved-repeat.log`. Logs contain only status/count/category metadata.
The session bearer token is generated and retained only in Python memory;
credentials enter through a masked prompt and are not persisted. Clipboard
handoff is no longer used. Each verification process shuts down after its run;
no second process opens the pilot store while it is owned. Startup checks require
12 chunks and the expected Gemini embedding identity.

The interrupted first run did not retain its final diagnostic. PowerShell's
`ErrorActionPreference=Stop` combined with native stderr redirection is a
suspected driver failure mechanism, not a proven provider root cause. The driver
now logs to stdout and permits native stderr without terminating the pipeline.
It also records only numeric HTTP status codes and uses a 240-second local HTTP
timeout, exceeding the approximately 181-second maximum for an embedding plus
two generation attempts. These operational adjustments do not weaken production
validation or broaden retry behavior. No credentials or raw provider bodies are
included in diagnostics.

The original Downloads launcher pins pre-fix hashes and cannot launch this tree
unchanged. It was preserved; the separate verification driver was used instead.

## Readiness and remaining limitations

The repeat conclusively failed at the provider boundary with HTTP 504 on both
attempts; no generated answer reached local schema validation on that repeat.
The final full suite passed. Stage 2 must not be declared fully complete: one
question remains unverified due to provider timeouts, and live browser answer
rendering is outstanding. Stage 3 deployment preparation can be planned, but
execution should wait for the owner to accept or close these verification gaps. The exact historical `ANSWER_SCHEMA_INVALID` category remains
unknown because the failing JSON was not retained; the reproducible contract gap
and diagnostic loss have been addressed and tested. Provider availability remains
a separate operational concern. Browser rendering of a live answer remains an
explicit verification gap. Stage 3 planning may use these findings, but this is
not a public-deployment approval. The local-session API is not a production
multi-user authorization system.

All unrelated untracked files and existing user changes were preserved. The
persistent index was neither rebuilt nor deleted, and published main was unchanged.

## Final disposition

The verification backend exited after its bounded run, releasing pilot-store
ownership. The frontend development server remains available on loopback port
5173. No persistent credential or bearer-token file was created. All 11 approved
calls are consumed; another live investigation requires new approval and secure
credential entry. No further production change is justified by the observed
504 provider failures. The exact historical schema-rejection cause remains
unrecoverable from the original content-free log.

Files changed in the repository: `backend/causal_reasoner/answer_models.py`,
`backend/causal_reasoner/providers/gemini.py`,
`tests/test_module10_gemini_adapter.py`, and this report. The provider and test
changes include the user's pre-existing uncommitted fixes. Final review found
no actionable production regression; the operational client timeout finding was
corrected outside the repository.
