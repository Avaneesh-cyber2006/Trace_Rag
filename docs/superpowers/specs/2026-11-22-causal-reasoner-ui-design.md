# Module 10 — Causal Reasoner + UI: Architecture Proposal

**Status:** Proposed design, not implementation approval
**Repository:** TraceRAG
**Starting commit:** `3c215c0` (`main` / `origin/main`)
**Working branch:** `feature/module10-causal-reasoner-ui`
**Isolation:** Changes only in `Trace_Rag_Module10_Worktree`; keep existing worktrees and `main` unchanged until integration.

## 1. Goal and exclusions

Transform a bounded Module 9 result into an answer with verifiable source references, explicitly labelled historical/runtime clues, possible explanations, and an accessible user interface. Preserve the source-revision, namespace, immutability, and provider boundaries of Modules 1–9.

For v1, **do not** execute analyzed repository code, infer causal proof from call edges or log correlations, accept arbitrary user-selected server filesystem paths, send credentials or raw logs to a model, create numerical probabilities without calibration, or automatically rewrite/commit source files. No automatic remote publication.

## 2. Existing interfaces (confirmed from Module 9)

- `HybridRetriever.search(namespace, query, top_k) -> tuple[HybridHit, ...]`.
- `search_relevant(..., policy)` opt-in relevance gating.
- `search_with_graph(..., graph_revision, graph_top_k=20) -> GraphRetrievalResult`.
- `search_with_history(..., git_history, ..., relevance_policy, graph_revision, ...) -> HistoricalRetrievalResult`.
- `search_with_runtime(..., runtime_evidence, ..., relevance_policy, graph_revision, git_history, ...) -> RuntimeRetrievalResult`.
- `HybridHit` exposes: `repository_namespace`, `chunk_id`, `content_hash`, `relative_path`, `language`, `chunk_kind`, symbol/qualified names, `content`, `score`, `rank`, and lexical/semantic ranks. **It does not expose an authoritative source line interval**.
- `GraphContext` has a `LexicalDocument` backed by the pinned index plus graph-edge metadata.
- `HistoricalContext` and `RuntimeContext` are separate, bounded clues, with flags indicating their revision has **not** been verified.

All retrieval must use these public methods and returned immutable values. Module 10 must not directly access private Chroma storage, obtain a second snapshot, requery to support a citation, or reread potentially newer repository files to fill gaps. Runtime and Git inventories must come from the application's verified services, not directly from arbitrary browser-serialized objects.

## 3. Proposed dependency layout

```text
frontend/ (React + TypeScript, later)
    |
    v
backend/api/ (FastAPI, later)
    |
    v
backend/causal_reasoner/ (Module 10; provider-neutral core)
    |-- query_service.py       orchestration
    |-- evidence.py            immutable bounded evidence + source registry
    |-- citations.py           deterministic ID lookup, quote checking
    |-- redaction.py           sensitive-content controls before model calls
    |-- answer_models.py       schema, claim categories, explicit uncertainties
    |-- causal.py              hypotheses, counter-evidence, abstention
    |-- providers/base.py      generation protocol
    `-- providers/gemini.py    sole generation-SDK boundary
    |
    v
