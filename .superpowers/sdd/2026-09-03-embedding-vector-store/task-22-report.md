# Task 22 Report: Search Result Validation and Deterministic Ordering

## Outcome

- Added tuple-wide, fail-closed validation for store search evidence.
- Rejects excessive counts, duplicate chunk IDs, namespace mismatches, malformed hashes/metadata/content, invalid paths, and non-finite or out-of-range scores as `VectorStoreCorruptionError`.
- Constructs only public `VectorSearchResult` values after the complete tuple validates.
- Preserves source content exactly and exposes no store namespace, vector, distance, generation, collection, locator, or token state.
- Applies the exact deterministic key `(-score, relative_path.casefold(), relative_path, chunk_id)`.
- Connected `SemanticSearcher.search` to return normalized evidence instead of discarding store results.

## TDD Evidence

- RED selector: `26 failed, 61 deselected`; failures were caused by the missing normalizer and the searcher returning `()`.
- A focused overflow RED then exposed one malformed huge-integer score escaping as `OverflowError`; the validator was tightened to map it to corruption.
- Final GREEN selector: `27 passed, 61 deselected`.
- Full semantic-search suite: `88 passed`.
- Adjacent semantic-search, validation, public-model, vector-store-contract, and Chroma-store suites: `350 passed`.

## Review Fix Evidence

- RED hostile-subclass selector: `13 failed, 88 deselected`; ten store-provided string subclasses were accepted, and hostile namespace equality, chunk-ID hashing, and path ordering each escaped as `RuntimeError`.
- Exact primitive-type validation now rejects every string field before equality, hashing, public-model construction, or sorting.
- Focused hostile-subclass GREEN selector: `13 passed, 88 deselected`.
- Fresh full semantic-search suite: `101 passed`.
- Fresh combined semantic-search, validation, public-model, vector-store-contract, and Chroma-store suites: `363 passed`.

## Second Review Fix Evidence

- RED request-subclass selector: `2 failed, 101 deselected`; a hostile query leaked `RuntimeError` from its overridden `isspace`, while a hostile namespace was accepted and reached the store instead of raising `InvalidSearchRequest`.
- Request validation now requires exact `str` instances before string truthiness, method calls, length checks, dependency calls, or later comparisons.
- Existing exact-`int` guards continue to reject booleans for `top_k`, `max_query_chars`, and `max_top_k`; valid boundary integers remain covered.
- Focused request-subclass GREEN selector: `2 passed, 101 deselected`.
- Fresh full semantic-search suite: `103 passed`.
- Fresh combined semantic-search, validation, public-model, vector-store-contract, and Chroma-store suites: `365 passed`.

## Self-review

- Confirmed the entire tuple is validated before any public result is constructed.
- Confirmed no invalid record is filtered or shortened into apparent success.
- Confirmed equality uses the complete requested namespace and uniqueness uses exact chunk IDs.
- Confirmed fixed corruption errors do not interpolate store-controlled values.
- Confirmed ordinary exact strings remain accepted and query text remains unchanged through validation, embedding, and search orchestration.
- Confirmed `git diff --check` reports no whitespace errors.
- Task 23 was not started.
