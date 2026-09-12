# Task 30 Chroma persistence-handle leak report

Date: 2026-09-12

Branch: `codex/module-5-embedding-vector-store`

Interpreter: `C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe`

## Recovery note

The requested pre-existing `task-30-handle-leak-report.md` was not present in
the feature worktree or elsewhere under the repository tree when this resumed
task began. This file records the recovered diff, the final test correction,
and all fresh verification performed during the resumed task.

## Root cause and fix

`_PERSISTENCE_STATES` held a strong process-lifetime reference to every Chroma
client and ownership file ever opened. A full suite that created hundreds of
isolated persistence roots therefore retained their Chroma systems and OS
handles until interpreter exit, eventually surfacing as `OSError 24`.

The fix gives each shared root state a live-adapter reference count and attaches
a `weakref.finalize` callback to every adapter. The last adapter closes the
shared Chroma client before closing the ownership file and removing the state
from the registry. Acquisition remains serialized throughout teardown. A
zero-reference state is fail-closed if client teardown fails. Constructor
failure releases the reference it acquired. Fork-child finalizers do not enter
inherited locks or close the parent's client/lease.

Two existing non-owner-PID tests now scope the PID monkeypatch with
`monkeypatch.context()`, restore the real PID before deleting the adapter, force
collection, and assert that the ownership file closes. Without this scoping,
their finalizers correctly interpreted the synthetic PID as a fork child and
left test-created state alive.

The README change is intentional. It documents the new user-observable
last-adapter shutdown/reopen contract, retention during active calls and
snapshot contexts, and the fail-closed behavior after client shutdown failure.

## Focused and adjacent verification

Recovered lifecycle group before the PID-test scoping correction:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest tests/test_chroma_vector_store.py -q -p no:cacheprovider --basetemp .pytest_tmp/task30-recovered-lifecycle -k 'non_owning_process or fork_discards or concurrent_subprocess_writer or ownership_released or unused_persistence_roots or last_shared_adapter or active_snapshot_context or active_writer_retains or adapter_setup_failure or finalization_during_client_construction or teardown_rejects or failed_client_teardown' -rs
```

Result: exit 0; `17 passed, 1 skipped, 159 deselected in 25.37s`. The skip was
the POSIX-only fork test on Windows.

PID-scoping tests after correction:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest tests/test_chroma_vector_store.py -q -p no:cacheprovider --basetemp .pytest_tmp/task30-pid-scope -k 'non_owning_process' -rs
```

Result: exit 0; `5 passed, 172 deselected in 2.21s`.

Complete Chroma adapter suite:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest tests/test_chroma_vector_store.py -q -p no:cacheprovider --basetemp .pytest_tmp/task30-chroma-full -rs
```

Result: exit 0; `176 passed, 1 skipped in 42.37s`. The skip was the POSIX-only
fork test on Windows.

Relevant vector-store contract, synchronization/search concurrency, ownership,
and security selection:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest tests/test_vector_store_contract.py tests/test_semantic_synchronization.py tests/test_semantic_search.py tests/test_embedding_security.py -q -p no:cacheprovider --basetemp .pytest_tmp/task30-adjacent-lifecycle -k 'concurrent or snapshot or owner or process or chroma or credential or leak or security' -rs
```

Result: exit 0; `524 passed, 171 deselected in 3.95s`.

## Populated-root handle stress

No 550-root stress helper was present in the recovered worktree. A one-off
helper was created under `.pytest_tmp/task30_handle_stress.py` and deliberately
left there (the task explicitly forbids deleting `.pytest_tmp`). For each of 550
unique roots it created a real Chroma adapter, wrote and validated one synthetic
record, published and inspected the snapshot, dropped all adapter references,
forced GC, and asserted both `_PERSISTENCE_STATES` and Chroma's shared-system
cache were empty. It then reopened root 549 and verified the persisted record.

The first helper invocation exited 1 because optional `psutil` is not installed.
The helper was changed to use native Windows `GetProcessHandleCount`; no project
dependency or production code changed. The second invocation exited 1 because
running a script below `.pytest_tmp` did not put the repository root on
`sys.path`. The successful invocation explicitly supplied `PYTHONPATH`:

