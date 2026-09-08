# Embedding & Vector Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent, repository-scoped semantic vector index from `CodeChunkInventory` with deterministic embedding inputs, compatible-vector reuse, fail-safe publication, and bounded low-level semantic search.

**Architecture:** Provider-independent core orchestration depends on `EmbeddingProvider` and `VectorStore` contracts. Gemini and ChromaDB are replaceable edge adapters; Chroma owns all physical generations, durable active-pointer mechanics, metadata encoding, and score conversion. Synchronization constructs and validates a complete private logical candidate before one publication commit point, so the prior active index remains authoritative on every earlier failure.

**Tech Stack:** Python 3 as verified by Task 1, frozen/slotted dataclasses, protocols, pytest, the exact official Gemini Python SDK pin selected by Task 1, and the exact ChromaDB pin selected by Task 1.

**Spec:** docs/superpowers/specs/2026-09-03-embedding-vector-store-design.md

## Global Constraints

- Execution begins by invoking `superpowers:using-git-worktrees` and creating an isolated `codex/module-5-embedding-vector-store` feature branch/worktree; never implement directly on primary `main`.
- Task 1 is a hard dependency and capability gate. Tasks 2–30 may execute only after its committed result is `PASS`; `BLOCKED` stops Module 5 implementation.
- The revised Task 10 vector-representation gate is `PASS — REVISED VECTOR REPRESENTATION`, contingent on implementation review. Task 11 and later must not begin until Task 10 removes the vector mirror and proves the checked projection, store-authoritative readback, and fresh-process cases below.
- Do not select a Gemini SDK, model, dimension, task mode, limit, exception mapping, Chroma version, metadata encoding, metric, score formula, or publication mechanism from memory. Consume the facts committed by Task 1.
- Only `backend/embedding_vector_store/providers/gemini.py` may import the Gemini SDK. Only `backend/embedding_vector_store/stores/chroma.py` may import ChromaDB or manipulate physical collection names, private generation identifiers, raw distances, and Chroma metadata APIs.
- Core public interfaces remain provider-independent. No public model contains a generation ID, collection name, API key, retry state, raw distance, or raw provider response.
- Use `tracerag-embedding-document-v1` and `tracerag-chroma-schema-v1` exactly.
- Module 4 `CodeChunkInventory` is immutable source authority. Never reread, rescan, normalize, truncate, execute, or import analyzed repository content.
- The persistence root is mandatory external configuration and has no repository-local default.
- Provider responses and stored records are untrusted. Validate before storage, reuse, or return.
- After provider validation, Chroma writes use checked component-wise IEEE-754 binary32 projection and fail closed if a finite provider value projects non-finite. Do not clip or normalize vectors. Chroma's embedding field is the sole persisted vector authority; do not mirror vectors in metadata, files, or control data, and do not require exact Python-float equality on readback.
- There is no `PARTIAL_SUCCESS`. Any pre-publication failure preserves the previous active index and raises a typed exception.
- Search is read-only, uses one active snapshot, and returns exact stored `CodeChunk.content`.
- Ordinary tests perform no real Gemini network request. The optional live test is explicitly selected and externally credentialed.
- Every production task follows RED → minimal GREEN → focused regression → diff review → commit. Never weaken a test to obtain GREEN.
- Do not implement Module 6–10 retrieval, graphs, history/runtime evidence, reranking, answering, or UI behavior.

---

## Planned File and Package Structure

```text
backend/embedding_vector_store/
    __init__.py                 provider-independent public exports
    models.py                   public frozen/slotted values and invariants
    exceptions.py               public typed exception hierarchy
    documents.py                V1 canonical embedding-document renderer
    validation.py               Module 4, vector, search, and result validation
    retry.py                    bounded typed retry executor
    synchronization.py          diffing, batching, and complete-candidate orchestration
    search.py                   SemanticSearcher orchestration
    providers/
        __init__.py             provider exports
        base.py                 EmbeddingProvider protocol
        gemini.py               verified Gemini adapter only
    stores/
        __init__.py             store exports
        base.py                 VectorStore protocol and opaque internal values
        chroma.py               schema, persistence, publication, score conversion

tests/
    capability/
        test_module5_dependency_capabilities.py
    test_embedding_models.py
    test_embedding_documents.py
    test_embedding_validation.py
    test_embedding_providers.py
    test_embedding_retry.py
    test_gemini_embedding_provider.py
    test_vector_store_contract.py
    test_chroma_vector_store.py
    test_semantic_synchronization.py
    test_semantic_search.py
    test_embedding_security.py
    test_embedding_scaling.py
    test_embedding_integration.py
    integration/
        test_gemini_embeddings_live.py

docs/superpowers/verification/
    module-5-dependency-capability-gate.md
```

`requirements.txt` changes only in Task 1 after a `PASS`. `README.md` and the existing dependency-boundary test change only in Task 29. Files may be omitted when Task 1 proves that a proposed capability cannot satisfy the approved design; no production package is then created.

## Canonical Interfaces Used Throughout

Tasks must preserve these exact names and signatures unless Task 1 proves a dependency-level impossibility and the module is stopped:

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


class VectorStore(Protocol):
    def inspect_active(self, repository_namespace: str) -> RepositoryIndexState: ...
    def read_manifest(
        self, snapshot: RepositoryIndexSnapshot
    ) -> tuple[StoredRecord, ...]: ...
    def begin_candidate(
        self,
        repository_namespace: str,
        identity: EmbeddingModelIdentity,
        document_version: str,
        expected_chunk_count: int,
    ) -> CandidateIndex: ...
    def add_reused(
        self, candidate: CandidateIndex, records: tuple[StoredRecord, ...]
    ) -> None: ...
    def add_embedded(
        self, candidate: CandidateIndex, records: tuple[VectorRecord, ...]
    ) -> None: ...
    def validate_candidate(self, candidate: CandidateIndex) -> None: ...
    def publish(self, candidate: CandidateIndex) -> RepositoryIndexSnapshot: ...
    def abort(self, candidate: CandidateIndex) -> None: ...
    def search(
        self,
        snapshot: RepositoryIndexSnapshot,
        query: EmbeddingVector,
        top_k: int,
    ) -> tuple[StoreSearchResult, ...]: ...
    def delete_repository_index(self, repository_namespace: str) -> None: ...


class SemanticIndexer:
    def __init__(
        self,
        provider: EmbeddingProvider,
        store: VectorStore,
        retry_policy: RetryPolicy | None = None,
    ) -> None: ...
    def synchronize(self, inventory: CodeChunkInventory) -> IndexSyncResult: ...


class SemanticSearcher:
    def __init__(
        self,
        provider: EmbeddingProvider,
        store: VectorStore,
        *,
        max_query_chars: int = 16_384,
        max_top_k: int = 100,
        retry_policy: RetryPolicy | None = None,
    ) -> None: ...
    def search(
        self, repository_namespace: str, query_text: str, top_k: int
    ) -> tuple[VectorSearchResult, ...]: ...
