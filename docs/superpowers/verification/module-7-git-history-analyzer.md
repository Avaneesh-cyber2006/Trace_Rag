# Module 7 — Git History Analyzer: Verification Evidence

## Result

MODULE 7 INDEPENDENT AUDIT PASSED — READY FOR USER INTEGRATION APPROVAL

The final code/test candidate was independently reviewed with explicit
Specification compliance and Implementation/test quality verdicts of PASS.
No merge, push, rebase, reset, clean, or worktree deletion was performed.

## Environment

- Feature base: `1646c683ac1b0ccdfa8257b663188ebde3a687c8`
- Primary `main` HEAD at audit close: `1646c683ac1b0ccdfa8257b663188ebde3a687c8` (unchanged)
- Feature branch: `codex/module-7-git-history-analyzer`
- Feature worktree: `C:\Users\avane\Desktop\Studies\Projects\Trace_Rag_Module7_Worktree`
- Initial audited candidate: `bd075b29fc09b529acdc0f5bf4be0ad8908bc5ca`
- Final code/test candidate HEAD: `14840f10a2e28e71b89f998108618a6f3acaba83`
- Verification-document commit: the commit containing this file (see final report)
- Python: `C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe` (3.12.14)
- Git: `git version 2.55.0.windows.5`
- `PYTHONPATH`: `C:\Users\avane\Desktop\Studies\Projects\Trace_Rag_Module7_Worktree`
- All pytest runs used fresh, verified writable `--basetemp` directories under
  `C:\Users\avane\AppData\Local\Temp\opencode\...`, outside the repository.
- No production limit was lowered and the slow batch-input test was retained.

## Task approval status

| Item | Status |
| --- | --- |
| Approved specification: `docs/superpowers/specs/2026-10-18-git-history-analyzer-design.md` | Implemented |
| Approved plan: `docs/superpowers/plans/2026-10-18-git-history-analyzer.md` | Tasks 1–24 complete |
| Ten mandatory Module 7 test files | Present |
| Module 7 README usage documentation | Complete and reviewed |
| Independent specification review | PASS |
| Independent implementation/test-quality review | PASS |
| Merge/push | Not performed |

## Implementation commit IDs (Module 7)

```text
3a1b5706b18c537faa427c7f43858fe2ed9449ad feat: add git history exception hierarchy
d020ea9eac93ef5393f84c5a98f6cf3fce795dc0 feat: add enums identities and invariants
0a8f25d7e8f63b13ff992507a61811a64a4283f3 feat: add core data models
72d2b446f87b3e8a114eeab30be7aa41ca395b6e feat: parse raw git commit objects
52a0ded475ed8253a1d670c0e117a32e01ca5dbe feat: parse nul-delimited diff-tree output
d20f779940a6e76b2412fd8869832d82522fa774 feat: add secure git adapter and git_repo fixture
13ccbe1e262f02f45012caebbb1c0611a9cca14c feat: implement repository revision enumeration and diff helpers
f246eb4246e0bc63fc1acc56847a535a4cdf74ff feat: implement git history analyzer orchestration
13bea97480dbfd14a57fb0eb678584aa16d8b8b7 fix: validate commit message encoding in parser
0ac574b4a1b8998940e0d7ca29bceeeda5d00f02 feat: add changes_for_commit helper
634ad2004d7bd74cc44fbb172e8fda13d6dc8f71 fix: restore shared fixtures and add git_repo factory
20b52dcba285da31e51a005a3741451767702bfa fix: remove stray heredoc delimiter from conftest
88e9eb7733e806d62589d6e7fd7e7b4118862e4e docs: record module 7 verification evidence
19f61c6310bf13d004fdee6549325efd7457ad67 fix: remove trailing whitespace
ca2cf1eab2000439cce33826fdef282dac16fc04 fix: repair parse_name_status indentation
aa79028f832ac189f3c6c8a30b3ec7b8ac4e4718 fix: apply independent review corrections
df517eb73959dd43f9b9f430e8c11f4fce68380e fix: align module 7 implementation with approved contracts
24abd252a38c37091b76748a60d08b575fe113cb test: add module 7 automated coverage
bd075b29fc09b529acdc0f5bf4be0ad8908bc5ca docs: record git history analyzer verification evidence
bd9dbce4e94fa75835d10b8cdeb8438f61ef6509 fix: resolve independent audit Important findings for Module 7
808f87da142e1d22fb48f683cff253d0060866d0 fix: close per-analysis git adapter helpers
7e4e47ea077607496ce2e30a3515074a9306fa26 test: add audit regressions for module 7 corrections
96bc215894c06b17c2c29605857313ac22cc837c docs: add module 7 usage documentation
97b739c7a27c8bb93d7d9bdb744ca5cb53cf5f1a docs: correct module 7 limit and co-change documentation
14840f10a2e28e71b89f998108618a6f3acaba83 style: remove trailing blank line from module 7 scaling tests
```

