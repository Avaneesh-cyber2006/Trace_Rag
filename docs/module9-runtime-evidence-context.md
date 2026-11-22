# Module 9 — Increment 9: Bounded Runtime Evidence Context

## Boundary

This increment attaches Module 8's **user-supplied** log/stack-derived file
clues to already pinned Module 9 results. It does **not** execute code, read
runtime artifacts again, obtain timestamps from the environment, enrich the
query, adjust RRF scores, or infer causes. Module 8's graph binding is
`NAMESPACE_MATCHED_UNVERSIONED`, not an authenticated binding to the immutable
Module 5 published source revision. Even a matching `declared_revision` is a
user-supplied claim, not cryptographic proof. `source_revision_verified` and
`causal_relationship_verified` therefore remain **False**.

## API

`HybridRetriever.search_with_runtime(namespace, query, top_k, runtime_inventory,
*, runtime_top_k=5, relevance_policy=None, graph_revision=None,
graph_top_k=20, git_history=None, history_top_k=5)` returns
`RuntimeRetrievalResult(hits, graph_context, runtime, history)`.

- The hybrid primary hits and optional graph context are produced inside a
  **single Module 5 active generation lease** through the existing verified
  orchestrator. Graph expansion still requires Module 9's source-revision
  validation; runtime frames cannot satisfy or override it.
- The Module 8 inventory is strictly validated and capped **before** obtaining
  that lease or calling the embedding provider. Optional Module 7 history is
  likewise preflighted. No file, network, or vector store lookups are made
  during runtime annotation after lease release.
- Context is matched by the exact, case-sensitive normalized `relative_path`
  from a Module 8 stack frame against each selected hit's path. Ambiguous and
  unmatched path references are excluded. Accepted path-match bases are
  retained, allowing consumers to explain the strength/limits of matching.
  A path match never means that the runtime stack executed this source version.
- Repeated hit paths are deduplicated in ranked first-appearance order.
  Per-path references preserve Module 8's canonical artifact/event/frame order
  and are capped to 1–20 (default 5). Total matching frame counts are preserved
  for transparency even when only a subset is exposed.
- The returned `RuntimeFrameClue` includes only opaque content-addressed IDs,
  event kind, frame language, reported line/column, path-match basis, and
  association. Raw log messages, exception details, free-text frame functions,
  tags, trace identifiers, or artifact bytes are **not copied** to Module 9.
  The original Module 8 inventory is privacy-sensitive and must be handled
  independently of this sanitized context.
- Completeness, truncation, graph-binding, and **declared** revision/time
  metadata are preserved. Missing runtime path clues are not proof that code
  was never executed. Git history stays a separate unverified section.

## Limits

Inputs must be an exact `RuntimeEvidenceInventory` for the query namespace,
with no more than 64 artifacts, 50,000 events, 200,000 frames, and 10,000
issues. The full immutable inventory is revalidated before use, including
cross-record/index invariants. Output is bounded by retrieval `top_k <= 100`
and `runtime_top_k <= 20`. Invalid evidence fails closed with content-free
Module 9 exceptions.

## Verification

`tests/test_runtime_context.py`, `tests/test_runtime_orchestrator.py`, and
`tests/test_runtime_chroma.py` test namespace isolation, privacy, bounding,
exact path matching, forged evidence rejection, optional relevance/graph/Git
integration, and the authoritative real Chroma snapshot. Chroma execution is
required in the Windows environment before accepting this increment.

No commit, merge, or push is included. The existing uncommitted Module 9
worktree and `main` are preserved.