```

`RepositoryIndexState`, `RepositoryIndexSnapshot`, `CandidateIndex`, `StoredRecord`, and `StoreSearchResult` are internal frozen/slotted types in `stores/base.py`, are opaque to callers, and are absent from top-level `__all__`. The 16,384-character query bound is provider-independent input protection; adapters may reject a smaller verified provider request limit without truncation.

---

### Task 1: Dependency and Capability Gate

**Files:**
- Create: `tests/capability/test_module5_dependency_capabilities.py`
- Create: `docs/superpowers/verification/module-5-dependency-capability-gate.md`
- Modify on PASS only: `requirements.txt`

**Interfaces:**
- Consumes: current interpreter, `requirements.txt`, official Gemini embedding documentation, official Gemini SDK reference, official ChromaDB documentation, and the approved design invariant.
- Produces: a committed `PASS` or `BLOCKED` gate report; on `PASS`, exact direct pins and verified facts consumed by every adapter task.

- [ ] **Step 1: Start isolated execution and capture the baseline**

Invoke `superpowers:using-git-worktrees`, create the feature worktree/branch, then run:

```powershell
python --version
python -m pip freeze
python -m pip check
Get-Content -Raw requirements.txt
git status --short --branch
```

Record the interpreter and exact baseline dependencies. Do not modify the primary checkout.

- [ ] **Step 2: Verify current official provider facts**

Use current official Gemini documentation only. Record the official Python package, exact proposed pin, supported embedding model, document and query task configuration, configurable/default dimensions, request and batch limits, client construction, credential injection, and documented exception/status types. Cite direct official URLs and access date in the gate artifact. Do not send a real embedding request in the ordinary gate.

- [ ] **Step 3: Verify current official Chroma facts**

Use current official Chroma documentation/source reference only. Record an exact proposed pin, supported Python version, local persistence lifecycle, external embedding API, embedding-function disabling, collection identifier constraints, metadata value types, metric configuration, query distance semantics, and documented concurrency limitations. Cite direct official URLs and access date.

- [ ] **Step 4: Propose pins without upgrading unrelated packages**

Create a temporary virtual environment outside the repository, install the existing `requirements.txt` plus only the two exact proposed direct pins, and run:

```powershell
python -m pip install -r requirements.txt
$env:TRACERAG_GATE_GEMINI_REQUIREMENT = Read-Host 'Exact verified Gemini package==version'
$env:TRACERAG_GATE_CHROMA_REQUIREMENT = Read-Host 'Exact verified Chroma package==version'
$env:TRACERAG_GATE_GEMINI_IMPORT = Read-Host 'Exact verified Gemini import package'
python -m pip install $env:TRACERAG_GATE_GEMINI_REQUIREMENT $env:TRACERAG_GATE_CHROMA_REQUIREMENT
python -m pip check
python -c "import importlib, os; importlib.import_module(os.environ['TRACERAG_GATE_GEMINI_IMPORT']); import chromadb; print('imports-ok')"
```

The angle-bracket values are filled with the facts just verified in Steps 2–3, then copied verbatim into the committed gate artifact. Compare `pip freeze` before/after and fail the gate if an unrelated direct dependency change is required.

- [ ] **Step 5: Write executable capability tests**

In `tests/capability/test_module5_dependency_capabilities.py`, encode the verified imports/APIs and temporary-directory prototypes. Tests must prove: client construction without a request; external embeddings with no Chroma embedding function; add/query/persist/reopen; legal/illegal collection identifiers; every metadata type used by Module 5; the reversible representation for `None`; the configured metric; raw distances for identical, orthogonal, and opposite normalized vectors; the mathematically derived `[0,1]` formula; isolated candidate collections; an explicit durable active-pointer prototype; injected failure before pointer replacement; interruption immediately before and at pointer replacement; reopen after publication before cleanup; abandoned/obsolete collection behavior; same-namespace writer exclusion; and a reader pinned to the old active snapshot while a candidate exists.

Use fixed synthetic vectors such as `(1.0, 0.0)`, `(0.0, 1.0)`, and `(-1.0, 0.0)`—never source chunks or provider output. The publication prototype must use only guarantees demonstrated for the exact pin.

- [ ] **Step 6: Run the capability suite and dependency checks**

```powershell
python -m pytest tests/capability/test_module5_dependency_capabilities.py -q -rs
python -m pip check
```

Expected for `PASS`: every prototype passes after interpreter restart/reopen, and `pip check` reports no broken requirements. A missing guarantee, ambiguous publication outcome, unsupported environment, or required unrelated upgrade yields `BLOCKED`.

- [ ] **Step 7: Commit an explicit gate result**

Write `docs/superpowers/verification/module-5-dependency-capability-gate.md` with verification date, Python version, exact pins, model/configuration/dimensions, document/query modes, relied-upon limits and exception classes/statuses, metric, raw-distance meaning, score formula, metadata encoding, identifier constraints, durable publication mechanism, concurrency limits, exact commands/tests, sanitized outcomes, and final `PASS` or `BLOCKED`. Include no credentials, source, vectors, or raw secret-bearing responses.

If `PASS`, add only the verified direct pins to `requirements.txt` and commit:

```powershell
git add requirements.txt tests/capability/test_module5_dependency_capabilities.py docs/superpowers/verification/module-5-dependency-capability-gate.md
git commit -m "build: verify embedding vector dependencies"
```

If `BLOCKED`, do not modify `requirements.txt`, do not create `backend/embedding_vector_store`, commit only the capability test/report with `docs: record blocked embedding capability gate`, and stop all later tasks.

---

### Task 2: Public Models and Exception Contracts

**Files:**
- Create: `backend/embedding_vector_store/models.py`
- Create: `backend/embedding_vector_store/exceptions.py`
- Create: `tests/test_embedding_models.py`

**Interfaces:**
- Consumes: approved field definitions and the Task 1 `PASS` gate.
- Produces: `EmbeddingModelIdentity`, `EmbeddingVector`, `EmbeddingDocument`, `VectorRecord`, `IndexSyncStatus`, `IndexSyncResult`, `VectorSearchResult`, and the typed exception hierarchy from spec Section 26.

- [ ] **Step 1: Write RED model and exception tests**

Test exact dataclass field order from the spec, `frozen=True`, `slots=True`, tuple vector values, nonempty identity strings, positive integer-but-not-bool dimensions, stable enum values `success`/`unchanged`, finite `[0,1]` result scores, lowercase 64-character chunk/content hashes, nonempty paths/languages/kinds/content types, and all sync counter equations. Assert `UNCHANGED` requires zero mutation counters and `reused_chunks == total_chunks`. Assert forbidden fields (`generation`, `collection`, `api_key`, `retry`, `distance`, `response`) do not occur in public dataclass fields.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_embedding_models.py -q
```

Expected: collection fails because the package/models do not exist.

- [ ] **Step 3: Implement minimum immutable contracts**

Create exactly the public dataclasses/enums shown in spec Section 8. Implement local `__post_init__` checks with fixed sanitized messages. Define the exception tree rooted at `EmbeddingVectorStoreError`, including configuration, inventory, document/size, provider authentication/rate-limit/transient/invalid-request/invalid-response, store configuration/read/write/publication/corruption, and search invalid-request/not-found/space-mismatch classes.

- [ ] **Step 4: Run focused and adjacent tests**

```powershell
python -m pytest tests/test_embedding_models.py -q
python -m pytest tests/test_code_chunker.py -q
git diff --check
```

- [ ] **Step 5: Review and commit**

```powershell
git diff -- backend/embedding_vector_store/models.py backend/embedding_vector_store/exceptions.py tests/test_embedding_models.py
git add backend/embedding_vector_store/models.py backend/embedding_vector_store/exceptions.py tests/test_embedding_models.py
git commit -m "feat: define embedding vector store contracts"
```

---

### Task 3: Deterministic Embedding Document V1

**Files:**
- Create: `backend/embedding_vector_store/documents.py`
- Create: `tests/test_embedding_documents.py`

**Interfaces:**
- Consumes: `CodeChunk`, file-level `relative_path: str`, file-level `ParsedLanguage`, and `EmbeddingDocument`.
- Produces: `EMBEDDING_DOCUMENT_VERSION = "tracerag-embedding-document-v1"` and `build_embedding_document(chunk: CodeChunk, relative_path: str, language: ParsedLanguage) -> EmbeddingDocument`.

- [ ] **Step 1: Write byte-exact RED tests**

Create real Module 4 chunks for Python, Java, JavaScript, TypeScript, `CONTEXT`, and `FRAGMENT`. Independently construct expected bytes for Unicode, LF, CRLF, tabs, quotes, backslashes, and both optional-name fields as JSON null. Assert key order is exactly `chunk_kind`, `language`, `parent_qualified_name`, `qualified_name`, `relative_path`, `symbol_kind`; `ensure_ascii=False`; `separators=(",", ":")`; and exact text:

```python
expected = (
    "tracerag-embedding-document-v1\n"
    + canonical_metadata_json
    + "\n---TRACERAG-SOURCE---\n"
    + chunk.content
)
```

