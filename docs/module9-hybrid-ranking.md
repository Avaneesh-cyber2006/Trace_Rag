# Module 9 — Hybrid rank fusion (increment 2)

Status: **development only**. This is a pure ranking component, not a live
hybrid-search orchestrator or a completed Module 9 implementation.

## API and ranking

The incremental component adds `HybridRanker`, `SemanticCandidate`, and
`HybridHit`. It uses reciprocal rank fusion (RRF), with configurable positive
rank constant `k=60` by default:

`RRF(chunk) = sum(1 / (k + rank))` across the lexical and semantic result lists.

RRF combines **rank positions**, never raw BM25 and vector similarity scores.
The resulting number is *not* a probability, a normalized relevance judgment,
or a measure of retrieval completeness. Each source contributes at most once.
The index ranks up to 100 lexical hits and accepts up to 100 semantic candidates;
`top_k` must be a strict integer in `[1,100]`.

```python
from backend.retrieval import HybridRanker, SemanticCandidate

# index is a LexicalIndex built from one already-validated published generation.
# semantic_hits is the ordered tuple returned by Module 5 SemanticSearcher.search.
# The caller has pinned and validated that both streams use that generation.
candidates = tuple(
    SemanticCandidate(index.repository_namespace, hit.chunk_id, hit.content_hash)
    for hit in semantic_hits
)
hits = HybridRanker(index).search("database error", candidates, top_k=10)
```

The result carries source metadata and source content **from the lexical
snapshot**, one-based hybrid rank, RRF score, and optional one-based source
ranks. It does not expose or reflect unvalidated semantic-result source text.

## Evidence and security boundaries

- Both retrieval streams must come from the **same actively pinned, validated
  published snapshot**. This pure function cannot acquire or hold Module 5's
  snapshot lease on its own; the future orchestrator is responsible for that.
- Each semantic candidate carries repository namespace, chunk ID, and content
  hash. The function rejects a foreign namespace, unknown or duplicate chunk
  identity, or a content-hash mismatch with the lexical source catalogue.
  Even identity/hash agreement is **not** proof that two independent retrieval
  calls used the same generation; snapshot pinning remains mandatory.
- Semantic candidates must be derived, in their normalized ranking order, from
  Module 5's validated semantic-search results, not caller-supplied scores.
  This rank-only component does not call the embedding provider or ChromaDB.
- Fused content/metadata comes only from the trusted lexical index. Its inputs
  must already have been validated for source authenticity and repository
  ownership; the lexical foundation itself does not validate Chroma storage.
- Malformed input is rejected before ranking. Public exceptions and object
  `repr`s avoid exposing source content. No filesystem, network or database I/O
  occurs in the fusion component.
- In the event of an empty semantic branch, lexical results are returned.
  Semantic-only hits may appear if they exist in the trusted lexical catalogue.
  Equal scores sort by casefolded relative path, original path, then chunk ID.

## Deferred integration

Still required for Module 9: a single pinned snapshot builder, synchronized
lexical/semantic retrieval, graph expansion, relevance gating, reranking,
resource budgeting across these stages, and full cross-module tests. This
increment is not sufficient to merge Module 9 into `main`.

## Verification

```text
python -m pytest tests/test_lexical_retrieval.py tests/test_hybrid_retrieval.py -q -p no:cacheprovider
```
