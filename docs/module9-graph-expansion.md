# Module 9 — Increment 4: bounded one-hop Code Graph expansion

`GraphExpander` is a **pure projection** from a Module 6 `CodeGraphInventory`
and a Module 9 immutable `LexicalIndex` onto optional source context. It does
not alter the fused `HybridHit` ranks or scores. `GraphContext.document` is the
very same validated `LexicalDocument` supplied by the Module 5-pinned lexical
index; the graph never supplies or reconstitutes source content.

## Contract

- Caller constructs `LexicalIndex` from an already validated, pinned Module 5
  manifest (Increment 3), and supplies a Module 6 graph from **the same source
  revision**. Both must use the exact same repository namespace.
- Module 6 currently lacks a source-revision/generation binding in its public
  `CodeGraphInventory`. Matching namespace, path, symbol name and kind **does
  not prove graph freshness**. Consequently the expander is **not yet** called
  automatically from `HybridRetriever`; that would require a verified graph
  revision/provenance contract. A structurally valid but stale graph must not
  be passed to this API.
- Seed hits must be consecutive, distinct, ranked `HybridHit` values matching
  the lexical index's chunk ID, content hash, relative path, language, kind,
  symbol identities and full stored content.
- Graph symbol nodes are mapped only by exact path, language, qualified name
  and symbol kind. Ambiguous graph symbol keys are skipped; symbol/fragment
  chunks are eligible. Unresolved references, file-level chunk matches, and
  `DEFINES`/`IMPORTS` edges are not extrapolated onto arbitrary chunks.
- Only resolved `CALLS`, `CONTAINS`, `INHERITS`, and `IMPLEMENTS` edges are
  traversed. The caller selects incoming, outgoing or both directions, with
  *one hop* and no recursion. Each output includes the original seed chunk ID,
  edge ID, relation kind, direction, deterministic context rank, and an
  authoritative immutable lexical document.
- Seed chunks are excluded from context. Each neighbor chunk appears at most
  once, with deterministic order by seed RRF rank, relation kind, direction,
  graph endpoint/edge ID, then chunk ID. Input tuple order and graph builder
  iteration order cannot change the output.

## Bounds and failure semantics

- Graph inventory: at most **65,536 nodes**, **262,144 edges**, **2,048 resolved
  edges per node** in either direction. These are explicit memory/CPU bounds.
- At most **100 seeds**, **100 context results**, **32 emitted contexts per
  seed** and **4,096 examined candidate documents per seed**.
- Invalid graph/seed evidence raises `InvalidHybridEvidence`; invalid request
  limits or direction raises `InvalidRetrievalQuery` with content-free errors.
- No filesystem, network, database, working-tree or embedding access; no
  mutable caches; no suppression of published-manifest validation.

## Verification and subsequent work

`tests/test_graph_expansion.py` tests one-hop directionality, each supported and
excluded edge type, authoritative text provenance, permutation invariance,
ambiguity suppression, missing chunks, exact symbol matching, malformed graph
nodes/edges, duplicate evidence, query limits, and immutability.

**Not implemented here:** binding a Module 6 graph to the same verified source
revision as a pinned Module 5 manifest; automatically invoking expansion under
the `HybridRetriever` lease; relevance thresholds; reranking. A green unit
suite for this increment is not sufficient evidence to authorize graph
relationships from an unbound inventory.
