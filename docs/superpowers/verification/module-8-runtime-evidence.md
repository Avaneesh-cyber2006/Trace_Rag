# Module 8 — Runtime Evidence Verification

## Status

DRAFT — Task 23 evidence collection complete; Task 24 independent implementation audit pending. This document does not claim a final PASS in this state.

## Environment

| Field | Value |
|---|---|
| Date (UTC) | 2026-11-01 (full-suite start `2026-11-01T04:46:45Z`) |
| Worktree | `C:\Users\avane\Desktop\Studies\Projects\Trace_Rag_Module8_Worktree` |
| Branch | `codex/module-8-runtime-evidence` |
| Base/spec commit | `9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa` |
| Plan commit | `0e8bb23311b5f7a2fcf1e9a1d673e5dccd2b7941` |
| Plan commit parent | `9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa` |
| Upstream baseline | `6f25511af8fa1615342a6be3083b09ac3e73d4be` |
| Python | `Python 3.12.14` |
| pytest | `pytest 9.1.1` |
| OS | `Microsoft Windows [Version 10.0.26300.9818]` |
| `PYTHONPATH` | `C:\Users\avane\Desktop\Studies\Projects\Trace_Rag_Module8_Worktree` |
| Basetemp | `C:\Users\avane\AppData\Local\Temp\trace-rag-module8-full-1da5f2b079ca4744b47d13c5a280f637` |
| Marker filter | `not integration and not gemini_live` |

## Scope and changed paths

Expected implementation paths:

- `docs/superpowers/plans/2026-10-22-runtime-evidence.md`
- `backend/runtime_evidence/__init__.py`
- `backend/runtime_evidence/association.py`
- `backend/runtime_evidence/config.py`
- `backend/runtime_evidence/drafts.py`
- `backend/runtime_evidence/exceptions.py`
- `backend/runtime_evidence/identities.py`
- `backend/runtime_evidence/indexes.py`
- `backend/runtime_evidence/ingestor.py`
- `backend/runtime_evidence/json_adapter.py`
- `backend/runtime_evidence/json_scanner.py`
- `backend/runtime_evidence/lines.py`
- `backend/runtime_evidence/models.py`
- `backend/runtime_evidence/source_references.py`
- `backend/runtime_evidence/stack_parser.py`
- `backend/runtime_evidence/text_adapter.py`
- `backend/runtime_evidence/validation.py`
- `tests/test_runtime_evidence_models.py`
- `tests/test_runtime_evidence_validation.py`
- `tests/test_runtime_evidence_identities.py`
- `tests/test_runtime_evidence_text.py`
- `tests/test_runtime_evidence_json.py`
- `tests/test_runtime_evidence_stack.py`
- `tests/test_runtime_evidence_association.py`
- `tests/test_runtime_evidence_determinism.py`
- `tests/test_runtime_evidence_security.py`
- `tests/test_runtime_evidence_integration.py`
- `tests/test_runtime_evidence_scaling.py`
- `README.md`
- `docs/superpowers/verification/module-8-runtime-evidence.md`

The committed branch range `9803be4..HEAD` at the start of Task 23 was exactly the 28 implementation and plan paths above, with zero merge commits. The README and this verification record are the only Task 23 tracked additions.

Exact `git diff --cached --name-only 9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa` output after staging both documentation paths (must equal the post-commit `<base>..HEAD` output):

```text
README.md
backend/runtime_evidence/__init__.py
backend/runtime_evidence/association.py
backend/runtime_evidence/config.py
backend/runtime_evidence/drafts.py
backend/runtime_evidence/exceptions.py
backend/runtime_evidence/identities.py
backend/runtime_evidence/indexes.py
backend/runtime_evidence/ingestor.py
backend/runtime_evidence/json_adapter.py
backend/runtime_evidence/json_scanner.py
backend/runtime_evidence/lines.py
backend/runtime_evidence/models.py
backend/runtime_evidence/source_references.py
backend/runtime_evidence/stack_parser.py
backend/runtime_evidence/text_adapter.py
backend/runtime_evidence/validation.py
docs/superpowers/plans/2026-10-22-runtime-evidence.md
docs/superpowers/verification/module-8-runtime-evidence.md
tests/test_runtime_evidence_association.py
tests/test_runtime_evidence_determinism.py
tests/test_runtime_evidence_identities.py
tests/test_runtime_evidence_integration.py
tests/test_runtime_evidence_json.py
tests/test_runtime_evidence_models.py
tests/test_runtime_evidence_scaling.py
tests/test_runtime_evidence_security.py
tests/test_runtime_evidence_stack.py
tests/test_runtime_evidence_text.py
tests/test_runtime_evidence_validation.py
```

