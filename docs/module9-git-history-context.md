# Module 9 — Increment 8: opt-in bounded Git history context

The `HybridRetriever.search_with_history(...)` method decorates **pinned,
validated** Module 9 results with *separate* Module 7 Git change hints. It does
not re-rank, filter, expand, or otherwise alter the proven code result set.
The original `search`, `search_relevant`, `search_with_graph`, and
`search_with_graph_relevant` contracts are unchanged.

## API

```python
from backend.retrieval import HybridRetriever, RelevancePolicy

result = retriever.search_with_history(
    namespace, "invoice timeout", 5, git_history_inventory,
    history_top_k=5,  # 1–20 per unique hit path
    relevance_policy=RelevancePolicy(min_query_matches=1),  # optional
    graph_revision=trusted_graph_revision,  # optional
    graph_top_k=20,
)

result.hits            # unchanged source-verified HybridHit tuples
result.graph_context   # optional graph context, still revision-gated
result.history.paths   # Git change hints, distinct exact hit paths
assert result.history.source_revision_verified is False
```

## Explicit evidence trust boundary

- Git history is accepted only with a **non-null exact matching repository
  namespace**. Only a Module 7 `GitHistoryInventory` is accepted; arbitrary
  filesystem paths or user-provided raw Git text cannot be substituted.
- Source paths must match the selected pinned hits' `relative_path` **exactly**
  (case-sensitive). History is joined by each change's `new_path`; old rename
  paths, basename matches, suffixes, and inferred co-change links are excluded.
- Commit metadata includes only a commit ID, stable change ID, change type,
  original path when present, and committer timestamp. Commit messages,
  authors and emails are intentionally omitted. Summaries are deterministic,
  newest first with stable tie-breaking, with a strict per-path bound of 1–20.
- Source results and optional graph expansion still come from one Module 5
  pinned active-generation lease. Historical annotation is computed after the
  lease from immutable selected hit records and the captured Git inventory;
  it does not read files, query Git, or access the vector store.
- **The Module 7 inventory has no cryptographic binding to the Module 5 source
  snapshot.** A matching path or repository namespace does *not* prove that a
  Git commit changed the indexed version. `source_revision_verified=False` is
  always set; history records are *historical leads, not verified causes*.
  `scope_revision`, `scope_path_filter`, `history_is_complete`, and
  `incomplete_reasons` are returned, so a caller can display the evidence
  limitations rather than presenting absence as proof of no changes.
- The feature is opt-in and rejects malformed history, wrong-namespace history,
  oversized history (10,000 commits / 100,000 changes), bogus results, and
  unbounded requests. The existing full manifest and semantic-source checks
  still run before results are released. No third-party service is contacted.

## Testing

`tests/test_history_context.py` checks path correctness, rename semantics,
source trust labels, ordering, bounds, malformed inventories and missing data.
`tests/test_history_orchestrator.py` tests pinned snapshot behavior, opt-in
relevance, graph revision checks, malformed input and empty snapshots.
`tests/test_history_chroma.py` runs an offline, real-Chroma integration test.

This increment does **not** infer causal explanations, verify Git blobs against
indexed source files, or supply runtime diagnostics. These require separate
source-revision and trace-binding work before Module 10's causal reasoner.