```powershell
$env:PYTHONPATH = (Get-Location).Path
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' .pytest_tmp/task30_handle_stress.py .pytest_tmp/task30-stress-550-resume
```

Result: exit 0; final output:

```text
populated-and-released=500
populated-and-released=550
stress-ok roots=550 start_handles=276 end_handles=283 pid=25292
```

The seven-handle end delta is below the helper's fixed allowance of 16, and the
adapter registry/shared Chroma system cache were empty after every root and the
final reopen.

## Fresh full offline suite and known flakes

First fresh run:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest -q -m 'not gemini_live and not integration' -p no:cacheprovider --basetemp .pytest_tmp/task30-full-offline-resume-20260912 -rs
```

Result: exit 1; `1794 passed, 4 skipped, 2 deselected, 2 failed, 1 warning in
179.91s`. The two failures were upstream repository-loader local `git clone`
commands exiting 128. Both cases passed when immediately rerun in isolation
below. No code was changed for them.

Second fresh run with a shorter new basetemp:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest -q -m 'not gemini_live and not integration' -p no:cacheprovider --basetemp .pytest_tmp/t30f-0912b -rs
```

Result: exit 1; `1794 passed, 4 skipped, 2 deselected, 2 failed, 1 warning in
159.23s`. The failures were two parameter cases of
`test_publication_requires_readable_unambiguous_pointer_after_replace`, where
Chroma failed to read a just-written one-record HNSW collection and the adapter
correctly surfaced `VectorStoreReadError`. This is the pre-existing intermittent
Chroma HNSW "Nothing found on disk" class of flake. No retry, suppression, or
behavior change was added.

Focused reproduction checks:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest tests/test_chroma_vector_store.py -q -p no:cacheprovider --basetemp .pytest_tmp/t30h1 -k 'publication_requires_readable_unambiguous_pointer_after_replace' -rs
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest tests/test_repository_loader.py::test_loader_clones_into_deterministic_workspace tests/test_repository_loader.py::test_clone_uses_private_empty_hooks_and_git_configuration -q -p no:cacheprovider --basetemp .pytest_tmp/t30r1 -rs
```

Results: exit 0; `8 passed, 169 deselected in 3.58s`, then exit 0; `2 passed in
1.76s`.

Final fresh full run:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest -q -m 'not gemini_live and not integration' -p no:cacheprovider --basetemp .pytest_tmp/t30f3 -rs
```

Result: exit 0; `1796 passed, 4 skipped, 2 deselected, 1 warning in 160.58s`.
Skips were the POSIX-only fork case and three Windows symlink-privilege cases.
The warning was Chroma's known legacy embedding-function configuration
deprecation warning. No Gemini live or integration-marked test ran.

## Static checks

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m compileall -q backend tests
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pip check
git diff --check
```

Results: all exit 0; compileall emitted no output, pip reported
`No broken requirements found.`, and diff check reported no whitespace errors.

## Self-review

- Multiple live adapters for the same normalized root share one client/state;
  only the last finalizer performs teardown.
- Adapter construction failures release exactly the reference they acquired.
- A running bound method and an entered snapshot context retain the adapter, so
  teardown cannot precede writer/reader cleanup.
- Client close occurs while acquisition is serialized and before the ownership
  file is closed. Reentrant acquisition during close is rejected.
- Client-close failure leaves a zero-count state and ownership lease registered,
  rejecting same-process and competing-process reopen until exit.
- Fork children clear inherited registries/caches, reject inherited adapters,
  and their finalizers do not close the parent's client or ownership lease.
- Successful last-adapter teardown permits same-process and subprocess reopen;
  persisted snapshots and records remain readable.
- No retry or suppression was added for the known Chroma HNSW flake.
- The production diff is limited to Chroma lifecycle ownership; tests cover GC,
  cycles, shared adapters, active calls, constructor failure, fork safety,
  close ordering/failure, reopen, and handle stress. The README addition is a
  direct contract description rather than unrelated documentation churn.

## Independent-review fix: interrupted finalizer registration

Independent review of commit `499fd48` found one Important constructor window.
If `weakref.finalize` registered the callback and a `BaseException` interrupted
the constructor before finalizer setup completed, the exception handler released
the shared state explicitly and the registered callback later released it again.
With another live same-root adapter, that double decrement closed the surviving
adapter's Chroma client and ownership lease.

A deterministic real-Chroma regression wraps `weakref.finalize`, registers the
real callback, then raises `KeyboardInterrupt`. It keeps a first same-root
adapter alive and checks its adapter count, client, ownership lease, and public
inspection behavior after the interrupted second adapter is collected.

RED against `499fd48`:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest tests/test_chroma_vector_store.py::test_finalizer_registration_interruption_preserves_same_root_adapter -q -p no:cacheprovider --basetemp .pytest_tmp/t30-red-finalizer -rs
```