Forbidden diff check: `git diff --name-only 9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa..HEAD` returned exactly the 28 authorized implementation/plan paths above; no `backend/code_graph`, `backend/code_parser`, `backend/file_scanner`, `backend/code_chunker`, `backend/embedding_vector_store`, `backend/git_history`, `backend/repository_loader`, `requirements.txt`, or specification path changed. `git diff --name-only 9803be4..HEAD -- docs/superpowers/specs` and `-- requirements.txt` were both empty.

README verification:

- Worktree `README.md`: 47,898 bytes, SHA-256 `2C5E9E8A2026BB1B02D7A69F5BE77EF85C8DEED2C60D5EA27B9BAF5D5FD1EDA5`.
- Original 39,610-byte prefix: SHA-256 `38F5F7966607DD665D0FA09609A8F215BA8E62F7EFF364E9053623ECD3AE1536`; LF-normalized Git blob `31f31ab8b9462ed4f32e038a54b7a16ee39336aa` equals `HEAD:README.md`.
- `git diff --numstat 9803be4 -- README.md`: `210 0 README.md` (zero deletions).
- One `## Module 8 — Runtime Evidence` heading; all 11 required subheadings present; all 21 configuration defaults present, including `retain_event_text` = `True`; both focused and offline test commands present; bytes-in, no-sandbox, no-redaction, non-causal, and no-collection statements present.
- The append begins with one blank separator line before the literal 209-line block (210 inserted lines total). No original byte was changed.
- Plan/README assertion mismatch: the plan's Step 2 regex expects `does **not redact**`, while the literal README block says `**does not redact**`. The literal README block was treated as authoritative; an equivalent literal check (`**does not redact**`) passes. The README was not edited to satisfy the inconsistent regex.

## Task commits and focused test evidence

Task 1–6 task-time RED transcripts were not retained in the current workspace ledger; the plan-required RED observations and the combined T1–T6 baseline are recorded below without inventing a historical count. Tasks 7–22 use the task-time ledger/report values.

