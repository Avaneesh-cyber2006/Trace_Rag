# Module 5 — Embedding & Vector Store Design

## 1. Purpose

Module 5 converts a Module 4 `CodeChunkInventory` into a persistent, repository-scoped semantic vector index and exposes low-level semantic top-k search over that index. It constructs deterministic embedding documents, obtains vectors through a provider-independent boundary, validates all external and stored data, synchronizes incrementally, and publishes a complete new logical repository state without exposing a partially updated index.

The selected architecture is provider abstractions plus versioned fail-safe repository index publication. Gemini and ChromaDB are first edge adapters, not core concepts. This document is design only: it adds no production package, tests, dependencies, or implementation plan.

## 2. Goals

Module 5 must:

- consume the immutable Module 4 inventory without changing chunk identity, content, or provenance;
- deterministically render one embedding document per chunk;
- keep document and query embedding operations distinct;
- validate every provider response before it can reach storage;
- maintain one logical semantic index per exact repository namespace;
- reuse compatible unchanged vectors and embed only new or updated chunks;
- support a valid, persistent empty index;
- keep the previous active index authoritative through every pre-publication failure;
- expose repository-scoped, bounded, deterministic top-k semantic search returning exact source evidence; and
- keep provider configuration, credentials, storage mechanics, and raw distance semantics behind replaceable adapters.

## 3. Non-goals

Module 5 does not parse or chunk source; construct dependency, call, or evidence graphs; inspect Git history, runtime data, logs, or stack traces; execute repository code, tests, or builds; provide BM25, lexical or hybrid retrieval; expand graphs; rerank across sources; answer with an LLM; perform causal reasoning; or provide a frontend.

It also does not summarize chunks with an LLM, silently truncate oversized documents, infer active storage from timestamps, repair corrupt storage during search, manage distributed locks, or migrate incompatible storage automatically. Those responsibilities either belong to later modules or require a separately reviewed maintenance/migration design.

## 4. Position in TraceRAG Architecture

```text
Module 4: CodeChunkInventory
        |
        v
validate inventory and flatten chunks transiently
        |
        v
canonical embedding-document construction
        |
        v
EmbeddingProvider ---- first adapter: GeminiEmbeddingProvider
        |
        v
validate vectors and stable request/result association
        |
        v
VectorStore ---------- first adapter: ChromaVectorStore
        |
        v
complete private candidate -> validate -> atomic publication
        |
        v
active repository semantic index
        |
        v
embed_query -> nearest neighbors -> normalized VectorSearchResult tuple
```

Module 4 remains the source authority. The embedding document is derived retrieval input, and the stored `document` is exact `CodeChunk.content`. Module 5 never rereads the repository or reconstructs source evidence.

## 5. Existing Module 4 Contract

This design is based on `main` commit `69cb877fec05b5453c408fd71ce3aa1b683c98c7`.

`CodeChunkInventory` is frozen and slotted. It contains the nonempty canonical `repository_namespace`, repository path, file counters, `total_chunks`, and an ordered tuple of `ChunkedFile` values. A `ChunkedFile` supplies its POSIX `relative_path`, `ParsedLanguage`, status, and ordered chunks. Each `CodeChunk` supplies:

- a repository-scoped structural `chunk_id`;
- exact UTF-8 source `content`;
- SHA-256 `content_hash` of those exact bytes;
- `ChunkKind`;
- optional `SymbolKind`, `qualified_name`, and `parent_qualified_name`; and
- exact source location and other structural fields that Module 5 does not need for V1 embedding metadata.

Module 5 validates the inventory-level counters, tuple shapes, exact enum/model types, unique chunk IDs, lowercase SHA-256 fields, file/chunk ordering where required by the Module 4 contract, and `total_chunks == sum(len(file.chunks) for file in files)` before provider or store mutation. It uses file-level language and path with chunk-level metadata when flattening. `repository_path`, source locations, parameters, return types, modifiers, base types, implemented types, imports, parse status, file status, and source digest are not embedded in V1.

## 6. Inputs and Outputs

The primary synchronization API is conceptually:

```python
result = SemanticIndexer(provider, store, config).synchronize(inventory)
```

The search API is conceptually:

```python
results = SemanticSearcher(provider, store, config).search(
    repository_namespace,
    query_text,
    top_k,
)
```

Synchronization returns `IndexSyncResult`. Search returns `tuple[VectorSearchResult, ...]`. Public collections follow existing TraceRAG convention and are immutable tuples. Failures raise typed exceptions; neither API returns partial success.

An optional explicit administrative API may be exposed as:

```python
store.delete_repository_index(repository_namespace)
```

It validates and matches the complete namespace and affects only that logical repository index. It never deletes by substring, display name, or global wildcard and never touches source repository files.

## 7. Package and Component Architecture

The planned package boundary is `backend.embedding_vector_store` with focused components:

