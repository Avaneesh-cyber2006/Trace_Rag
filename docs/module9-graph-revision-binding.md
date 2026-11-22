# Module 9 — Increment 5: explicit source-revision-bound graph expansion

`build_graph_revision(file_inventory, parse_inventory, chunk_inventory)` constructs
an immutable `GraphRevision` directly from the validated Module 2/3/4 inputs.
`HybridRetriever.search_with_graph(namespace, query, top_k, revision, graph_top_k=20)`
then compares that evidence with **every** record of the pinned, published
Module 5 lexical manifest before any query embedding or graph expansion.

## Why the binding is intentionally strict

Module 6's `CodeGraphInventory` has no revision token. Module 5's stored
`StoredRecord` does **not** carry original-file SHA-256 or byte positions.
Matching namespace, relative path, symbol name, or even a subset of chunk
content hashes cannot independently establish source freshness.

For that reason, the binding permits graph expansion **only when the entire
original source file is provably represented by chunk content**. For each
source file, it checks:

1. The scanner and parser inventories pass Module 6's input validation and
   share namespace and repository path with the Module 4 inventory.
2. Parser and chunker report exactly the same file set, language, successful
   or partial parse state, and nonempty SHA-256 of original source bytes.
3. Chunk locations form a **gapless, non-overlapping byte partition**, starting
   at zero and ending at the scanner's original file size. Individual encoded
   chunk lengths and hashes must agree with their locations and content.
4. The SHA-256 obtained by hashing **all concatenated published chunk bytes**
   equals the original-file SHA-256 recorded independently by Module 3 and 4.
5. Module 6's graph is **rebuilt from these parser inputs**; callers cannot
   pass in a separately constructed stale `CodeGraphInventory` to the factory.
6. The entire set of chunk IDs, hashes, relative paths, languages, chunk kinds,
   and symbol identities is fingerprinted. While the active Chroma generation
   remains leased, `GraphRevision.validate()` recomputes that fingerprint from
   the *fully validated* Module 5 lexical manifest and checks stored source
   text against the corresponding content SHA-256.

All violations raise sanitized `InvalidHybridEvidence` rather than falling
back to unchecked graph relationships. Search with no graph remains available
via `HybridRetriever.search()`.

**Coverage limitation:** Module 4 intentionally omits whitespace-only gaps.
Therefore many real-world files will **not** satisfy the strict gapless-source
proof yet. Such files must not be graph-expanded through this API. A future
indexing/schema increment can persist a verified per-file source digest in
Module 5 to support all normally chunked files. Never fill omitted gaps from
the current working tree at retrieval time, since it may no longer match the
published index.

## Result and concurrency semantics

`search_with_graph()` returns `GraphRetrievalResult(hits, context)`: primary
hybrid hits retain their RRF scores and order, and bounded one-hop graph
context contains only authoritative Module 5 chunk text. The same Module 5
lease spans manifest read, digest validation, query embedding, semantic
search, RRF fusion, and graph expansion; it is released on errors.

The binding certifies *source-content equivalence* under trusted in-process
scanner/parser/chunker inputs, **not cryptographic authentication of callers**.
Untrusted callers can forge Python dataclasses; production code must use
captured same-revision inventories at indexing time, not caller-supplied
claims. The factory reads no filesystem or remote data, and neither does the
retriever's graph path.

No automatic graph registry, persistence, working-tree reread, relevance
thresholds, or reranker is introduced in this increment. As long as genuine
matching original-file inventories cannot be supplied, callers should use
`HybridRetriever.search()` without graph context.

## Tests

`tests/test_graph_provenance.py` checks genuine Module 6 builder execution,
validated same-revision source bytes, exact complete chunk identity matching,
permutations, stale/foreign/modified inputs, forged SHA claims, missing byte
coverage, protected source representations, lease release, early rejection
before embedding, and an actual resolved Python call contributing safe context
through `search_with_graph()`.