| Task | Commit | Focused selector | RED observation | GREEN result |
|---|---|---|---|---|
| T1 | `d7764ad` | `tests/test_runtime_evidence_models.py` | Plan-required `test_all_fatal_errors_descend_from_runtime_evidence_error` AssertionError; task-time transcript not retained. | T1–T6 combined checkpoint baseline: 484 passed, exit 0. |
| T2 | `b4e83f9` | `tests/test_runtime_evidence_models.py tests/test_runtime_evidence_validation.py` | Plan-required defaults/booleans/surrogates/truncation assertion failures; task-time transcript not retained. | T1–T6 combined checkpoint baseline: 484 passed, exit 0. |
| T3 | `10f897e` | `tests/test_runtime_evidence_models.py` | Plan-required enum/default/validation assertion failures; task-time transcript not retained. | T1–T6 combined checkpoint baseline: 484 passed, exit 0. |
| T4 | `9259f73` | `tests/test_runtime_evidence_models.py` | Plan-required issue-mapping and invariant assertion failures; task-time transcript not retained. | T1–T6 combined checkpoint baseline: 484 passed, exit 0. |
| T5 | `b8e2d58` | `tests/test_runtime_evidence_models.py` | Plan-required mapping/type/counter assertion failures; task-time transcript not retained. | T1–T6 combined checkpoint baseline: 484 passed, exit 0. |
| T6 | `51fc9a3` | `tests/test_runtime_evidence_identities.py` | Plan-required empty-string digest assertion failures; task-time transcript not retained. | T1–T6 combined checkpoint baseline: 484 passed, exit 0. |
| T7 | `5a8339e` | `tests/test_runtime_evidence_text.py` | `test_split_removes_one_bom_from_first_line_only`: `AssertionError: assert [] == [...]`; 1 failed, exit 1. | 20 focused passed; 484 neighboring passed. |
| T8 | `7477b6d` | `tests/test_runtime_evidence_stack.py` | Plan-required empty-parser-result assertion failures; task-time transcript not retained. | 46 focused passed; 550 combined runtime-evidence passed. |
| T9 | `1c1767e` | `tests/test_runtime_evidence_json.py` | Plan-required structural/accounting assertion failures; task-time transcript not retained. | 105 focused passed; 655 combined runtime-evidence passed. |
| T10 | `b627932` (fix `1d105cc`) | `tests/test_runtime_evidence_json.py` | `test_log_event_is_normalized_not_empty`, exit 1; fix RED 2 failed/1 passed. | 219 focused passed; post-fix JSON 222 passed. |
| T11 | `7596bb6` | `tests/test_runtime_evidence_json.py` | `test_single_line_object_is_document_mode_with_no_line_provenance`, exit 1. | 273 JSON passed; full Module 8 823 passed. |
| T12 | `a83cc1f` (fix `c63687b`) | `tests/test_runtime_evidence_stack.py` | `test_one_block_is_one_error_event_with_source_line`, exit 1; fix RED 3 failed/71 passed. | 71 focused passed; post-fix 74 passed; full Module 8 851 passed. |
| T13 | `52932ef` | `tests/test_runtime_evidence_association.py` | `test_normalization_matrix[pkg/app.py-relative-pkg/app.py-components1]`, exit 1. | 88 focused passed; 939 neighboring passed. |
| T14 | `fa5f81e` | `tests/test_runtime_evidence_association.py` | `test_indexes_exact_file_and_symbol_buckets`, `KeyError: 'pkg/main.py'`, exit 1. | 177 focused passed; full Module 8 1,028 passed. |
| T15 | `ac8fc4c` | `tests/test_runtime_evidence_association.py` | `test_no_graph_relative_candidate_is_unverified_and_has_hash` (`UNMATCHED` vs `UNVERIFIED_RELATIVE_PATH`), exit 1. | 205 focused passed; full Module 8 1,056 passed. |
| T16 | `610c40f` | six-file validation/models/text/json/stack/association selector | `1 failed, 19 passed`: first named `test_submitted_artifact_count_envelope_is_exact_and_fatal_one_over` (`0 == 2`); six-file `42 failed, 989 passed`, exit 1. | 1,034 six-file passed; full Module 8 1,102 passed after fix. |
| T17 | `4dbe59b` | `tests/test_runtime_evidence_validation.py tests/test_runtime_evidence_json.py tests/test_runtime_evidence_stack.py` | Event budget `9 failed, 4 passed, 326 deselected`; frame budget `7 failed, 1 passed, 416 deselected`; exit 1. | 435 focused passed; full Module 8 1,132 passed. |
| T18 | `745b279` | `tests/test_runtime_evidence_models.py tests/test_runtime_evidence_security.py` | 28 failed, 403 passed, exit 1 (export/convenience/logging/sanitization assertions). | 432 focused passed; full Module 8 1,164 passed. |
| T19 | `3072723` | `tests/test_runtime_evidence_determinism.py tests/test_runtime_evidence_identities.py` | First run 1 failed, 93 passed on the brief's `duplicate_artifacts == 1`; corrected to 3 under design §7.3/§8.3, no production change. | 94 passed; full Module 8 1,190 passed. |
| T20 | `6d61517` | `tests/test_runtime_evidence_models.py tests/test_runtime_evidence_security.py` | New audit test `test_source_audit_classifies_absolute_from_imports` failed before the helper fix; no fabricated production defect. | 451 model/security passed; 1,209 full Module 8 passed. |
| T21 | `222ed05` (fix `5ff2d7a`) | `tests/test_runtime_evidence_integration.py` | New tests passed immediately; controlled `EXACT_RELATIVE_PATH`→`UNIQUE_PATH_SUFFIX` mutation produced 3 failed/3 passed, exit 1; I1 walker correction then 7 passed. | 7 integration passed; full Module 8 1,216 passed. |
| T22 | `13d76a8` (remediation `71a46f2`) | `tests/test_runtime_evidence_scaling.py` | Tests passed immediately; six controlled mutations produced assertion failures (scanner growth 149,404 vs 114,516; over-budget decoder calls 1 vs 0; JSONL 11 vs 10; basename global iterations 20 vs 0; all-pairs symbol yields 100,000 vs 2,010); audit remediation added direct and growth gates. | 14 focused passed; full Module 8 1,230 passed. |
| T23 | this commit | full Module 8 + Module 1–7 + full offline suite | documentation/regression only | Module 8 1,230 passed; Module 1–7 retained results as tabulated (Module 5 E1 retained); full offline suite 3,795 passed, 5 skipped, 2 deselected, exit 0. |
| T24 | final evidence commit | independent audit and final rerun | not applicable (audit task) | not run; Task 24 is not authorized in this state |