Assert the suffix is byte-identical and forbidden metadata never appears in the JSON.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_embedding_documents.py -q
```

Expected: import or function-not-found failure.

- [ ] **Step 3: Implement exact renderer**

Build an insertion-ordered literal `dict` with the six keys, serialize through `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`, and concatenate the three exact strings above. Never strip, normalize, summarize, or truncate.

- [ ] **Step 4: Run GREEN and regression**

```powershell
python -m pytest tests/test_embedding_documents.py tests/test_embedding_models.py -q
git diff --check
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/documents.py tests/test_embedding_documents.py
git commit -m "feat: render deterministic embedding documents"
```

---

### Task 4: Module 4 Inventory Validation and Flattening

**Files:**
- Create: `backend/embedding_vector_store/validation.py`
- Create: `tests/test_embedding_validation.py`

**Interfaces:**
- Consumes: exact `CodeChunkInventory`, `ChunkedFile`, and `CodeChunk` public models.
- Produces: internal frozen/slotted `ChunkInput` and `validate_and_flatten_inventory(inventory: CodeChunkInventory) -> tuple[str, tuple[ChunkInput, ...]]`.

- [ ] **Step 1: Write RED inventory tests**

Prove rejection before any collaborator call for wrong model types; empty namespace; inconsistent inventory/file counters; non-tuples; duplicate file paths/chunk IDs; invalid lowercase SHA-256 IDs/hashes; invalid POSIX relative paths; wrong language/kind/symbol enum types; malformed optional names/content; and file/chunk ordering that violates Module 4’s documented keys. Assert valid empty and mixed-language inventories flatten in file/chunk order with language/path attached once per `ChunkInput`.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_embedding_validation.py -q -k "inventory or flatten"
```

- [ ] **Step 3: Implement one linear validation pass**

Define `ChunkInput(chunk, relative_path, language)` internally. Validate only Module 5 dependencies, build one `set[str]` for IDs, compare documented sort keys, and return immutable values. Do not read `repository_path`, open files, recalculate content, or duplicate Module 4 chunking.

- [ ] **Step 4: Run GREEN and Module 4 regression**

```powershell
python -m pytest tests/test_embedding_validation.py -q -k "inventory or flatten"
python -m pytest tests/test_code_chunker.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/validation.py tests/test_embedding_validation.py
git commit -m "feat: validate module four chunk inventories"
```

---

### Task 5: Embedding Vector Validation and Provider Boundary

**Files:**
- Create: `backend/embedding_vector_store/providers/__init__.py`
- Create: `backend/embedding_vector_store/providers/base.py`
- Modify: `backend/embedding_vector_store/validation.py`
- Create: `tests/test_embedding_providers.py`
- Modify: `tests/test_embedding_validation.py`

**Interfaces:**
- Consumes: `EmbeddingModelIdentity`, `EmbeddingDocument`, and `EmbeddingVector`.
- Produces: runtime-checkable `EmbeddingProvider` protocol; `validate_embedding_vector(vector, identity) -> EmbeddingVector`; `validate_embedding_batch(documents, vectors, identity) -> tuple[EmbeddingVector, ...]`.

- [ ] **Step 1: Write RED contract/validation tests**

Define deterministic fake providers and assert the protocol requires `identity`, positive `max_batch_size`, positional `embed_documents(tuple[EmbeddingDocument, ...])`, and separate `embed_query(str)`. Reject missing, empty, wrong-dimension, string, bool, NaN, positive/negative infinity, and batch cardinality mismatch. Validate the whole batch before returning any association.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_embedding_providers.py tests/test_embedding_validation.py -q -k "provider or vector or cardinality"
```

- [ ] **Step 3: Implement provider-independent boundary**

Use `Protocol` and tuple signatures from the canonical interface. Validate with exact length and `math.isfinite`; reject `bool` explicitly. Raise only `EmbeddingInvalidResponseError` with fixed messages and return the original ordered tuple when valid. This is provider-boundary validation; Task 10 separately owns checked store-representation projection.

- [ ] **Step 4: Run GREEN**

```powershell
python -m pytest tests/test_embedding_providers.py tests/test_embedding_validation.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/providers backend/embedding_vector_store/validation.py tests/test_embedding_providers.py tests/test_embedding_validation.py
git commit -m "feat: add embedding provider boundary"
```

---

### Task 6: Bounded Typed Retry Policy

**Files:**
- Create: `backend/embedding_vector_store/retry.py`
- Create: `tests/test_embedding_retry.py`

**Interfaces:**
- Consumes: callable operation and typed provider exceptions.
- Produces: internal frozen/slotted `RetryPolicy(max_attempts: int = 3, initial_delay_seconds: float = 0.25, multiplier: float = 2.0, max_delay_seconds: float = 2.0, sleeper: Callable[[float], None] = time.sleep)` and `run_with_embedding_retries(operation: Callable[[], T], policy: RetryPolicy) -> T`.

- [ ] **Step 1: Write deterministic RED tests**

Assert delay sequence `0.25, 0.5` for two failures then success, cap behavior, exact maximum attempts, and zero sleep after success. Retry only `EmbeddingRateLimitError` and `EmbeddingTransientError`. Assert authentication, configuration, invalid request, document-too-large, invalid response, inventory, vector dimension/cardinality/value, and arbitrary exceptions execute once and sleep zero times.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_embedding_retry.py -q
```

- [ ] **Step 3: Implement bounded generic executor**

Validate finite positive numeric settings and integer-but-not-bool attempts. Catch exactly the two retryable public types, invoke the injected sleeper between attempts, preserve the final typed class with a sanitized message, and never inspect exception strings.

- [ ] **Step 4: Run GREEN**

```powershell
python -m pytest tests/test_embedding_retry.py tests/test_embedding_providers.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/retry.py tests/test_embedding_retry.py
git commit -m "feat: add bounded embedding retries"
```

---

### Task 7: GeminiEmbeddingProvider Adapter

**Files:**
- Create: `backend/embedding_vector_store/providers/gemini.py`
- Create: `tests/test_gemini_embedding_provider.py`

**Interfaces:**
- Consumes: exact Task 1 SDK/model/configuration/dimension/limit/error facts, `EmbeddingProvider`, public embedding models/exceptions.
- Produces: `GeminiEmbeddingProvider` implementing the canonical provider protocol with injected verified SDK client support.

- [ ] **Step 1: Write RED adapter tests with an injected fake client**

Assert eager rejection of missing credentials/client, invalid identity/configuration, and invalid batch capacity; document calls use the verified document task mode; query calls use the verified query task mode; order and dimensions are retained; oversize requests fail without SDK invocation; and the verified SDK authentication, rate-limit, transient, invalid-request/configuration, and response shapes map to exact Module 5 exceptions. Assert no message-substring classification.

- [ ] **Step 2: Verify RED without network**

```powershell
python -m pytest tests/test_gemini_embedding_provider.py -q
```

- [ ] **Step 3: Implement only the verified SDK surface**

Import the Task 1 pin only here. Accept either externally injected credentials through the verified client constructor or a trusted injected client. Construct `EmbeddingModelIdentity` from Task 1 facts, enforce verified size/batch limits, make document/query requests through distinct verified task configuration, convert responses positionally, and map documented exception types/statuses without raw bodies or secrets.

- [ ] **Step 4: Run GREEN and capability regression**

```powershell
python -m pytest tests/test_gemini_embedding_provider.py tests/test_embedding_providers.py tests/capability/test_module5_dependency_capabilities.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/providers/gemini.py tests/test_gemini_embedding_provider.py
git commit -m "feat: integrate gemini embeddings"
```

---

### Task 8: VectorStore Internal Contracts

**Files:**
- Create: `backend/embedding_vector_store/stores/__init__.py`
- Create: `backend/embedding_vector_store/stores/base.py`
- Create: `tests/test_vector_store_contract.py`

**Interfaces:**
- Consumes: public model types.
- Produces: canonical `VectorStore` protocol plus opaque frozen/slotted `RepositoryIndexState`, `RepositoryIndexSnapshot`, `CandidateIndex`, `StoredRecord`, and `StoreSearchResult` internal values.

- [ ] **Step 1: Write RED interface tests**

Assert every canonical method name/signature exists. Model `RepositoryIndexState` as `indexed: bool` plus `snapshot: RepositoryIndexSnapshot | None`; require exactly one consistent state. Internal snapshot/candidate handles may wrap opaque adapter tokens but expose only namespace, identity, document version, schema version, and expected count to core. `StoredRecord` carries all vector-record evidence plus the finite dimension-valid embedding values read from the store and versions; it does not promise equality to the provider's original Python-float tuple. `StoreSearchResult` adds repository namespace and normalized score. Assert none are re-exported at package top level.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_vector_store_contract.py -q
```

- [ ] **Step 3: Implement contracts only**

Define protocol signatures exactly as the canonical interface. Validate internal value invariants without importing Chroma, naming a collection, or exposing a public generation field. Keep physical locators inside an underscore-prefixed opaque token held only by adapter-created instances.

- [ ] **Step 4: Run GREEN and static import check**

```powershell
python -m pytest tests/test_vector_store_contract.py tests/test_embedding_models.py -q
rg -n "chromadb|gemini|google\.genai|generation_id|collection_name" backend/embedding_vector_store/stores/base.py backend/embedding_vector_store/models.py
```

Expected: tests pass; static search returns no provider-specific import or public field.

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/stores tests/test_vector_store_contract.py
git commit -m "feat: define vector store lifecycle"
```