Result: exit 1; the assertion `state.adapter_count == 1` failed because the
actual count was `0` after explicit cleanup and finalization both ran.

The minimal fix assigns every adapter acquisition a unique lease token held in
the shared persistence state. Explicit constructor cleanup and the finalizer use
the same token. Whichever path releases it first removes the token; every later
release is an idempotent no-op. `adapter_count` is derived from the live lease
set, so the count cannot diverge from release ownership.

Initial GREEN and directly adjacent cases:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest tests/test_chroma_vector_store.py -q -p no:cacheprovider --basetemp .pytest_tmp/t30x-green -k 'finalizer_registration_interruption or adapter_setup_failure or last_shared_adapter' -rs
```

Result: exit 0; `3 passed, 175 deselected in 7.60s`.

Focused lifecycle suite:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest tests/test_chroma_vector_store.py -q -p no:cacheprovider --basetemp .pytest_tmp/t30x-life -k 'non_owning_process or fork_discards or concurrent_subprocess_writer or ownership_released or unused_persistence_roots or last_shared_adapter or active_snapshot_context or active_writer_retains or adapter_setup_failure or finalizer_registration_interruption or finalization_during_client_construction or teardown_rejects or failed_client_teardown' -rs
```

Result: exit 0; `18 passed, 1 skipped, 159 deselected in 22.50s`. The
POSIX-only fork case was skipped on Windows.

Complete Chroma suite:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest tests/test_chroma_vector_store.py -q -p no:cacheprovider --basetemp .pytest_tmp/t30x-chroma -rs
```

Result: exit 0; `177 passed, 1 skipped in 42.42s`.

Relevant vector-store contract, concurrency, search, and security selection:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest tests/test_vector_store_contract.py tests/test_semantic_synchronization.py tests/test_semantic_search.py tests/test_embedding_security.py -q -p no:cacheprovider --basetemp .pytest_tmp/task30-adjacent-lifecycle -k 'concurrent or snapshot or owner or process or chroma or credential or leak or security' -rs
```

Result: exit 0; `524 passed, 171 deselected in 3.95s`.

The 550 populated-root helper was rerun after the review fix:

```powershell
$env:PYTHONPATH = (Get-Location).Path
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' .pytest_tmp/task30_handle_stress.py .pytest_tmp/task30-stress-550-resume
```

Result: exit 0; `stress-ok roots=550 start_handles=276 end_handles=283
pid=49012` after every root and final reopen left both registries empty.

Fresh full offline suite:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pytest -q -m 'not gemini_live and not integration' -p no:cacheprovider --basetemp .pytest_tmp/t30xfull1 -rs
```

Result: exit 0; `1797 passed, 4 skipped, 2 deselected, 1 warning in 152.08s`.
Skips were the POSIX fork test and three Windows symlink-privilege cases. The
warning was the known Chroma embedding-function configuration deprecation.
No HNSW flake occurred and no retry or suppression was added.

Post-review static checks:

```powershell
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m compileall -q backend tests
& 'C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe' -m pip check
git diff --check
```

Results: all exit 0; compileall emitted no output, pip reported
`No broken requirements found.`, and diff check reported no whitespace errors.