Additional commit in the branch range: `10e4447` (`fix: redact runtime evidence model representations`, two files, +354) is the privacy correction between T6 and T7; it is included in the 28-path committed range.

## Test inventory

| File | Collected | Result |
|---|---:|---|
| `tests/test_runtime_evidence_models.py` | 417 | 417 passed |
| `tests/test_runtime_evidence_validation.py` | 77 | 77 passed |
| `tests/test_runtime_evidence_identities.py` | 71 | 71 passed |
| `tests/test_runtime_evidence_text.py` | 23 | 23 passed |
| `tests/test_runtime_evidence_json.py` | 279 | 279 passed |
| `tests/test_runtime_evidence_stack.py` | 79 | 79 passed |
| `tests/test_runtime_evidence_association.py` | 206 | 206 passed |
| `tests/test_runtime_evidence_determinism.py` | 23 | 23 passed |
| `tests/test_runtime_evidence_security.py` | 34 | 34 passed |
| `tests/test_runtime_evidence_integration.py` | 7 | 7 passed |
| `tests/test_runtime_evidence_scaling.py` | 14 | 14 passed |
| **Total** | **1,230** | **1,230 passed** |

Collection: `1230/3802 tests collected (2572 deselected) in 2.28s`.

## Module 8 focused suite

Command:

```powershell
$env:PYTHON = "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe"
$env:PYTHONPATH = (Resolve-Path .).Path
$env:RT_BASETEMP = Join-Path $env:TEMP ("trace-rag-module8-focused-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $env:RT_BASETEMP -Force | Out-Null
& $env:PYTHON -m pytest tests -q -k runtime_evidence -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
```

Observed: `1230 passed, 2572 deselected in 4.07s` (retained `task23/focused.log`). The retained log does not print an exit code; the Task 23 ledger records exit 0. No skips and no warnings were reported in the focused run.

The four evidence groups were run separately with fresh basetemps: determinism 23 passed; security 34 passed; integration 7 passed; scaling 14 passed. A fresh read-only T23 rerun of `tests/test_runtime_evidence_identities.py` produced 71 passed and `tests/test_runtime_evidence_security.py` produced 34 passed, both exit 0.

## Determinism, association, and privacy evidence

- Repeated-run equal-content: PASS (`tests/test_runtime_evidence_determinism.py`, 23 passed).
- Artifact permutation invariance: PASS (determinism suite).
- Input-order preservation: PASS (determinism suite).
- Timestamp opacity: PASS (determinism suite).
- Fingerprint grouping/occurrence preservation: PASS (`tests/test_runtime_evidence_identities.py`, 71 passed).
- Issue-limit sentinel ordering/copy: PASS (determinism/validation suites; 435 focused budget tests).
- Real Module 2 → 3 → 6 exact symbol association: PASS (`tests/test_runtime_evidence_integration.py`, 7 passed).
- FILE-only symbol-free association: PASS (integration suite).
- Namespace mismatch/fail-closed: PASS (integration suite).
- No source reread/graph mutation: PASS (integration suite).
- Metadata-only projection before truncation/fingerprint: PASS (models/security/integration suites).
- Redacted reprs/log/errors/runtime no-I/O guard: PASS (`tests/test_runtime_evidence_security.py`, 34 passed).