```text
backend/embedding_vector_store/
    __init__.py          public provider-independent API
    models.py            frozen/slotted public values
    exceptions.py        typed failure taxonomy
    documents.py         canonical embedding-document renderer
    validation.py        inventory, vector, request, and result validation
    providers/
        base.py          EmbeddingProvider protocol/ABC
        gemini.py        Gemini-only SDK adapter
    stores/
        base.py          VectorStore protocol/ABC and internal store values
        chroma.py        Chroma persistence/publication adapter
    synchronization.py   provider-independent diff and orchestration
    search.py            provider-independent query/search orchestration
    retry.py             bounded typed retry policy with injected sleeper
```

Exact filenames may be adjusted in the implementation plan to match dependency findings, but boundaries are normative. Core files import only provider-independent contracts and models. Only `providers/gemini.py` may import the Gemini SDK; only `stores/chroma.py` may import ChromaDB or manipulate collections, collection names, generation identifiers, raw distances, and Chroma metadata APIs.

## 8. Public Models

Public models are `@dataclass(frozen=True, slots=True)`. Enums derive from `str, Enum`. Constructors validate their own local invariants and reject `bool` where an integer is required.

```python
@dataclass(frozen=True, slots=True)
class EmbeddingModelIdentity:
    provider: str
    model: str
    dimensions: int
    compatibility_version: str


@dataclass(frozen=True, slots=True)
class EmbeddingVector:
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingDocument:
    chunk_id: str
    text: str


@dataclass(frozen=True, slots=True)
class VectorRecord:
    chunk_id: str
    content_hash: str
    relative_path: str
    language: str
    chunk_kind: str
    symbol_kind: str | None
    qualified_name: str | None
    parent_qualified_name: str | None
    content: str
    embedding: EmbeddingVector


class IndexSyncStatus(str, Enum):
    SUCCESS = "success"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class IndexSyncResult:
    repository_namespace: str
    status: IndexSyncStatus
    total_chunks: int
    reused_chunks: int
    embedded_chunks: int
    inserted_chunks: int
    updated_chunks: int
    deleted_chunks: int
    embedding_identity: EmbeddingModelIdentity
    document_version: str


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    chunk_id: str
    content_hash: str
    relative_path: str
    language: str
    chunk_kind: str
    symbol_kind: str | None
    qualified_name: str | None
    parent_qualified_name: str | None
    content: str
    score: float
```

`EmbeddingDocument.text` is transient provider input, not evidence and not stored as the vector-store document. A `VectorRecord` is provider-independent construction input to the store; implementations may use a separate private stored-record type when reading manifests. No public model includes a generation identifier, collection name, API key, retry setting, timestamp, raw provider response, or raw Chroma distance.

`IndexSyncResult` enforces nonnegative integer-but-not-boolean counters and:

```text
inserted_chunks + updated_chunks == embedded_chunks
reused_chunks + embedded_chunks == total_chunks
```

For `UNCHANGED`, `embedded_chunks`, `inserted_chunks`, `updated_chunks`, and `deleted_chunks` are zero, `reused_chunks == total_chunks`, and no write or publication occurred. For successful synchronization to an empty inventory, `total_chunks`, `reused_chunks`, and `embedded_chunks` are zero; `deleted_chunks` equals the previous active count, if any. A first publication of an empty repository is `SUCCESS`, not `UNCHANGED`.

## 9. EmbeddingProvider Contract

The provider-independent contract exposes:

```python
class EmbeddingProvider(Protocol):
    @property
    def identity(self) -> EmbeddingModelIdentity: ...

    @property
    def max_batch_size(self) -> int: ...

    def embed_documents(
        self, documents: tuple[EmbeddingDocument, ...]
    ) -> tuple[EmbeddingVector, ...]: ...

    def embed_query(self, query_text: str) -> EmbeddingVector: ...
```

`max_batch_size` is a validated positive provider/configuration capacity used by core batching; core does not contain Gemini batch limits. If an adapter has an additional request-size bound, it validates each complete document before sending and raises a typed size error. It never truncates.

The result at position `i` must correspond to request position `i`. The core retains `chunk_id` in each request wrapper, validates returned cardinality before zipping, then validates every vector. It does not associate responses through unordered maps or provider-generated IDs.

Document and query methods are deliberately separate because a provider can apply distinct retrieval-document and retrieval-query task semantics. Search must call only `embed_query`; synchronization must call only `embed_documents`.

## 10. GeminiEmbeddingProvider

`GeminiEmbeddingProvider` is the only component allowed to depend on the Gemini SDK/API. Its constructor receives externally supplied credentials or an already configured trusted client plus explicit non-secret model/configuration. It fails eagerly when credentials, identity, batch capacity, dimensions, or required configuration are missing or malformed.

The adapter maps SDK outcomes into the Module 5 typed provider hierarchy. Authentication, invalid configuration, unsupported model/request, document-too-large, and malformed-response failures are permanent. Explicit SDK rate-limit and transient/service failures are retryable. Classification uses documented SDK exception types and status information, never arbitrary substring matching.

