# Module 9 — Increment 7: opt-in relevance filtering and deterministic reranking

The original `HybridRetriever.search()` and `search_with_graph()` retain their
exact Increment 6 contract. Relevance filtering is explicitly opt-in; no
existing application caller silently loses semantic-only hits.

## API

```python
from backend.retrieval import HybridRetriever, RelevancePolicy

policy = RelevancePolicy(
    min_query_matches=1,
    require_dual_channel=False,
    rerank_by_query_coverage=True,
)
selected = retriever.search_relevant(namespace, "payment failure", 10, policy)
# Optional graph-bound equivalent, with the same authoritative snapshot lease:
expanded = retriever.search_with_graph_relevant(
    namespace, "payment failure", 10, trusted_graph_revision, policy,
    graph_top_k=20,
)
```

The selected hits are derived from a **100-candidate RRF pool**, not just the
requested `top_k`. The full Module 5 manifest and all Module 5 semantic hits
are validated **before** relevance filtering. No candidate is allowed to bypass
repository ownership, content hash checks, source-revision binding, or graph
provenance by being filtered out. Graph expansion uses only the final selected
seeds while the original pinned generation lease is still active.

## Explicit heuristic, not a trained reranker

- Query matching is based on distinct NFKC/casefolded lexical tokens, consistent
  with the existing Module 9 lexical tokenizer. Camel-case identifiers are
  split; the source content, relative path, and symbol metadata contribute
  candidate tokens. Only verified Module 5 evidence is used.
- `min_query_matches` is a strict integer from 0 to 64 (default 1). A candidate
  must match that many **distinct** query terms. Setting zero disables the
  lexical gate; a semantic-only paraphrase may otherwise be excluded. This is
  an expected recall tradeoff, not proof of semantic irrelevance.
- `require_dual_channel=True` additionally requires both a lexical and a
  semantic RRF rank. Default false preserves single-channel candidates.
- With `rerank_by_query_coverage=True` (default), surviving hits are ordered
  by descending distinct matched query terms, preserving original RRF order
  on equal coverage. With false, surviving hits preserve the RRF order.
- Returned `rank` is re-numbered contiguously from 1. The returned `score`
  remains the *original RRF score*, not a calibrated relevance probability or
  the new ranking score. Original `lexical_rank` and `semantic_rank` are kept.
- Query validation, result count, output size and manifest bounds remain
  unchanged. Searchable metadata and source text per candidate have an explicit
  32,768-character aggregate cap before tokenization; oversized evidence is
  rejected. All policy configuration uses strict non-coercive validation.

This deterministic gate is deliberately not called a cross-encoder or LLM
reranker. A later increment may add a vetted, opt-in semantic reranker with
explicit timeouts, resource limits, privacy policy, and source-bound input
checks; this increment does not send source code to any additional provider.

## Verification

- `tests/test_relevance_gate.py`: filtering, deterministic reranking, matched
  terms, malformed policies, forged hits, bounds and immutable input behavior.
- `tests/test_relevance_orchestrator.py`: one lease, pool before top-k, legacy
  behavior, source corruption, and graph provenance with filtered seeds.
- `tests/test_relevance_chroma.py`: offline real-Chroma relevance integration.

The full selected Module 9 regression suite must pass on the Windows pinned
runtime before Increment 7 is accepted. Remain on the isolated worktree; do
not merge or commit Module 9 yet.