## Independent audit history and finding disposition

The initial independent audit reviewed `bd075b2` in fresh-context reviewer
session `ses_ea6a3d3e8ffeEhPpzaqUu2MAK6`. It returned **Specification
compliance: FAIL**, **Implementation/test quality: PASS with findings**,
with 0 Critical, 3 Important, and additional Minor/Nit findings.

### Important findings

| Finding | Disposition | Correction and regression evidence |
| --- | --- | --- |
| A. Unborn-symbolic-ref handling was applied only to literal `HEAD`; a non-HEAD unborn symbolic ref was not recognized. | FIXED | `bd9dbce` validates the requested revision and calls `symbolic-ref --quiet <revision>`. Regressions prove a genuine non-HEAD unborn symbolic ref returns an empty inventory, while plain `main`, `refs/heads/main`, and `alias` remain `GitRevisionError`; a born non-HEAD symbolic ref resolves. |
| B. The adapter did not validate revisions before invoking Git and did not place `--end-of-options` before the revision; path-filter arguments needed literal pathspec handling. | FIXED | `bd9dbce` rejects non-string, NUL-containing, and dash-prefixed revisions before `run_raw`, adds `--end-of-options`, and uses `:(literal)` pathspecs after `--`. Regressions cover NUL/dash rejection before any subprocess, option ordering, literal pathspecs, full commit IDs, and revision expressions. |
| C. The analyzer reused a mutable adapter without deterministic cleanup, allowing timeout/cumulative-output/helper state to leak across analyses. | FIXED | `bd9dbce` creates one adapter per analysis; `808f87d` adds a `try/finally` that always calls `adapter.close()`. Regressions prove reused analyzers have independent timeout/cumulative/helper state, failed analyses clean helpers, and later reuse is not poisoned. |

The correction commits are `bd9dbce` and `808f87d`; the dedicated regression
commit is `7e4e47e`. The new regression tests include:

- non-HEAD unborn symbolic ref returns empty inventory;
- plain `main`, `refs/heads/main`, and `alias` remain `GitRevisionError`;
- born non-HEAD symbolic ref resolves;
- invalid revision still raises;
- adapter rejects NUL and dash before any `run_raw`;
- `--end-of-options` ordering and literal pathspec after `--`;
- full commit ID and revision expression enumerate;
- reused analyzer keeps independent timeout/cumulative/helper state;
- failed analysis cleans the helper and does not poison reuse.

Minor dispositions from the same review:

- constructor validation ordering/type strictness — FIXED in `bd9dbce`;
- `build_changes_by_path` deterministic change-order tie-break — FIXED in `bd9dbce`;
- change cap checked after the entire commit rather than after each change — FIXED in `bd9dbce`;
- empty `repository_namespace` accepted — FIXED in `bd9dbce`;
- missing Module 7 README — FIXED in `96bc215`.

### Re-review history

An attempted re-review in session `ses_ea65294c9ffe132G33skH5QPjB` produced no
verdict: the final assistant content was empty with `finish: error` and an
HTTP 429 provider quota response. It is not approval and was not counted as
one.

A fresh-context independent reviewer in session
`ses_ea620a16cffe2wt7txB9Wg3D4k` then reviewed the corrected candidate at
`96bc215` and returned:

- **Specification compliance: PASS**
- **Implementation/test quality: PASS**
- no Critical or Important findings
- final line: `MODULE 7 RE-REVIEW APPROVED`

The README accuracy delta was then reviewed proportionately in the same
session at `97b739c`. The reviewer verified every changed claim against the
implementation, reported no new findings, confirmed that both prior verdicts
remain PASS, and ended with
`MODULE 7 DOCUMENTATION RE-REVIEW APPROVED`.

## README completion

`96bc215` added the complete Module 7 README section (170 lines): public API,
revision/path-filter semantics, limits, incomplete reasons, security posture,
and executable examples. `97b739c` corrected four accuracy points found during
review:

- the example now demonstrates `co_changed_paths`;
- `co_change_incomplete_paths` documents both the global pair budget and the
  per-commit path cap;
- `truncated_by_limit` documents the per-call/constructor limit and the
  internal 100,000-commit cap;
- the Module 1 example notes that cloning may use the network while history
  analysis itself is local.

The documentation makes no symbol-level historical blame or causal-inference
claim; co-change is described as temporal correlation, not dependency proof.

## Verification commands and results

All results below were observed at final code/test candidate `14840f1` with
fresh `--basetemp` directories.

### Per-file collection of the ten mandatory Module 7 test files

Command shape:
`<python> -m pytest --collect-only -q <file>`

| File | Collected | Exit |
| --- | ---: | ---: |
| `tests/test_git_history_adapter.py` | 56 | 0 |
| `tests/test_git_history_analyzer.py` | 41 | 0 |
| `tests/test_git_history_api.py` | 3 | 0 |
| `tests/test_git_history_exceptions.py` | 3 | 0 |
| `tests/test_git_history_identities.py` | 5 | 0 |
| `tests/test_git_history_integration.py` | 5 | 0 |
| `tests/test_git_history_models.py` | 27 | 0 |
| `tests/test_git_history_parser.py` | 18 | 0 |
| `tests/test_git_history_scaling.py` | 8 | 0 |
| `tests/test_git_history_security.py` | 13 | 0 |
| **Total** | **179** | **0** |

### Module 7 suites

| Suite | Result | Time | Exit |
| --- | --- | ---: | ---: |
| Full Module 7: `pytest tests -q -k "git_history" -m "not integration and not gemini_live" -rs` | **178 passed, 1 skipped, 2393 deselected** | 362.87 s | 0 |
| Integration: `tests/test_git_history_integration.py` | **5 passed** | 3.39 s | 0 |
| Security: `tests/test_git_history_security.py` | **13 passed** | 8.25 s | 0 |
| Scaling: `tests/test_git_history_scaling.py` | **8 passed** | 14.39 s | 0 |

The single Module 7 skip is the expected Windows filename test:
`*` and `?` are illegal filename characters on Windows.

### Upstream regressions (Modules 1–6)

| Gate | Result | Time | Exit |
| --- | --- | ---: | ---: |
| Module 1: `tests/test_repository_loader.py` | **48 passed, 1 deselected** | 3.22 s | 0 |
| Module 2: `tests/test_file_scanner.py` | **178 passed, 2 skipped** | 0.67 s | 0 |
| Module 3 grammar gate: `tests/test_code_parser_dependencies.py tests/test_code_parser.py` | **214 passed, 1 skipped** | 0.65 s | 0 |
| Module 4: `tests/test_code_chunker.py` | **143 passed** | 0.48 s | 0 |
| Module 5/vector/search aggregate (16 files/globs) | **1214 passed, 1 skipped** (clean final run) | 194.98 s | 0 |
| Module 6 graph aggregate (11 files) | **564 passed** | 1.05 s | 0 |
| Module 6 integration: `tests/test_code_graph_integration.py` | **26 passed** | 0.26 s | 0 |

### Full offline suite

Command:
`<python> -m pytest -q -m "not integration and not gemini_live" -rs -p no:cacheprovider --basetemp=<fresh-outside-repo>`

Result: **2565 passed, 5 skipped, 2 deselected, 1 warning in 530.40 s**, exit 0.

The five runtime skips are the POSIX-fork test, three symlink-creation tests
(two in `test_file_scanner.py` and one in `test_code_parser.py`), and the
Module 7 Windows filename test. The warning is the pre-existing ChromaDB
`legacy embedding function config` deprecation warning.

The historical pre-Module-7 baseline was **2387 passed, 4 skipped,
2 deselected**. The measured final count is **2565 passed, 5 skipped,
2 deselected**; the additional Module 7 tests account for the difference.

### Static and repository gates

| Gate | Observed result | Exit |
| --- | --- | ---: |
| `python -m compileall -q backend tests` | clean | 0 |
| `python -m pip check` | `No broken requirements found.` | 0 |
| `git diff --check` (worktree) | clean | 0 |
| `git diff --check 1646c68..HEAD` | clean | 0 |
| `git status --short --branch` | only branch line; clean worktree | 0 |
| `git rev-parse HEAD` | `14840f10a2e28e71b89f998108618a6f3acaba83` | 0 |
| `git -C <main> rev-parse HEAD` | `1646c683ac1b0ccdfa8257b663188ebde3a687c8` | 0 |