The design intentionally does not name a Gemini model, SDK package, vector dimension, task option, request limit, or version pin. The implementation prerequisite gate must obtain those facts from then-current official documentation and prove them with the proposed exact dependency set.

## 11. Embedding Document Format

The fixed V1 version is:

```text
tracerag-embedding-document-v1
```

For each chunk, the metadata object contains exactly these keys in this exact order:

```text
chunk_kind
language
parent_qualified_name
qualified_name
relative_path
symbol_kind
```

Values use their stable string enum values; absent optional values are JSON `null`. Metadata is serialized as UTF-8 JSON with `ensure_ascii=False`, `separators=(",", ":")`, and the explicit insertion order above. The complete embedding text is:

```text
tracerag-embedding-document-v1
<canonical compact JSON>
---TRACERAG-SOURCE---
<exact CodeChunk.content>
```

The two line-feed separators shown are literal U+000A characters regardless of the source file's newline convention. The exact construction is:

```python
text = (
    "tracerag-embedding-document-v1\n"
    + canonical_metadata_json
    + "\n---TRACERAG-SOURCE---\n"
    + chunk.content
)
```

Only the framing uses fixed LF. The source suffix is appended unchanged: no stripping, trimming, newline normalization, tab replacement, reformatting, comment removal, Unicode normalization, case change, or summary generation is permitted. Tests independently serialize expected Unicode, LF, CRLF, tabs, quotes, backslashes, and JSON-null cases.

The document excludes repository namespace, chunk ID, content hash, absolute path, byte offsets, generation/storage identifiers, embedding identity, timestamps, and machine information.

## 12. Embedding Identity and Compatibility

`EmbeddingModelIdentity` identifies one compatible vector space through provider, model, positive dimension, and semantic `compatibility_version`. The last field versions all provider options that can change vector semantics but are not already captured by the other fields, including task/configuration behavior. All string fields are nonempty and normalized by explicit adapter configuration, not guessed by core.

Identity contains no credentials, account identifiers, timestamps, batch sizes, timeouts, retries, or other operational tuning. Compatibility in V1 is exact equality of the validated identity. No cross-model or cross-provider equivalence table exists.

Vector reuse requires all four conditions:

```text
same chunk_id
and same content_hash
and same embedding document version
and equal EmbeddingModelIdentity
```

A content hash change re-embeds that chunk. A document-version or embedding-identity change disables reuse for the entire repository and requires complete re-embedding. Search requires equality between the configured provider identity and the active index identity before embedding the query.

## 13. Embedding Validation

Provider responses are untrusted external data. Before any store call, provider-boundary validation requires:

- batch result count exactly equals requested document count;
- each result is present and is an `EmbeddingVector` or validated adapter value;
- the vector is nonempty;
- its length equals `EmbeddingModelIdentity.dimensions`;
- every element is numeric but not `bool`; and
- every value is finite, excluding NaN and positive/negative infinity.

The same validation applies to query vectors. Validation occurs for the complete batch before any record from that batch is accepted into candidate construction. Malformed data raises `EmbeddingInvalidResponseError`, aborts the candidate, and leaves the active index authoritative.

For a Chroma write, the adapter then performs a checked component-wise IEEE-754 binary32 projection of the already validated provider vector. A finite provider value whose binary32 projection is non-finite is rejected before mutation. The adapter does not clip or normalize vector values. ChromaDB 1.5.9 may return a component a few binary32 ULPs from even the submitted projection, so equality with the original Python-float tuple, or with a locally projected tuple, is not a persistence invariant. This derived numeric representation is distinct from exact source/content preservation.

## 14. VectorStore Contract

The provider-independent store contract deals in exact namespaces, active index snapshots, manifest records, complete candidates, and normalized search results. Conceptually:

```python
class VectorStore(Protocol):
    def inspect_active(self, repository_namespace: str) -> RepositoryIndexState: ...
    def read_manifest(self, snapshot: RepositoryIndexSnapshot) -> tuple[StoredRecord, ...]: ...
    def begin_candidate(
        self,
        repository_namespace: str,
        identity: EmbeddingModelIdentity,
        document_version: str,
        expected_chunk_count: int,
    ) -> CandidateIndex: ...
    def add_reused(self, candidate: CandidateIndex, records: tuple[StoredRecord, ...]) -> None: ...
    def add_embedded(self, candidate: CandidateIndex, records: tuple[VectorRecord, ...]) -> None: ...
    def validate_candidate(self, candidate: CandidateIndex) -> None: ...
    def publish(self, candidate: CandidateIndex) -> RepositoryIndexSnapshot: ...
    def abort(self, candidate: CandidateIndex) -> None: ...
    def search(
        self, snapshot: RepositoryIndexSnapshot, query: EmbeddingVector, top_k: int
    ) -> tuple[VectorSearchResult, ...]: ...
    def delete_repository_index(self, repository_namespace: str) -> None: ...
```