backend/retrieval/ (Module 9; **read only from Module 10**)
```

No dependency from Module 9 or earlier modules to Module 10. No dependency from the pure core on `google.genai`, FastAPI, React, Chroma, or filesystem operations.

## 4. Single-question pipeline

1. Validate repository namespace, query text, authorized repository scope, and bounded `top_k` before retrieval or a model request.
2. Invoke exactly one appropriate Module 9 public entry point depending on the available **server-provided** graph, Git, and runtime inventories. Preserve the returned hit order, chunk IDs, hashes, and verified/unverified distinctions.
3. Build an immutable `EvidenceBundle` from the returned values. Source code and bound graph chunks may be cited as indexed source. History/runtime records must remain *unverified clues*; never upgrade their trust based on exact-path matches.
4. Assign request-local stable citation labels (`S1`, `S2`, ... for source chunks; `G1`, ... for graph relationships; `H1`, ... for historical change; `R1`, ... for runtime frames). Each label maps to exactly one validated record with repository namespace and immutable identity. Do not use a citation label that maps to multiple records.
5. Apply privacy filtering **before** constructing the prompt. Refuse or omit unsafe content if needed rather than silently paraphrasing a secret. Redact secrets in source chunks, not just runtime messages. Store only minimal safe diagnostics in logs.
6. Enforce a finite context budget by selecting whole evidence items deterministically. If an item cannot fit, skip it and flag incomplete evidence; do not invent missing lines or truncate in ways that change identity/provenance.
7. Call a provider-neutral `ReasoningProvider` with a structured request. Gemini is one adapter; tests use a fake adapter without internet.
8. Parse the result into an internal strict schema. Validate all cited labels, source identities, any quoted snippets, answer length, and allowed claim types on the server. A schema-valid JSON result is **not** proof that a claim is true.
9. Return grounded answer text with evidence IDs, alternative hypotheses, limitations, and a clear insufficient-evidence result when the material cannot support a conclusion. The UI must not render untrusted model HTML or execute source code.

## 5. Trust and citation categories

| Kind | Source | Permitted claim | Disallowed claim |
|---|---|---|---|
| `indexed_source` | Module 9 `HybridHit` | "Indexed chunk contains ..." | "This definitely ran at failure time" |
| `revision_bound_graph` | Module 9 `GraphContext` | "A resolved graph relationship exists for the pinned source revision" | "Edge proves a runtime call occurred" |
| `unverified_git_history` | Module 9 `HistoricalContext` | "An inventory reports a commit touching this path" | "The commit caused this incident" |
| `unverified_runtime` | Module 9 `RuntimeContext` | "A supplied capture reports a frame here" | "This frame executes the currently indexed bytes" |
| `hypothesis` | Model output | "Possible explanation, with stated evidence and gaps" | "Confirmed root cause" |

**Citations:** Each claim must reference a recognized ID. Source IDs map to `repository_namespace + chunk_id + content_hash` and optionally a qualified name. Graph IDs map to a source-backed chunk and allowed edge. The server checks an explicit excerpt occurs in an authorized cited item if an excerpt is supplied. It must reject invented IDs, cross-namespace identities, fabricated ranges, and citations to excluded or redacted text. Claim/evidence semantic agreement is not fully machine-verifiable; this remains a documented limitation.

**Line numbers:** `HybridHit` does not contain verified start/end line numbers. V1 citations show relative path, qualified name (when present), and chunk identity. Precise line highlighting is deferred until a source-revision-bound location mapping is explicitly added and tested; do not derive ranges by reading the current checkout.

## 6. Answer schema

Suggested core shapes (final names can change before implementation):

- `EvidenceItem`: label, kind, namespace, source identity, safe excerpt, optional relationship metadata, verified-source flag, provenance limitations.
- `EvidenceBundle`: query, namespace, ranked source items, optional graph/history/runtime items, omissions, completeness, immutable registry.
- `Claim`: text, category (`indexed_fact`, `graph_relation`, `reported_history`, `reported_runtime`, `hypothesis`), citation labels.
- `Hypothesis`: summary, supporting IDs, contradicting IDs, missing observations, status (`possible` / `insufficient_evidence`).
- `ReasonedAnswer`: overview, claims, hypotheses, limitations, citations resolved by server, status (`answered` / `insufficient_evidence` / `invalid_model_output`).

No unsourced numerical confidence score. Use qualitative evidence sufficiency with its observable basis. Consider calibrated numeric confidence only after measurable evaluation.

## 7. Model and prompt security

- Keep `TRACERAG_GEMINI_API_KEY` server-side; never send it in frontend state, API responses, URLs, or logs. Exact model name is configuration, not hardcoded business logic.
- Use the supported Google Gen AI Python SDK *only* in `backend/causal_reasoner/providers/gemini.py`; make the adapter substitutable and test the request/response boundary offline. Use structured JSON output where the selected model supports it, followed by independent application validation.
- Treat repository text, history metadata, supplied capture details, and model output as untrusted. Separate instructions from quoted evidence; do not obey instructions embedded in a source chunk. This reduces but cannot eliminate prompt-injection risk.
- Establish bounded request size, model output size, timeout, retry limits, rate limits, and per-repository authorization before any public deployment. Do not retry malformed/unsafe model output forever.
- Avoid raw prompt retention. Expose safe error codes, not provider responses, keys, raw logs, or arbitrary file paths.

## 8. API and UI scope

**Initial backend API:** `POST /api/v1/answer` accepts authorized repository ID/namespace, query, and bounded retrieval options; all evidence inventories and provider credentials are resolved server-side. Return a strict typed answer envelope with citation targets, warnings, and errors. `GET /api/v1/health` for basic local readiness. No arbitrary server path, model-name override, or raw inventory upload in this endpoint.

**UI:** React + TypeScript after backend and offline tests. Start with repository selection, question input, cited answer, evidence panel, hypothesis/limitations tabs. Render all source code as inert escaped text, with syntax highlighting. Optional React Flow graph and Monaco viewer later, clearly labelling static code relationships versus hypothesized causal paths. API and UI tests must cover HTML injection, citation clicks, empty evidence, loading and error states, and long snippets. Use explicit local CORS origins; add real authentication/authorization before deploying publicly.

**Repository onboarding:** Public GitHub URL ingestion through Modules 1–5 is a separate, explicitly bounded service/API increment. V1 `POST /api/v1/answer` does not silently clone/index arbitrary URLs.

## 9. Failure behavior and limits

- Absent indexed namespace -> typed "not indexed" response, not invented answer.
- No relevant source evidence -> insufficient-evidence response, with no Gemini call by default.
- Stale graph provenance -> fail closed exactly as Module 9 does; do not downgrade graph trust to accept it silently.
- Invalid Git/runtime inventories -> preserve Module 9 validation failure; do not send unsanitized evidence to Gemini.
- Provider unavailable/rate-limited -> bounded sanitized error, no hallucinated fallback.
- Malformed model JSON/citation ID -> reject or return safe insufficient-evidence result; never present fabricated references.
- Empty/partial history/runtime -> disclose incompleteness; do not use as negative proof.

Configuration limits must be explicit and independently testable. Proposed initial defaults (to validate in implementation): <=8 primary source hits; <=8 graph contexts; <=5 historical and <=5 runtime clues per retrieved path; <=4,096 query chars (respect Module 9); pre-model context capped by UTF-8 bytes and provider-specific token budget; maximum 6 claims and 3 hypotheses in first response. These are policy choices, not current Module 9 guarantees.

## 10. Incremental delivery plan and quality gates

| Increment | Deliverable | Test requirements |
|---|---|---|
| 0 | Design proposal only | Design/scope review; no production changes |
| 1 | Pure evidence bundle, citation registry, deterministic limits | Offline unit tests for source identity, omission, trust typing, namespace, ordering |
| 2 | Redaction + citation validation + strict answer model | Secret patterns, injected labels, mismatched identities, invalid excerpts, insufficient evidence |
| 3 | Provider-neutral reasoning contract and Gemini adapter | Fake SDK/response, schema parsing, error mapping, offline default; separate opt-in live test |
| 4 | Reasoning orchestration, counter-evidence, abstention | Full Module 9 fake+real-Chroma integration; no causal promotion, no additional snapshot |
| 5 | FastAPI local API with dependencies and authorization seams | Contract, failure, CORS, size-budget, security integration tests |
| 6 | React/TypeScript UI and end-to-end local demo | Citation navigation, no secret leakage, static XSS, offline smoke, accessibility basics |
| 7 | Complete regression gate, design/code review, local commit | Module 10 suite, full offline suite, optional isolated provider live test, staged diff review |

No increment changes `main` or is committed/merged/pushed until its verification and the user's staged review workflow are complete. Preserve existing untracked artifacts, auxiliary worktrees, Module 9 contracts, `pytest.ini` marker behavior, and all original compatibility tests.

## 11. Open design decisions (before production implementation)

1. Decide how the application will hold and authorize the selected indexed repository's graph, Git and runtime inventories. These are currently caller-supplied to Module 9, not automatically fetched by a public repository session layer.
2. Choose and pin a compatible *generation* Gemini model/SDK combination in the development environment, independently of the embedding model's strict dimensionality and input caps.
3. Define exactly which source text may leave the local machine for Gemini generation; obtain a clear user-facing disclosure before any upload of private repository content.
4. Decide whether a first local proof-of-concept uses an in-memory repository session or persists a server-side registry. Do not infer or construct a revision mapping from ambiguous paths.

**Review outcome for this proposal:** No code has been implemented by this document. Review and validate these choices before starting Increment 1.
