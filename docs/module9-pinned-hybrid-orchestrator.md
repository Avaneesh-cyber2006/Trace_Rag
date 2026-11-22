# Module 9 — Increment 3: pinned hybrid retrieval

This increment connects the existing deterministic lexical index and reciprocal-rank
fusion to Module 5's published semantic index. `HybridRetriever.search(namespace,
query, top_k)` uses **one** `VectorStore.acquire_active(namespace)` lease for the
entire request:

1. Validate bounded user input before accessing the provider or vector store.
2. Resolve and validate the active Module 5 generation and embedding identity.
3. Read its **authoritative stored manifest**, check its record identities and
   content hashes, and build the bounded immutable BM25 index in memory.
4. Embed the query using Module 5's existing typed-retry and vector validation.
5. Search **the same pinned snapshot** for at most 100 semantic candidates;
   apply Module 5's existing result normalization and source-integrity checks.
6. Reject semantic results whose ID, hash, stored content, or source metadata
   differ from the pinned manifest. Fuse BM25 and semantic **ranks** using RRF.
7. Release the active-generation lease on success **or any failure**.

This design does **not** read mutable working-tree files, query a second active
pointer, compare incomparable BM25/vector raw scores, cache a potentially stale
lexical index, or suppress Chroma/provider integrity errors. The source content
returned to callers is always taken from the validated manifest. A never-indexed
repository is an explicit error; an indexed empty repository yields zero hits.

## V1 resource limits

- Query: maximum 4,096 characters, including Unicode.
- Results: 1–100; semantic candidate pool is fixed at 100.
- Manifest: maximum 8,192 chunks, identical to the lexical-index limit.
- Per-chunk content: maximum 16,384 characters.
- Total lexical corpus content: maximum 16,777,216 characters.
- RRF constant `k`: 1–10,000, default 60.

These are explicit V1 limits, not an optimization claim. Building an index for
each search costs O(corpus text) time and memory; later caching must include a
verified generation key and preserve the same pin/invalidation guarantees.

## Verification

`tests/test_hybrid_orchestrator.py` covers protocol collaborators, lease
lifetime, snapshot consistency, source/hash mismatches, malformed manifests,
empty/absent repositories, embeddings, deterministic ordering and bounds.
`tests/test_hybrid_orchestrator_chroma.py` exercises fresh real Chroma stores
without network access or live Gemini calls on the project's pinned runtime.

Graph expansion, relevance policies, reranking, and a persistence-backed BM25
index are **not** included in this increment.