`RepositoryIndexState`, `RepositoryIndexSnapshot`, `CandidateIndex`, and `StoredRecord` are internal opaque values, not public exports. The snapshot binds one search to one explicit published active generation for its whole operation. Core cannot inspect or construct its storage locator.

The contract specifies complete logical generation semantics, not physical copying. `add_reused` authorizes the adapter to reference, copy, or otherwise reuse unchanged vectors so long as the candidate is isolated until publication and remains a complete immutable snapshot afterward.

## 15. ChromaVectorStore

`ChromaVectorStore` owns:

- deterministic safe internal identifiers derived from complete namespaces;
- physical collection names and private generation identifiers;
- original namespace retention and exact validation in control and record metadata;
- external-vector insertion with no Chroma embedding function;
- checked binary32 projection before external-vector insertion;
- storage metadata encoding/decoding;
- candidate construction, validation, atomic publication, and retirement;
- mapping the configured Chroma metric's distance to the public score; and
- persistent restart discovery through an explicit active pointer.

The persistence root is mandatory caller-supplied external configuration. There is no default below the analyzed repository and no discovery of a path from repository content.

Chroma's transactional or metadata-update guarantees are not assumed. Before production implementation, a capability prototype against the exact proposed pin must prove that any failure before publication leaves the prior valid repository index authoritative, including process interruption around the publication boundary. An equivalent durable pointer/indirection mechanism is permitted. If the guarantee cannot be demonstrated, implementation stops and reports architectural incompatibility; the invariant is not weakened to fit the API.

## 16. Repository Isolation

Each exact `repository_namespace` owns one logical semantic index. Records from unrelated repositories are never placed in one logical index with metadata filtering as the only isolation boundary.

A collision-resistant deterministic digest may form part of a Chroma-safe physical identifier, but the full original namespace is stored in control metadata and every record and is validated on reads and search results. A derived identifier collision or namespace mismatch is corruption and fails closed.

Different namespaces can synchronize concurrently only to the extent proven safe by the pinned local Chroma configuration. Every read, write, publication, search, and deletion operation is scoped by exact namespace.

## 17. Persistence and Storage Schema

The fixed internal schema version is:

```text
tracerag-chroma-schema-v1
```

Every published generation records and validates repository namespace, embedding identity, embedding-document version, schema version, and expected chunk count. Every vector record contains synchronization/provenance metadata equivalent to:

- repository namespace;
- chunk ID and content hash;
- relative path and language;
- chunk kind and optional symbol kind;
- optional qualified and parent-qualified names;
- embedding-document version;
- full non-secret embedding identity; and
- storage schema version.

The Chroma document is exact `CodeChunk.content`; this source/evidence field remains byte-exact. The Chroma embedding field is the sole persisted vector authority: it receives the checked binary32 projection of the validated provider vector, and manifest reads return the finite dimension-valid values that Chroma persisted. Exact Python-float equality with provider output is not promised. The synthetic embedding text is not persisted as source evidence. Optional values use an adapter-defined reversible representation because Chroma metadata capabilities must be verified; decoding must reproduce `None` without ambiguity.

There is no SQLite/JSON or other second per-chunk manifest and no vector mirror in Chroma metadata, a sidecar file, or control data. The Chroma vector records are the manifest. Small durable storage-control data used solely to locate the explicitly active generation is allowed.

This binary32 representation is a pre-release clarification of `tracerag-chroma-schema-v1`, not a migration or new embedding space. `gemini-embedding-001-retrieval-3072-v1` continues to identify provider/model/task/dimension semantics; the store-owned numeric representation is versioned only by `tracerag-chroma-schema-v1`.

Incompatible schema is not silently guessed or migrated. Corrupt or incompatible storage raises a typed error before reuse or search.

## 18. Repository Index and Generation Lifecycle

The authoritative state transition is:

```text
ACTIVE G1 (or explicitly no active index)
    |
    +--> create private candidate G2
             |
             +--> add every reused and newly embedded logical record
             |
             +--> validate namespace, identity, schema, count, uniqueness,
             |    dimensions, metadata, content, and candidate completeness
             |
             +--> atomically publish active pointer to G2
                         |
                         +--> G2 is authoritative
                         +--> best-effort retire G1 and abandoned candidates
```

Before successful publication, G1 remains authoritative and searchable. G2 is private and cannot be selected by search. Embedding, candidate construction, validation, or publication failure raises a typed exception; G1 remains active; G2 is aborted or left as an unreachable orphan for later cleanup; and no success result is returned.

Publication is the single logical commit point. If publication reports failure or has an indeterminate outcome, the adapter must resolve the durable active pointer before returning: it may report success only if G2 is provably the active complete generation, otherwise it raises while preserving or proving G1 authoritative. It must never expose both as ambiguously active.

