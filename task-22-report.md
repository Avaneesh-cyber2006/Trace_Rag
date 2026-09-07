# Task 22 Report: Search Result Validation and Deterministic Ordering

## Provenance fix scope

The final Task 22 audit at `654bacb` found that semantic-search result
validation accepted any lowercase 64-character hexadecimal `content_hash`,
even when it did not identify the exact UTF-8 bytes in `content`. That allowed
syntactically valid but false source provenance into the public result model.

The validator now computes SHA-256 over `content.encode("utf-8")` after the
existing exact-string checks and requires the digest to equal `content_hash`.
This check remains in the complete validation pass before any public
`VectorSearchResult` is constructed, so a later corrupt record cannot yield a
partial result tuple. Existing rejection of string subclasses and hostile
primitive subclasses remains unchanged.

## RED evidence

The regression supplies one valid record followed by a nonempty exact-content
record whose `content_hash` is a valid-looking lowercase SHA-256 value for
different bytes:

```text
.venv\Scripts\python.exe -m pytest tests/test_semantic_search.py -q -k "valid_looking_mismatched_content_hash"
1 failed, 103 deselected
Failed: DID NOT RAISE VectorStoreCorruptionError
```

## GREEN evidence

Named provenance regression:

```text
.venv\Scripts\python.exe -m pytest tests/test_semantic_search.py -q -k "valid_looking_mismatched_content_hash"
1 passed, 103 deselected
```

Focused Task 22 result validation and ordering coverage:

```text
.venv\Scripts\python.exe -m pytest tests/test_semantic_search.py -q -k "result or ordering or corrupt"
41 passed, 63 deselected
```

Full semantic-search suite:

```text
.venv\Scripts\python.exe -m pytest tests/test_semantic_search.py -q
104 passed
```

Adjacent synchronization, boundary validation, model, and store-contract
tests:

```text
.venv\Scripts\python.exe -m pytest tests/test_semantic_synchronization.py tests/test_embedding_validation.py tests/test_embedding_models.py tests/test_vector_store_contract.py -q
199 passed
```

## Files changed

- `backend/embedding_vector_store/validation.py`: verifies exact-content digest
  provenance before public model construction.
- `tests/test_semantic_search.py`: keeps valid fixtures digest-consistent and
  adds the mismatched-content regression.
- `task-22-report.md`: records RED/GREEN and adjacent-suite evidence.

No Task 23 behavior was started.