## Scaling observations

- The eight scaling tests passed. They cover deterministic operation counts,
  one baseline-resolution command per non-root commit, no per-symbol command
  growth, duplicate-pair budget behavior, atomic global-budget overflow, and
  immutable published indexes.
- Production limits remain unchanged:
  `MAX_CHANGES = 1_000_000`, `CO_CHANGE_PER_COMMIT_PATH_CAP = 1000`, and
  `CO_CHANGE_PAIR_BUDGET = 10_000_000`.
- The slow batch-input test was included in the final Module 7 run; it was not
  skipped or deselected, and no timeout or production limit was reduced.
- Full Module 7 runtime was 362.87 s and the full offline suite was 530.40 s on
  this Windows host.

## Security evidence

The 13 Module 7 security tests passed. They cover:

- only allowed, read-only Git subcommands are invoked (`--version`,
  `rev-parse`, `rev-list`, `cat-file`, `diff-tree`, and `symbolic-ref`);
- forbidden subcommands, fetches, filters, and lazy fetching are never used;
- the analyzed repository is not mutated;
- hooks are overridden to an empty directory;
- `textconv` and external diff drivers are disabled;
- replacement objects and replacement refs are ignored and not modified;
- child-process environment and error messages do not leak secrets or paths;
- revision option injection and NUL-containing revisions are rejected.

Related adapter/analyzer regressions additionally cover path-filter rejection
of traversal, absolute paths, backslashes, empty components, and NUL; literal
pathspec arguments after `--`; bounded subprocess timeouts and output size;
temporary-helper cleanup after success and failure; and a genuine non-HEAD
unborn symbolic ref producing an empty inventory rather than a false revision
error.

## Scope audit

`git diff --name-status 1646c68..HEAD` contains only:

- `backend/git_history/` (eight new production modules);
- `tests/test_git_history_*.py` (the ten mandatory test files);
- `tests/conftest.py` (shared `git_repo`/`GitRepoBuilder` fixtures and
  `symbolic_ref` helper);
- `README.md` (Module 7 usage documentation);
- `docs/superpowers/verification/module-7-git-history-analyzer.md` (this file).

`git diff 1646c68..HEAD -- requirements.txt` and
`git diff 1646c68..HEAD -- pytest.ini` are both empty. No Module 1–6
production file was modified. No Module 8–10 content was added.

Dependency audit: `backend/git_history/` imports only Python standard-library
modules. It has no third-party imports and does not import any other backend
package (the optional `backend.file_scanner.models` type is not imported).

## Whole-module review verdict

- Initial audit: Specification compliance **FAIL**, Implementation/test
  quality **PASS with findings** (0 Critical, 3 Important).
- Corrected candidate at `96bc215`: Specification compliance **PASS**,
  Implementation/test quality **PASS**, no Critical/Important findings,
  `MODULE 7 RE-REVIEW APPROVED`.
- README delta at `97b739c`: no new findings, both prior verdicts remain PASS,
  `MODULE 7 DOCUMENTATION RE-REVIEW APPROVED`.
- Reviewer identity/session evidence: fresh-context OpenCode `general`
  subagent session `ses_ea620a16cffe2wt7txB9Wg3D4k`; original audit session
  `ses_ea6a3d3e8ffeEhPpzaqUu2MAK6`. The failed quota session
  `ses_ea65294c9ffe132G33skH5QPjB` produced no verdict.

### Remaining Minor/Nit findings

These were reviewed and consciously left unchanged because they do not change
the approved public contract or block integration:

| Severity | Finding | Status |
| --- | --- | --- |
| Minor | `GitFileChange` permits `similarity=None` for manually constructed rename/copy records. Published analyzer inventories pass through Git’s reported similarity score; this is model-level permissiveness, not a published-data defect. | Remaining; contract permits `None`. |
| Minor | Shallow-boundary commit IDs are not independently validated as 40-hex object IDs before publication. | Remaining. |
| Minor | Baseline tree object validation is limited; an unexpected tree type is not separately rejected before diffing. | Remaining. |
| Minor | Cleanup regressions could still pass if only `__del__` closed helpers; there is no direct assertion that `close()` was called. | Remaining; deterministic `finally: adapter.close()` is implemented. |
| Minor | `command_timeout_seconds=float("nan")` passes the `<= 0` check. | Remaining. |
| Minor | No direct tests assert `build_changes_by_path` ordering and the per-change cap boundary. | Remaining coverage gap. |
| Nit | `GitAdapter.__del__` imports `shutil` lazily. | Remaining. |
| Nit | A blank-line style issue between adapter test groups. | Remaining. |
| Nit | `require_tuple` ignores its `element_type` argument. | Remaining. |
| Nit | `GitFileChange` coerces a string `change_type` to the enum instead of requiring the enum. | Remaining. |
| Nit | `build_diff_flags` is a thin delegate. | Remaining. |
| Nit | `GitRepoBuilder.head_timestamp` contains a redundant first `git log` call before the correct call. | Test-helper only; harmless. |

## Chroma flake ruling

During isolated Module 5 aggregate runs at `14840f1`, transient Chroma
concurrency failures appeared in tests that monkeypatch publication and
backend-read outages. The failing parametrization changed between runs. The
affected test families were:

- `test_publication_requires_readable_unambiguous_pointer_after_replace[...]`;
- `test_concurrent_query_retains_snapshot_from_identity_through_normalization[...]`;
- `test_committed_publication_survives_backend_read_outage[...]`;
- `test_delete_repository_index_is_busy_while_obsolete_reader_is_leased`.

Four individually rerun failing nodes each passed. The observed non-clean
investigation runs were 2 failed/1212 passed, 1 failed/1213 passed, and
2 failed/1212 passed; two intermediate full-suite attempts each had
1 failed/2564 passed. No production or test file was changed in response.
Clean reruns at `14840f1` were then obtained:

- `tests/test_chroma_vector_store.py` alone: **177 passed, 1 skipped**
  (covers every parametrization in the affected families);
- isolated Module 5 aggregate final run: **1214 passed, 1 skipped**;
- full offline suite final run: **2565 passed, 5 skipped, 2 deselected**.

The failure was a `VectorStoreReadError` raised from Chroma collection
internals while the test simulated an outage; it did not match the plan's
narrow sanctioned `Nothing found on disk` signature. No production retry,
`xfail`, suppression, or assertion weakening was added. No Module 5 production
or test file was changed by this work. Ruling: a test-order/environment
ChromaDB concurrency flake in unchanged upstream code, not a Module 7 defect;
the required clean Module 5 aggregate and full-suite runs were obtained.

## Graph integration evidence

- Module 7 local integration: **5 passed**, including
  `test_file_level_association_with_module_6_nodes` and inventory namespace
  matching.
- Module 6 graph suite: **564 passed**.
- Module 6 integration: **26 passed**.

## Conclusion

At final code/test candidate `14840f10a2e28e71b89f998108618a6f3acaba83`, all
ten Module 7 test files collect (179 tests), the full Module 7 suite passes
(178 passed, 1 expected Windows skip), the integration, security, and scaling
suites pass, Modules 1–6 regressions pass, the full offline suite passes
(2565 passed, 5 skipped, 2 deselected), and `compileall`, `pip check`, and
`git diff --check` are clean. The scope audit confirms no Module 1–6 production
changes and no Module 8–10 content.

The independent reviewer's final verdict is PASS for both specification
compliance and implementation/test quality, with no Critical or Important
findings. This module is ready for the user's separate integration approval.
No merge or push has been performed; primary `main` remains at
`1646c683ac1b0ccdfa8257b663188ebde3a687c8`.

## Post-merge verification and upstream ChromaDB publication exception

**Recorded:** 2026-10-22 (Asia/Kolkata).

### Chronology and current repository state

The independent audit above was completed before integration. Its statements
that no merge or push had occurred were true at that time and are retained
unchanged as historical evidence. After that audit, a separate user
authorization permitted a local fast-forward-only integration of the audited
feature.

The primary repository reflog records the integration at
`2026-10-21 20:24:28 +0530`:

```text
c9000ff HEAD@{2026-10-21 20:24:28 +0530}: merge c9000ff11b9af743b5021f609048952e04d74429: Fast-forward
```

`c9000ff11b9af743b5021f609048952e04d74429` has one parent,
`3c96aadb76f00065fe2fad70029cdfec878db86b`. The integrated range contains no
merge commit, squash, or rebase. The post-integration local `main`, the Module 7
feature branch, and the Module 7 worktree were all at `c9000ff`.