After publication succeeds, cleanup failure does not roll back or invalidate G2. Cleanup is maintenance rather than correctness and is reported only through sanitized logging/maintenance telemetry, not by changing `IndexSyncResult` to failure.

Generation identifiers and candidate handles are private adapter data and never appear in public results, exceptions, or logs intended for ordinary consumers.

## 19. Empty and Never-indexed States

The store distinguishes:

- `NOT_INDEXED`: no published active index exists for the namespace; and
- `ACTIVE`: an explicitly published valid index exists, including one with zero records.

Synchronizing a legitimate zero-chunk `CodeChunkInventory` publishes a valid empty index when the repository is new or the previous index was nonempty. It requires no provider call. Search over a valid active empty index returns `()` without embedding the query. Search over `NOT_INDEXED` raises `RepositoryIndexNotFound`.

A repeat synchronization of an already active compatible empty index is `UNCHANGED` with no provider call, vector write, or publication.

## 20. Incremental Synchronization Algorithm

Core synchronization is repository-scoped and uses dictionaries keyed by `chunk_id` for approximately `O(current chunks + stored records)` comparison:

```text
validate inventory and provider identity
acquire per-namespace synchronization guard
inspect exactly one active state

if active exists:
    validate active control metadata and complete stored manifest

compatible = active exists
             and active.identity == provider.identity
             and active.document_version == V1 document version

if compatible:
    for current chunk by chunk_id:
        absent from stored                 -> NEW
        same content_hash                  -> UNCHANGED
        different content_hash             -> UPDATED
    for stored chunk_id absent in current  -> DELETED
else:
    every current chunk                    -> NEW for embedding purposes
    no vector reuse

if compatible and no NEW/UPDATED/DELETED:
    return UNCHANGED without provider/store mutation

create private complete candidate
add compatible UNCHANGED records through adapter reuse
render NEW and UPDATED documents deterministically
embed in bounded batches; validate each complete response
add validated embedded records
validate complete candidate
publish candidate
return SUCCESS
best-effort cleanup after the commit point
```

For an identity/document incompatibility, result counters describe the work actually performed: `reused_chunks == 0`, `embedded_chunks == total_chunks`, `inserted_chunks` is the number of current chunk IDs absent from the former manifest, `updated_chunks` is the remaining current count re-embedded because reuse was incompatible, and `deleted_chunks` is the number of former IDs absent from current. Thus `inserted + updated == embedded` remains true while indicating structural additions separately from forced replacement. For a first index, every chunk is inserted.

All current records, including reused records, must be represented in the complete logical candidate. Deleted records are omitted. No all-pairs comparison or complete vector matrix is constructed in core.

## 21. No-op Synchronization

No-op detection happens after active manifest validation and before candidate creation. It requires equal identity, equal document version, identical unique chunk-ID sets, and equal corresponding content hashes.

Required observable behavior is:

```text
provider document calls = 0
vector writes = 0
candidate creations = 0
publications = 0
status = UNCHANGED
```

An offline test uses a provider whose embedding methods raise immediately and a recording store that rejects mutating calls. The same test covers an active compatible empty index.

## 22. Semantic Search Flow

```text
validate namespace, query, and top_k
        |
        v
resolve one active repository snapshot
        |
        +--> never indexed: RepositoryIndexNotFound
        +--> corrupt/incompatible: typed failure
        +--> valid empty: return () without provider call
        |
        v
require active identity == configured provider identity
        |
        v
provider.embed_query(query_text)
        |
        v
validate vector -> store.search(snapshot, vector, top_k)
        |
        v
validate provenance/content/scores/uniqueness/count
        |
        v
sort by public deterministic ordering -> immutable results
```

`repository_namespace` must be a nonempty string. `query_text` must be a string containing non-whitespace content and must not exceed a configurable positive character bound. The implementation plan must select and document a conservative provider-independent V1 default after reviewing existing configuration conventions. Whitespace is validated but the query is passed unchanged; it is not stripped or normalized.

`top_k` must be an integer but not `bool`, at least 1, and at most configurable `max_top_k`, whose V1 default is 100. Search is read-only: it never repairs, re-embeds stored documents, creates/publishes generations, deletes records, or changes metadata.

## 23. Score Semantics and Result Invariants

The public score is finite, lies in `[0.0, 1.0]`, and increases with similarity. It is not a probability. Raw Chroma distance and metric-specific details do not escape the adapter.

`ChromaVectorStore` configures one explicit metric and owns the verified transformation from that metric's returned distance to the public score. The exact metric and formula are a prerequisite-gate output documented against and tested with the exact Chroma pin. Search operates on Chroma's persisted vector representation, not an original-vector mirror. For ChromaDB 1.5.9 cosine distance, the formula remains `score = 1 - (distance / 2)`. Finite distance error within the gate-proven absolute `1e-6` tolerance of `0` or `2` is treated as that boundary before applying the formula; values farther outside `[0, 2]` are corruption. This boundary rule is not permission to clip arbitrary distances. No universal `min_score` exists in V1.

