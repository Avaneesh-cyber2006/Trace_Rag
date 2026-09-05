# Task 13 Report: Linear Synchronization Diff Classification

## Scope

Implemented the provider-independent `SyncDiff` contract and `_classify_chunks`
helper in `backend/embedding_vector_store/synchronization.py`. Classification
builds exactly one `chunk_id` mapping for current inputs and one for stored
records, preserves input order, reuses only matching hashes in compatible
spaces, and forces all retained current chunks through re-embedding when the
identity or document version is incompatible. Stored-only records remain in
the deleted category. Duplicate chunk rejection remains owned by the existing
Module 5 inventory validator.

## RED evidence

Before the production module existed:

```text
\.venv\Scripts\python.exe -m pytest tests/test_semantic_synchronization.py -q -k "classif or diff or linear"
1 error during collection: ModuleNotFoundError: No module named 'backend.embedding_vector_store.synchronization'
```

## GREEN evidence

Focused Task 13 selector:

```text
\.venv\Scripts\python.exe -m pytest tests/test_semantic_synchronization.py -q -k "classif or diff or linear"
5 passed, 1 deselected
```

Full new synchronization file plus adjacent validation/store contracts:

```text
\.venv\Scripts\python.exe -m pytest tests/test_semantic_synchronization.py tests/test_embedding_validation.py tests/test_vector_store_contract.py -q
101 passed
```

`git diff --check` passed. Scope is limited to the Task 13 classifier,
focused tests, and this report; no provider, store, or prior-module behavior
was changed.
