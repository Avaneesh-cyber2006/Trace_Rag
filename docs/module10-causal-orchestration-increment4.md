# Module 10 — Increment 4: One-Shot Causal Orchestration

## Intended boundary

The `answer_question(...)` service coordinates **one** Module 9 public retrieval entry point, an immutable local `EvidenceBundle`, its independently checked redacted projection, one configured `ReasoningProvider.generate` call, and a final server-owned `validate_answer`. It returns a `CausalResult` with only the validated answer, cited redacted evidence, and deterministic caveats. It does not obtain a second Chroma snapshot, access a working checkout, run code, or create a model client. No changes to Modules 1–9.

The selected retrieval path is, in precedence order, `search_with_runtime` (which can also attach history/graph), `search_with_history`, `search_with_graph_relevant`, `search_with_graph`, `search_relevant`, or `search`. The graph path retains Module 9's graph-revision checks; failures from Module 9 propagate rather than falling back to weaker retrieval. Git and runtime records retain *unverified* trust types. Caller-supplied inventories must come exclusively from a trusted **server-side repository session**, not browser input.

## Authorization and safe failure

The controller requires `external_processing_approved=True` **before retrieval**; it is false by default. This flag is **not** an authentication system or a repository-consent UI. The future API must check the selected repository and actual owner permission before setting it. Gemini additionally requires a separately enabled `GeminiSettings` object. The current offline tests use fake providers; they make no external requests.

Invalid queries (including recognized credentials) and unsupported options fail before retrieval. If redaction admits no indexed source, return a deterministic `insufficient_evidence` answer with **zero** provider calls. Never use history or runtime-only observations as a substitute for indexed source material. Provider errors are converted to a sanitized `CausalProviderError`, with no automatic retry; malformed models and invalid citations become `CausalDraftError`. Provider drafts are converted into strict JSON and reparsed before the citation registry validates labels, kinds and excerpts. A valid citation is **not** semantic proof of a claim, especially causal claims; the UI must display limitations and hypotheses explicitly.

## Returned data

- `CausalResult.answer`: strict `ReasonedAnswer` which has passed citation validation, or deterministic abstention.
- `CausalResult.citations`: redacted admitted evidence referenced in the answer; no raw index paths, hashes or local evidence bundle.
- `CausalResult.warnings`: safe system-generated warnings for incomplete Git/runtime evidence or omitted/redacted items, independent of the model's own limitations.

The redaction algorithm remains heuristic. Even redacted result data may be sensitive under a particular organization's policies: authorization, response visibility checks, content classification, CORS/auth/rate limiting and privacy disclosure belong to Increment 5; do not expose `CausalResult` through a public API yet.

## Verification

Run offline Module 10 test files `test_module10_evidence.py`, `test_module10_redaction.py`, `test_module10_citations.py`, `test_module10_gemini_adapter.py`, `test_module10_causal.py`, and `test_module10_causal_chroma.py`. The Chroma file requires the pinned chromadb package and relies on existing Module 9 real-Chroma fixtures. Run the prior Module 9 integration and Module 5 contract tests as regression gates. Never run a live Gemini request during CI or automatically transmit private code.

Keep all Increment 0–4 changes **uncommitted** until the complete Module 10 review and full offline regression gate.