---

### Task 9: Chroma Schema Encoding and Namespace Isolation

**Files:**
- Create: `backend/embedding_vector_store/stores/chroma.py`
- Create: `tests/test_chroma_vector_store.py`

**Interfaces:**
- Consumes: Task 1 metadata/identifier facts, `tracerag-chroma-schema-v1`, internal stored values.
- Produces: `ChromaVectorStore(persistence_root: str | Path)` initialization, deterministic private namespace mapping, and reversible record/control metadata encode/decode helpers.

- [ ] **Step 1: Write RED schema/isolation tests**

Assert persistence root is mandatory, resolved, external, and never inferred from inventory. Use two nearly identical namespaces and a forced derived-ID collision to prove full namespace validation. Round-trip every required record/control field, optional `None` values using Task 1 encoding, Unicode paths/names, exact content, identity, document/schema version, expected count, and validated dimensions. Assert record/control metadata contains no serialized vector or vector-mirror field. Reject unknown schema, malformed metadata, namespace mismatch, and unsupported values.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_chroma_vector_store.py -q -k "schema or metadata or namespace or persistence_root"
```

- [ ] **Step 3: Implement verified encoding only in Chroma adapter**

Set `STORAGE_SCHEMA_VERSION = "tracerag-chroma-schema-v1"`. Use the Task 1 collision-resistant safe identifier algorithm/constraints, always retain the complete original namespace, and keep all Chroma metadata calls private. Decode into internal validated models; never filter malformed records.

- [ ] **Step 4: Run GREEN**

```powershell
python -m pytest tests/test_chroma_vector_store.py -q -k "schema or metadata or namespace or persistence_root"
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/stores/chroma.py tests/test_chroma_vector_store.py
git commit -m "feat: encode chroma repository metadata"
```

---

### Task 10: Chroma External-vector Persistence and Restart

**Files:**
- Modify: `backend/embedding_vector_store/stores/chroma.py`
- Modify: `tests/test_chroma_vector_store.py`

**Interfaces:**
- Consumes: Task 9 store/configuration and `VectorRecord`.
- Produces: external-vector candidate writes and complete manifest/evidence round-trip after store reopen.

- [ ] **Step 1: Write RED temporary-directory integration tests**

Create a candidate containing exactly float32-representable, ordinary non-unit, negative, small-magnitude, and representative normalized-like vectors plus distinctive Unicode/CRLF source documents. Validate every provider vector first, then close the first client and read the candidate from a fresh process/store instance. Assert source content and synchronization metadata return exactly. Assert returned embeddings are finite, dimension-valid, component-wise float32 values read from Chroma and are stable across reopen; require exact original equality only for cases proven exactly representable. Explicitly prove that ordinary Python floats need not equal the returned values and that some Chroma-returned components may differ by a few float32 ULPs from the submitted projection. Install a fail-if-called Chroma embedding function sentinel and prove it is never invoked. Assert wrong dimensions, duplicate chunk IDs, count mismatch, malformed records, NaN/infinity, and a finite Python float whose float32 projection is non-finite all fail closed before mutation. Assert no metadata or sidecar/control file mirrors vector values.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_chroma_vector_store.py -q -k "external_vector or reopen or restart or manifest"
```

- [ ] **Step 3: Implement basic persistent record operations**

Use only the Task 1 verified external-embedding APIs and explicitly disable/omit Chroma embedding generation. After the provider-independent vector has passed finite/dimension validation, perform checked component-wise IEEE-754 binary32 projection before `collection.add`; reject projection overflow, and do not clip or normalize. Store `VectorRecord.content` as the exact Chroma document, the projected vector only in Chroma's embedding field, and encoded synchronization metadata without any vector copy. Read all manifest records with deterministic pagination/bounds appropriate to the verified API, return Chroma's persisted finite dimension-valid values, and validate completeness. Exact Python-float equality is not a manifest invariant.

- [ ] **Step 4: Run GREEN and capability regression**

```powershell
python -m pytest tests/test_chroma_vector_store.py tests/capability/test_module5_dependency_capabilities.py -q -k "external_vector or reopen or restart or manifest or float32 or representation or capability"
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/stores/chroma.py tests/test_chroma_vector_store.py
git commit -m "feat: persist chroma vector records"
```

---

### Task 11: Candidate Lifecycle and Durable Publication

**Files:**
- Modify: `backend/embedding_vector_store/stores/chroma.py`
- Modify: `tests/test_chroma_vector_store.py`

**Interfaces:**
- Consumes: Task 1 proven publication mechanism and Task 10 persistence.
- Produces: `inspect_active`, `begin_candidate`, `add_reused`, `add_embedded`, `validate_candidate`, `publish`, and `abort` with one durable commit point.

- [ ] **Step 1: Write RED lifecycle tests**

Cover never-indexed, active nonempty, and explicitly active empty states. Assert candidates are invisible to `inspect_active`; reuse produces a complete logical candidate without requiring core to copy vectors; validation checks namespace/identity/versions/schema/count/unique IDs/dimensions; publication atomically changes the durable active pointer; and reopen resolves only the published snapshot.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_chroma_vector_store.py -q -k "candidate or active or publish or empty_state"
```

- [ ] **Step 3: Implement the Task 1 proven mechanism**

Keep physical generation locators private. Make candidate handles unsearchable until `publish`. Treat durable active-pointer replacement as the sole commit point and validate the candidate immediately before it. `abort` removes or abandons only the candidate and never touches active state.

- [ ] **Step 4: Run GREEN**

```powershell
python -m pytest tests/test_chroma_vector_store.py -q -k "candidate or active or publish or empty_state"
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/stores/chroma.py tests/test_chroma_vector_store.py
git commit -m "feat: publish repository index generations"
```

---

### Task 12: Publication Failure and Crash Recovery

**Files:**
- Modify: `backend/embedding_vector_store/stores/chroma.py`
- Modify: `tests/test_chroma_vector_store.py`

**Interfaces:**
- Consumes: durable lifecycle from Task 11.
- Produces: fail-safe behavior and indeterminate-publication resolution across restart.

- [ ] **Step 1: Write failure-injection RED tests**

Inject candidate write failure, validation failure, failure/interruption immediately before pointer commit, interruption at the publication boundary, and restart with abandoned candidate. Prove old active remains authoritative unless the durable pointer proves the new complete candidate committed. Inject success followed by cleanup failure and prove new active remains authoritative. Reopen with obsolete old generations and prove timestamps/order never select them.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_chroma_vector_store.py -q -k "failure or crash or interrupt or orphan or cleanup"
```

- [ ] **Step 3: Implement deterministic outcome resolution**

On a publication exception, reread the durable pointer through the Task 1 verified mechanism: return the new snapshot only if it is provably committed and complete; otherwise raise `VectorStorePublicationError` with old active unchanged. Map corrupt/ambiguous states to `VectorStoreCorruptionError`. Make post-commit cleanup best effort with sanitized logging.

- [ ] **Step 4: Run GREEN and full store suite**

```powershell
python -m pytest tests/test_chroma_vector_store.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/stores/chroma.py tests/test_chroma_vector_store.py
git commit -m "test: prove fail-safe index publication"
```

---

### Task 13: Linear Synchronization Diff Classification

**Files:**
- Create: `backend/embedding_vector_store/synchronization.py`
- Create: `tests/test_semantic_synchronization.py`

**Interfaces:**
- Consumes: validated `ChunkInput`, `StoredRecord`, active identity/document version.
- Produces: internal frozen/slotted `SyncDiff(unchanged, new, updated, deleted, compatible)` and `_classify_chunks(current, stored, identity_compatible, document_compatible) -> SyncDiff`.

- [ ] **Step 1: Write RED classification tests**