The publication audit recorded here did not fetch, pull, reset, clean, rebase,
amend, or merge. It did not change production code, test code, `README.md`,
requirements, or specifications. The only intended change is this
documentation file. At the start of the audit, local `main` was 32 commits ahead
of `origin/main`; `origin/main` was
`159a2ee45aed6d11a07c846c3fc8982b4d207fdc`.

### Post-merge gates that exited successfully

These results were observed from the primary repository at local `main` =
`c9000ff`, with fresh writable `--basetemp` directories outside the repository
and `-p no:cacheprovider`.

| Gate | Result | Exit |
| --- | --- | ---: |
| Full Module 7 (`-k "git_history"`) | **178 passed, 1 skipped** | 0 |
| Module 7 integration | **5 passed** | 0 |
| Module 7 security | **13 passed** | 0 |
| Module 7 scaling | **8 passed** | 0 |
| Module 1 repository loader | **48 passed, 1 deselected** | 0 |
| Module 2 file scanner | **178 passed, 2 skipped** | 0 |
| Module 3 and grammar gate | **214 passed, 1 skipped** | 0 |
| Module 4 code chunker | **143 passed** | 0 |
| Module 5/vector/search aggregate, clean isolated rerun | **1214 passed, 1 skipped** | 0 |
| Module 6 graph aggregate | **564 passed** | 0 |
| Module 6 integration | **26 passed** | 0 |
| `python -m compileall -q backend tests` | clean | 0 |
| `python -m pip check` | `No broken requirements found.` | 0 |
| `git diff --check` and range diff check | clean | 0 |

The single Module 7 skip is the expected Windows filename test: `*` and `?`
are illegal filename characters on Windows. The Module 5 clean isolated rerun
followed an earlier aggregate run with one failure, described below. A clean
aggregate rerun is not a full-suite pass.

### Latest full-suite status: not passing

The latest full offline-suite attempts at `c9000ff` used:

```text
<python> -m pytest -q -m "not integration and not gemini_live" -rs -p no:cacheprovider --basetemp=<fresh-outside-repo>
```

Both recorded attempts exited `1` with **2 failed, 2563 passed, 5 skipped,
2 deselected, 1 warning**:

| Attempt | Result | Exit |
| --- | --- | ---: |
| Initial post-merge full suite | **2 failed, 2563 passed, 5 skipped, 2 deselected** | 1 |
| Fresh-`basetemp` rerun | **2 failed, 2563 passed, 5 skipped, 2 deselected** | 1 |

The two attempts produced four distinct failing node IDs:

- `tests/test_chroma_vector_store.py::test_publication_requires_readable_unambiguous_pointer_after_replace[unreadable-True]`;
- `tests/test_embedding_scaling.py::test_real_chroma_candidate_id_work_is_linear[True-257]`;
- `tests/test_chroma_vector_store.py::test_committed_publication_survives_backend_read_outage[False-False]`;
- `tests/test_chroma_vector_store.py::test_publication_requires_readable_unambiguous_pointer_after_replace[third-False]`.

Two additional observations occurred at the same manifest-read call site:

- the Module 5 aggregate run failed
  `tests/test_chroma_integrity_deletion.py::test_delete_repository_index_removes_incomplete_abandoned_candidates[True-1]`;
- the targeted-prefix diagnostic run failed the neighboring parametrization
  `tests/test_chroma_vector_store.py::test_publication_requires_readable_unambiguous_pointer_after_replace[checksum-True]`.

All six observations ended in
`backend/embedding_vector_store/stores/chroma.py:1255` with
`VectorStoreReadError: Vector store data could not be read.` The raw Chroma
exception captured by the Phase B probe was:

```text
chromadb.errors.InternalError: Error executing plan: Internal error:
Error creating hnsw segment reader: Nothing found on disk
```

The 2565-pass result in the pre-integration section was observed at the audited
candidate `14840f1` before integration. It remains historical evidence for that
candidate and time only. It is not a result for `c9000ff`, and it must not be
presented as the current full-suite status.

### Failure path and non-causation evidence

The four full-suite failures, the Module 5 aggregate failure, and the Phase B
failure all traverse:

```text
publish()
  -> inspect_active()
  -> _snapshot_for_collection()
  -> _read_manifest_from_collection()
  -> Collection.get(limit=..., offset=..., include=["documents", "embeddings", "metadatas"])
  -> VectorStoreReadError at backend/embedding_vector_store/stores/chroma.py:1255

delete_repository_index()
  -> _repository_collection_names()
  -> _read_manifest_from_collection()
  -> Collection.get(limit=..., offset=..., include=["documents", "embeddings", "metadatas"])
  -> VectorStoreReadError at backend/embedding_vector_store/stores/chroma.py:1255
```

In the publication tests, the failure occurs during the first
`inspect_active()` call inside `publish()`, before the active-pointer
replacement is attempted. The test's `committed` flag is therefore still
`False`. This is important because:

- the simulated pointer-read outage in
  `test_publication_requires_readable_unambiguous_pointer_after_replace` is
  guarded by `committed`;
- the simulated backend outage in
  `test_committed_publication_survives_backend_read_outage` is guarded by
  `committed`;
- the failure therefore occurs before either simulated outage path can run;
- `test_real_chroma_candidate_id_work_is_linear` does not simulate a backend
  outage.

All four full-suite failures are in unchanged Module 5 test files. No Module 7
test runs before them in the session collection order: the failing positions
are 87, 92, 95, and 1269, while the first `test_git_history` node is at position
2160. The four failures are not evidence that Module 7's production path was
executed.

### Evidence, mechanism, and dependency versions

The observed environment was:

- Python `3.12.14`;
- ChromaDB `1.5.9`;
- Foyer `0.17.4` (the crate version locked by the inspected Chroma source);
- SQLite `3.53.1`;
- Windows `_getmaxstdio()` = `512`.

ChromaDB's Rust binding computes:

```text
hnsw_cache_size = _getmaxstdio() // 5 = 512 // 5 = 102
```

The Foyer default configuration uses 64 shards. In `foyer-memory` 0.17.4,
`shard_capacity = capacity / shards`, so the default 102-entry capacity becomes
64 shards of one entry each. HNSW indexes are keyed by vector-segment UUID.
When a live client needs an index whose cache entry was evicted and whose
on-disk `index_metadata.pickle` was never written, the reader returns
`UninitializedSegment` (`Nothing found on disk`).

ChromaDB's default `sync_threshold` is 1000. The small Module 5 collections
contain far fewer than 1000 records and therefore do not reach the normal
persist threshold. Chroma's local executor also records collections it has
backfilled and suppresses a second backfill for the same live client. Source
inspection of the inspected 1.5.9 source supports this sequence; the exact live
shard assignment was not introspected.

The preserved failing roots corroborate the unpersisted-index condition. For
each of the four full-suite failing roots, the HNSW segment directories
contained HNSW data files but no `index_metadata.pickle`. The same was true of
the preserved Module 5 aggregate failing root. This is strong diagnostic
evidence, although the raw Chroma exception was captured directly only by the
Phase B probe; the other observed nodes were wrapped at line 1255 before their
raw Chroma exception was recorded.

### Controlled reproductions

The following diagnostic scripts were run outside the repository and made no
production or test changes.

| Reproduction | Default result | Forced larger cache result |
| --- | --- | --- |
| Two collections, 320 fresh roots; force both indexes into the cache | **1 failure / 320**, exact `Nothing found on disk` | 1024: **0 failures / 320** |
| Load one old collection plus 128 other collections | **failed**, exact `Nothing found on disk` | 1024: **passed** |
| Load one old collection plus 512 other collections | **failed**, exact `Nothing found on disk` | 8192: **passed** |
| Two-record collection with `hnsw:sync_threshold=2`, then load 128 or 512 other collections | **passed**; an `index_metadata.pickle` was present | not applicable |

The two-collection result is intermittent, as expected from a probabilistic
shard collision. An earlier 320-round run passed with zero failures; a later
run failed once. The deterministic 128- and 512-collection overflow runs both
failed at the default capacity and passed when the effective cache was
enlarged. The `sync_threshold=2` runs preserved the index and passed under the
same overflow pressure. `sync_threshold=1` was rejected as an invalid
configuration and is not part of the root-cause claim.

### Diagnostic reruns and limits

Phase A reran each of the four full-suite failing node IDs three times in fresh
processes: **12/12 passed**. These reruns characterized nondeterminism; they are
not a substitute for a passing full suite and do not convert the latest suite
result into a pass.