For every successful search:

```text
0 <= len(results) <= top_k
all chunk IDs are unique
all complete stored namespaces equal the requested namespace
all scores are finite and in [0, 1]
all content is exact stored Module 4 source
```

Core normalizes final ordering by:

```text
(-score, relative_path.casefold(), relative_path, chunk_id)
```

The store may request at most `top_k` neighbors because duplicate IDs or wrong namespaces indicate corruption rather than records to filter and replace. Module 5 fails closed instead of returning a shortened apparently valid subset after discarding bad results.

## 24. Validation and Corruption Behavior

The following fail closed before reuse or results are accepted:

- an active pointer whose generation is missing or incomplete;
- repository namespace mismatch at control or record level;
- unknown schema or document version;
- inconsistent expected/actual chunk count;
- duplicate or malformed chunk IDs/content hashes;
- missing or malformed required metadata;
- invalid source-document values;
- vector absence, malformed/non-finite values, incompatible dimensions, or a non-finite binary32 projection on write;
- incompatible embedding identity; and
- invalid, duplicate, out-of-scope, excessive, or non-finite search results.

The adapter never chooses the newest collection, filters invalid records and pretends success, or repairs storage as a side effect of search. A missing active generation referenced by durable control metadata is corruption, not `NOT_INDEXED`.

Module 5 inventory validation occurs before acquiring provider results or beginning candidates. Store data is independently validated because persistence is untrusted mutable state.

## 25. Security and Credential Boundary

Gemini credentials are injected from outside Module 5 through application configuration or a trusted preconfigured client. Module 5 never searches the target repository for keys; reads its `.env` or configuration as credentials; persists credentials; includes them in identity, metadata, logs, `repr`, or exceptions; or derives provider configuration from repository content.

Missing credentials/configuration fail before indexing or query embedding. Repository path and content are untrusted data, never configuration. The persistence root is also external trusted configuration and must not resolve inside the analyzed repository unless a caller explicitly supplies that location after an application-level policy decision; Module 5 provides no such default.

Normal logs may include provider name, non-secret model identity, operation, document count, retry attempt, and sanitized outcome. They omit API keys, authorization headers, source chunks, embedding documents, full vectors, raw provider bodies, raw Chroma responses, and repository-controlled exception text. Typed exception messages are fixed/sanitized and do not automatically chain a secret-bearing raw response into public display. Vector values occur only in Chroma's designated embedding field; they are not duplicated into record metadata, the active-pointer file, logs, exceptions, or another manifest.

## 26. Error Taxonomy

The public hierarchy is conceptually:

```text
EmbeddingVectorStoreError
├── EmbeddingVectorStoreConfigurationError
├── InvalidCodeChunkInventory
├── EmbeddingDocumentError
│   └── EmbeddingDocumentTooLarge
├── EmbeddingProviderError
│   ├── EmbeddingAuthenticationError
│   ├── EmbeddingRateLimitError
│   ├── EmbeddingTransientError
│   ├── EmbeddingInvalidRequestError
│   └── EmbeddingInvalidResponseError
├── VectorStoreError
│   ├── VectorStoreConfigurationError
│   ├── VectorStoreReadError
│   ├── VectorStoreWriteError
│   ├── VectorStorePublicationError
│   └── VectorStoreCorruptionError
└── SemanticSearchError
    ├── InvalidSearchRequest
    ├── RepositoryIndexNotFound
    └── EmbeddingSpaceMismatch
```

The implementation plan may refine names to avoid awkward inheritance while preserving these typed distinctions. A Chroma failure is mapped by operation. Storage incompatibility/corruption is never downgraded to a missing index. There is no `PartialSuccess` status or exception.

## 27. Retry and Failure Behavior

Core or the Gemini adapter applies bounded exponential backoff only to typed `EmbeddingRateLimitError` and `EmbeddingTransientError`. Configuration owns maximum attempts, initial delay, multiplier, and maximum delay; all values are finite and bounded. A sleeper callable is injected so tests record delays without waiting.

Authentication, invalid configuration/request, unsupported model, oversized document, invalid response, cardinality/dimension/value failure, and all inventory errors are not retried. Retry exhaustion re-raises a sanitized typed provider error with causal classification but no source or raw response.

Batches are bounded by provider capacity. A later batch failure aborts the whole candidate; earlier candidate writes remain private and are never reported as partial success. Store read, write, validation, and publication failures are not blindly retried by core because their idempotency and commit semantics belong to the adapter and must be proven first.

## 28. Crash Recovery

Only the explicitly published durable active pointer is authoritative after restart. Storage may contain a valid active generation, abandoned candidates, and obsolete generations. Candidates and obsolete generations are unreachable by search and may be garbage-collected through adapter maintenance.