Cover same ID/hash, same ID/different hash, current-only, stored-only, first index, reordered inputs, duplicate rejection upstream, identity mismatch, document-version mismatch, and mixed added/removed IDs during incompatibility. Instrument key lookup counts so classification is bounded by current plus stored counts and has no nested all-pairs comparison.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_semantic_synchronization.py -q -k "classif or diff or linear"
```

- [ ] **Step 3: Implement dictionary-based classification**

Create exactly one mapping per side keyed by `chunk_id`. When compatible, populate four categories by hash and set difference. When incompatible, reuse none; count current IDs absent from stored as inserted/new and all other current IDs as updated-for-reembedding while retaining stored-only deletions.

- [ ] **Step 4: Run GREEN**

```powershell
python -m pytest tests/test_semantic_synchronization.py -q -k "classif or diff or linear"
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/synchronization.py tests/test_semantic_synchronization.py
git commit -m "feat: classify semantic index changes"
```

---

### Task 14: Bounded Document Embedding Batches

**Files:**
- Modify: `backend/embedding_vector_store/synchronization.py`
- Modify: `tests/test_semantic_synchronization.py`

**Interfaces:**
- Consumes: `EmbeddingProvider.max_batch_size`, `build_embedding_document`, retry executor, vector batch validation.
- Produces: `_embed_chunk_inputs(inputs, provider, retry_policy) -> tuple[VectorRecord, ...]` preserving request order.

- [ ] **Step 1: Write RED batching tests**

For capacities 1, 2, and larger than input, assert exact batch sizes/call counts, no empty call, positional chunk/vector association, complete-batch validation before records are returned, retry of only typed transient/rate-limit calls, and aborting return on cardinality/dimension/NaN/infinity/oversize failures. Use content containing CRLF and trailing whitespace to prove no truncation or normalization.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_semantic_synchronization.py -q -k "batch or association or malformed"
```

- [ ] **Step 3: Implement bounded batching**

Slice immutable inputs by the validated provider capacity, render documents immediately before each request, execute through `run_with_embedding_retries`, validate the entire returned tuple, then construct ordered `VectorRecord` values with exact source evidence. Do not retain all embedding documents or create a vector matrix.

- [ ] **Step 4: Run GREEN**

```powershell
python -m pytest tests/test_semantic_synchronization.py tests/test_embedding_retry.py -q -k "batch or association or malformed or retry"
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/synchronization.py tests/test_semantic_synchronization.py
git commit -m "feat: batch semantic document embeddings"
```

---

### Task 15: Complete Candidate Synchronization Orchestration

**Files:**
- Modify: `backend/embedding_vector_store/synchronization.py`
- Modify: `tests/test_semantic_synchronization.py`

**Interfaces:**
- Consumes: canonical provider/store protocols, Tasks 4 and 13–14 helpers.
- Produces: `SemanticIndexer(...).synchronize(inventory) -> IndexSyncResult` for changed nonempty compatible indexes.

- [ ] **Step 1: Write RED orchestration tests**

Using recording fakes, assert exact order: validate inventory → inspect/validate active manifest → classify → begin candidate → add all reused logical records → embed only new/updated → add embedded → validate candidate → publish → return `SUCCESS`. Assert deleted records are absent, complete expected count is passed, and provider/write/validation/publication failures call `abort`, preserve searchable old active, raise typed errors, and never return a result.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_semantic_synchronization.py -q -k "orchestration or complete_candidate or preservation"
```

- [ ] **Step 3: Implement minimum orchestrator**

Validate dependencies eagerly in `__init__`; in `synchronize`, use one active snapshot and manifest, build the complete logical candidate, and treat publication as the only success commit point. Abort best effort on earlier failures without masking the primary typed error. Populate counters from `SyncDiff` and actual embeddings.

- [ ] **Step 4: Run GREEN and store contract tests**

```powershell
python -m pytest tests/test_semantic_synchronization.py tests/test_vector_store_contract.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/synchronization.py tests/test_semantic_synchronization.py
git commit -m "feat: synchronize semantic indexes"
```

---

### Task 16: No-op Synchronization

**Files:**
- Modify: `backend/embedding_vector_store/synchronization.py`
- Modify: `tests/test_semantic_synchronization.py`

**Interfaces:**
- Consumes: compatible active manifest and `SyncDiff`.
- Produces: `IndexSyncStatus.UNCHANGED` fast path.

- [ ] **Step 1: Write the non-negotiable RED no-op test**

Use a provider whose document/query methods raise and a store whose candidate/write/publish methods raise. Supply an equal identity, document version, chunk-ID set, and hashes. Assert provider calls, vector writes, candidate creations, and publications are all zero; `status is UNCHANGED`; `reused_chunks == total_chunks`; and every mutation counter is zero.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_semantic_synchronization.py::test_compatible_unchanged_sync_performs_no_provider_or_store_mutation -q
```

- [ ] **Step 3: Implement pre-candidate fast path**

Return `IndexSyncResult` immediately after validated classification when all changed/deleted groups are empty. Do not call `begin_candidate` or cleanup.

- [ ] **Step 4: Run GREEN and synchronization regression**

```powershell
python -m pytest tests/test_semantic_synchronization.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/synchronization.py tests/test_semantic_synchronization.py
git commit -m "feat: skip unchanged semantic synchronization"
```

---

### Task 17: Full Re-index on Compatibility Changes

**Files:**
- Modify: `backend/embedding_vector_store/synchronization.py`
- Modify: `tests/test_semantic_synchronization.py`

**Interfaces:**
- Consumes: exact identity equality and embedding-document version.
- Produces: complete forced re-embedding counters with no vector reuse.

- [ ] **Step 1: Write RED compatibility tests**

Test same IDs/hashes with a different provider, model, dimension, or compatibility version; a different document version; mixed retained/added/removed IDs under incompatibility; and first indexing. Assert `reused_chunks == 0`, `embedded_chunks == total_chunks`, current-only IDs count as inserted, other current IDs count as updated, stored-only IDs count as deleted, and both counter equations hold.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_semantic_synchronization.py -q -k "full_reindex or incompatible or first_index"
```

- [ ] **Step 3: Implement exact compatibility routing**

Reuse vectors only on exact `EmbeddingModelIdentity` equality and exact document version. Pass no stored records to `add_reused` on incompatibility and embed every current chunk.

- [ ] **Step 4: Run GREEN**

```powershell
python -m pytest tests/test_semantic_synchronization.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/synchronization.py tests/test_semantic_synchronization.py
git commit -m "feat: reindex incompatible embedding spaces"
```

---

### Task 18: Empty Repository Synchronization

**Files:**
- Modify: `backend/embedding_vector_store/synchronization.py`
- Modify: `tests/test_semantic_synchronization.py`

**Interfaces:**
- Consumes: valid zero-chunk inventory and active state.
- Produces: valid empty publication or compatible empty no-op.

- [ ] **Step 1: Write three exact RED cases**

Assert never indexed + empty publishes a zero-count candidate with `SUCCESS`; active nonempty + empty publishes a replacement with `deleted_chunks == old count` and `SUCCESS`; active compatible empty + empty returns `UNCHANGED`. All three use a fail-if-called provider and make zero document/query embedding calls.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_semantic_synchronization.py -q -k "empty_inventory"
```

- [ ] **Step 3: Implement empty state paths**

Allow `expected_chunk_count=0`, validate/publish explicit empty candidates for the first two cases, and reuse Task 16 no-op behavior for the third.

- [ ] **Step 4: Run GREEN**

```powershell
python -m pytest tests/test_semantic_synchronization.py tests/test_chroma_vector_store.py -q -k "empty"
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/synchronization.py tests/test_semantic_synchronization.py
git commit -m "feat: persist empty semantic indexes"
```

---

### Task 19: Semantic Search Request Validation

**Files:**
- Create: `backend/embedding_vector_store/search.py`
- Modify: `backend/embedding_vector_store/validation.py`
- Create: `tests/test_semantic_search.py`

**Interfaces:**
- Consumes: provider/store protocols and retry policy.
- Produces: `SemanticSearcher` constructor and `validate_search_request(repository_namespace, query_text, top_k, max_query_chars, max_top_k) -> None`.

- [ ] **Step 1: Write RED request/config tests**