Six pinned identity vectors remain exact: content SHA-256 `5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03`; artifact `09f8483cbc155fd3e52bf5d82de4af300c99ffac76212b1f2f73f739c7f89993`; event `c34329c46e8733344d378749ce8f3244b367a9be403f4b8b1b8f321eabbf93ec`; frame `8abaeb036ef874fb9c03e109cc7883ddf0ec09d9cb6fc846448d27f264a4010d`; source hash `815183491aa22012907fbd76531faaeaab8c5b829f7206b49c7838928994a0a9`; fingerprint `af9aeed9c7f817b89c5caa7a062d591875a091907b93fa8aa0ab8b02d024ef82`.

## Scaling and resource evidence

Record only deterministic counts; no wall-clock timing.

- JSON structural scan line counts (32/64/128 fields): **4,640 / 9,376 / 19,269**; node counts **65 / 129 / 257**.
- Document `json.loads` calls (valid/over-budget): **1 / 0**.
- JSONL per-line `json.loads` calls: **10 for 10 lines**.
- Association `files_by_path`/`files_by_basename` iteration counts: **`by_path.iter=0`, `by_base.iter=0`, `by_base.get=20`, `by_file.get=1`**; exactly one `match_symbols_by_line` call.
- `match_symbols_by_line` yielded-symbol counts: **`iter=1`, `yield=1,000`** for 1,000 symbols and 100 frame lines; growth gate at 200 vs 400 symbols/lines passed at production ratio approximately **1.998x** (mutant 3.94x failed).
- Event/frame/issue budget counters/flags/sentinel: event budget **10 events / one `EVENT_LIMIT_REACHED` / PARTIAL**; frame budget **5 frames ordinals 0–4 per event**, artifact/event/inventory `frames_truncated=True`, one `FRAME_LIMIT_REACHED`; issue budget **5 ordinary + 1 sentinel**, `issues_truncated=True`, 6 artifact issues, provenance lines 1–6.
- Output counts at 20/40 records: **20/40 events for 20/40 lines**.
- Index/tuple immutability: five index mappings are `MappingProxyType`; four record tuples are exact tuples; assignment raises `FrozenInstanceError`; inventory has no `__dict__`.
- No retained bytes: raw-byte leaves **0**.
- Mutation-check outputs (restored immediately): scanner prefix rescan `149,404 <= 3*38,172` failed; over-budget document materialization `[1] == []` failed; double materialization `[1,1] == [1]` failed; whole-JSONL materialization `11 == 10` failed; basename global scan `20 == 0` failed; all-pairs line sweep `100,000 <= 2,010` failed. All six were reversed byte-for-byte; production hashes were restored.

## Module 1–7 regression evidence

| Module | Selector | Result |
|---|---|---|
| 1 | `tests/test_repository_loader.py` | 48 passed, 1 deselected |
| 2 | `tests/test_file_scanner.py` | 178 passed, 2 skipped |
| 3 | `tests/test_code_parser.py tests/test_code_parser_dependencies.py` | 214 passed, 1 skipped |
| 4 | `tests/test_code_chunker.py` | 143 passed |
| 5 | `tests -q -k "embedding or semantic or vector_store or chroma"` | 1 failed, 1,176 passed, 1 skipped, 2,624 deselected (E1; exit 1) |
| 6 | `tests -q -k code_graph` | 590 passed, 3,212 deselected |
| 7 | `tests -q -k git_history` | 178 passed, 1 skipped, 3,623 deselected |

These counts are from the retained Task 23 logs. Each command used a fresh external basetemp, `PYTHONPATH` to the Module 8 worktree, `-p no:cacheprovider`, and `-m "not integration and not gemini_live"`. The retained logs do not print exit codes; the Task 23 ledger records exit 0 for Modules 1–4, 6, and 7 and exit 1 for the Module 5 selector. The Module 5 failure is retained, not rerun for a favorable result.

## Full offline suite attempt

Command:

```powershell
$env:PYTHON = "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe"
$env:PYTHONPATH = (Resolve-Path .).Path
$env:RT_BASETEMP = Join-Path $env:TEMP ("trace-rag-module8-full-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $env:RT_BASETEMP -Force | Out-Null
& $env:PYTHON -m pytest tests -q -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
```

Observed summary:

```text
UTC_START=2026-11-01T04:46:45.8404562Z
UTC_END=2026-11-01T04:55:54.4642230Z
3795 passed, 5 skipped, 2 deselected, 1 warning in 547.23s (0:09:07)
EXIT_CODE=0
```

Failure set:

```text
None. No FAILED or ERROR node was reported.
```

The single warning is an upstream `DeprecationWarning: legacy embedding function config` from `chromadb/api/models/CollectionCommon.py:155`, emitted by `tests/capability/test_module5_dependency_capabilities.py::test_external_embeddings_persist_and_reopen_without_embedding_function`. The five skips are the retained Windows/POSIX skip reasons: POSIX fork unavailable (1), symlink creation unavailable (3), and Windows-illegal filename characters (1).

Baseline failure set: the six historical Chroma observations recorded in `docs/superpowers/verification/module-7-git-history-analyzer.md`, plus the two explicitly authorized Task 23 external-exception nodes E1 and E2. The original six retain their original requirements; E1 and E2 are not a general waiver. None of these eight observations appeared in this full-suite attempt.

Difference: None. The full offline suite produced no failures, so the authorized E1/E2 exception was not applied to this run. The separate retained Module 5 selector result (E1) is unchanged and is recorded below.

## Module 5 Chroma isolation

- Baseline failing node IDs (six historical observations, copied from `module-7-git-history-analyzer.md`):
  - `tests/test_chroma_vector_store.py::test_publication_requires_readable_unambiguous_pointer_after_replace[unreadable-True]`
  - `tests/test_embedding_scaling.py::test_real_chroma_candidate_id_work_is_linear[True-257]`
  - `tests/test_chroma_vector_store.py::test_committed_publication_survives_backend_read_outage[False-False]`
  - `tests/test_chroma_vector_store.py::test_publication_requires_readable_unambiguous_pointer_after_replace[third-False]`
  - `tests/test_chroma_integrity_deletion.py::test_delete_repository_index_removes_incomplete_abandoned_candidates[True-1]` (aggregate observation)
  - `tests/test_chroma_vector_store.py::test_publication_requires_readable_unambiguous_pointer_after_replace[checksum-True]` (diagnostic observation)
- Baseline signature: `VectorStoreReadError: Vector store data could not be read.` at `backend/embedding_vector_store/stores/chroma.py:1255`, wrapping `chromadb.errors.InternalError: Error executing plan: Internal error: Error creating hnsw segment reader: Nothing found on disk` for directly captured instances.
- Current failing node ID(s) from the retained Task 23 Module 5 selector: `tests/test_chroma_vector_store.py::test_restart_ignores_abandoned_candidate_after_precommit_interruption` (E1).
- Current signature: `backend.embedding_vector_store.exceptions.VectorStoreReadError: Vector store data could not be read.` at `backend/embedding_vector_store/stores/chroma.py:1255`; path `publish -> inspect_active -> _snapshot_for_collection -> _read_manifest_from_collection -> Collection.get`. E1's raw inner Chroma exception was not captured; the isolated diagnostic rerun passed in 1.84s.
- Difference: E1 is outside the original six and is covered only by the explicit external verification exception recorded below. No Module 8 involvement and no different call site/signature were observed.
- Full-suite comparison: the Step 6 full offline suite completed with zero failures (`3795 passed, 5 skipped, 2 deselected`, exit 0), so neither E1 nor E2 was observed there. E1 remains the retained Step 5 Module 5 selector observation, not an erased or reclassified result.
- Environment: ChromaDB `1.5.9`, Python `3.12.14`, Windows `_getmaxstdio()` = 512. Foyer `0.17.4` is the Rust crate version locked by the inspected Chroma source; it is not a Python distribution in this environment (`foyer` is not importable). SQLite `3.53.1`.
- Disposition: the six historical observations retain their documented pre-existing upstream ChromaDB/Foyer manifest-read disposition. E1 is not assigned that cause: its raw cause is **UNCONFIRMED**, its B classification is probable only, and it remains a provisional risk-accepted exception. E2's raw cause is directly confirmed only for the observed occurrence. None of these failures is waived, xfailed, skipped, retried, monkeypatched, or attributed to Module 8. Module 8 does not import or depend on Module 5. No Module 5 file changed.

