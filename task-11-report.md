# Task 11 Report: Candidate Lifecycle and Durable Publication

## Scope and recovered state

Task 11 was resumed in worktree `workspace/module-5-embedding-vector-store` at
`ec22b06` (`docs: finalize revised vector capability gate`). The exhausted worker
left only uncommitted changes in `backend/embedding_vector_store/stores/chroma.py`
and `tests/test_chroma_vector_store.py`; no Task 11 report existed.

The recovered implementation already supplied the lifecycle methods, isolated
Chroma candidate collections, encoded complete records, and a checksum-protected
active-pointer file. The recovered lifecycle tests were green before this audit
(focused selector: 21 passed; full Chroma file: 65 passed). The audit found and
completed two contract gaps: active locators were not checked as adapter-owned
names, and `inspect_active` checked only control metadata/count rather than the
complete published manifest. The publication path now revalidates immediately
before the `os.replace` commit point.

## RED evidence available from the recovered partial state

At the recovered base, the production implementation did not yet expose the
Task 11 lifecycle methods (`inspect_active`, `begin_candidate`, `add_reused`,
`add_embedded`, `validate_candidate`, `publish`, or `abort`); only the earlier
private persistence helpers existed. The recovered Task 11 tests therefore
provided direct RED coverage for the missing API surface and lifecycle behavior.
The implementation was completed in-place rather than discarding the worker's
changes.

## Requirements covered

- Distinguishes never-indexed from an explicitly published empty generation.
- Keeps private candidate collections invisible to `inspect_active` and restart
  discovery until publication.
- Supports adapter-owned `add_reused` and `add_embedded` without core-side vector
  copying and prevalidates complete batches before candidate mutation.
- Validates namespace, embedding identity, document/schema versions, expected
  count, unique IDs, dimensions, finite values, exact metadata, and content.
- Publishes through canonical compact JSON, SHA-256 checksum, temporary-file
  flush plus `os.fsync`, then same-directory `os.replace` as the sole logical
  commit point; the candidate is revalidated immediately before replacement.
- Rejects noncanonical/checksum-invalid pointers, adapter-foreign locators, and
  missing target collections as typed corruption rather than `NOT_INDEXED`.
- Reopen resolves only the durable pointer, never collection ordering or newer
  abandoned candidates.
- `abort` removes only an unpublished candidate and leaves active authority
  untouched; published active candidates are retained.
- Vector values remain solely in Chroma's embedding field; no metadata vector
  mirror or per-chunk sidecar was introduced.

## GREEN evidence

Focused lifecycle selector:

```text
python -m pytest tests/test_chroma_vector_store.py -q -k "candidate or active or publish or empty_state"
27 passed, 44 deselected
```

Required Task 11 regression:

```text
python -m pytest tests/test_chroma_vector_store.py tests/capability/test_module5_dependency_capabilities.py tests/test_vector_store_contract.py -q
128 passed, 1 warning
```

The single warning is ChromaDB's existing `DeprecationWarning` for its legacy
embedding-function configuration loader; all Task 1 capability assertions pass.

Additional audit result: `git diff --check` passed.

## Files changed

- `backend/embedding_vector_store/stores/chroma.py`: durable pointer validation,
  complete active-manifest validation, immediate pre-commit candidate
  revalidation, and lifecycle implementation completion.
- `tests/test_chroma_vector_store.py`: lifecycle, corruption, prevalidation,
  no-mirror, and publication-boundary regression coverage.
- `task-11-report.md`: this evidence report.

## Concerns and deferred scope

Candidate/obsolete collection cleanup remains maintenance work for Task 12 and
is intentionally not part of publication correctness. The capability gate's
single-process/local-persistence-root concurrency limitation remains unchanged.
No Gemini request, credential, source content outside synthetic fixtures, or
Task 12 failure-resolution behavior was added.