Reject empty/non-string namespace; non-string, empty, whitespace-only, or over-16,384-character query; bool/non-int/zero/negative/over-limit `top_k`; and invalid constructor bounds. Accept an exactly 16,384-character non-whitespace query and `top_k` 1/100. Assert validation occurs before provider/store calls and query text is passed unchanged rather than stripped.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_semantic_search.py -q -k "request or config or bounds"
```

- [ ] **Step 3: Implement validation and constructor**

Use `max_query_chars=16_384` and `max_top_k=100`, both positive integer-but-not-bool. Raise `InvalidSearchRequest` with fixed messages. Store only provider, store, bounds, and validated retry policy.

- [ ] **Step 4: Run GREEN**

```powershell
python -m pytest tests/test_semantic_search.py -q -k "request or config or bounds"
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/search.py backend/embedding_vector_store/validation.py tests/test_semantic_search.py
git commit -m "feat: validate semantic search requests"
```

---

### Task 20: Query Embedding, Snapshot, and Identity Enforcement

**Files:**
- Modify: `backend/embedding_vector_store/search.py`
- Modify: `tests/test_semantic_search.py`

**Interfaces:**
- Consumes: `inspect_active`, active snapshot metadata, `EmbeddingProvider.embed_query`, vector validation.
- Produces: search flow through the store boundary before result normalization.

- [ ] **Step 1: Write RED flow tests**

Assert never indexed raises `RepositoryIndexNotFound` before query embedding; active valid empty returns `()` with zero query calls; incompatible identity raises `EmbeddingSpaceMismatch` before query embedding/store search; valid nonempty calls only `embed_query`, validates dimension/finite values, and passes the same resolved snapshot once to store search. Simulate publication between resolution and lookup and prove the original snapshot remains used.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_semantic_search.py -q -k "not_indexed or active_empty or identity or snapshot or query_embedding"
```

- [ ] **Step 3: Implement read-only orchestration**

Resolve one state, validate snapshot/schema/identity/count, short-circuit valid empty, execute `embed_query` through typed retry policy, validate the vector, then call `store.search(snapshot, query_vector, top_k)`. Never call document embedding or any mutating store method.

- [ ] **Step 4: Run GREEN**

```powershell
python -m pytest tests/test_semantic_search.py tests/test_embedding_retry.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/search.py tests/test_semantic_search.py
git commit -m "feat: query active semantic index snapshots"
```

---

### Task 21: Chroma Nearest-neighbor Search and Score Conversion

**Files:**
- Modify: `backend/embedding_vector_store/stores/chroma.py`
- Modify: `tests/test_chroma_vector_store.py`

**Interfaces:**
- Consumes: Task 1 verified metric, raw-distance semantics, score formula, active snapshot, query vector.
- Produces: `ChromaVectorStore.search(...) -> tuple[StoreSearchResult, ...]` with normalized scores.

- [ ] **Step 1: Write mathematical RED tests**

Use Task 1 synthetic vectors and Task 10 persisted-representation cases to assert exact/approximately justified formula results at identical, orthogonal, opposite, and representative intermediate distances. Prove search operates on Chroma's persisted embedding, not an original-vector mirror. Accept only finite cosine-distance boundary error within the gate-proven absolute `1e-6` tolerance, treat an in-tolerance value as the corresponding `0` or `2` endpoint, retain `score = 1 - distance / 2`, and reject values farther outside the domain. Assert higher score means closer. Prove the query addresses only the snapshot’s repository collection and requests at most `top_k`.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_chroma_vector_store.py -q -k "search or score or distance or metric"
```

- [ ] **Step 3: Implement the recorded formula**

Configure the verified metric at collection creation and convert raw distance with the exact formula and endpoint-tolerance rule recorded in the gate artifact. Decode complete record metadata and exact stored document. Do not expose raw distances, vectors, collection names, or private locators.

- [ ] **Step 4: Run GREEN and capability regression**

```powershell
python -m pytest tests/test_chroma_vector_store.py tests/capability/test_module5_dependency_capabilities.py -q -k "search or score or distance or metric"
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/stores/chroma.py tests/test_chroma_vector_store.py
git commit -m "feat: search chroma vector records"
```

---

### Task 22: Search Result Validation and Deterministic Ordering

**Files:**
- Modify: `backend/embedding_vector_store/validation.py`
- Modify: `backend/embedding_vector_store/search.py`
- Modify: `tests/test_semantic_search.py`

**Interfaces:**
- Consumes: `tuple[StoreSearchResult, ...]`, requested namespace/top-k.
- Produces: `validate_and_normalize_search_results(...) -> tuple[VectorSearchResult, ...]`.

- [ ] **Step 1: Write RED result tests**

Reject more than `top_k`, duplicate chunk IDs, wrong namespace, missing/malformed metadata/content/hashes, non-finite score, and score outside `[0,1]`. Assert no invalid record is silently filtered. For ties and case variants, assert exact key `(-score, relative_path.casefold(), relative_path, chunk_id)`. Assert output has exact source and no synthetic text, vector, distance, generation, or collection field.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_semantic_search.py -q -k "result or ordering or corrupt"
```

- [ ] **Step 3: Implement fail-closed normalization**

Validate the complete tuple before constructing results, use a set for uniqueness, compare complete namespace equality, instantiate public frozen models, and sort by the exact key. Raise `VectorStoreCorruptionError` for malformed store output.

- [ ] **Step 4: Run GREEN and full search suite**

```powershell
python -m pytest tests/test_semantic_search.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/validation.py backend/embedding_vector_store/search.py tests/test_semantic_search.py
git commit -m "feat: normalize semantic search evidence"
```

---

### Task 23: Explicit Repository Index Deletion

**Files:**
- Modify: `backend/embedding_vector_store/stores/chroma.py`
- Modify: `tests/test_chroma_vector_store.py`

**Interfaces:**
- Consumes: exact validated repository namespace.
- Produces: `delete_repository_index(repository_namespace: str) -> None` affecting only that logical index.

- [ ] **Step 1: Write RED deletion tests**

Index two namespaces, delete one by exact name, reopen, and prove the other remains searchable and unchanged. Reject empty namespace, substring/display-name deletion, derived-identifier collision mismatch, and global deletion. Assert source repository paths/files are never touched and obsolete/candidate collections for only the exact namespace follow Task 1 safe deletion semantics.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_chroma_vector_store.py -q -k "delete_repository_index"
```

- [ ] **Step 3: Implement exact-scope deletion**

Resolve control data by deterministic identifier, validate the complete stored namespace, remove its active pointer and adapter-owned collections in the Task 1 proven safe order, and map failures to typed store errors. Never accept patterns.

- [ ] **Step 4: Run GREEN**

```powershell
python -m pytest tests/test_chroma_vector_store.py -q -k "delete or isolation"
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/stores/chroma.py tests/test_chroma_vector_store.py
git commit -m "feat: delete repository semantic indexes"
```

---

### Task 24: Same-namespace Synchronization Guard and Concurrent Reads

**Files:**
- Modify: `backend/embedding_vector_store/stores/chroma.py`
- Modify: `backend/embedding_vector_store/synchronization.py`
- Modify: `tests/test_chroma_vector_store.py`
- Modify: `tests/test_semantic_synchronization.py`

**Interfaces:**
- Consumes: Task 1 writer/read capability result and `SemanticIndexer.synchronize`.
- Produces: one-writer-per-exact-namespace guard held from inspection through publication outcome; snapshot-stable concurrent search.

- [ ] **Step 1: Write deterministic concurrency RED tests**

Use barriers/events, not sleeps. Start one synchronization, block during candidate construction, and assert a second same-namespace writer receives the documented typed busy/configuration outcome while a different namespace follows the Task 1 supported behavior. Search during the block must use the old snapshot; search after publication uses the new snapshot. Add the Task 1-required explicit rejection/documentation test when multi-process mutation is not proven safe.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_chroma_vector_store.py tests/test_semantic_synchronization.py -q -k "concurr or writer or snapshot"
```

- [ ] **Step 3: Implement only proven local mechanics**

Acquire the exact-namespace writer guard before active inspection and release after publication resolution/abort. Do not block readers or create distributed locking. Reject unsupported multi-process writer configuration explicitly according to the gate artifact.

- [ ] **Step 4: Run GREEN repeatedly**

```powershell
1..5 | ForEach-Object { python -m pytest tests/test_chroma_vector_store.py tests/test_semantic_synchronization.py -q -k "concurr or writer or snapshot" }
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/stores/chroma.py backend/embedding_vector_store/synchronization.py tests/test_chroma_vector_store.py tests/test_semantic_synchronization.py
git commit -m "feat: guard repository index synchronization"
```

---

### Task 25: Security, Credential, and Logging Boundary

**Files:**
- Create: `tests/test_embedding_security.py`
- Modify only if a test exposes a violation: `backend/embedding_vector_store/**`

**Interfaces:**
- Consumes: complete provider/core/store implementation.
- Produces: regression evidence that secrets, source, vectors, and provider/storage details do not cross approved boundaries.

- [ ] **Step 1: Write RED security tests with recognizable sentinels**