## Static, packaging, and scope gates

| Gate | Command | Result |
|---|---|---|
| Compile | `& "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe" -m compileall -q backend/runtime_evidence` | exit 0 |
| Dependencies | `& "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe" -m pip check` | exit 0; `No broken requirements found.` |
| Whitespace | `git diff --check` | exit 0 |
| Imports | 16-file AST audit (`tests/test_runtime_evidence_security.py`) | 34 passed; no violations |
| File count | 16 production `.py` files | 16 |
| Public exports | exactly 28 names; exact set equals `EXPECTED_EXPORTS`; `CodeGraphInventory` absent | PASS |
| Upstream untouched | `git diff --name-only 9803be4..HEAD` | exactly 28 authorized implementation/plan paths |
| Spec untouched | `git diff --name-only 9803be4..HEAD -- docs/superpowers/specs` | empty |
| Status | `git status --short` | ` M README.md` before staging; index empty |

Spec blob `d6773f1d32ad815c75fbd6da9155688244a5cbc4` and plan blob `5e67caf68b006c0f751a9f385f8a149ae54b2d32` equal their establishing commits and the worktree.

### Task 23 external Module 5 verification exception

This section records an explicit, narrowly scoped external verification exception. It is not a general Module 5 waiver and not a production-readiness approval. The Task 23 full offline suite completed green (`3795 passed, 5 skipped, 2 deselected`, exit 0), so this exception was not applied to that run; it governs only the separately retained Module 5 selector result (E1) and the directly confirmed E2 occurrence. Original six observations retain their original requirements; no wildcard or implicit additional exception exists.

#### Original six historical observations

The six observations in `module-7-git-history-analyzer.md` (four full-suite nodes plus two aggregate/diagnostic nodes) remain the baseline set. They ended at `backend/embedding_vector_store/stores/chroma.py:1255` with `VectorStoreReadError` and the documented `Nothing found on disk` manifest-read signature. Their original evidence and limitations are unchanged.

#### E1 — provisional risk-accepted upstream exception

- Node: `tests/test_chroma_vector_store.py::test_restart_ignores_abandoned_candidate_after_precommit_interruption`.
- Observed once in the Task 23 Step 5 Module 5 aggregate: `1 failed, 1,176 passed, 1 skipped, 2,624 deselected`, exit 1, outer `VectorStoreReadError` at `chroma.py:1255`.
- Isolated diagnostic rerun: `1 passed in 1.84s`, exit 0 (`task23/m5-diag.log`). The passing rerun does not erase the aggregate failure.
- Raw cause: **UNCONFIRMED**. The raw inner Chroma exception was not captured for this node. The HNSW/Foyer cache-eviction explanation must not be asserted for E1.
- B1 comparison/baseline: a clean 151-file archive of upstream `6f25511` (SHA-256 `BE07FF7B64AB76DACD787429118546E8DC18876FF37605FDCD056AF3CA94EB86`; README blob `31f31ab8b9462ed4f32e038a54b7a16ee39336aa` and `chroma.py` blob `398347034c10eef9cbf05590af31ce7d673d3118` equal `6f25511`) ran the same Module 5 selector: `1177 passed, 1 skipped, 1394 deselected`. E1 did not reproduce in that single baseline run.
- B1 classification: **B — probable pre-existing Module 5 behavior**, supported by the B1 reviewer but **not confirmed**. The baseline did not reproduce the node, and the upstream failure is nondeterministic. Classification B is a risk assessment, not a causal finding.
- Uncertainty/risk: E1 is a provisional exception accepted by the user. If E1 is actually a new Module 8-adjacent or Module 5 behavior change, the exception would be too broad; the evidence does not prove otherwise. No production or test change was made to force a conclusion.

#### E2 — directly confirmed raw cause for the observed occurrence