Phase B ran the targeted prefix group three times: **1 of 3 runs failed**, with
the neighboring `checksum-True` parametrization and the raw Chroma exception
above. Phase C ran an isolated `159a2ee` baseline three times: **3/3 passed,
218 passed, 1 skipped** each time. With an assumed true 1/3 failure rate, three
passing baseline runs have probability about 29.6%; Phase C therefore does not
prove that the baseline is immune.

The evidence supports a narrow upstream publication exception, not a finding
that every observed failure has an identical internal state. The shared
observable failure mode is the manifest/collection read at line 1255, the same
publish/inspection call chain, the pre-commit timing, and missing
`index_metadata.pickle` in the preserved failing roots. The precise live shard
mapping remains unobserved.

### Shared fixture and repository-scope audit

`tests/conftest.py` changed by adding the Module 7 `GitRepoBuilder`,
`ShallowClone`, `_run_git`, and `git_repo` fixtures, the imports they need, and
a replacement of the module docstring. The diff contains no `autouse` fixture,
no `pytest_configure` or session hook, and no module-level environment mutation.
The `os.environ` assignment is inside the per-call `commit_with_timestamp`
helper. Neither the fixture addition nor the docstring replacement installs a
global Chroma or process-state side effect.

`backend/git_history/` imports only the Python standard library and its own
package modules. It does not import ChromaDB or the Module 5 embedding store.
The integration range from `1646c68` contains only the eight new
`backend/git_history/` modules, the ten `tests/test_git_history_*.py` files,
the shared `tests/conftest.py` fixture addition and module-docstring
replacement, `README.md`, and this verification document. The wider publication
range from `159a2ee` also carries
the approved Module 7 plan and specification documents. The range changes no
Module 1–6 production file, no Module 5 test file, and no Module 8–10 content.

At the start and end of the diagnostic work, the four registered worktrees were
preserved, and the 22 pre-existing untracked repository paths (127 files) were
covered by an identical SHA-256 manifest before and after. No untracked artifact
was removed or modified by the diagnostic work.

### Publication exception

This exception is for GitHub **source publication** of the Module 7 range only.
It is not a full-suite verification, not a Module 5 regression waiver, and not a
production-readiness certification. It does not authorize changing Module 5,
adding a retry or suppression, skipping/xfail, weakening an assertion, or
declaring the failing full suite clean. Diagnostic isolated reruns were used
only to characterize the nondeterministic upstream failure; they are not offered
as a passing verification result.

A fresh-context independent reviewer in OpenCode `general` subagent session
`ses_e9d93633effevphOQ9VB5SMlDa` reviewed this exception and the evidence above.
It returned the exact verdict `PUBLICATION EXCEPTION APPROVED`, with 0 Critical
findings, 0 Important findings, and 3 Minor documentation findings: the
`tests/conftest.py` description omitted the module-docstring replacement, the
publication-state paragraph used present-tense wording before the documentation
commit existed, and this verdict record was absent. The three Minor corrections
were applied, and the reviewer confirmed the corrected text still carries
`PUBLICATION EXCEPTION APPROVED`.

The exception is therefore accepted, subject to the scope and limits stated in
this section.

The exception is limited to the observed intermittent unchanged Module 5
manifest/collection-read failures at `chroma.py:1255` whose evidence matches the
investigated `Nothing found on disk` upstream ChromaDB/Foyer cache-eviction
condition or its directly corroborated unpersisted-index variant. A failure
that cannot be linked to this condition is outside the exception and requires a
stop, not broader acceptance of `VectorStoreReadError`.

The required publication disclosure is:

An explicit, narrowly scoped upstream-dependency verification exception was accepted for GitHub source publication. The latest full offline-suite runs did not pass. The ChromaDB cache-eviction/read-recovery defect remains unresolved and may affect real small-collection reads on Windows. This exception does not constitute production-readiness approval.

### Publication state

At the time this section was written, this document was the only intended change
in a single documentation commit to be directly parented by
`c9000ff11b9af743b5021f609048952e04d74429`, the commit that contains this
section. Local `main` was 32 commits ahead of `origin/main`; adding the
documentation commit makes the outgoing publication range 33 commits. The
preceding 32 commits are unchanged. No push has been performed as part of this
work. The remote main tip must be checked again immediately before the separate
single push, and only a non-force `refs/heads/main:refs/heads/main` update is
permitted.