Use fake secret `TRACERAG_TEST_SECRET_DO_NOT_LEAK`, unique source, vector, auth header, and raw response text. Assert none appears in identities, metadata, public results beyond the required exact `content`, repr of provider/config/errors, logs, or sanitized exceptions. Prove vector values exist only in Chroma's embedding field—not record metadata, active-pointer/control JSON, or another sidecar/manifest—and that manifest readback comes from that field. Patch repository `.env`/config reads, repository imports/execution, subprocess, shell, network outside the injected Gemini client, and Chroma embedding functions to raise if called. Assert persistence metadata contains no secret and ordinary tests cannot instantiate a real network client accidentally.

- [ ] **Step 2: Verify RED or record already-GREEN evidence**

```powershell
python -m pytest tests/test_embedding_security.py -q
```

Expected: any boundary leak fails with the sentinel. If all tests are already GREEN, make no speculative production edit.

- [ ] **Step 3: Fix only reproduced violations**

Replace raw exception/log interpolation with fixed messages, remove secret-bearing repr fields, and keep credentials solely in the Gemini adapter’s trusted client construction path. Preserve `VectorSearchResult.content` because exact source evidence is required; assert it is not logged.

- [ ] **Step 4: Run GREEN and provider/store regressions**

```powershell
python -m pytest tests/test_embedding_security.py tests/test_gemini_embedding_provider.py tests/test_chroma_vector_store.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store tests/test_embedding_security.py
git commit -m "test: lock embedding security boundary"
```

---

### Task 26: Structural Resource and Scaling Bounds

**Files:**
- Create: `tests/test_embedding_scaling.py`
- Modify only if a test exposes a violation: `backend/embedding_vector_store/synchronization.py` or `backend/embedding_vector_store/search.py`

**Interfaces:**
- Consumes: synchronization/search implementations and recording fakes.
- Produces: operation-count evidence for bounded batches, linear manifests, bounded top-k, bounded intermediates, and failure rather than truncation.

- [ ] **Step 1: Write operation-count RED tests**

Generate large synthetic manifests and count hash-map accesses, provider batch sizes/calls, candidate writes, and search `top_k`. Assert no provider call on no-op or active-empty search, no rendering/retention of all documents beyond the current batch, no repository-wide vector matrix in core, and an oversize document raises `EmbeddingDocumentTooLarge` while exact source remains unchanged and old active remains searchable.

- [ ] **Step 2: Verify behavior**

```powershell
python -m pytest tests/test_embedding_scaling.py -q
```

Expected: fails if operations are quadratic/unbounded or truncation occurs; otherwise stays GREEN without production changes.

- [ ] **Step 3: Make only structural corrections**

Replace nested comparisons with dictionaries, full-document accumulation with batch-local iteration, and unbounded requests with validated limits. Do not add timing thresholds, parallel embedding, streaming public APIs, caching, or vector-matrix helpers.

- [ ] **Step 4: Run GREEN and synchronization/search regression**

```powershell
python -m pytest tests/test_embedding_scaling.py tests/test_semantic_synchronization.py tests/test_semantic_search.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/synchronization.py backend/embedding_vector_store/search.py tests/test_embedding_scaling.py
git commit -m "test: verify embedding index scaling"
```

---

### Task 27: Real Module 4 to Module 5 Integration

**Files:**
- Create: `tests/test_embedding_integration.py`
- Modify only for reproduced Module 5 defects: `backend/embedding_vector_store/**`

**Interfaces:**
- Consumes: real `FileScanner -> CodeParser -> CodeChunker` output, deterministic fake provider, temporary Chroma store, semantic indexer/searcher.
- Produces: end-to-end offline evidence preserving Module 4 content/identity across persistence and search.

- [ ] **Step 1: Write cross-module RED integration tests**

Build temporary Python, Java, JavaScript, TypeScript, TSX, context, and fragmented inputs through Modules 2–4. Synchronize with a deterministic provider, reopen Chroma, search, and assert exact chunk IDs, hashes, paths, structural metadata, and byte-exact content round-trip. Separately assert returned stored vectors follow Task 10's Chroma-authoritative binary32 representation rather than provider Python-float equality. Repeat unchanged sync for zero calls; mutate source through the real pipeline for update/delete/add; synchronize empty; and prove two repositories remain isolated.

- [ ] **Step 2: Verify integration behavior**

```powershell
python -m pytest tests/test_embedding_integration.py -q -rs
```

Expected: RED only for an actual Module 5 integration gap; never change Module 4 semantics to fit Module 5.

- [ ] **Step 3: Correct only Module 5 integration defects**

Adjust validation/rendering/store orchestration while retaining exact Module 4 models, ordering, identities, hashes, and content. Do not reread source inside Module 5.

- [ ] **Step 4: Run GREEN and Modules 2–4 regression**

```powershell
python -m pytest tests/test_embedding_integration.py -q -rs
python -m pytest tests/test_file_scanner.py tests/test_code_parser_dependencies.py tests/test_code_parser.py tests/test_code_chunker.py -q -rs
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store tests/test_embedding_integration.py
git commit -m "test: integrate chunk inventory semantic indexing"
```

---

### Task 28: Optional Opt-in Real Gemini Integration Test

**Files:**
- Create: `tests/integration/test_gemini_embeddings_live.py`
- Modify: `pytest.ini`

**Interfaces:**
- Consumes: externally injected credential, Task 1 verified provider configuration, `GeminiEmbeddingProvider`.
- Produces: explicitly opt-in minimal live verification excluded from ordinary pytest selection.

- [ ] **Step 1: Write the guarded live test**

Register `gemini_live`. At module setup, skip unless both `TRACERAG_RUN_GEMINI_LIVE=1` and the Task 1 documented external credential variable are present. Make the smallest verified document and query requests, assert expected dimensions/finite values and distinct task modes, and capture logs to ensure no credential, input text, vector, or raw response appears.

- [ ] **Step 2: Prove ordinary suite performs zero live calls**

```powershell
Remove-Item Env:TRACERAG_RUN_GEMINI_LIVE -ErrorAction SilentlyContinue
python -m pytest tests/integration/test_gemini_embeddings_live.py -q -rs -m gemini_live
python -m pytest -q -m "not integration and not gemini_live"
```

Expected: live test skips and ordinary suite passes without network.

The original exact `-m "not gemini_live"` selector overrides, rather than combines
with, pytest.ini's marker filter and selects the existing networked GitHub-clone
integration test. Use the combined selector above (or default pytest selection)
for a fully offline run. Default addopts excludes both markers even when both
live guards are set; explicitly select `-m gemini_live` to exercise the guards
or intentionally execute the live check.

- [ ] **Step 3: Keep the live path minimal**

Use only `GeminiEmbeddingProvider` public operations; do not persist returned vectors, print responses, or add credential loading from repository files. Execution with real credentials is optional evidence and not a normal completion requirement.

- [ ] **Step 4: Run marker/config checks**

```powershell
python -m pytest --markers
python -m pytest tests/integration/test_gemini_embeddings_live.py -q -rs -m gemini_live
```

- [ ] **Step 5: Commit**

```powershell
git add pytest.ini tests/integration/test_gemini_embeddings_live.py
git commit -m "test: add opt-in gemini embedding check"
```

---

### Task 29: Public API, Dependency Boundary, and Documentation

**Files:**
- Create: `backend/embedding_vector_store/__init__.py`
- Modify: `backend/embedding_vector_store/providers/__init__.py`
- Modify: `backend/embedding_vector_store/stores/__init__.py`
- Modify: `tests/test_code_parser_dependencies.py`
- Modify: `README.md`
- Modify: `tests/test_embedding_models.py`

**Interfaces:**
- Consumes: all completed public Module 5 contracts.
- Produces: exact top-level exports and user-facing Module 5 configuration/usage/security documentation.

- [ ] **Step 1: Write RED export and boundary tests**