- Node: `tests/test_chroma_vector_store.py::test_concurrent_query_retains_snapshot_from_identity_through_normalization[None-None]`.
- Observed in the authorized round-2 aggregate: `1 failed, 1,176 passed, 1 skipped, 2,624 deselected`, exit 1, basetemp `C:\Users\avane\AppData\Local\Temp\opencode\t23-b1-round2-20261031-a91c7e\basetemp-aggregate-20261031`.
- Raw cause captured directly at `ChromaVectorStore._read_manifest_from_collection` (`chroma.py:1217`, traceback frame line 1237): `chromadb.errors.InternalError` (MRO `InternalError -> ChromaError -> Exception`) with the exact 95-character message `Error executing plan: Internal error: Error creating hnsw segment reader: Nothing found on disk`. Outer `VectorStoreReadError` at `chroma.py:1255`.
- This confirms the raw cause **only for that observed E2 occurrence**. It does not confirm E1's raw cause and does not generalize to every Module 5 failure.
- Evidence files and hashes: `aggregate-summary.json` SHA-256 `838DC4C515ECF3A55D0BEA1F05AB05A190201581CE88CD09BF33A6D2A008F80A`; `aggregate-report.json` SHA-256 `5F56E0E5505929EAC4032C31490F02568240349D7838B0BA46DC8753C1F931F7`; `aggregate.log` SHA-256 `10E8DFDACBB61E1F6148F262349542DC5FB084C94DCD641221E3D261B26BA1B3`.

#### Reviewer results and user approval

- B1 reviewer session `ses_e700548faffeluhxU3fKGf1kKT`: `B1 REVIEW: CLASSIFICATION B SUPPORTED`; Task 23 blocked at that time.
- First diagnostic reviewer session `ses_e6fe3dd42ffeAP1qp30OTtEoJK`: no completed verdict.
- Replacement diagnostic reviewer session `ses_e6aada12bffenfqf1AgnhZx6G9`: `DIAGNOSTIC INCONCLUSIVE FOR ORIGINAL B1 NODE — TASK 23 BLOCKED`; E2 raw cause confirmed; no authorization to expand the baseline set.
- User decision: explicit approval of the two-node external Module 5 verification exception (E1 and E2), with E1's raw cause unconfirmed and E2's raw cause confirmed only for the observed occurrence. The user authorized Task 23 to resume and complete, but not Task 24.
- Task 23 independent task reviewer session `ses_e6a884409ffeJUEVVUpcirz7ZE` (initial, before remediation): Specification compliance FAIL; Code quality/security PASS; Evidence/documentation integrity FAIL; Exception-scope compliance NO; Ready to commit NO. Findings: (1) the E2 evidence hash was attributed to the wrong file; (2) the E1 disposition asserted a cause the record elsewhere marks unconfirmed; (3) the required exception heading used `##` instead of `###`. All three findings were verified and corrected in this record; no production or test change was requested.
- Task 23 independent re-review session `ses_e635d4a3cffe3E3nn6PBywF6BS`: Specification compliance PASS; Code quality/security PASS; Evidence/documentation integrity PASS; Exception-scope compliance YES; Ready to commit YES. No Critical or Important findings. Five Minor documentation observations were reported (warning-count omissions in several summaries; the static-gate status row being a pre-staging snapshot; the exception-section placement; the then-unresolved re-review placeholder; and B1 archive file-count wording). None is a false claim or an exception-scope violation; all are non-blocking and are reported in the Task 23 completion report.

#### Scope limits

Only E1 and E2 are added. Any other Module 5 node, any different call site/signature, any Module 8 involvement, any retry/skip/xfail/suppression/monkeypatch, or any Module 5 change still blocks Task 23. The failing Module 5 selector result is retained; it was not rerun to obtain GREEN. This exception is not a general waiver of `VectorStoreReadError` or of Module 5.

## Independent audit

Pending Task 24.

Reviewer session ID: `<pending>`

Specification compliance: `<pending>`

Implementation/test quality: `<pending>`

Critical/Important findings and dispositions: `<pending>`

Residual Minor/Nit risks: `<pending>`

## Conclusion

DRAFT — do not claim completion until Task 24 records both required PASS verdicts and the final rerun.
