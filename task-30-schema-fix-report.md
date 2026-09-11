# Task 30: Replaceable-store schema ownership fix

Base revision: `13c3f02` (`fix: report committed index publication`).

## Finding and scope

The approved replaceable-adapter architecture makes Chroma storage representation
an edge-adapter concern. Task 20 requires snapshot/schema validation, but does not
make Chroma's schema identifier a universal store identifier. The neutral
`RepositoryIndexSnapshot` contract already accepts nonempty adapter-owned schema
names, and synchronization publishes such snapshots. `SemanticSearcher` instead
required `tracerag-chroma-schema-v1`, rejecting valid alternative-store snapshots.

The regression runs `SemanticIndexer.synchronize()` followed by
`SemanticSearcher.search()` through the existing protocol-compatible recording
store using `schema-v1`. Both empty and nonempty publication cases failed before
the production fix with `VectorStoreCorruptionError` at `_resolve_snapshot`.

## Change

- Core search validates that the schema name is a nonempty string, without
  importing Chroma or assuming its schema identifier. Existing state, namespace,
  embedding identity, document version, and expected-count checks remain.
- `VectorStore.acquire_active` now explicitly requires adapters to reject
  unsupported/corrupt storage schemas before yielding, including empty snapshots.
  Chroma already enforces this through its pointer and collection metadata readers.
- Search tests use a neutral schema identifier and retain corruption tests for
  forged snapshots with empty, null, and numeric schema values.
- Added publication-to-search coverage for empty and nonempty alternative-store
  snapshots, checking empty embedding avoidance and exact nonempty public content.
- Added real Chroma acquisition tests that tamper collection schema metadata for
  empty and nonempty indexes and assert rejection before the context yields.

No public signatures, Chroma schema format, Chroma production code, dependency
pins, Modules 1–4 production code, or plan selectors changed.

## Verification

All commands ran in the Module 5 worktree with:
`C:/Users/avane/AppData/Local/Temp/tracerag-module5-gate-20260903-01/Scripts/python.exe`.

1. RED: `-m pytest tests/test_semantic_synchronization.py -q -k index_to_search_accepts_adapter_owned_schema`
   produced **2 failed, 41 deselected**. Both failures were the reproduced schema
   rejection after successful publication.
2. Initial GREEN: `-m pytest tests/test_semantic_search.py tests/test_semantic_synchronization.py tests/test_vector_store_contract.py -q`
   produced **184 passed**.
3. Broad verification selected semantic search, synchronization, store contracts,
   Chroma, integrity/deletion, dependency boundaries, and offline integration:

   ```powershell
   & C:/Users/avane/AppData/Local/Temp/tracerag-module5-gate-20260903-01/Scripts/python.exe -m pytest tests/test_semantic_search.py tests/test_semantic_synchronization.py tests/test_vector_store_contract.py tests/test_chroma_vector_store.py tests/test_chroma_integrity_deletion.py tests/test_code_parser_dependencies.py tests/test_embedding_integration.py -q -rs --basetemp=C:/Users/avane/AppData/Local/Temp/task30-schema-fix-repeat-20260911
   ```

   Final result: **389 passed, 1 skipped in 35.14s**. The skip requires POSIX fork
   and is expected on Windows.

   The first broad run (same selection, initial temporary directory
   `task30-schema-fix-20260911`) produced **388 passed, 1 skipped, 1 failed**.
   `test_delete_repository_index_is_busy_while_obsolete_reader_is_leased` raised
   `VectorStoreReadError` from a Chroma `collection.get` during synchronization.
   Its isolated rerun in `task30-schema-fix-delete-isolated-20260911` passed
   (**1 passed in 1.72s**); the complete fresh-directory repeat above also passed.
   The initial read failure's cause was not established; no unrelated deletion
   or concurrency changes were made.
4. `git diff --check` passed. A production source search found the Chroma schema
   literal and constant only in `stores/chroma.py`.

The requested schema defect is covered by observed RED/GREEN behavior. This
report records the scoped fix and its selected-suite verification; it does not
claim completion of the overall independent Task 30 review.