Assert top-level `__all__` exports public models, public exceptions, `EmbeddingProvider`, `GeminiEmbeddingProvider`, `VectorStore`, `ChromaVectorStore`, `SemanticIndexer`, and `SemanticSearcher`, but no internal snapshot/candidate/stored/search result, generation, collection, raw distance, or SDK/Chroma object. Extend AST import tests: core modules cannot import Gemini/Chroma; only their edge files can. Assert README documents external persistence/credentials, exact document version, no-op/empty behavior, fail-safe publication, search score contract, opt-in live test, and Modules 6–10 non-goals.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/test_embedding_models.py tests/test_code_parser_dependencies.py -q -k "export or embedding or readme or dependency"
```

- [ ] **Step 3: Implement the final public surface and docs**

Use explicit imports/`__all__`; do not wildcard-export adapter internals. Add concise README construction examples that use Task 1 verified non-secret model/config names but no credential value or repository `.env` loading. Document that multi-process writes are unsupported if Task 1 did not prove them.

- [ ] **Step 4: Run GREEN and compile**

```powershell
python -m pytest tests/test_embedding_models.py tests/test_code_parser_dependencies.py -q
python -m compileall -q backend tests
```

- [ ] **Step 5: Commit**

```powershell
git add backend/embedding_vector_store/__init__.py backend/embedding_vector_store/providers/__init__.py backend/embedding_vector_store/stores/__init__.py tests/test_embedding_models.py tests/test_code_parser_dependencies.py README.md
git commit -m "docs: document embedding vector store contracts"
```

---

### Task 30: Full Regression, Security Verification, and Independent Review

**Files:**
- Review: `backend/embedding_vector_store/**`, Module 5 tests, `requirements.txt`, `README.md`, capability artifact, and dependency-boundary tests.
- Modify only for reproduced Critical or Important findings: files in the approved Module 5/dependency/documentation scope.

**Interfaces:**
- Consumes: complete Module 5 branch and gate evidence.
- Produces: fresh verification evidence, independent review findings, focused fix commits, and a clean reviewable branch; no merge or push.

- [ ] **Step 1: Reconfirm the hard gate and focused Module 5 suites**

```powershell
Select-String -Path docs/superpowers/verification/module-5-dependency-capability-gate.md -Pattern "PASS"
python -m pytest tests/capability/test_module5_dependency_capabilities.py -q -rs
python -m pytest tests/test_embedding_models.py tests/test_embedding_documents.py tests/test_embedding_validation.py tests/test_embedding_providers.py tests/test_embedding_retry.py tests/test_gemini_embedding_provider.py tests/test_vector_store_contract.py tests/test_chroma_vector_store.py tests/test_semantic_synchronization.py tests/test_semantic_search.py tests/test_embedding_security.py tests/test_embedding_scaling.py tests/test_embedding_integration.py -q -rs
```

Expected: gate explicitly says `PASS`; all ordinary Module 5 tests pass; no real Gemini request occurs.

- [ ] **Step 2: Run every upstream and full regression command**

```powershell
python -m pytest tests/test_repository_loader.py -q
python -m pytest tests/test_file_scanner.py -q
python -m pytest tests/test_code_parser_dependencies.py tests/test_code_parser.py -q
python -m pytest tests/test_code_chunker.py -q
python -m pytest -q -m "not gemini_live" -rs
python -m compileall -q backend tests
python -m pip check
git diff --check
git status --short --branch
```

Expected: Modules 1–5 and full ordinary suite pass, compilation succeeds, dependencies are consistent, diff whitespace is clean, and only intentional uncommitted review fixes (if any) appear.

- [ ] **Step 3: Audit scope and critical invariants**

```powershell
git diff --name-only 0215f4095dba40cce8284775e0193cd44f8f9594..HEAD
git diff 0215f4095dba40cce8284775e0193cd44f8f9594..HEAD -- backend/repository_loader backend/file_scanner backend/code_parser backend/code_chunker
rg -n "PARTIAL_SUCCESS|generation_id|collection_name|chromadb|google\.genai|gemini|read_text|read_bytes|subprocess|eval\(|exec\(" backend/embedding_vector_store
rg -n "embedding_function" backend/embedding_vector_store tests
rg -n "embedding_values|vector_mirror|embedding_mirror" backend/embedding_vector_store tests
```

Expected: no Modules 1–4 production diff; changed files stay within approved Module 5, tests, exact dependency pins, README, and verification docs. Review every static match: provider imports occur only in `providers/gemini.py`, Chroma imports/physical details only in `stores/chroma.py`, no source reread/execution exists, Chroma automatic embedding is absent/disabled, no vector mirror exists, and no partial-success path exists.

- [ ] **Step 4: Verify evidence-specific invariants**

Rerun the exact named tests proving zero-call no-op, active-empty zero-call search, fail-safe pre-publication preservation, post-publication cleanup independence, exact source round-trip, credential non-leakage, and snapshot-stable concurrent search:

```powershell
python -m pytest tests/test_semantic_synchronization.py::test_compatible_unchanged_sync_performs_no_provider_or_store_mutation -q
python -m pytest tests/test_semantic_search.py -q -k "active_empty or snapshot"
python -m pytest tests/test_chroma_vector_store.py -q -k "failure or interrupt or cleanup or restart"
python -m pytest tests/test_embedding_integration.py -q -k "round_trip"
python -m pytest tests/test_embedding_security.py -q
```

- [ ] **Step 5: Request independent review**

Invoke `superpowers:requesting-code-review` over `0215f4095dba40cce8284775e0193cd44f8f9594..HEAD`. Require explicit findings on design coverage, dependency-gate evidence, adapter isolation, atomic publication, corruption behavior, identity/document compatibility, exact evidence, score proof, security, concurrency, scaling, ordinary offline behavior, and Modules 1–4 scope preservation.

- [ ] **Step 6: Resolve every Critical or Important finding with TDD**

For each finding: reproduce it with one focused failing test; run that test and capture RED; make the smallest in-scope fix; rerun the focused suite; then rerun all commands in Steps 1–4. Commit each coherent fix with a concrete message beginning `fix:` and naming the verified behavior, such as `fix: preserve active index on pointer failure`. Do not dismiss or fix by inspection alone.

- [ ] **Step 7: Record final branch evidence and stop**

```powershell
git status --short --branch
git log --oneline 0215f4095dba40cce8284775e0193cd44f8f9594..HEAD
git diff --check 0215f4095dba40cce8284775e0193cd44f8f9594..HEAD
```

Expected: clean feature branch with focused commits and no push. Stop for integration approval.

---

## Requirement Traceability

- Purpose, boundaries, architecture, exact Module 4 authority: Tasks 3–5, 15, 27, and 29.
- Public frozen/slotted models, counters, and typed errors: Task 2.
- Provider boundary and document/query distinction: Tasks 5 and 7.
- Exact V1 embedding document and no normalization/truncation: Tasks 3, 14, and 26.
- Embedding identity/compatibility and forced re-index: Tasks 2, 7, and 17.
- Provider vector/cardinality/finite validation and checked Chroma binary32 projection: Tasks 5, 10, 14, and 20.
- VectorStore opaque lifecycle contracts: Task 8.
- Chroma-only schema, namespace isolation, persistence, external vectors, and sole-authority binary32 representation: Tasks 9–10.
- Complete candidates, atomic publication, failure preservation, cleanup independence, and crash recovery: Tasks 11–12 and 15.
- Linear incremental synchronization, batching, no-op, and empty indexes: Tasks 13–18.
- Search validation, one snapshot, query identity, persisted-representation score semantics/tolerance, exact source results, and deterministic order: Tasks 19–22.
- Exact repository deletion: Task 23.
- One writer per namespace and concurrent readers: Task 24.
- Credential/logging/source-execution boundary: Task 25.
- Resource and scaling constraints: Task 26.
- Cross-module exact-evidence integration: Task 27.
- Optional live Gemini path and ordinary offline suite: Task 28.
- Public exports, README, dependency isolation, and deferred Modules 6–10: Task 29.
- Full regression, capability evidence, scope audit, and independent review: Task 30.

## Hard Stop Conditions

Stop Module 5 implementation after Task 1 with a committed `BLOCKED` report if any of these remains unproven for exact pins: supported Gemini document/query embeddings; stable dimensions/limits/error mapping; external Chroma embeddings without automatic embedding; checked finite binary32 projection and Chroma-authoritative readback across a fresh process; exact metric/distance/score semantics including bounded endpoint tolerance; reversible required metadata; durable unambiguous active-pointer publication; preservation of the old active index before commit; or the required single-writer/concurrent-reader deployment.

Do not substitute a weaker publication invariant, a guessed dependency fact, a second per-chunk manifest or vector mirror, a repository-local persistence default, silent truncation, result filtering, newest-generation inference, or partial success. `gemini-embedding-001-retrieval-3072-v1` continues to own provider/model/task/dimension compatibility, while `tracerag-chroma-schema-v1` solely owns the clarified store representation. Any further required architectural change returns to design review before production work resumes.