Recovery never infers authority from creation time, collection ordering, lexicographic generation name, or newest metadata. Correctness does not depend on cleanup. The capability prototype must exercise interruption before candidate completion, after candidate validation but before publication, at the publication boundary, and after publication but before cleanup.

## 29. Concurrency

V1 permits only one synchronization to modify a given exact repository namespace at a time within the supported process/storage deployment. The guard is acquired before active-state comparison and held through publication outcome resolution. A second same-namespace synchronization fails with a typed busy/configuration outcome or waits according to an explicitly bounded application policy selected in the implementation plan; it must not race silently.

Search continues against the old active snapshot while a candidate is built. A search resolves one published snapshot once and uses that same snapshot for identity validation and nearest-neighbor lookup even if publication occurs concurrently. Different repositories are logically independent, subject to capabilities proven for the pinned local Chroma configuration.

Module 5 does not design a distributed lock manager. Multi-process mutation is unsupported unless the prerequisite prototype proves a Chroma-backed or external single-writer mechanism for the exact deployment; otherwise configuration must reject it or documentation must constrain V1 to one writer process.

## 30. Resource and Scaling Constraints

Core performs manifest comparison with two chunk-ID dictionaries and avoids all-pairs work. It flattens only references/metadata required for synchronization, renders embedding text in bounded batches, and releases batch documents/vectors after transfer to the candidate where practical. It does not construct a repository-wide vector matrix or duplicate exact source beyond the immutable Module 4 input and bounded transient provider requests.

Search is bounded by `max_top_k`. Provider batches and document/query sizes are bounded. No document is silently truncated. If the complete canonical embedding document exceeds a verified provider/configured request limit, synchronization raises `EmbeddingDocumentTooLarge`, aborts the candidate, and preserves the prior active index. Core adds no Gemini tokenizer dependency.

Scaling tests assert structural evidence—provider batch counts and sizes, linear manifest lookups, bounded search request size, absence of calls on no-op/empty paths, and no core vector-matrix construction—rather than fragile wall-clock thresholds.

## 31. Testing Strategy

Normal tests are offline with respect to Gemini. Test doubles include deterministic, recording, fail-if-called, transient/rate-limited, permanently failing, and malformed-response providers, plus recording/failing stores.

Unit and contract tests cover:

- frozen/slotted model fields, enums, exports, validation, and counter invariants;
- deterministic document bytes for Unicode, LF/CRLF, tabs, quotes, backslashes, null optionals, Python/Java/JavaScript/TypeScript, context chunks, and fragments;
- strict distinction between `embed_documents` and `embed_query`;
- batch boundaries, stable positional association, bounded size, and no truncation;
- new/update/delete classification and compatible reuse;
- complete re-embedding on identity or document-version incompatibility;
- no-op synchronization with a fail-if-called provider and zero mutating store calls;
- first and repeated empty indexes and nonempty-to-empty synchronization;
- retry eligibility, delay sequence, exhaustion, and permanent no-retry failures;
- missing/empty/wrong-dimension/non-numeric/bool/NaN/infinite vectors, finite values that overflow binary32 projection, and cardinality mismatch;
- credential absence and non-leakage through identity, metadata, logs, exceptions, and repr;
- repository isolation, exact namespace deletion, and collision/mismatch rejection;
- never-indexed versus active-empty search, request bounds, identity mismatch, and query validation;
- checked binary32 write projection and fresh-process manifest reads for exactly representable, ordinary non-unit, small-magnitude, negative, and representative normalized-like vectors, without requiring exact equality to provider Python floats or storing a vector mirror;
- score validation over Chroma's persisted representation, including the proven `1e-6` distance-boundary tolerance, exact content, uniqueness, count bounds, and deterministic tie ordering;
- candidate abort on embedding/write/validation failure and preservation of the old active index;
- publication failure and indeterminate-outcome resolution;
- cleanup failure after successful publication;
- schema, count, namespace, metadata, duplicate-ID, dimension, and active-pointer corruption;
- restart behavior, orphan candidates, obsolete generations, and explicit active selection;
- same-namespace synchronization guard and concurrent search snapshot stability; and
- scaling call counts and bounded intermediates.

Chroma integration tests use isolated temporary persistence directories and never a real user index. They prove external embeddings with no Chroma embedding function, checked binary32 projection, persistence/restart, active/candidate lifecycle, repository isolation, valid empty publication, exact source/evidence round-trip, store-authoritative vector readback, metric/score normalization with boundary tolerance, failure preservation, and cleanup independence.

An optional real Gemini test is explicitly opt-in, uses externally supplied credentials, performs the minimum request, and is excluded from the ordinary suite. It never logs content, vectors, responses, or secrets.

## 32. Dependency Prerequisite and Capability Gate

The later implementation plan must begin with a separate compatibility gate before production Module 5 code. It must consult current official documentation and test exact proposed direct pins for the official Gemini Python SDK, a currently supported embedding model and configuration, and ChromaDB. No version or model is selected from memory in this specification.

The gate must verify:

- project and interpreter Python compatibility and wheel availability;
- compatibility with existing exact TraceRAG dependencies;
- imports and trusted client/adapter construction;
- document versus query embedding configuration and returned dimensions;
- provider request/batch limits and typed error surfaces;
- a minimal local persistent Chroma lifecycle and restart;
- external-vector operation without a Chroma embedding function;
- metric configuration, returned-distance meaning, and score conversion;
- metadata type/null constraints and collection naming constraints;
- durable active-pointer/publication behavior under injected failures/interruption;
- supported single-writer and concurrent-reader behavior; and
- clean-environment installation plus `pip check`.

No unrelated dependency is upgraded. If the proposed pins conflict, the provider facts cannot be verified, or the fail-safe publication invariant cannot be proven, the gate stops and reports the incompatibility before production implementation.

## 33. Cross-module Compatibility

Module 5 imports Module 4 public models and treats `CodeChunkInventory` as immutable authority. It does not alter Module 4 chunk construction, IDs, hashes, text, locations, ordering, repository namespace, source-reading behavior, or dependencies. It does not change Modules 1–3.

Later implementation verification must run Module 1, Module 2, Module 3 and grammar, Module 4, Module 5, and full-suite tests; compilation checks; dependency smoke and `pip check`; and a scope/diff review. Module 5 tests may construct inventories directly for focused cases but must include real Module 4 integration proving exact content and metadata flow.

## 34. Acceptance Criteria

The eventual Module 5 implementation is acceptable only when all of the following are demonstrated:

1. Public core contracts contain no Gemini, Chroma, collection, or generation concepts.
2. Embedding document V1 is byte-deterministic and preserves the exact source suffix.
3. Every document/query vector and batch cardinality is validated before use; Chroma writes additionally use checked finite binary32 projection without clipping or normalization.
4. Compatible unchanged chunks are reused; new and updated chunks alone are embedded.
5. A true no-op performs no provider call, write, candidate creation, or publication.
6. Identity or document-version incompatibility causes complete repository re-embedding.
7. Empty published indexes are distinct from never-indexed repositories.
8. Any failure before the proven publication commit point leaves the prior index authoritative and searchable.
9. Cleanup failure after publication does not invalidate the new active index.
10. Search binds one active snapshot, enforces embedding compatibility, and is mutation-free.
11. Results contain exact source evidence, valid normalized scores derived from search over Chroma's persisted vector representation, unique in-scope chunks, and deterministic order.
12. Storage corruption and incompatibility fail closed without repair or guessing.
13. Credentials do not leak; exact source and derived vectors persist only in their designated Chroma document and embedding fields, with no metadata/file vector mirror or exposure through identities, logs, repr, or exceptions.
14. Repository namespaces are isolated logically and validated in complete stored metadata.
15. Resource bounds are proven structurally and no oversized document is truncated.
16. The exact Gemini/Chroma dependency set passes the prerequisite capability gate, including the revised binary32 vector-representation cases and floating-distance tolerance.
17. Modules 1–4 and the complete test suite remain green with no production-behavior changes.

## 35. Explicit Deferred Work for Modules 6–10

- **Module 6:** dependency/call graph construction and graph persistence from syntactic evidence.
- **Module 7:** Git-history evidence and temporal change analysis.
- **Module 8:** runtime, log, error, and stack-trace evidence ingestion.
- **Module 9:** BM25/lexical retrieval, hybrid candidate fusion, graph expansion, relevance thresholds, cross-source scoring, and reranking.
- **Module 10:** LLM context assembly, answering, causal reasoning, citation presentation, and user-facing application/API behavior.

Module 5 exposes low-level semantic neighbors only. It does not anticipate later ranking policy by adding a universal score cutoff, query rewriting, evidence fusion, or answer generation.

## 36. Resolved Decisions and Remaining Gates

Resolved architecture decisions are:

- provider abstractions with Gemini and Chroma as replaceable edges;
- exact-equality embedding-space compatibility;
- `tracerag-embedding-document-v1` with six fixed metadata fields and exact source suffix;
- vector records as the only per-chunk synchronization manifest;
- Chroma's embedding field as the sole vector authority, with checked binary32 write projection and store-authoritative readback;
- one logical index per exact repository namespace;
- complete logical candidates and one explicit atomic publication point;
- private generation details and explicit active-pointer authority;
- valid persistent empty indexes;
- bounded typed retries and no partial-success state; and
- public score normalization inside the storage adapter.

The dependency gate resolves the exact Gemini SDK/model/configuration/dimension/pins and limits; exact Chroma pin; binary32 vector representation; supported metadata representation; persistence/publication mechanism; configured metric and verified score formula/tolerance; and supported single-writer deployment mechanics. The revised vector-representation gate remains contingent on Task 10 implementation review proving the documented projection, single vector authority, and fresh-process behavior before later production tasks resume.
