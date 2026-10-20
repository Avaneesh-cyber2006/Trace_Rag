# Git History Analyzer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Module 7 Git History Analyzer: a read-only, offline analyzer that converts a local Git repository's commit history into a deterministic, immutable `GitHistoryInventory` of historical facts — commit DAG, file changes, rename/copy lineage, author/committer metadata, and bounded co-change indexes — with no new dependency, no network, and no mutation of the analyzed repository. This document is the executable plan for that future implementation; it creates no production code or tests itself.

**Architecture:** Hybrid: `GitAdapter` (sole subprocess user) -> `GitHistoryParser` (pure transformation of raw Git bytes into validated frozen models) -> `GitHistoryAnalyzer` (orchestrates adapter + parser + index construction) -> `GitHistoryInventory` (immutable dataclass with MappingProxyType published indexes). Standard library only, no new dependency. GitAdapter follows Module 1 safety posture: empty env, `GIT_NO_REPLACE_OBJECTS=1`, `GIT_NO_LAZY_FETCH=1`, empty hooks path, `core.hooksPath`, all `GIT_*` vars dropped except `PATH`. No network, no mutation, no checkout/reset/rebase/merge.

**Tech Stack:** Python 3.12+, standard library (`subprocess`, `dataclasses`, `enum`, `types`, `collections.abc`, `hashlib`, `json`), pytest 8+, git CLI 2.29+. All Git commands invoked as `git -C <validated_path> <arg list>`, `shell=False`.

**Spec:** `docs/superpowers/specs/2026-10-18-git-history-analyzer-design.md` (the clarified, authoritative contract for this plan; every task references exact sections and model fields).

**Global Constraints** (from the spec; each line restates a binding requirement, quoted or expanded from the cited section — the spec text remains authoritative):
- Hybrid architecture: GitAdapter + pure GitHistoryParser + GitHistoryAnalyzer. No new dependency. Standard library only.
- Immutable, frozen/slotted dataclasses; published indexes are MappingProxyType snapshots built in `__post_init__` from copied backing stores.
- First-parent-only diff semantics; original Git parent ordering preserved.
- Strict metadata/path parsing; copy-source lineage distinguished from actual modifications.
- No causal inference; no network; no mutation of analyzed repository.
- No Modules 1-6 production changes; no Modules 8-10 functionality.
- Documentation-only round: spec defines behavior, not an implementation plan; no production code or tests created by this document.
- `GIT_NO_REPLACE_OBJECTS=1` set for every Git invocation.
- Cumulative output budget: 256 MiB internal default, not configurable; checked after each chunk.
- Global co-change pair budget: 10,000,000 unique unordered normalized pairs, not configurable.
- 100,000-commit hard cap with N+1 lookahead (request 100,001; truncate if exceeded).
- Per-command timeout: 30s default; aggregate analysis timeout: 300s default.
- Per-command output cap: 50 MB default, enforced during reading via concurrent reader threads.
- Path filter selects commits only; every selected commit retains its full first-parent file-change set.
- `PATH_FILTER_APPLIED` recorded in `incomplete_reasons`.
- SHA-1 40-hex and SHA-256 64-hex commit-ID validation.
- Timezone normalization: Git writes `±HHMM` -> normalized to `±HH:MM`; missing metadata -> `+00:00` with `MISSING_METADATA` reason.
- `GIT_NO_LAZY_FETCH=1` prevents promisor/partial-clone lazy fetches; `--filter` never passed.
- No `git clone`, `fetch`, `pull`, `push`, `checkout`, `reset`, `rebase`, `merge`, `commit`, `tag` (write), `gc`, `prune`, `filter-branch`.
- `core.hooksPath` set to empty dir via `-c`; no hooks execution.
- Commit messages treated as untrusted data (stored, never executed or interpreted).
- Error messages must not include env vars, credentials, or remote URLs beyond what is necessary.
- Secrets in historical commits are not redacted (Module 7 stores facts; redaction is Module 10 concern).
- `detached_head` records repository state, not a history-completeness signal; does not affect `history_is_complete`.
- Root commits baseline = empty tree (SHA-1 `4b825dc642cb6eb9a060e54bf8d69288fbee4904`; SHA-256 `6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321`); fixed constants, never computed at runtime.
- Non-root, non-merge commit: baseline = first parent's tree; compare first parent tree vs. commit's tree.
- Merge commit: baseline = first parent's tree; compare first parent tree vs. merge commit's tree; only first-parent changes emitted; other parents' changes not reported; `MERGE_SEMANTICS_PARTIAL` added to `incomplete_reasons` whenever any merge commit is present.
- Shallow boundary: `git rev-parse --is-shallow-repository` detected; shallow file read from `--git-common-dir`; intersected with reachable commit set; `--git-common-dir` for linked worktrees share shallow metadata.
- Root commit: baseline = empty tree; compare empty tree vs. commit's tree.
- `path_filter`: literal pathspec `:(literal)<path>` after `--`; selects commits touching at least one listed path; changes list NOT filtered; `PATH_FILTER_APPLIED` recorded.
- `co_changed_paths` symmetry: B ∈ partners(A) iff A ∈ partners(B); pair normalized as `(min(A,B), max(A,B))`; same pair in multiple commits counts once toward global budget.
- Commit contribution = number of NEW unique normalized pairs not already materialized from earlier-processed commits; whole contribution tested against remaining budget BEFORE any index mutation; overflow omits commit atomically.
- Skipped commit's genuinely touched paths appended to `co_change_incomplete_paths`; `CO_CHANGE_LIMIT_EXCEEDED` recorded.
- Processing order: commits in deterministic inventory order (newest-first).
- 1,000-touched-path per-commit cap still applies (operates on different dimension).
- Copy source (`old_path` of `COPIED` change) is lineage evidence, NOT counted as touched by that commit; NOT added to `commits_touching_path`; NOT a co-change partner.
- Missing historical change evidence distinguishable from genuinely unchanged commit: the latter has empty change list and is absent from `commits_with_unavailable_baseline`.
- `history_is_complete` True only when `incomplete_reasons` is empty.
- Incomplete reasons: `SHALLOW_HISTORY`, `PARENT_UNAVAILABLE`, `MAX_COMMITS_REACHED`, `MERGE_SEMANTICS_PARTIAL`, `MERGES_EXCLUDED`, `MISSING_METADATA`, `PATH_FILTER_APPLIED`, `CO_CHANGE_LIMIT_EXCEEDED`.
- `OUTPUT_TOO_LARGE` and `COMMAND_TIMEOUT` are NOT incomplete reasons; those raise `GitOutputTooLargeError` / `GitCommandTimeoutError` and no inventory returned.
- `MAX_OUTPUT_EXCEEDED` removed from enum; was historical finding only.

---

## Review Focus

These are the inputs and conditions the specification implies but that no single task's tests obviously exercise. Each line names the behavior a reasonable person would expect, most likely to bite first. Every line is pinned by a test in the task that owns the code (the owning task is named in parentheses).

1. **A depth-one shallow clone is mistaken for a root commit** (T13, T14, T19). The boundary commit's raw object still carries a `parent` line. It must be retained as a non-root commit with its real parent ID, marked `PARENT_UNAVAILABLE` + `SHALLOW_HISTORY`, and produce zero fabricated file changes — never an empty-tree comparison that reports every file as added.
2. **A co-change pair is counted twice, or the index becomes asymmetric** (T18, T23). The same path pair in two commits is ONE budget unit; `co_changed_paths` must satisfy `B in partners(A) == A in partners(B)` for every materialized pair.
3. **A path filter silently drops real changes** (T12, T19, T21). `path_filter` selects commits only. A selected commit that touched both a filtered and an unrelated path must retain BOTH changes and its co-change partners.
4. **A budget overrun leaves a half-materialized commit** (T18, T23). Overflow must omit a commit's co-change contribution atomically — no partial partners — while the ordinary file-change and commit indexes stay complete.
5. **A Git command deadlocks or leaks on Windows** (T9). stdout and stderr must be drained concurrently; a timed-out or oversize command must terminate and reap the child, with no orphaned `git.exe`.

## Task Map

| # | Task | Produces |
|---|---|---|
| 1 | Exceptions and error classification | `backend/git_history/exceptions.py`, `backend/git_history/__init__.py` |
| 2 | Enums, identities, and validation helpers | `models.py` (enums), `identities.py`, `invariants.py` |
| 3 | `GitCommit` and `GitHistoryScope` models | `models.py` |
| 4 | `GitFileChange` invariants and canonical change IDs | `models.py`, `identities.py` |
| 5 | `GitHistoryInventory` and immutable snapshots | `models.py` |
| 6 | Pure raw commit-object and batch parsing | `parser.py` |
| 7 | Pure NUL-delimited diff-tree parsing | `parser.py` |
| 8 | GitAdapter environment and command security | `adapter.py` (`GitAdapter`, `CommandResult`, `run_raw`, `build_git_environment`, `read_shallow_boundary_ids`, `MINIMUM_GIT_VERSION`), `tests/conftest.py` (`git_repo` fixture) |
| 9 | Bounded subprocess I/O and process cleanup | `adapter.py` (`run_raw` bounds, `terminate_process`) |
| 10 | Aggregate timeout and cumulative output accounting | `adapter.py` (`consume_output_budget`, `check_aggregate_deadline`, `cumulative_output_bytes`) |
| 11 | Repository validation, version, and revision handling | `adapter.py` (`GitRepositoryContext`, `git_version`, `inspect_repository`, `resolve_revision`, `is_detached_head`, `is_unborn_head`) |
| 12 | Commit enumeration, path filters, and limits | `adapter.py` (`CommitSelection`, `enumerate_commits`) |
| 13 | Authoritative batch commit metadata and DAG parsing | `adapter.py` (`fetch_commit_objects`), `parser.py` |
| 14 | Root, first-parent, and unavailable-baseline resolution | `adapter.py` (`BaselineResolution`, `resolve_baseline`) |
| 15 | Root/linear/merge file-change extraction | `adapter.py` (`diff_trees`, `commit_tree_id`, `changes_for_commit`) |
| 16 | Rename/copy similarity and option combinations | `adapter.py` (`build_diff_flags`) |
| 17 | Deterministic commit and path indexes | `analyzer.py` (`build_commits_by_id`, `build_changes_by_commit`, `build_changes_by_path`, `build_commits_touching_path`, `touched_paths`) |
| 18 | Co-change pair accounting and budgets | `analyzer.py` (`CoChangeResult`, `build_co_change_index`, `CO_CHANGE_PAIR_BUDGET`, `CO_CHANGE_PER_COMMIT_PATH_CAP`) |
| 19 | Full analyzer orchestration and completeness reasons | `analyzer.py` (`GitHistoryAnalyzer`, `MAX_CHANGES`) |
| 20 | Convenience API, exports, and model integration | `analyzer.py`, `__init__.py` |
| 21 | Module 1/2/6 integration and README usage | `README.md`, `backend/git_history/analyzer.py` (conditional) |
| 22 | Adversarial Git security tests | `tests/test_git_history_security.py` |
| 23 | Scaling, determinism, and sensitivity probes | `tests/test_git_history_scaling.py` |
| 24 | Complete regression verification and whole-module review | `docs/superpowers/verification/module-7-git-history-analyzer.md` |

## File Ownership Map

Each file is owned by exactly one task. A task that must extend a file another task owns MUST wait for that task to land.

| File | Owner | Also modified by |
|---|---|---|
| `backend/git_history/__init__.py` | 1 (minimal package marker) | 20 (full public exports) |
| `backend/git_history/exceptions.py` | 1 | — |
| `backend/git_history/models.py` | 2 (enums) | 3 (`GitCommit`, `GitHistoryScope`), 4 (`GitFileChange`), 5 (`GitHistoryInventory`) |
| `backend/git_history/identities.py` | 2 | 4 |
| `backend/git_history/invariants.py` | 2 | — |
| `backend/git_history/parser.py` | 6 | 7, 13 |
| `backend/git_history/adapter.py` | 8 | 9, 10, 11, 12, 13, 14, 15, 16 |
| `backend/git_history/analyzer.py` | 17 | 18, 19, 20, 21 |
| `README.md` | 21 | — |
| `tests/conftest.py` | 8 (the whole `git_repo` fixture, including every helper) | — (no later task may modify it) |
| `tests/test_git_history_exceptions.py` | 1 | — |
| `tests/test_git_history_models.py` | 2 | 3, 4, 5 |
| `tests/test_git_history_identities.py` | 2 | 4 |
| `tests/test_git_history_parser.py` | 6 | 7, 13 |
| `tests/test_git_history_adapter.py` | 8 | 9, 10, 11, 12, 13, 14, 15, 16 |
| `tests/test_git_history_analyzer.py` | 17 | 18, 19 |
| `tests/test_git_history_api.py` | 20 | — |
| `tests/test_git_history_integration.py` | 21 | — |
| `tests/test_git_history_security.py` | 22 | — |
| `tests/test_git_history_scaling.py` | 23 | — |
| `docs/superpowers/verification/module-7-git-history-analyzer.md` | 24 | — |

## Dependency Graph

```text
 1 ──► 2 ──► 3 ──► 4 ──► 5
 │      │      │      │
 │      └──────┴──► 6 ──► 7 ──┐
 │                              │
 └──► 8 ──► 9 ──► 10 ──► 11 ──► 12
               │                 │
               └────► 13 ◄───────┘
                      │
                      └──► 14 ──► 15 ──► 16
                              ▲      ▲
                              │      │
                              └──────┘ (14 also consumes 7)
 5 ──► 17 ──► 18 ──► 19 ──► 20 ──► 21
                                ├──► 22 ──► 23
                                └──► 24 (depends on all)
```

Linearization rule: tasks may be executed in the numeric order 1-24. The only cross-task interface risk is `models.py` (tasks 2-5), `parser.py` (6, 7, 13), `adapter.py` (8-16), and `analyzer.py` (17-19); each later task in a group must state the exact names it consumes from the earlier one in its **Interfaces** block.

## Acceptance Gates (all required before the module is complete)

1. Every Module 7 unit, parser, adapter, analyzer, API, integration, security, and scaling test passes.
2. Module 1-6 production behavior is unchanged (empty diff for `backend/repository_loader`, `backend/file_scanner`, `backend/code_parser`, `backend/code_chunker`, `backend/embedding_vector_store`, `backend/code_graph`, `requirements.txt`, `pytest.ini`).
3. The full offline repository suite passes: `python -m pytest -q -m "not integration and not gemini_live" -rs`.
4. `python -m compileall -q backend tests` exits 0.
5. `python -m pip check` reports `No broken requirements found.`
6. `git diff --check` exits 0.
7. No new runtime dependency; `requirements.txt` unchanged.
8. Independent whole-module review reports no Critical or Important findings.
9. Verification evidence written to `docs/superpowers/verification/module-7-git-history-analyzer.md`.
10. No Modules 8-10 functionality; no merge or push; the module awaits a separate user-approved integration phase.

## Execution Boundaries (binding on the future implementation phase)

- Implementation MUST run in an isolated feature worktree, NOT on primary `main`.
- Branch (mandatory): `codex/module-7-git-history-analyzer`. Sibling worktree (mandatory): `C:\Users\avane\Desktop\Studies\Projects\Trace_Rag_Module7_Worktree`. The worktree MUST be a sibling directory, never nested inside the primary repository. These names are fixed by this plan, not open to the implementer.
- The execution base is the reviewed implementation-plan commit, including any later approved plan corrections.
- Every task follows RED -> minimal GREEN -> focused regression -> commit -> independent review.
- Corrections require re-review before the task is closed.
- No integration, merge, or push is part of implementation completion.
- Production caps are never weakened to make tests fast; large-budget tests use test-only injection.


## TDD Convention (binding on every task)

Every task below uses the same RED -> GREEN discipline, adapted so that **the first failing test always fails on the intended missing behavior, never on an import or environment error**:

1. **Scaffolding step (first).** When a task introduces a new module, create it with the exact public names and signatures the task's **Interfaces** block specifies, but with the behavior the task pins left unimplemented (placeholders). This is setup, not the deliverable.
2. **RED step.** Write the behavior test against that scaffold. Run it. It MUST fail on a named assertion (e.g. `assert compute_change_id(...) == "<expected digest>"` -> `AssertionError`), not with `ModuleNotFoundError`, `ImportError`, `SyntaxError`, or a missing interpreter. A task whose RED fails with an import error is NOT a valid RED: fix the scaffolding and re-run.
3. **GREEN step.** Implement the minimum that satisfies the task's assertions. No speculative generality, no extra public names.
4. **Regression step.** Run the neighboring suites listed in the task. They must stay green.
5. **Commit step.** One commit per task, message given in the task.
6. **Independent review step.** A fresh-context reviewer checks the task against its checklist. Findings are corrected and re-reviewed before the task is closed.

Command form used throughout (Windows PowerShell, from the worktree root):

```powershell
$env:PYTHONPATH = "<worktree-root>"
& "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe" -m pytest tests/test_git_history_models.py -q -m "not integration and not gemini_live" -rs
```

`<worktree-root>` is the Module 7 worktree root created at execution time (never the primary repository). If the worktree's own virtual environment is used instead, its `Scripts\python.exe` replaces the path above; the pytest arguments do not change.

**Command abbreviation.** Throughout this plan, a command written as `... -m pytest <files> -q -m "not integration and not gemini_live" -rs` is the abbreviated form of exactly the interpreter invocation defined above. Expand it by substituting the interpreter path; never invent a different selector.

**Test helpers and module aliases (binding).** The test snippets in this plan use local helper functions and module aliases. Each test file MUST define the ones its own snippets use, in the file's Step 1/Step 2, before the RED run — otherwise the test errors with `NameError` instead of failing on an assertion:

| Test file | Helpers and aliases it must define |
|---|---|
| `tests/test_git_history_exceptions.py` | (none) |
| `tests/test_git_history_models.py` | `_commit(...)`, `_scope(...)`, `_change(...)`, `_inventory(...)` — each returning a valid instance of the corresponding model with overridable keyword arguments |
| `tests/test_git_history_identities.py` | (none beyond the imports shown) |
| `tests/test_git_history_parser.py` | `_batch(payload: bytes) -> bytes` — wraps a raw commit body into a `cat-file --batch` frame |
| `tests/test_git_history_adapter.py` | `_repo(tmp_path, git_repo) -> Path` and `_big_repo(tmp_path, git_repo) -> Path` (see Task 9), `adapter() -> GitAdapter` (a module-level helper returning a fresh `GitAdapter()`), plus `from pathlib import Path` and `from backend.git_history import adapter as adapter_module` |
| `tests/test_git_history_analyzer.py` | `_change(...)`, `_commits(...)`, plus `from backend.git_history import analyzer as analyzer_module` and `from backend.git_history.analyzer import GitHistoryAnalyzer` (import the class from ITS module: `backend/git_history/__init__.py` is only a package marker until Task 20) |
| `tests/test_git_history_api.py` | `from backend.git_history import GitHistoryAnalyzer, analyze_git_history` |
| `tests/test_git_history_integration.py` | (none) |
| `tests/test_git_history_security.py` | `_repo_state(path) -> tuple`, `FORBIDDEN_SUBCOMMANDS`, `_forbidden_subcommand(args)`, plus `from pathlib import Path` and `from backend.git_history import adapter as adapter_module` |
| `tests/test_git_history_scaling.py` | `from backend.git_history import adapter as adapter_module` and `from backend.git_history import analyzer as analyzer_module` |

**Helper construction contract.** The model helpers `_commit(...)`, `_scope(...)`, `_change(...)`, `_inventory(...)`, and `_commits(...)` pass every override through VERBATIM and perform no pre-validation, so a test can construct a deliberately INVALID instance to prove rejection. `_commit(...)` derives `is_root`/`is_merge` from the parent count ONLY when the caller does not supply them explicitly; when the caller DOES supply them, the supplied value is passed through unchanged so the model's own validation can reject it.

**Required imports.** Modules used by the snippets that must be imported in the file that uses them: `sys` (Task 12's platform skip), `pytest` (every file), `pathlib.Path` (Tasks 8, 9, 22), `hashlib.sha256` (Task 2's identity test, imported as `from hashlib import sha256`), `dataclasses.FrozenInstanceError` (Task 3), `types.MappingProxyType` (Task 5), `io` and `subprocess` (Task 9's deterministic-timeout test), `backend.git_history.invariants.EMPTY_TREE_SHA1` (Task 14 — the SINGLE home of this constant; the adapter re-exports it, but the import in the test file comes from `invariants`), and the production classes each file touches — the set to import is: `GitChangeType`, `GitIncompleteReason`, `GitErrorCode`, `GitCommit`, `GitFileChange`, `GitHistoryScope`, `GitHistoryInventory`, `GitHistoryAnalyzer`, `RawCommitObject`, `RawFileChange`, `CommandResult`, `GitRepositoryContext`, `CommitSelection`, `BaselineResolution`, `GraphNode`, `GraphNodeKind`, `CodeGraphInventory`, `ParsedLanguage`, `ParseStatus`, `GitHistoryError` and its subclasses (`GitUnavailableError`, `GitRepositoryError`, `GitRevisionError`, `GitCommandError`, `GitCommandTimeoutError`, `GitOutputTooLargeError`, `GitParseError`, `GitInventoryMismatchError`, `GitConfigurationError`). The production FUNCTIONS the snippets call must be imported too, unqualified, from their owning module: `compute_change_id` and `require_object_id` / `require_timezone` / `require_tuple` / `snapshot_mapping` (`backend.git_history.identities` / `.invariants`), `parse_batch_stream` / `normalize_timezone` / `parse_name_status` (`backend.git_history.parser`), `build_git_environment` (`backend.git_history.adapter`), `touched_paths` / `build_commits_by_id` / `build_changes_by_commit` / `build_changes_by_path` / `build_commits_touching_path` / `build_co_change_index` (`backend.git_history.analyzer`).

**Adapter test helpers (defined once in `tests/test_git_history_adapter.py`, Tasks 8-16).** These two call sites appear throughout Tasks 8-16, so their definitions are fixed here and MUST NOT be re-invented per task:

```python
from pathlib import Path

from backend.git_history import adapter as adapter_module
from backend.git_history.adapter import GitAdapter


def adapter() -> GitAdapter:
    """Return a fresh adapter with the default configuration."""
    return GitAdapter()


def _repo(tmp_path: Path, git_repo) -> Path:
    """Create a small local repository and return its path."""
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    return repo.path


def _big_repo(tmp_path: Path, git_repo) -> Path:
    """Create a local repository whose HEAD commit MESSAGE exceeds 64 KiB.

    A large BLOB does NOT enlarge the commit object (`git cat-file -p HEAD`
    prints the metadata, which references a blob by hash), so the message is
    what must be large for the command's output to exceed a cap.
    """
    repo = git_repo()
    big_message = "x" * (70 * 1024)
    repo.commit({"a.txt": b"1\n"}, message=big_message)
    return repo.path
```

---

### Task 1: Exceptions and Error Classification

**Prerequisites:** none. This is the first task in the module.

**Files:**
- Create: `backend/git_history/__init__.py` (package marker only — no public exports yet; Task 20 fills them in)
- Create: `backend/git_history/exceptions.py`
- Test: `tests/test_git_history_exceptions.py`

**Spec references:** §4.6 (exception hierarchy), §15 (error handling mapping), §11 (operational failures raise, no partial inventory).

**Inputs / Outputs:** Input: none (pure declarations). Output: the ten exception classes named in §4.6.

**Interfaces:**
- Consumes: nothing.
- Produces (importable from `backend.git_history.exceptions`): `GitHistoryError`, `GitUnavailableError`, `GitRepositoryError`, `GitRevisionError`, `GitCommandError`, `GitCommandTimeoutError`, `GitOutputTooLargeError`, `GitParseError`, `GitInventoryMismatchError`, `GitConfigurationError`.

- [ ] **Step 1: Scaffolding.** Create `backend/git_history/__init__.py` containing only a module docstring. Create `backend/git_history/exceptions.py` defining all ten class names, each currently deriving directly from `Exception` (the hierarchy is the behavior this task pins).
- [ ] **Step 2: Write the failing test** in `tests/test_git_history_exceptions.py`:
  ```python
  from backend.git_history.exceptions import (
      GitCommandError, GitCommandTimeoutError, GitConfigurationError,
      GitHistoryError, GitInventoryMismatchError, GitOutputTooLargeError,
      GitParseError, GitRepositoryError, GitRevisionError, GitUnavailableError,
  )

  def test_every_error_descends_from_git_history_error():
      for cls in (GitUnavailableError, GitRepositoryError, GitRevisionError,
                  GitCommandError, GitCommandTimeoutError,
                  GitOutputTooLargeError, GitParseError,
                  GitInventoryMismatchError, GitConfigurationError):
          assert issubclass(cls, GitHistoryError), cls.__name__

  def test_command_failures_descend_from_git_command_error():
      assert issubclass(GitCommandTimeoutError, GitCommandError)
      assert issubclass(GitOutputTooLargeError, GitCommandError)

  def test_error_message_is_preserved():
      assert str(GitParseError("bad header at byte 12")) == "bad header at byte 12"
  ```
- [ ] **Step 3: Run RED.** Run: `& "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe" -m pytest tests/test_git_history_exceptions.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError` on `test_every_error_descends_from_git_history_error` (the flat scaffold makes `issubclass(cls, GitHistoryError)` false). An `ImportError` here means the scaffolding is wrong — fix it, do not proceed.
- [ ] **Step 4: Minimal GREEN.** In `exceptions.py`, make `GitHistoryError(Exception)` the base and re-parent the other classes: `GitUnavailableError`, `GitRepositoryError`, `GitRevisionError`, `GitCommandError`, `GitParseError`, `GitInventoryMismatchError`, `GitConfigurationError` extend `GitHistoryError`; `GitCommandTimeoutError` and `GitOutputTooLargeError` extend `GitCommandError`. Add a one-line docstring to each class. No `__init__`, no extra attributes, no `error_code` field (`GitErrorCode` is reserved per §4.1 and is NOT wired up in v1).
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests in the file PASS.
- [ ] **Step 6: Neighboring regression.** Run: `& "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe" -m pytest tests -q -m "not integration and not gemini_live" -rs`
  Expected: PASS; the pre-existing suite count is unchanged from the recorded baseline (no new failures, no new skips).
- [ ] **Step 7: Security/contract checks.** Confirm `backend/git_history/exceptions.py` imports nothing from `backend.*` and no module outside `backend/git_history/` was touched: `git diff --name-only HEAD~1` lists only `backend/git_history/__init__.py`, `backend/git_history/exceptions.py`, `tests/test_git_history_exceptions.py`.
- [ ] **Step 8: Commit.** `git add backend/git_history/__init__.py backend/git_history/exceptions.py tests/test_git_history_exceptions.py` then `git commit -m "feat: add git history exception hierarchy"`.
- [ ] **Step 9: Independent review checklist.** [ ] Exactly the ten §4.6 classes exist. [ ] Hierarchy matches §4.6 exactly. [ ] No `error_code` field or `GitErrorCode` usage. [ ] The RED failure was an `AssertionError`, not an import error. [ ] No production file outside `backend/git_history/` changed.
- [ ] **Step 10: Acceptance evidence.** The GREEN command output (all PASS), the regression output, and the `git diff --name-only` listing are pasted into the task's review note.

---

### Task 2: Enums, Identities, and Validation Helpers

**Prerequisites:** Task 1 (exceptions).

**Files:**
- Create: `backend/git_history/models.py` (enum definitions only in this task; model dataclasses land in Tasks 3-5)
- Create: `backend/git_history/identities.py`
- Create: `backend/git_history/invariants.py`
- Test: `tests/test_git_history_models.py`, `tests/test_git_history_identities.py`

**Spec references:** §4.1 (enums), §10 (change identity), §7.1 (canonical path identity, object-ID validation), §4 (frozen/slotted dataclasses, MappingProxyType snapshots).

**Inputs / Outputs:** Input: commit ID, change type, and path strings. Output: enum members, a canonical SHA-256 change ID, and shared validation helpers.

**Interfaces:**
- Consumes: `backend.git_history.exceptions.GitParseError`, `GitRevisionError` (Task 1).
- Produces (from `backend.git_history.models`): `GitChangeType` (8 members: `ADDED`, `MODIFIED`, `DELETED`, `RENAMED`, `COPIED`, `TYPE_CHANGED`, `UNMERGED`, `UNKNOWN`), `GitIncompleteReason` (8 members: `SHALLOW_HISTORY`, `MAX_COMMITS_REACHED`, `PARENT_UNAVAILABLE`, `MERGE_SEMANTICS_PARTIAL`, `MERGES_EXCLUDED`, `MISSING_METADATA`, `PATH_FILTER_APPLIED`, `CO_CHANGE_LIMIT_EXCEEDED`), `GitErrorCode` (8 members: `NOT_A_REPOSITORY`, `GIT_UNAVAILABLE`, `INVALID_REVISION`, `COMMAND_FAILED`, `COMMAND_TIMEOUT`, `OUTPUT_TOO_LARGE`, `PARSE_ERROR`, `INVENTORY_MISMATCH`). All are `(str, Enum)`.
- Produces (from `backend.git_history.identities`): `compute_change_id(commit_id: str, change_type: GitChangeType, old_path: str | None, new_path: str | None) -> str` — a lowercase SHA-256 hex digest.
- Produces (from `backend.git_history.invariants`): `require_object_id(value: str, field_name: str) -> str`; `require_timezone(value: str, field_name: str) -> str`; `require_tuple(value: object, field_name: str) -> tuple`; `snapshot_mapping(mapping: object, field_name: str) -> MappingProxyType`; `EMPTY_TREE_SHA1 = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"`; `EMPTY_TREE_SHA256 = "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321"`.

- [ ] **Step 1: Scaffolding.** Create `models.py` with the three enum classes present but with placeholder member values (e.g. every member mapped to `"?"`); create `identities.py` with `compute_change_id` returning `""`; create `invariants.py` with the five helpers present but returning the input unchanged and the two tree constants as `""`.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_models.py` and `tests/test_git_history_identities.py`:
  ```python
  def test_change_type_members_and_values():
      assert GitChangeType.ADDED.value == "added"
      assert GitChangeType.MODIFIED.value == "modified"
      assert GitChangeType.DELETED.value == "deleted"
      assert GitChangeType.RENAMED.value == "renamed"
      assert GitChangeType.COPIED.value == "copied"
      assert GitChangeType.TYPE_CHANGED.value == "type_changed"
      assert GitChangeType.UNMERGED.value == "unmerged"
      assert GitChangeType.UNKNOWN.value == "unknown"

  def test_incomplete_reason_members_and_values():
      assert GitIncompleteReason.SHALLOW_HISTORY.value == "shallow_history"
      assert GitIncompleteReason.MAX_COMMITS_REACHED.value == "max_commits_reached"
      assert GitIncompleteReason.PARENT_UNAVAILABLE.value == "parent_unavailable"
      assert GitIncompleteReason.MERGE_SEMANTICS_PARTIAL.value == "merge_semantics_partial"
      assert GitIncompleteReason.MERGES_EXCLUDED.value == "merges_excluded"
      assert GitIncompleteReason.MISSING_METADATA.value == "missing_metadata"
      assert GitIncompleteReason.PATH_FILTER_APPLIED.value == "path_filter_applied"
      assert GitIncompleteReason.CO_CHANGE_LIMIT_EXCEEDED.value == "co_change_limit_exceeded"

  def test_error_code_members_and_values():
      assert GitErrorCode.NOT_A_REPOSITORY.value == "not_a_repository"
      assert GitErrorCode.GIT_UNAVAILABLE.value == "git_unavailable"
      assert GitErrorCode.INVALID_REVISION.value == "invalid_revision"
      assert GitErrorCode.COMMAND_FAILED.value == "command_failed"
      assert GitErrorCode.COMMAND_TIMEOUT.value == "command_timeout"
      assert GitErrorCode.OUTPUT_TOO_LARGE.value == "output_too_large"
      assert GitErrorCode.PARSE_ERROR.value == "parse_error"
      assert GitErrorCode.INVENTORY_MISMATCH.value == "inventory_mismatch"

  def test_enums_are_str_subclasses():
      assert isinstance(GitChangeType.ADDED, str)
  ```
  In `tests/test_git_history_identities.py`:
  ```python
  def test_change_id_is_canonical_sha256_over_sorted_json():
      digest = compute_change_id("a" * 40, GitChangeType.MODIFIED, None, "src/app.py")
      assert digest == sha256(
          b'git-history:change:v1:{"change_type":"modified","commit_id":"' + b"a" * 40
          + b'","new_path":"src/app.py","old_path":null}'
      ).hexdigest()
      assert len(digest) == 64 and digest == digest.lower()

  def test_change_id_distinguishes_null_and_path():
      assert compute_change_id("b" * 40, GitChangeType.ADDED, None, "x.py") != \
             compute_change_id("b" * 40, GitChangeType.ADDED, "x.py", None)

  def test_change_id_is_stable_across_calls():
      args = ("c" * 40, GitChangeType.RENAMED, "old.py", "new.py")
      assert compute_change_id(*args) == compute_change_id(*args)
  ```
  The expected digest in the first test is the literal canonical form from §4.3: the UTF-8 bytes of `git-history:change:v1:` followed by `json.dumps({"commit_id":..., "change_type":..., "old_path":..., "new_path":...}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)`.
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_models.py tests/test_git_history_identities.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError` (enum values are `"?"`; the change-ID digest does not match). Import errors indicate broken scaffolding — fix before proceeding.
- [ ] **Step 4: Minimal GREEN.** Implement the real enum values; implement `compute_change_id` exactly as §4.3 specifies (prefix `git-history:change:v1:`, `json.dumps(..., ensure_ascii=False, separators=(",", ":"), sort_keys=True)`, UTF-8 encode, `hashlib.sha256().hexdigest()`, `change_type` serialized as its string value, `old_path`/`new_path` as strings or `null`); implement the five helpers so that `require_object_id` accepts only `^(?:[0-9a-f]{40}|[0-9a-f]{64})$` and otherwise raises `GitRevisionError`, `require_timezone` accepts only `^[+-]\d{2}:\d{2}$` and otherwise raises `GitParseError`, `require_tuple` raises `GitParseError` for a non-tuple, `snapshot_mapping` raises `GitParseError` for a non-mapping and returns a `MappingProxyType` over a copied dict, and the two empty-tree constants hold the exact fixed values.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests in both files PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_exceptions.py tests -q -m "not integration and not gemini_live" -rs` — expected: PASS with no new failures.
- [ ] **Step 7: Security/contract checks.** Confirm no new dependency: `git diff --name-only HEAD~1` shows only the three new `backend/git_history/` files and the two new test files. Confirm no Module 6 identity namespace (`code-graph:*`) is used anywhere in `identities.py` (§10) — `Select-String -Path backend/git_history/identities.py -Pattern "code-graph"` returns nothing.
- [ ] **Step 8: Commit.** `git add backend/git_history/models.py backend/git_history/identities.py backend/git_history/invariants.py tests/test_git_history_models.py tests/test_git_history_identities.py` then `git commit -m "feat: add git history enums identities and validation helpers"`.
- [ ] **Step 9: Independent review checklist.** [ ] All 24 enum members present with the exact §4.1 values. [ ] `compute_change_id` matches the §4.3 canonical form byte-for-byte. [ ] `require_object_id` rejects uppercase and wrong-length IDs. [ ] `require_timezone` rejects `+0000` and other malformed offsets, and ACCEPTS the normalized `+00:00` form (spec §4.2). [ ] `snapshot_mapping` copies before freezing. [ ] No `code-graph:` namespace reused. [ ] RED was an assertion failure.
- [ ] **Step 10: Acceptance evidence.** GREEN output, regression output, and the exact `compute_change_id` canonical input recorded in the review note.

---

### Task 3: `GitCommit` and `GitHistoryScope` Models

**Prerequisites:** Task 2.

**Files:**
- Modify: `backend/git_history/models.py` (add the two dataclasses; do not touch the enums)
- Test: `tests/test_git_history_models.py`

**Spec references:** §4.2 (`GitCommit`), §4.4 (`GitHistoryScope`), §7.1 (object-ID validation, strict UTF-8), §7 (ordering).

**Inputs / Outputs:** Input: parsed commit fields. Output: validated frozen `GitCommit` and `GitHistoryScope` values.

**Interfaces:**
- Consumes: `GitParseError`, `GitRevisionError` (Task 1); `require_object_id`, `require_timezone`, `require_tuple` (Task 2).
- Produces: `GitCommit` (frozen, slotted dataclass with fields `commit_id: str`, `parent_ids: tuple[str, ...]`, `author_name: str`, `author_email: str`, `author_timestamp: int`, `author_timezone: str`, `committer_name: str`, `committer_email: str`, `committer_timestamp: int`, `committer_timezone: str`, `message: str | None`, `is_merge: bool`, `is_root: bool`) and `GitHistoryScope` (frozen, slotted dataclass with fields `revision: str`, `path_filter: tuple[str, ...]`, `max_commits: int | None`, `include_merges: bool`, `detect_renames: bool`, `detect_copies: bool`, `include_commit_messages: bool`).

- [ ] **Step 1: Scaffolding.** Add `GitCommit` and `GitHistoryScope` to `models.py` as frozen/slotted dataclasses with the exact field names, types, and field order above, but with `__post_init__` doing no validation.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_models.py`:
  ```python
  def test_commit_is_frozen_and_slotted():
      commit = _commit()
      with pytest.raises(FrozenInstanceError):
          commit.commit_id = "z" * 40
      assert not hasattr(commit, "__dict__")

  def test_commit_validates_object_id():
      with pytest.raises(GitRevisionError):
          _commit(commit_id="A" * 40)
      with pytest.raises(GitRevisionError):
          _commit(commit_id="a" * 39)

  def test_commit_preserves_original_parent_order():
      commit = _commit(parent_ids=("b" * 40, "a" * 40))
      assert commit.parent_ids == ("b" * 40, "a" * 40)

  def test_commit_rejects_duplicate_parents():
      with pytest.raises(GitParseError):
          _commit(parent_ids=("a" * 40, "a" * 40))

  def test_merge_and_root_flags_follow_parent_count():
      assert _commit(parent_ids=()).is_root is True
      assert _commit(parent_ids=()).is_merge is False
      assert _commit(parent_ids=("a" * 40, "b" * 40)).is_merge is True

  def test_merge_and_root_flags_are_derived_not_trusted():
      with pytest.raises(GitParseError):
          _commit(parent_ids=(), is_root=False)
      with pytest.raises(GitParseError):
          _commit(parent_ids=("a" * 40, "b" * 40), is_merge=False)
      with pytest.raises(GitParseError):
          _commit(parent_ids=(), is_merge=True)

  def test_commit_accepts_negative_epoch_timestamps():
      assert _commit(author_timestamp=-1).author_timestamp == -1

  def test_commit_rejects_non_utc_style_timezone():
      with pytest.raises(GitParseError):
          _commit(author_timezone="+0000")

  def test_scope_is_frozen_and_tuple_typed():
      scope = _scope()
      with pytest.raises(FrozenInstanceError):
          scope.revision = "other"
      assert isinstance(scope.path_filter, tuple)
  ```
  where `_commit(...)` and `_scope(...)` are module-level helpers in the test file constructing the dataclasses with valid defaults.
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_models.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`Failed` (the un-validating scaffold accepts an uppercase ID, keeps duplicate parents, and allows `+0000`). No `ImportError`.
- [ ] **Step 4: Minimal GREEN.** Add `__post_init__` to `GitCommit` that: validates `commit_id` via `require_object_id`; validates `parent_ids` is a tuple of valid, non-duplicate object IDs in their ORIGINAL order (never sorted); asserts `is_merge == (len(parent_ids) > 1)` and `is_root == (len(parent_ids) == 0)`; requires integer timestamps (negative allowed); validates both timezones via `require_timezone`; requires `message` to be `str` or `None`. Add `__post_init__` to `GitHistoryScope` that requires `path_filter` to be a tuple of strings and `max_commits` to be `None` or a positive int.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests in the file PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_models.py tests/test_git_history_identities.py tests/test_git_history_exceptions.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm the model performs no I/O: `Select-String -Path backend/git_history/models.py -Pattern "open\(|subprocess|os\.|Path\("` returns nothing. Confirm no `__dict__` on the slotted dataclass.
- [ ] **Step 8: Commit.** `git add backend/git_history/models.py tests/test_git_history_models.py` then `git commit -m "feat: add git commit and history scope models"`.
- [ ] **Step 9: Independent review checklist.** [ ] Field names, types, and order match §4.2/§4.4 exactly. [ ] Parent order is never sorted. [ ] Negative epochs accepted. [ ] Timezone regex enforced. [ ] `is_merge`/`is_root` derived, not trusted. [ ] Frozen + slotted confirmed. [ ] No I/O in the model layer.
- [ ] **Step 10: Acceptance evidence.** GREEN and regression output plus one recorded example showing a non-sorted parent list preserved verbatim.

---

### Task 4: `GitFileChange` Invariants and Canonical Change IDs

**Prerequisites:** Tasks 2 and 3.

**Files:**
- Modify: `backend/git_history/models.py` (add `GitFileChange`)
- Modify: `backend/git_history/identities.py` (no new public names expected; adjust only if a helper is required)
- Test: `tests/test_git_history_models.py`, `tests/test_git_history_identities.py`

**Spec references:** §4.3 (`GitFileChange` invariants), §10 (change identity), §9 (copy source is lineage, not a modification), §7 (change ordering).

**Inputs / Outputs:** Input: commit ID, change type, old/new paths, similarity. Output: a validated frozen `GitFileChange`.

**Interfaces:**
- Consumes: `compute_change_id` (Task 2); `GitParseError`, `GitRevisionError` (Task 1).
- Produces: `GitFileChange` (frozen, slotted dataclass with fields `change_id: str`, `commit_id: str`, `change_type: GitChangeType`, `old_path: str | None`, `new_path: str | None`, `similarity: int | None`).

- [ ] **Step 1: Scaffolding.** Add `GitFileChange` to `models.py` as a frozen/slotted dataclass with the exact six fields and no `__post_init__` validation.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_models.py`:
  ```python
  def test_change_requires_at_least_one_path():
      with pytest.raises(GitParseError):
          _change(old_path=None, new_path=None)

  def test_similarity_only_for_rename_and_copy():
      assert _change(change_type=GitChangeType.RENAMED, old_path="a", new_path="b", similarity=87).similarity == 87
      with pytest.raises(GitParseError):
          _change(change_type=GitChangeType.MODIFIED, new_path="a", similarity=10)

  def test_added_sets_only_new_path():
      change = _change(change_type=GitChangeType.ADDED, new_path="a.py")
      assert change.old_path is None and change.new_path == "a.py"

  def test_deleted_sets_only_old_path():
      change = _change(change_type=GitChangeType.DELETED, old_path="a.py")
      assert change.old_path == "a.py" and change.new_path is None

  def test_copy_and_rename_set_both_paths():
      change = _change(change_type=GitChangeType.COPIED, old_path="src.py", new_path="dst.py", similarity=100)
      assert (change.old_path, change.new_path) == ("src.py", "dst.py")

  def test_change_id_matches_canonical_digest():
      change = _change(change_type=GitChangeType.MODIFIED, new_path="src/app.py")
      assert change.change_id == compute_change_id(change.commit_id, GitChangeType.MODIFIED, None, "src/app.py")

  def test_change_id_cannot_be_forged_by_the_caller():
      # a caller that supplies a WRONG digest must not be able to publish it
      forged = _change(change_type=GitChangeType.MODIFIED, new_path="src/app.py",
                       change_id="0" * 64)
      assert forged.change_id != "0" * 64
      assert forged.change_id == compute_change_id(forged.commit_id, GitChangeType.MODIFIED,
                                                   None, "src/app.py")

  def test_similarity_range_is_enforced():
      with pytest.raises(GitParseError):
          _change(change_type=GitChangeType.RENAMED, old_path="a", new_path="b", similarity=101)
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_models.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `Failed`/`AssertionError` (the un-validating scaffold accepts `similarity=10` on a MODIFIED change and a change with both paths `None`). No `ImportError`.
- [ ] **Step 4: Minimal GREEN.** Add `__post_init__` that requires at least one of `old_path`/`new_path` non-`None`; requires `similarity` to be `None` unless the type is `RENAMED`/`COPIED`, and within `0..100` when present; enforces the per-type path rules from §4.3 (ADDED: only `new_path`; DELETED: only `old_path`; RENAMED/COPIED: both; MODIFIED/TYPE_CHANGED/UNMERGED/UNKNOWN: only `new_path`); validates `commit_id` via `require_object_id`; and sets `change_id` to `compute_change_id(commit_id, change_type, old_path, new_path)` — recomputed in `__post_init__` so a caller cannot inject a mismatched digest. Do NOT re-sort or normalize paths.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests in the file PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_models.py tests/test_git_history_identities.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm no path is silently rewritten: a `MODIFIED` change's `old_path` must be rejected rather than dropped, and a `RENAMED` change must keep both paths verbatim. Confirm `change_id` cannot be supplied mismatched by the caller.
- [ ] **Step 8: Commit.** `git add backend/git_history/models.py backend/git_history/identities.py tests/test_git_history_models.py tests/test_git_history_identities.py` then `git commit -m "feat: add git file change model with canonical change ids"`.
- [ ] **Step 9: Independent review checklist.** [ ] Six fields match §4.3. [ ] Per-type path rules enforced. [ ] `similarity` restricted to RENAMED/COPIED and range-checked. [ ] `change_id` recomputed from the canonical form. [ ] Copy source preserved in `old_path` (lineage, not dropped). [ ] RED was an assertion failure.
- [ ] **Step 10: Acceptance evidence.** GREEN output plus one recorded `change_id` and its canonical pre-image.


---

### Task 5: `GitHistoryInventory` and Immutable Snapshots

**Prerequisites:** Tasks 2, 3, 4.

**Files:**
- Modify: `backend/git_history/models.py` (add `GitHistoryInventory`)
- Test: `tests/test_git_history_models.py`

**Spec references:** §4.5 (inventory model, invariants, snapshot rules), §11 (completeness), §7 (ordering).

**Inputs / Outputs:** Input: commits, changes, and the index mappings. Output: a frozen, published `GitHistoryInventory` whose mappings cannot be mutated by a caller.

**Interfaces:**
- Consumes: `GitCommit`, `GitFileChange`, `GitHistoryScope` (Tasks 3, 4), `GitIncompleteReason` (Task 2), `snapshot_mapping`, `require_tuple` (Task 2).
- Produces: `GitHistoryInventory` (frozen, slotted dataclass) with fields exactly: `repository_namespace: str | None`, `scope: GitHistoryScope`, `commits: tuple[GitCommit, ...]`, `changes: tuple[GitFileChange, ...]`, `commit_ids: tuple[str, ...]`, `commits_by_id: Mapping[str, GitCommit]`, `changes_by_commit: Mapping[str, tuple[GitFileChange, ...]]`, `changes_by_path: Mapping[str, tuple[GitFileChange, ...]]`, `commits_touching_path: Mapping[str, tuple[str, ...]]`, `co_changed_paths: Mapping[str, tuple[str, ...]]`, `co_change_incomplete_paths: tuple[str, ...]`, `history_is_complete: bool`, `incomplete_reasons: tuple[GitIncompleteReason, ...]`, `commit_count: int`, `change_count: int`, `truncated_by_limit: bool`, `shallow_boundary_commit_ids: tuple[str, ...]`, `commits_with_unavailable_baseline: tuple[str, ...]`, `detached_head: bool`, `git_version: str | None`.

- [ ] **Step 1: Scaffolding.** Add `GitHistoryInventory` to `models.py` as a frozen/slotted dataclass with the twenty fields above, no `__post_init__` logic, and plain `dict` values for the five mapping fields. The five mapping fields are supplied by the CALLER as plain dicts; they have NO dataclass defaults (a mutable default would raise `ValueError` at import time, which would be an ImportError RED — forbidden by the TDD Convention).
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_models.py`:
  ```python
  def test_inventory_publishes_immutable_snapshots():
      inv = _inventory()
      assert isinstance(inv.commits_by_id, MappingProxyType)
      with pytest.raises(TypeError):
          inv.commits_by_id["x"] = None
      with pytest.raises(TypeError):
          inv.changes_by_commit["x"] = ()

  def test_inventory_copies_the_backing_store():
      backing = {"a" * 40: _commit()}
      inv = _inventory(commits_by_id=backing)
      backing["b" * 40] = _commit()
      assert "b" * 40 not in inv.commits_by_id

  def test_inventory_counts_match_lengths():
      inv = _inventory()
      assert inv.commit_count == len(inv.commits)
      assert inv.change_count == len(inv.changes)
      assert inv.commit_ids == tuple(c.commit_id for c in inv.commits)

  def test_inventory_rejects_an_inconsistent_count():
      with pytest.raises(GitParseError):
          _inventory(commit_count=99)
      with pytest.raises(GitParseError):
          _inventory(change_count=99)

  def test_history_is_complete_iff_no_reasons():
      assert _inventory(incomplete_reasons=()).history_is_complete is True
      assert _inventory(incomplete_reasons=(GitIncompleteReason.SHALLOW_HISTORY,)).history_is_complete is False

  def test_inventory_rejects_non_tuple_collections():
      with pytest.raises(GitParseError):
          _inventory(commits=[])
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_models.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`Failed` (the scaffold publishes mutable plain dicts and accepts a list for `commits`).
- [ ] **Step 4: Minimal GREEN.** Add `__post_init__` that: requires every collection field to be a `tuple`; rebuilds each mapping field through `snapshot_mapping` (which copies into a new dict and freezes it with `MappingProxyType`) and installs it with `object.__setattr__(self, "<field>", snapshot)` — required because the dataclass is `frozen=True` and a plain assignment raises `FrozenInstanceError` (the upstream idiom is visible in `backend/code_graph/models.py`); verifies `commit_count == len(commits)`, `change_count == len(changes)`, and `commit_ids == tuple(c.commit_id for c in commits)` — raising `GitParseError` on any violation; verifies every `GitFileChange.commit_id` references a commit in `commits`; and verifies `history_is_complete == (len(incomplete_reasons) == 0)`. Do NOT compute or validate the index contents here — the analyzer owns index construction (Tasks 17-18); this task only guarantees immutability and internal consistency.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_models.py tests/test_git_history_identities.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm the published mapping is a `MappingProxyType` over a COPY: mutating the caller's original dict after construction must not affect the inventory (test above). Confirm `__post_init__` performs no I/O.
- [ ] **Step 8: Commit.** `git add backend/git_history/models.py tests/test_git_history_models.py` then `git commit -m "feat: add git history inventory with immutable index snapshots"`.
- [ ] **Step 9: Independent review checklist.** [ ] Exactly the twenty §4.5 fields, in order. [ ] All five mappings are `MappingProxyType` over copies. [ ] Counts and `commit_ids` verified. [ ] `history_is_complete` derived from `incomplete_reasons`. [ ] No index content is computed in the model. [ ] No I/O.
- [ ] **Step 10: Acceptance evidence.** GREEN output and the recorded mutation-isolation check.

---

### Task 6: Pure Raw Commit-Object and Batch Parsing

**Prerequisites:** Tasks 2, 3.

**Files:**
- Create: `backend/git_history/parser.py`
- Test: `tests/test_git_history_parser.py`

**Spec references:** §6.3 (raw object format and header rules), §6.4 (the `tree` header is the commit's tree), §4.2 (parent ordering, timezone normalization, missing metadata), §7.1 (strict UTF-8, encoding header), §5 (missing metadata vs malformed).

**Inputs / Outputs:** Input: raw bytes of a `git cat-file --batch` stream. Output: parsed raw commit records. This module is PURE: it performs no I/O and imports no subprocess.

**Interfaces:**
- Consumes: `GitParseError`, `GitRevisionError` (Task 1); `require_object_id` (Task 2). (`RawCommitObject` has no change-type field, so `GitChangeType` is NOT consumed here.)
- Produces (from `backend.git_history.parser`): `RawCommitObject` — a frozen, slotted dataclass with fields `commit_id: str`, `tree_id: str`, `parent_ids: tuple[str, ...]`, `author_name: str`, `author_email: str`, `author_timestamp: int`, `author_timezone: str`, `author_header_present: bool`, `committer_name: str`, `committer_email: str`, `committer_timestamp: int`, `committer_timezone: str`, `committer_header_present: bool`, `message_bytes: bytes`, `encoding: str | None`; plus `parse_batch_stream(data: bytes, expected_ids: Sequence[str]) -> dict[str, RawCommitObject]` and `normalize_timezone(raw: str) -> str`. The two `*_header_present` booleans are INTERNAL parsed-header-presence evidence: they record whether the corresponding header was actually present in the raw object, so absence is not inferred from empty strings, zero timestamps, or sentinel timezones. They are not part of the approved public `GitCommit` data model.

- [ ] **Step 1: Scaffolding.** Create `parser.py` with `RawCommitObject` (all fields, no defaults) and `parse_batch_stream`/`normalize_timezone` present but returning `{}`/`""` respectively.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_parser.py`:
  ```python
  def _batch(payload: bytes) -> bytes:
      body = b"tree " + b"a" * 40 + b"\n" + payload
      return b"a" * 40 + b" commit " + str(len(body)).encode() + b"\n" + body + b"\n"

  def test_parses_tree_and_parents_in_original_order():
      raw = _batch(b"parent " + b"b" * 40 + b"\nparent " + b"c" * 40 + b"\n"
                   b"author A U Thor <a@example.com> 1577865600 -0800\n"
                   b"committer A U Thor <a@example.com> 1577865600 -0800\n\nmsg\n")
      parsed = parse_batch_stream(raw, ["a" * 40])
      commit = parsed["a" * 40]
      assert commit.tree_id == "a" * 40
      assert commit.parent_ids == ("b" * 40, "c" * 40)

  def test_normalizes_timezone_to_colon_form():
      assert normalize_timezone("-0800") == "-08:00"
      assert normalize_timezone("+0530") == "+05:30"
      assert normalize_timezone("+00:00") == "+00:00"

  def test_missing_metadata_headers_yield_sentinels():
      raw = _batch(b"\nno metadata here\n")
      commit = parse_batch_stream(raw, ["a" * 40])["a" * 40]
      assert commit.author_name == "" and commit.author_timestamp == 0
      assert commit.author_timezone == "+00:00"
      assert commit.author_header_present is False
      assert commit.committer_header_present is False
  def test_malformed_header_raises_parse_error():
      raw = _batch(b"author broken header\n\nmsg\n")
      with pytest.raises(GitParseError):
          parse_batch_stream(raw, ["a" * 40])

  def test_invalid_utf8_message_raises_parse_error():
      raw = _batch(b"author A <a@e.com> 1 +0000\ncommitter A <a@e.com> 1 +0000\n\n\xff\xfe\n")
      with pytest.raises(GitParseError):
          parse_batch_stream(raw, ["a" * 40])

  def test_missing_requested_object_is_reported():
      raw = _batch(b"\nmsg\n")
      with pytest.raises(GitParseError):
          parse_batch_stream(raw, ["a" * 40, "d" * 40])

  def test_encoding_header_is_captured():
      raw = _batch(b"encoding ISO-8859-1\nauthor A <a@e.com> 1 +0000\ncommitter A <a@e.com> 1 +0000\n\ncaf\xe9\n")
      commit = parse_batch_stream(raw, ["a" * 40])["a" * 40]
      assert commit.encoding == "ISO-8859-1"

  def test_unicode_message_is_decoded():
      raw = _batch(b"author A <a@e.com> 1 +0000\ncommitter A <a@e.com> 1 +0000\n\ncaf\xc3\xa9 \xe2\x9c\x93\n")
      commit = parse_batch_stream(raw, ["a" * 40])["a" * 40]
      assert b"caf\xc3\xa9" in commit.message_bytes
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_parser.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`KeyError`/`Failed` (the stub returns an empty dict). No `ImportError`.
- [ ] **Step 4: Minimal GREEN.** Implement `parse_batch_stream` to split the stream on the `<oid> <type> <size>\n<bytes>\n` frame format, read exactly `size` bytes of object body, then split the body on the first blank line into headers and message. Parse `tree` (validate via `require_object_id`), `parent` lines (keep ORIGINAL order, validate each), `author`/`committer` (name, email, timestamp, raw timezone) using the `Name <email> <timestamp> <tz>` shape, and the optional `encoding` header. `normalize_timezone` converts `±HHMM` to `±HH:MM` and passes `±HH:MM` through unchanged. A missing `author`/`committer` header yields `""`/`0`/`"+00:00"` and sets the matching `author_header_present`/`committer_header_present` flag to `False`. A present header — including an unusual but syntactically valid one such as an empty name, an unusual email, a pre-1970 (negative) timestamp, or a non-UTC offset — sets the flag to `True` and is accepted. A present-but-unparseable required header raises `GitParseError`. `MISSING_METADATA` therefore reflects actual header absence recorded by these flags, never a guess from empty strings or a zero timestamp. Decode the message strictly as UTF-8 (or the declared encoding) and raise `GitParseError` on failure; the error message MUST include the offending byte offset (spec §7.1: *"the offending byte offset"*). A requested object that is absent or reported as `missing` raises `GitParseError`.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_parser.py tests/test_git_history_models.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm `parser.py` performs no I/O and no subprocess: `Select-String -Path backend/git_history/parser.py -Pattern "subprocess|open\(|os\.|Path\("` returns nothing. Confirm the parser never re-orders `parent_ids` and never substitutes a replacement character on a decode failure.
- [ ] **Step 8: Commit.** `git add backend/git_history/parser.py tests/test_git_history_parser.py` then `git commit -m "feat: parse raw git commit objects from batch output"`.
- [ ] **Step 9: Independent review checklist.** [ ] `tree` header captured as the commit tree (§6.4). [ ] Parent order preserved verbatim. [ ] Timezone normalized to `±HH:MM`. [ ] Missing metadata -> `""`/`0`/`"+00:00"`, non-fatal. [ ] Malformed header -> `GitParseError`. [ ] Invalid UTF-8 -> `GitParseError`, no replacement chars. [ ] Module is pure. [ ] RED was not an import error.
- [ ] **Step 10: Acceptance evidence.** GREEN output plus the recorded canonical bytes of one parsed object.

---

### Task 7: Pure NUL-Delimited diff-tree Parsing

**Prerequisites:** Tasks 2, 4, 6.

**Files:**
- Modify: `backend/git_history/parser.py` (add the diff-tree parser and `RawFileChange`)
- Test: `tests/test_git_history_parser.py`

**Spec references:** §6.4 (NUL-delimited record grammar), §6.5 (similarity), §9 (copy source lineage), §7.1 (strict UTF-8 paths, NUL boundaries).

**Inputs / Outputs:** Input: raw stdout bytes of `git diff-tree -z --name-status`. Output: parsed raw change records. Pure — no I/O.

**Interfaces:**
- Consumes: `GitChangeType` (Task 2), `GitParseError` (Task 1).
- Produces (from `backend.git_history.parser`): `RawFileChange` — a frozen, slotted dataclass with fields `change_type: GitChangeType`, `old_path: str | None`, `new_path: str | None`, `similarity: int | None`; plus `parse_name_status(data: bytes) -> tuple[RawFileChange, ...]`.

- [ ] **Step 1: Scaffolding.** Add `RawFileChange` and `parse_name_status` to `parser.py`, with `parse_name_status` returning `()`.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_parser.py`:
  ```python
  def test_parses_single_letter_statuses():
      records = parse_name_status(b"A\0added.py\0M\0mod.py\0D\0gone.py\0T\0type.py\0U\0merge.py\0")
      assert [(r.change_type, r.new_path) for r in records] == [
          (GitChangeType.ADDED, "added.py"), (GitChangeType.MODIFIED, "mod.py"),
          (GitChangeType.DELETED, "gone.py"), (GitChangeType.TYPE_CHANGED, "type.py"),
          (GitChangeType.UNMERGED, "merge.py")]

  def test_parses_rename_with_similarity():
      records = parse_name_status(b"R087\0old.py\0new.py\0")
      assert records[0].change_type is GitChangeType.RENAMED
      assert records[0].similarity == 87
      assert (records[0].old_path, records[0].new_path) == ("old.py", "new.py")

  def test_parses_copy_with_similarity():
      records = parse_name_status(b"C100\0src.py\0dst.py\0")
      assert records[0].change_type is GitChangeType.COPIED
      assert records[0].old_path == "src.py" and records[0].new_path == "dst.py"

  def test_ignores_trailing_empty_record():
      assert len(parse_name_status(b"A\0a.py\0")) == 1

  def test_rejects_unexpected_field_count():
      with pytest.raises(GitParseError):
          parse_name_status(b"R087\0old.py\0")

  def test_rejects_invalid_utf8_path():
      with pytest.raises(GitParseError):
          parse_name_status(b"A\0\xff\xfe.py\0")

  def test_paths_with_spaces_are_preserved():
      records = parse_name_status(b"A\0a file with spaces.py\0")
      assert records[0].new_path == "a file with spaces.py"

  def test_invalid_utf8_path_error_carries_the_offending_bytes():
      with pytest.raises(GitParseError) as excinfo:
          parse_name_status(b"A\0\xff\xfe.py\0")
      message = str(excinfo.value).lower()
      # the offending bytes must appear in the message in SOME hex form
      assert "fffe" in message or "ff fe" in message or "0xff" in message

  def test_unicode_filename_is_preserved():
      records = parse_name_status("A\0café.py\0".encode("utf-8"))
      assert records[0].new_path == "café.py"
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_parser.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`Failed` (the stub returns an empty tuple) or with `IndexError` for the snippets that index `records[0]` directly — both are legitimate behavioural REDs; an `ImportError` would not be.
- [ ] **Step 4: Minimal GREEN.** Implement `parse_name_status` to split the byte stream on NUL, walk the records, and map the leading status byte: `A`->ADDED, `M`->MODIFIED, `D`->DELETED, `T`->TYPE_CHANGED, `U`->UNMERGED, `R<score>`->RENAMED, `C<score>`->COPIED, any other status -> UNKNOWN (single-path form). A rename/copy record consumes three fields (status, old path, new path); every other status consumes two. Ignore a trailing empty record. Decode every path strictly as UTF-8 and raise `GitParseError` on failure; the error message MUST include the hex of the offending bytes (spec §7.1). Raise `GitParseError` for an unexpected field count, a malformed score, or an unterminated record.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_parser.py -q -m "not integration and not gemini_live" -rs` — expected: PASS, including the Task 6 tests in the same file.
- [ ] **Step 7: Security/contract checks.** Confirm no lossy decoding: `Select-String -Path backend/git_history/parser.py -Pattern "surrogateescape|errors=.replace.|errors=.ignore."` returns nothing. Confirm a path is never split on whitespace (NUL is the only delimiter).
- [ ] **Step 8: Commit.** `git add backend/git_history/parser.py tests/test_git_history_parser.py` then `git commit -m "feat: parse nul-delimited git diff-tree output"`.
- [ ] **Step 9: Independent review checklist.** [ ] All eight change types mapped. [ ] Rename/copy consume three fields with a numeric score. [ ] Trailing empty record ignored. [ ] Wrong field count -> `GitParseError`. [ ] Strict UTF-8, no lossy decoding. [ ] Paths with spaces preserved. [ ] Copy source kept in `old_path`.
- [ ] **Step 10: Acceptance evidence.** GREEN output plus one recorded raw byte stream and its parsed result.

---

### Task 8: GitAdapter Environment and Command Security

**Prerequisites:** Task 1 (exceptions).

**Files:**
- Create: `backend/git_history/adapter.py`
- Modify: `tests/conftest.py` (add the shared `git_repo` fixture)
- Test: `tests/test_git_history_adapter.py`

**Spec references:** §6.1 (adapter safety, allowed commands, environment, temp helpers, option-injection defense), §13 (security posture), §15 (error mapping).

**Inputs / Outputs:** Input: a validated repository path and a Git argument list. Output: raw stdout bytes of one completed command. This is the ONLY module in Module 7 permitted to touch `subprocess`.

**Interfaces:**
- Consumes: `GitUnavailableError`, `GitCommandError`, `GitRepositoryError`, `GitConfigurationError` (Task 1).
- Produces (from `backend.git_history.adapter`): `CommandResult` — a frozen, slotted dataclass with fields `returncode: int`, `stdout: bytes`, `stderr: bytes`; the module constant `MINIMUM_GIT_VERSION = (2, 29)` (the conservative Git version floor of spec §13); `GitAdapter` class with `__init__(self, *, git_executable: str = "git", command_timeout_seconds: float = 30.0, max_output_bytes: int = 50 * 1024 * 1024) -> None` and a read-only `environment` property returning a COPY of the sanitized environment it hands to every child; `GitAdapter.run(args: Sequence[str], repository_path: Path, *, stdin: bytes | None = None) -> bytes` (raises `GitCommandError` on a non-zero exit); `GitAdapter.run_raw(args: Sequence[str], repository_path: Path | None = None, *, stdin: bytes | None = None) -> CommandResult` (NEVER raises for a non-zero exit — this is the exit-status channel the spec §6.4 classification requires). `repository_path` is OPTIONAL: when it is `None` the `-C <path>` prefix is omitted, but the sanitized environment and the `-c core.hooksPath=<empty dir>` override are applied EXACTLY as for a repository-scoped command — that is how `git --version` (which is not repository-scoped) stays inside the same security boundary); `GitAdapter.read_shallow_boundary_ids(git_common_dir: Path) -> tuple[str, ...]` (owned by THIS task: reads the `shallow` file and returns the boundary object IDs, one per line); and `build_git_environment(helper_dir: Path) -> dict[str, str]` (module-level function).
- Produces (in `tests/conftest.py`): the `git_repo` pytest fixture. Its full contract is specified below and is binding for every task that uses it.

**`git_repo` fixture contract (binding for Tasks 8-23).** The fixture yields a **CALLABLE FACTORY**, not a builder instance. A test declares the fixture as a parameter and calls it to obtain a repository:

```python
def test_something(git_repo):        # the fixture parameter
    repo = git_repo()                 # the factory call -> a NEW GitRepoBuilder
    repo.commit({"a.txt": b"1\n"})
```
Each call to `git_repo()` returns a NEW `GitRepoBuilder` bound to a NEW repository in a distinct sub-directory of `tmp_path` (for example `tmp_path/repo-1`, `tmp_path/repo-2`). Two calls in the same test MUST yield two DIFFERENT repositories. A test that needs two independent repositories (for example Task 23's command-count comparison) relies on this. The builder MUST behave as follows — these details are what make the downstream tests deterministic:

- `repo.path` -> `Path` of the repository working tree.
- `repo.head` -> the 40-hex commit ID of the most recent commit (empty string before the first commit).
- The repository is created with `git init -b main` so the initial branch is **`main`** (every test that calls `repo.checkout("main")` depends on this), and with repository-local `user.name` and `user.email` configured (the analyzer's sanitized environment drops the global config, so an unconfigured repo cannot commit).
- `repo.commit(files: dict[str, bytes], message: str = "commit") -> str` -> the new commit ID; stages and commits the given files. A message longer than the platform command-line limit (32 767 characters on Windows) MUST be written to a temporary file and passed with `git commit -F <file>` — passing it as `git commit -m <message>` would fail on Windows. `commit_with_timestamp` uses the same rule.
- `repo.commit_empty(message: str = "empty commit") -> str` -> commits with NO file changes; returns the commit ID.
- `repo.commit_with_timestamp(files: dict[str, bytes], timestamp: str, message: str = "commit") -> str` -> commits with an explicit committer/author date (for example `"1577865600 +0000"`); returns the commit ID.
- `repo.head_timestamp() -> str` -> the raw committer timestamp of HEAD, in Git's `"<epoch> <tz>"` form.
- `repo.branch(name: str, at: str) -> None` -> creates a branch at the given commit.
- `repo.checkout(name: str) -> None` -> checks out a branch or commit.
- `repo.merge(other: str) -> str` -> merges and returns the merge commit ID; MUST pass `--no-edit` so no editor is launched (a non-interactive subprocess would otherwise hang).
- `repo.detach(sha: str) -> None` -> detaches HEAD at the given commit.
- `repo.commit_rename(old: str, new: str) -> str` and `repo.commit_copy(src: str, dst: str) -> str` -> commit a rename or a copy, returning the commit ID.
- `repo.delete(path: str) -> str` -> removes a file and commits the removal, returning the commit ID.
- `repo.shallow_clone(depth: int = 1) -> "ShallowClone"` -> clones THIS repository at the given depth and returns an object with `.path`, `.head`, and `.linked_worktree(name) -> Path` (a linked worktree of the CLONE). The clone MUST use the `file:///` URL form (`git clone --depth <depth> file:///<abs-path> <dest>`); a plain local path makes Git ignore `--depth` and produce a FULL clone (spec §16), which would silently defeat every shallow test.
- `repo.linked_worktree(name: str) -> Path` -> `git worktree add` a linked worktree, returning its path (used to prove `--git-common-dir` shares shallow metadata).
- `repo.is_unborn() -> bool` -> True when `git symbolic-ref --quiet HEAD` succeeds while `git rev-parse --verify --end-of-options HEAD^{commit}` fails.
- `repo.install_hook(name: str, body: str) -> None` -> writes a hook into `.git/hooks/`. The file MUST begin with the shebang line `#!/bin/sh` followed by the given POSIX body: without a shebang, Git for Windows cannot spawn the hook at all ("error: cannot spawn .git/hooks/post-commit"), so the hook would never run and any assertion about it would be vacuous.
- `repo.add_replacement_for_head(message: str) -> None` -> creates a `refs/replace` ref substituting the HEAD commit with one carrying a different message (used by Task 22).
- `repo.head_message() -> str` -> the HEAD commit's message (used by Task 22 to prove the ORIGINAL message is published).
- Every builder method runs Git through an argument list with `shell=False` and never touches the network.

- [ ] **Step 1: Scaffolding.** Create `adapter.py` with `GitAdapter.__init__` storing its configuration (no validation yet), a `run` that returns `b""`, a `run_raw` that returns `CommandResult(0, b"", b"")`, a `build_git_environment` that returns `{}`, a `read_shallow_boundary_ids` that returns `()`, and the module constant `MINIMUM_GIT_VERSION = (2, 29)`. Create the `git_repo` fixture in `tests/conftest.py` as a FACTORY THAT YIELDS A BUILDER (per the binding contract above): the fixture returns a callable, and each call returns a new `GitRepoBuilder` whose `commit()` method writes files and runs `git commit` but does not yet return the commit ID (returns `""`).
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_adapter.py`:
  ```python
  def test_environment_contains_no_inherited_git_variables(tmp_path):
      env = build_git_environment(Path(tmp_path))
      assert env["GIT_CONFIG_NOSYSTEM"] == "1"
      assert env["GIT_ATTR_NOSYSTEM"] == "1"
      assert env["GIT_TERMINAL_PROMPT"] == "0"
      assert env["GIT_PAGER"] == "cat" and env["PAGER"] == "cat"
      assert env["GIT_EXTERNAL_DIFF"] == ""
      assert env["GIT_NO_LAZY_FETCH"] == "1"
      assert env["GIT_NO_REPLACE_OBJECTS"] == "1"
      assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
      assert "GIT_INDEX_FILE" not in env and "GIT_OBJECT_DIRECTORY" not in env
      assert "GIT_ALTERNATE_OBJECT_DIRECTORIES" not in env
      assert "GIT_TRACE" not in env and "GIT_TRACE2" not in env
      assert "HTTP_PROXY" not in env and "ALL_PROXY" not in env

  def test_environment_starts_from_empty_mapping(tmp_path, monkeypatch):
      monkeypatch.setenv("GIT_CONFIG_GLOBAL", "C:/leak")
      monkeypatch.setenv("HTTPS_PROXY", "http://leak")
      env = build_git_environment(Path(tmp_path))
      assert "HTTPS_PROXY" not in env
      # the helper's own config file must be PRESENT and inside the helper dir
      assert "GIT_CONFIG_GLOBAL" in env
      config = Path(env["GIT_CONFIG_GLOBAL"])
      assert config.parent == Path(tmp_path)
      assert config.is_file() and config.stat().st_size == 0
      assert "leak" not in env["GIT_CONFIG_GLOBAL"]

  def test_adapter_rejects_invalid_configuration():
      with pytest.raises(GitConfigurationError):
          GitAdapter(command_timeout_seconds=0)
      with pytest.raises(GitConfigurationError):
          GitAdapter(max_output_bytes=0)
      with pytest.raises(GitConfigurationError):
          GitAdapter(git_executable="")

  def test_missing_git_executable_raises_git_unavailable(tmp_path):
      adapter = GitAdapter(git_executable="git-does-not-exist-xyz")
      with pytest.raises(GitUnavailableError):
          adapter.run(["--version"], tmp_path)

  def test_minimum_git_version_constant_holds_the_production_value():
      assert adapter_module.MINIMUM_GIT_VERSION == (2, 29)

  def test_run_raw_exposes_the_exit_status_without_raising(tmp_path):
      adapter = GitAdapter()
      result = adapter.run_raw(["rev-parse", "--verify", "--end-of-options", "no-such-rev"], tmp_path)
      assert result.returncode == 128
      assert isinstance(result.stdout, bytes) and isinstance(result.stderr, bytes)
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`Failed` (the stub environment is empty, so a missing key raises `KeyError`, and the configuration checks raise nothing) — a `KeyError` is a legitimate behavioural RED here. No `ImportError`.
- [ ] **Step 4: Minimal GREEN.** Implement `build_git_environment(helper_dir)` to return a NEW dict containing only: `PATH` (inherited), plus `SystemRoot`/`Path`/`TEMP`/`TMP` when present on the platform, plus `GIT_CONFIG_NOSYSTEM=1`, `GIT_ATTR_NOSYSTEM=1`, `GIT_TERMINAL_PROMPT=0`, `GIT_PAGER=cat`, `PAGER=cat`, `GIT_EXTERNAL_DIFF=`, `GIT_NO_LAZY_FETCH=1`, `GIT_NO_REPLACE_OBJECTS=1`, and `GIT_CONFIG_GLOBAL` pointing at an empty `gitconfig` file created inside `helper_dir`. `GitAdapter.__init__` creates its OWN helper directory with `tempfile.mkdtemp()` (outside any analyzed repository), holds it for the adapter's lifetime, and removes it in a `close()`/`__del__`-style cleanup that also runs on error — the empty `gitconfig` and the empty `core.hooksPath` directory live there. Implement `GitAdapter.__init__` validation (positive timeout, positive `max_output_bytes`, nonempty executable -> else `GitConfigurationError`). Implement `GitAdapter.run_raw(args, repository_path=None, *, stdin=None) -> CommandResult` as the SINGLE invocation primitive: when `repository_path` is given it validates that it is an existing directory and the argument list is `[git_executable, "-C", str(repository_path), "-c", "core.hooksPath=<empty dir>", *args]`; when `repository_path` is `None` the `-C` prefix is OMITTED and the argument list is `[git_executable, "-c", "core.hooksPath=<empty dir>", *args]`. In BOTH cases the command runs with `shell=False` and the sanitized environment, and an `OSError` from a missing executable maps to `GitUnavailableError`; it returns the exit status, stdout, and stderr WITHOUT raising on a non-zero exit. The `core.hooksPath` override and the sanitized environment apply to EVERY Git invocation, including the repository-independent `git --version`. Implement `GitAdapter.run(args, repository_path, *, stdin=None) -> bytes` on top of `run_raw`: it returns the stdout bytes and raises `GitCommandError` (with a sanitized stderr excerpt) when `returncode != 0`. This split is what makes the spec §6.4 exit-status classification possible in Task 14. Do NOT implement output caps, timeouts, or cleanup in this task — those are Tasks 9 and 10. **Also implement `GitAdapter.read_shallow_boundary_ids(git_common_dir)`**: it reads the `shallow` file in the given (already resolved) common directory and returns the boundary object IDs it contains, one per line, as a tuple; a missing file yields an empty tuple. **Also implement the `git_repo` fixture contract in full in `tests/conftest.py`**: the callable factory, and every builder method it specifies — `commit` (returning the new commit ID), `head`, `path`, `branch`, `checkout`, `merge` (with `--no-edit`), `detach`, `commit_empty`, `commit_with_timestamp`, `head_timestamp`, `commit_rename`, `commit_copy`, `delete`, `shallow_clone` (using the `file:///` URL form), `linked_worktree`, `is_unborn`, `install_hook` (with the `#!/bin/sh` shebang), `add_replacement_for_head`, and `head_message`.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_adapter.py tests -q -m "not integration and not gemini_live" -rs` — expected: PASS with no new failures.
- [ ] **Step 7: Security/contract checks.** Confirm the adapter is the only Module 7 module importing `subprocess`: `Select-String -Path backend/git_history/*.py -Pattern "^import subprocess|^from subprocess"` returns exactly one hit, in `adapter.py`. Confirm `shell=True` does not appear anywhere in `backend/git_history/`. Confirm the temp helper directory is created OUTSIDE the analyzed repository and contains an empty `gitconfig` and an empty hooks directory.
- [ ] **Step 8: Commit.** `git add backend/git_history/adapter.py tests/conftest.py tests/test_git_history_adapter.py` then `git commit -m "feat: add secure git adapter environment and command execution"`.
- [ ] **Step 9: Independent review checklist.** [ ] Environment starts empty; every inherited `GIT_*` and proxy variable is dropped. [ ] `GIT_NO_REPLACE_OBJECTS=1` and `GIT_NO_LAZY_FETCH=1` present. [ ] `core.hooksPath` points at an empty directory. [ ] `shell=False` with an argument list. [ ] `-C <path>` used instead of `cwd`. [ ] Invalid configuration raises `GitConfigurationError`; a missing executable raises `GitUnavailableError`. [ ] The `git_repo` fixture creates real local repositories with no network access.
- [ ] **Step 10: Acceptance evidence.** GREEN output, the environment dict captured verbatim in the review note, and the single-hit subprocess-import check.


---

### Task 9: Bounded Subprocess I/O and Process Cleanup

**Prerequisites:** Task 8.

**Files:**
- Modify: `backend/git_history/adapter.py`
- Test: `tests/test_git_history_adapter.py`

**Spec references:** §14 (per-command output cap enforced DURING reading, 64 KiB chunks, terminate/kill/wait cleanup, concurrent reader threads for Windows pipe safety), §6.1 (capture stdout/stderr with size caps), §15 (timeout/oversize error mapping).

**Inputs / Outputs:** Input: a running Git child process. Output: bounded stdout bytes, or a typed error with the child reaped.

**Interfaces:**
- Consumes: `GitOutputTooLargeError`, `GitCommandTimeoutError`, `GitCommandError` (Task 1); `GitAdapter.run_raw` and `CommandResult` (Task 8).
- Produces: `GitAdapter.run_raw` upgraded to enforce the per-command cap and to drain stdout/stderr concurrently, still returning `CommandResult` (so the exit status survives for Task 14); `GitAdapter.run` still raises `GitCommandError` on a non-zero exit; and a module-level `terminate_process(process: subprocess.Popen) -> None` helper.

- [ ] **Step 1: Scaffolding.** Replace the post-hoc capture inside `run_raw` with a `subprocess.run(..., capture_output=True)` scaffold that checks the length of the captured output AFTER the fact and raises `GitOutputTooLargeError`. `run` MUST continue to delegate to `run_raw` (it must not call `subprocess` itself) — that delegation is what carries the per-command cap, the cumulative budget, and the exit status to every caller and to the `run_raw` spies used by Tasks 11, 14, 22, and 23.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_adapter.py`:
  ```python
  def test_oversized_output_is_rejected(tmp_path, git_repo):
      adapter = GitAdapter(max_output_bytes=1024)
      with pytest.raises(GitOutputTooLargeError):
          adapter.run(["cat-file", "-p", "HEAD"], _big_repo(tmp_path, git_repo))

  def test_output_within_cap_is_returned(tmp_path, git_repo):
      adapter = GitAdapter()
      out = adapter.run(["rev-parse", "HEAD"], _repo(tmp_path, git_repo))
      assert len(out.strip()) == 40

  def test_child_process_is_reaped_after_failure(tmp_path, git_repo, monkeypatch):
      adapter = GitAdapter(max_output_bytes=16)
      spawned = []
      original_popen = adapter_module.subprocess.Popen
      def spy(*args, **kwargs):
          proc = original_popen(*args, **kwargs)
          spawned.append(proc)
          return proc
      monkeypatch.setattr(adapter_module.subprocess, "Popen", spy)
      with pytest.raises(GitOutputTooLargeError):
          adapter.run(["cat-file", "-p", "HEAD"], _big_repo(tmp_path, git_repo))
      # every spawned child must have been waited on (reaped), never left running
      assert spawned
      for proc in spawned:
          assert proc.returncode is not None

  def test_large_stdout_is_drained_without_deadlock(tmp_path, git_repo):
      # a repository whose HEAD commit MESSAGE is large enough to fill the
      # stdout pipe: stdout MUST be drained concurrently or the child blocks
      adapter = GitAdapter(max_output_bytes=50 * 1024 * 1024)
      out = adapter.run(["cat-file", "-p", "HEAD"], _big_repo(tmp_path, git_repo))
      assert len(out) > 64 * 1024

  def test_combined_stdout_stderr_share_one_cap(tmp_path, git_repo, monkeypatch):
      # each stream is individually under the cap, but their combined bytes
      # exceed it -> the shared counter must raise
      import io

      class _CombinedChild:
          def __init__(self):
              self.terminated = False
              self.killed = False
              self.returncode = 0
              self.stdin = None
              self.stdout = io.BytesIO(b"x" * 600)
              self.stderr = io.BytesIO(b"y" * 600)
              self.args = ["git"]
              self.pid = 1234
          def wait(self, timeout=None):
              return self.returncode
          def terminate(self):
              self.terminated = True
              self.returncode = -15
          def kill(self):
              self.killed = True
              self.returncode = -9
          def poll(self):
              return self.returncode

      child = _CombinedChild()
      monkeypatch.setattr(adapter_module.subprocess, "Popen", lambda *a, **k: child)
      adapter = GitAdapter(max_output_bytes=1024)
      with pytest.raises(GitOutputTooLargeError):
          adapter.run(["rev-parse", "HEAD"], tmp_path)
      assert child.terminated or child.killed

  def test_per_command_timeout_raises_and_reaps_the_child(tmp_path, git_repo, monkeypatch):
      # A real Git command cannot be relied upon to exceed a 0.5 s timeout
      # (git hash-object completes in ~0.03 s), so the timeout is made
      # DETERMINISTIC: the child's FIRST wait() reports the timeout expiry.
      class _TimeoutChild:
          def __init__(self):
              self.terminated = False
              self.killed = False
              self.returncode = None
              self.stdin = None
              self.stdout = io.BytesIO()
              self.stderr = io.BytesIO()
              self.args = ["git"]
              self.pid = 1234
              self._timed_out = False
          def wait(self, timeout=None):
              # raise ONLY on the first wait(); the cleanup path's later
              # wait() calls must RETURN, or the cleanup would raise instead
              # of the expected GitCommandTimeoutError
              if timeout is not None and not self._timed_out:
                  self._timed_out = True
                  raise subprocess.TimeoutExpired(cmd="git", timeout=timeout)
              return self.returncode
          def terminate(self):
              self.terminated = True
              self.returncode = -15
          def kill(self):
              self.killed = True
              self.returncode = -9
          def poll(self):
              return self.returncode
      child = _TimeoutChild()
      monkeypatch.setattr(adapter_module.subprocess, "Popen", lambda *a, **k: child)
      adapter = GitAdapter(command_timeout_seconds=0.5)
      with pytest.raises(GitCommandTimeoutError):
          adapter.run(["rev-parse", "HEAD"], _repo(tmp_path, git_repo))
      # the child was terminated and reaped, never left running
      assert child.terminated or child.killed
      assert child.returncode is not None
  ```
  `_repo(tmp_path, git_repo) -> Path` builds a small local repository through the `git_repo` factory; `_big_repo(tmp_path, git_repo) -> Path` builds one whose HEAD COMMIT MESSAGE is LARGER than 64 KiB (written with `git commit -F <file>`). A large BLOB does NOT enlarge the commit object — `git cat-file -p HEAD` prints the commit metadata, which references a blob by hash — so only a large MESSAGE makes the command's output exceed the cap, and that is what the oversize and pipe-pressure scenarios require. The orphan check asserts that the recorded `Popen` objects were reaped (`returncode is not None`); it does NOT scan the process table and does NOT require any dependency beyond the standard library.
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `Failed`/`AssertionError` — the post-hoc scaffold raises `GitOutputTooLargeError` only AFTER the whole buffer is captured, so `test_oversized_output_is_rejected` PASSES on the scaffold, and `test_child_process_is_reaped_after_failure` also PASSES on the scaffold (because `subprocess.run` -> `Popen` -> `communicate()` already reaps the child). The ONLY genuine RED signal in this task is `test_per_command_timeout_raises_and_reaps_the_child`: the scaffold has no timeout at all, so it never raises `GitCommandTimeoutError`. The reaping and oversize tests are POST-CONDITIONS that must hold after the fix, not RED signals. No `ImportError`.
- [ ] **Step 4: Minimal GREEN.** Rewrite `run_raw` to use `subprocess.Popen` with `stdin=PIPE`/`stdout=PIPE`/`stderr=PIPE`, `shell=False`, and the sanitized environment. `run_raw` gains an `stdin_data: bytes | None = None` parameter. When `stdin_data is None`, the implementation MUST NOT touch `process.stdin` at all (no `close()`, no `write()`) — and it MUST open the child's stdin as `subprocess.DEVNULL` rather than `PIPE`, so the child sees EOF immediately instead of waiting on an unclosed pipe. A test double whose `stdin` is `None` is then safe. When `stdin_data is not None`, open `stdin=PIPE` and feed it from a dedicated daemon WRITER thread in bounded chunks (so a payload larger than the OS pipe buffer cannot deadlock the writer against the readers); the writer closes stdin on completion and swallows `BrokenPipeError` (the child may have exited/terminated early). Start TWO daemon READER threads, one per pipe, each reading in 64 KiB chunks. The per-command cap is a SINGLE shared counter for stdout + stderr combined: a `threading.Lock` guards the accumulated byte count, and every chunk read from EITHER pipe is added to the same counter before it is appended to its per-stream buffer. Enforcement happens DURING streaming: as soon as the combined counter exceeds `max_output_bytes`, call `terminate_process` and raise `GitOutputTooLargeError`. The cumulative 256 MiB budget (Task 10) is charged from the same per-chunk accounting, so stderr-only output is never exempt. The main thread waits for the process with the per-command timeout (capped by the aggregate deadline per Task 10). `terminate_process` calls `process.terminate()`, waits up to a short grace period (e.g. 2 s), then calls `process.kill()` and `process.wait()` — on both Windows and POSIX. Always `wait()` the child so no zombie/orphan remains. Reader/writer threads are `daemon=True` and are joined with a short timeout after termination so a stuck child cannot wedge the interpreter. `terminate_process` calls `process.terminate()`, waits up to a short grace period (e.g. 2 s), then calls `process.kill()` and `process.wait()` — on both Windows and POSIX. Always `wait()` the child so no zombie/orphan remains. `run_raw` still returns the `CommandResult` (with the exit status) whenever the command completed normally; `run` raises `GitCommandError` on a non-zero exit status with a sanitized stderr excerpt that never includes the environment or a remote URL. Raise `GitCommandTimeoutError` on timeout (after the same terminate/kill/wait cleanup).
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs` — expected: PASS, including the Task 8 tests in the same file.
- [ ] **Step 7: Security/contract checks.** Confirm `shell=True` and `os.system` do not appear in `adapter.py`. Confirm the reader threads are `daemon=True` so a hung child cannot wedge the interpreter. Confirm the error message for a failed command contains the git stderr text but NOT any environment variable value. Confirm `run` does NOT call `subprocess` directly: it delegates to `run_raw` (grep for `subprocess` inside the `run` body must find nothing).
- [ ] **Step 8: Commit.** `git add backend/git_history/adapter.py tests/test_git_history_adapter.py` then `git commit -m "feat: bound subprocess output and reap terminated children"`.
- [ ] **Step 9: Independent review checklist.** [ ] The cap is enforced DURING reading, not post-hoc. [ ] stdout and stderr are drained concurrently (no Windows pipe deadlock). [ ] `terminate()` then `kill()` then `wait()`. [ ] Timeout raises `GitCommandTimeoutError`; oversize raises `GitOutputTooLargeError`; non-zero exit raises `GitCommandError`. [ ] No orphaned child remains. [ ] Error messages are sanitized.
- [ ] **Step 10: Acceptance evidence.** GREEN output, the orphan-check result, and a recorded stderr excerpt proving no environment leakage.

---

### Task 10: Aggregate Timeout and Cumulative Output Accounting

**Prerequisites:** Task 9.

**Files:**
- Modify: `backend/git_history/adapter.py`
- Test: `tests/test_git_history_adapter.py`

**Spec references:** §14 (300 s aggregate analysis timeout; 256 MiB cumulative output budget counted across all commands and checked after each chunk), §15 (both raise typed errors; no partial inventory).

**Inputs / Outputs:** Input: a sequence of commands through one adapter. Output: a running cumulative byte counter and a deadline; typed errors when either is exceeded.

**Interfaces:**
- Consumes: `GitOutputTooLargeError`, `GitCommandTimeoutError` (Task 1); `GitAdapter.run` (Tasks 8-9).
- Produces: `GitAdapter.__init__` gains an internal aggregate deadline and cumulative byte counter, both driven by two module-level constants: `AGGREGATE_TIMEOUT_SECONDS = 300.0` and `CUMULATIVE_OUTPUT_BUDGET_BYTES = 256 * 1024 * 1024`. `GitAdapter.run` consults both before and during each command.

- [ ] **Step 1: Scaffolding.** Add the two constants and a `self._cumulative_bytes = 0` counter to `GitAdapter`, and add the three public members `consume_output_budget(amount: int) -> None`, `cumulative_output_bytes` (read-only property), and `check_aggregate_deadline() -> None` — present but not yet consulted by `run_raw`.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_adapter.py`:
  ```python
  def test_cumulative_budget_blocks_many_small_commands(monkeypatch, tmp_path, git_repo):
      # many commands that are EACH under the per-command cap must still
      # trip the cumulative budget
      monkeypatch.setattr(adapter_module, "CUMULATIVE_OUTPUT_BUDGET_BYTES", 4096)
      adapter = GitAdapter(max_output_bytes=50 * 1024 * 1024)
      repo = _repo(tmp_path, git_repo)          # a small, legal command
      adapter.run(["rev-parse", "HEAD"], repo)
      with pytest.raises(GitOutputTooLargeError):
          for _ in range(200):
              adapter.run(["rev-parse", "HEAD"], repo)

  def test_cumulative_counter_accumulates_across_commands():
      adapter = GitAdapter()
      adapter.consume_output_budget(10)
      adapter.consume_output_budget(20)
      assert adapter.cumulative_output_bytes == 30

  def test_aggregate_deadline_is_enforced(monkeypatch):
      monkeypatch.setattr(adapter_module, "AGGREGATE_TIMEOUT_SECONDS", -1.0)
      adapter = GitAdapter()
      with pytest.raises(GitCommandTimeoutError):
          adapter.check_aggregate_deadline()

   def test_command_timeout_is_capped_by_remaining_aggregate_time(monkeypatch, tmp_path):
       # a command started just before the aggregate deadline gets only the
       # remaining time, never a full per-command timeout
       adapter = GitAdapter(command_timeout_seconds=30.0)
       started = adapter._started_at
       monkeypatch.setattr(adapter_module.time, "monotonic",
                           lambda: started + adapter_module.AGGREGATE_TIMEOUT_SECONDS - 0.25)
       seen = {}

       class _RecordingChild:
           def __init__(self):
               self.terminated = False
               self.killed = False
               self.returncode = None
               self.stdin = None
               self.stdout = __import__("io").BytesIO()
               self.stderr = __import__("io").BytesIO()
               self.args = ["git"]
               self.pid = 1234
               self._timed_out = False
           def wait(self, timeout=None):
               seen["timeout"] = timeout
               if timeout is not None and not self._timed_out:
                   self._timed_out = True
                   raise __import__("subprocess").TimeoutExpired(cmd="git", timeout=timeout)
               return self.returncode
           def terminate(self):
               self.terminated = True
               self.returncode = -15
           def kill(self):
               self.killed = True
               self.returncode = -9
           def poll(self):
               return self.returncode

       child = _RecordingChild()
       monkeypatch.setattr(adapter_module.subprocess, "Popen", lambda *a, **k: child)
       with pytest.raises(GitCommandTimeoutError):
           adapter.run(["rev-parse", "HEAD"], tmp_path)
       assert seen.get("timeout") is not None
       assert seen["timeout"] <= 0.25
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`Failed` — the scaffold's `consume_output_budget` does not yet raise, so the cumulative loop completes without error. The RED must be an assertion/exception mismatch, never an `AttributeError` (all three names exist in the scaffold) and never an import error.
- [ ] **Step 4: Minimal GREEN.** Implement `consume_output_budget(amount: int) -> None` to add `amount` to `self._cumulative_bytes` and raise `GitOutputTooLargeError` when the total exceeds `CUMULATIVE_OUTPUT_BUDGET_BYTES`; the constant is read from the MODULE at call time, never captured as a default argument, so a test that patches it is honored. Implement `check_aggregate_deadline() -> None` to read the CURRENT module constant `AGGREGATE_TIMEOUT_SECONDS` at call time and raise `GitCommandTimeoutError` when `time.monotonic()` is past `self._started_at + AGGREGATE_TIMEOUT_SECONDS`; `self._started_at` is set when the adapter is constructed, and the CONSTANT is read lazily so a test that patches it is honored. Implement `cumulative_output_bytes` as a read-only property. Wire `consume_output_budget` into the per-chunk accounting of Task 9 so EVERY byte read from stdout and stderr is counted against the cumulative budget. Call `check_aggregate_deadline()` at the ENTRY of `run_raw` itself (not only from `run`), so the 300-second aggregate budget covers every command regardless of which entry point issued it — Task 14 calls `run_raw` directly. The per-command cap and the cumulative budget are independent: a command under the per-command cap can still fail the cumulative budget.

Aggregate deadline enforcement is not merely a pre-check: `run_raw` must compute `effective_timeout = min(self.command_timeout_seconds, max(0.0, self._started_at + AGGREGATE_TIMEOUT_SECONDS - time.monotonic()))` using the (lazily read) current aggregate constant, and pass `effective_timeout` to the process wait. A command started just before the aggregate deadline therefore receives only the remaining time, never another full per-command timeout; if `effective_timeout <= 0`, `check_aggregate_deadline()` raises before the child is spawned. The effective timeout is recomputed per command and is never cached. Tests must prove the cap using a recorded wait timeout (not just that an error is raised). Thread-safe accounting: the per-command combined counter and the cumulative counter are guarded by the SAME lock used in Task 9, so concurrent adapter instances are not required but re-entrant chunk accounting is safe.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm the production constants are unchanged at `300.0` and `256 * 1024 * 1024` — the test may monkeypatch them, but the source must not lower them. Confirm the budget is never bypassed by a command that emits output only to stderr.
- [ ] **Step 8: Commit.** `git add backend/git_history/adapter.py tests/test_git_history_adapter.py` then `git commit -m "feat: enforce aggregate timeout and cumulative output budget"`.
- [ ] **Step 9: Independent review checklist.** [ ] Production constants are exactly 300.0 s and 256 MiB. [ ] Every stdout AND stderr byte is counted. [ ] The cumulative check happens after each chunk. [ ] The aggregate deadline is checked before each command. [ ] Both failures raise the §15 typed errors. [ ] Tests inject limits rather than lowering production values.
- [ ] **Step 10: Acceptance evidence.** GREEN output and the recorded constant values read back from the source.

---

### Task 11: Repository Validation, Version, and Revision Handling

**Prerequisites:** Tasks 8, 9, 10.

**Files:**
- Modify: `backend/git_history/adapter.py`
- Modify: none (the `is_unborn` helper is already implemented by Task 8, which owns the whole `git_repo` fixture contract)
- Test: `tests/test_git_history_adapter.py`

**Spec references:** §6.1 (allowed commands), §6.2 (shallow detection via `--is-shallow-repository`, `--git-common-dir` resolution), §3 (revision validation, unborn HEAD, detached HEAD), §8 (detached HEAD is state, not completeness), §13 (Git version floor).

**Inputs / Outputs:** Input: a repository path and a revision expression. Output: repository context, git version, resolved commit ID, and HEAD state.

**Interfaces:**
- Consumes: `GitRepositoryError`, `GitRevisionError`, `GitCommandError` (Task 1); `GitAdapter.run`, `GitAdapter.run_raw`, `CommandResult` (Tasks 8-9).
- Produces: `GitRepositoryContext` — a frozen, slotted dataclass with fields `is_bare: bool`, `is_shallow: bool`, `git_common_dir: Path`, `git_version: str`; plus `GitAdapter.git_version() -> str` (the FULL version string as Git prints it, e.g. `"git version 2.55.0.windows.5"` — the prefix is KEPT, which is the format asserted by the tests in Tasks 11 and 19), `GitAdapter.inspect_repository(repository_path: Path) -> GitRepositoryContext`, `GitAdapter.resolve_revision(repository_path: Path, revision: str) -> str`, `GitAdapter.is_detached_head(repository_path: Path) -> bool`, and `GitAdapter.is_unborn_head(repository_path: Path) -> bool` (True when `git symbolic-ref --quiet HEAD` succeeds while `git rev-parse --verify --end-of-options HEAD^{commit}` fails). The analyzer uses ONLY these adapter methods — it never invokes `subprocess` itself.

- [ ] **Step 1: Scaffolding.** `MINIMUM_GIT_VERSION` was already created by Task 8 in the same module — do NOT re-declare it. Add `GitRepositoryContext` and the FIVE methods (`git_version`, `inspect_repository`, `resolve_revision`, `is_detached_head`, `is_unborn_head`), each returning a placeholder (`GitRepositoryContext(False, False, Path("."), "")`, `""`, `""`, `False`, `False`) — every name the task's tests call MUST exist in the scaffold, so the RED is an assertion failure and never an `AttributeError`.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_adapter.py`:
  ```python
  def test_inspect_repository_reports_non_bare_non_shallow(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"a\n"})
      ctx = adapter().inspect_repository(repo.path)
      assert ctx.is_bare is False and ctx.is_shallow is False
      assert ctx.git_version.startswith("git version")

  def test_resolve_revision_returns_full_object_id(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"a\n"})
      assert len(adapter().resolve_revision(repo.path, "HEAD")) == 40

  def test_invalid_revision_raises_git_revision_error(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"a\n"})
      with pytest.raises(GitRevisionError):
          adapter().resolve_revision(repo.path, "no-such-revision")

  def test_revision_may_not_start_with_dash(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"a\n"})
      with pytest.raises(GitRevisionError):
          adapter().resolve_revision(repo.path, "--upload-pack=evil")

  def test_detached_head_is_detected(git_repo):
      repo = git_repo()
      head = repo.commit({"a.txt": b"a\n"})
      repo.detach(head)
      assert adapter().is_detached_head(repo.path) is True

  def test_unborn_head_is_reported(git_repo):
      repo = git_repo()
      assert repo.is_unborn() is True

  def test_rev_parse_verify_never_receives_two_revision_arguments(git_repo, monkeypatch):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      seen = []
      original = adapter_module.GitAdapter.run_raw
      def spy(self, args, repository_path, **kw):
          seen.append(list(args))
          return original(self, args, repository_path, **kw)
      monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
      adapter().resolve_revision(repo.path, "HEAD")
      assert seen, "no Git command was issued; the assertion below would be vacuous"
      for args in seen:
          if "rev-parse" in args and "--verify" in args:
              revisions = [a for a in args if a.endswith("^{commit}") or a.endswith("^{tree}")]
              assert len(revisions) <= 1, args

  def test_linked_worktree_shares_the_common_dir(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      worktree = repo.linked_worktree("wt")
      ctx = adapter().inspect_repository(worktree)
      # the common dir is shared with the main repository, not worktree-private
      # (both sides are resolved so the comparison does not depend on
      # WindowsPath separator or case normalisation)
      assert ctx.git_common_dir.resolve() == (repo.path / ".git").resolve()

  def test_unborn_head_is_detected_by_the_adapter(git_repo):
      repo = git_repo()
      # the adapter-level check is the one the analyzer uses
      assert adapter().is_unborn_head(repo.path) is True

  def test_an_old_git_version_is_rejected(monkeypatch):
      # spec §13: Git >= 2.29 is required (--end-of-options landed in 2.24,
      # --is-shallow-repository in 2.15; 2.29 is the conservative floor).
      # The floor is raised ABOVE the running Git so that the REAL
      # `git_version()` performs the comparison and raises -- the method
      # under test is deliberately NOT patched.
      monkeypatch.setattr(adapter_module, "MINIMUM_GIT_VERSION", (99, 0))
      with pytest.raises(GitUnavailableError):
          adapter().git_version()

  def test_the_running_git_version_is_accepted():
      version = adapter().git_version()
      assert version.startswith("git version ")
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`Failed` (the placeholder context and empty version string). No `ImportError`.
- [ ] **Step 4: Minimal GREEN.** Implement `git_version()` by issuing `git --version` THROUGH `run_raw` with NO repository path (`run_raw(["--version"])`) — never by spawning `git` directly — so the command inherits the same sanitized environment and the same `-c core.hooksPath=<empty dir>` override as every other Git invocation, and is subject to the same per-command timeout and output cap. The output is trimmed but the `"git version "` prefix is KEPT (the published format is the full string, e.g. `"git version 2.55.0.windows.5"`). The version floor is enforced HERE: parse the version numbers out of the string, compare them against the module constant `MINIMUM_GIT_VERSION = (2, 29)`, and raise `GitUnavailableError` when the running Git is OLDER than the floor (spec §13: *"Requires Git >= 2.29 ...; older versions raise `GitUnavailableError`/`GitCommandError`"*). `MINIMUM_GIT_VERSION` is read from the MODULE at call time, never captured as a default argument, so a test that patches it is honored. The floor is a CONSERVATIVE value: `--end-of-options` landed in Git 2.24 and `--is-shallow-repository` in 2.15, so 2.29 is the declared contract, not the first version where the commands work. Implement `inspect_repository` to run `git rev-parse --is-inside-work-tree` and `git rev-parse --is-bare-repository` (a directory that is neither raises `GitRepositoryError`), `git rev-parse --is-shallow-repository`, and `git rev-parse --git-common-dir`; resolve the common dir against the validated repository path (it may be relative or absolute) and store it as an absolute `Path`. Implement `resolve_revision` to reject a revision that starts with `-` or contains NUL (raise `GitRevisionError`), then run `git rev-parse --verify --end-of-options <revision>^{commit}` — EXACTLY ONE revision argument — and return the trimmed object ID; use `run_raw` so the exit status is available, and raise `GitRevisionError` when the status is non-zero. Implement `is_detached_head` via `git symbolic-ref --quiet HEAD` using `run_raw` (status 0 -> attached, non-zero -> detached). Implement `is_unborn_head` via `git symbolic-ref --quiet HEAD` succeeding while `git rev-parse --verify --end-of-options HEAD^{commit}` fails, using `run_raw` for both. NOTE: this check is scoped to `HEAD` (the default revision); a caller asking for a non-HEAD revision that happens to be unborn is out of scope for v1. Implement the fixture's `is_unborn()` helper the same way.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm every `rev-parse --verify` call passes EXACTLY ONE revision argument (add a guard test that records the argument lists and asserts no call contains two revision operands). Confirm `--end-of-options` is always present. Confirm the option-injection revision (`--upload-pack=evil`) never reaches the subprocess as a Git option.
- [ ] **Step 8: Commit.** `git add backend/git_history/adapter.py tests/test_git_history_adapter.py` then `git commit -m "feat: validate repository version and revision handling"`.
- [ ] **Step 9: Independent review checklist.** [ ] Exactly one revision argument per `rev-parse --verify`. [ ] `--end-of-options` always used. [ ] Relative and absolute `--git-common-dir` both resolve correctly. [ ] Detached HEAD is a boolean, not an error. [ ] Unborn HEAD is distinguishable from a bad revision. [ ] Non-repository raises `GitRepositoryError`. [ ] The Git version floor is enforced: a Git older than `MINIMUM_GIT_VERSION` raises `GitUnavailableError`.
- [ ] **Step 10: Acceptance evidence.** GREEN output and the recorded argument lists from the single-revision-argument guard test.

---

### Task 12: Commit Enumeration, Path Filters, and Limits

**Prerequisites:** Task 11.

**Files:**
- Modify: `backend/git_history/adapter.py`
- Test: `tests/test_git_history_adapter.py`

**Spec references:** §6.2 (rev-list invocation, `--topo-order`, `--reverse`, N+1 lookahead, `--no-merges`, literal pathspecs, truncation detection), §3 (`max_commits` override and the 100,000 hard cap), §11 (`MAX_COMMITS_REACHED`).

**Inputs / Outputs:** Input: repository path, revision, and selection options. Output: an ordered tuple of commit IDs plus a truncation flag.

**Interfaces:**
- Consumes: `GitAdapter.run`, `GitAdapter.resolve_revision` (Tasks 8-11); `GitParseError` (Task 1).
- Produces: `CommitSelection` — a frozen, slotted dataclass with fields `commit_ids: tuple[str, ...]` and `truncated: bool`; plus `GitAdapter.enumerate_commits(repository_path: Path, revision: str, *, max_commits: int | None, include_merges: bool, path_filter: Sequence[str]) -> CommitSelection`.

- [ ] **Step 1: Scaffolding.** Add `CommitSelection` and `enumerate_commits` returning `CommitSelection((), False)`.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_adapter.py`:
  ```python
  def test_enumeration_returns_oldest_first(git_repo):
      repo = git_repo()
      first = repo.commit({"a.txt": b"1\n"})
      second = repo.commit({"a.txt": b"2\n"})
      ids = adapter().enumerate_commits(repo.path, "HEAD", max_commits=None,
                                       include_merges=True, path_filter=()).commit_ids
      # oldest first, as `--reverse` guarantees
      assert ids == (first, second)

  def test_max_commits_boundary_is_exact(git_repo):
      repo = git_repo()
      first = repo.commit({"a.txt": b"1\n"})
      second = repo.commit({"a.txt": b"2\n"})
      third = repo.commit({"a.txt": b"3\n"})
      exact = adapter().enumerate_commits(repo.path, "HEAD", max_commits=3,
                                          include_merges=True, path_filter=())
      assert len(exact.commit_ids) == 3 and exact.truncated is False
      assert set(exact.commit_ids) == {first, second, third}
      over = adapter().enumerate_commits(repo.path, "HEAD", max_commits=2,
                                         include_merges=True, path_filter=())
      assert len(over.commit_ids) == 2 and over.truncated is True
      # the retained window is the NEWEST K commits (git rev-list --max-count
      # selects the newest K, then --reverse flips them to oldest-first)
      assert set(over.commit_ids) == {second, third}

  def test_include_merges_false_excludes_merge_commits(git_repo):
      repo = git_repo()
      base = repo.commit({"a.txt": b"1\n"})
      repo.branch("side", base)
      repo.commit({"b.txt": b"2\n"})          # on main
      repo.checkout("side")
      repo.commit({"c.txt": b"3\n"})          # on the side branch
      repo.checkout("main")
      repo.merge("side")                      # a REAL merge commit
      with_merges = adapter().enumerate_commits(repo.path, "HEAD", max_commits=None,
                                                include_merges=True, path_filter=())
      without = adapter().enumerate_commits(repo.path, "HEAD", max_commits=None,
                                            include_merges=False, path_filter=())
      assert len(without.commit_ids) == len(with_merges.commit_ids) - 1

  def test_path_filter_selects_commits_only(git_repo):
      repo = git_repo()
      repo.commit({"keep.txt": b"1\n", "other.txt": b"1\n"})
      repo.commit({"other.txt": b"2\n"})
      selected = adapter().enumerate_commits(repo.path, "HEAD", max_commits=None,
                                             include_merges=True,
                                             path_filter=("keep.txt",))
      assert len(selected.commit_ids) == 1

  def test_path_filter_uses_literal_pathspec(git_repo, monkeypatch):
      repo = git_repo()
      repo.commit({"lit[1].txt": b"1\n"})
      repo.commit({"lit1.txt": b"2\n"})       # a bare pathspec would match this too
      seen = []
      original = adapter_module.GitAdapter.run_raw
      def spy(self, args, repository_path, **kw):
          seen.append(list(args))
          return original(self, args, repository_path, **kw)
      monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
      selected = adapter().enumerate_commits(repo.path, "HEAD", max_commits=None,
                                             include_merges=True,
                                             path_filter=("lit[1].txt",))
      # the literal form selects ONLY the bracketed file
      assert len(selected.commit_ids) == 1
      # and the pathspec argument is literally in the `:(literal)` form
      rev_list = [a for a in seen if a[0] == "rev-list"]
      assert rev_list
      assert any(arg.startswith(":(literal)") for arg in rev_list[0])

  def test_invalid_path_filter_is_rejected(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      for bad in ("", "a\\b", "/abs", "../up", "./here", "a//b", "a\x00b"):
          with pytest.raises(GitParseError):
              adapter().enumerate_commits(repo.path, "HEAD", max_commits=None,
                                          include_merges=True, path_filter=(bad,))

  @pytest.mark.skipif(sys.platform == "win32",
                      reason="'*' and '?' are illegal filename characters on Windows")
  def test_glob_characters_in_a_path_filter_are_treated_literally(git_repo):
      repo = git_repo()
      repo.commit({"a*.txt": b"1\n"})
      repo.commit({"ab.txt": b"2\n"})
      selected = adapter().enumerate_commits(repo.path, "HEAD", max_commits=None,
                                             include_merges=True,
                                             path_filter=("a*.txt",))
      assert len(selected.commit_ids) == 1
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`Failed` (the stub returns an empty selection).
- [ ] **Step 4: Minimal GREEN.** Implement `enumerate_commits` to run `git rev-list --topo-order --reverse --max-count=<K> --end-of-options <revision>` where `K = max_commits + 1` when `max_commits` is not `None`, else `100000 + 1` (the internal 100,000 hard cap with the same N+1 lookahead, so exactly 100,000 commits is never falsely truncated). `--end-of-options` is REQUIRED: the revision is a user-supplied expression and must never be parsed as a Git option. Add `--no-merges` when `include_merges` is `False`. When `path_filter` is non-empty, append `--` followed by one `:(literal)<path>` argument per validated path. Parse the output one object ID per line, validate each with `require_object_id`, and set `truncated = len(ids) > effective_limit` while discarding the extra ID. Because `git rev-list --max-count=K` selects the NEWEST K commits and `--reverse` then flips them to oldest-first, the RETAINED window is the newest K commits and the DISCARDED entry is the OLDEST one (the first element of the reversed list). With exactly `max_commits` commits the window is the whole history and `truncated` is `False`. Validate each path filter entry (nonempty, no NUL, no backslash, forward slashes only, no leading slash, no `.` or `..` component, no empty component) and raise `GitParseError` on violation.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm the pathspec is always the literal form `:(literal)<path>` and never a bare path (which would enable pathspec magic). Confirm `--` always precedes the pathspec list. Confirm the requested `--max-count` is never less than the effective limit (no off-by-one truncation bug).
- [ ] **Step 8: Commit.** `git add backend/git_history/adapter.py tests/test_git_history_adapter.py` then `git commit -m "feat: enumerate commits with lookahead and literal path filters"`.
- [ ] **Step 9: Independent review checklist.** [ ] N+1 lookahead applied for BOTH the user limit and the 100,000 cap. [ ] Exactly-N is not truncated; N+1 is. [ ] `--no-merges` applied only when requested. [ ] Pathspecs are literal and preceded by `--`. [ ] Output IDs validated. [ ] Path-filter inputs validated.
- [ ] **Step 10: Acceptance evidence.** GREEN output and the recorded `--max-count` values used for the 3-commit boundary case.


---

### Task 13: Authoritative Batch Commit Metadata and DAG Parsing

**Prerequisites:** Tasks 6, 9, 10, 11, 12.

**Files:**
- Modify: `backend/git_history/adapter.py` (add the batch fetch method)
- Modify: `backend/git_history/parser.py` (expose a `commit_from_raw` helper if needed)
- Test: `tests/test_git_history_adapter.py`, `tests/test_git_history_parser.py`

**Spec references:** §6.3 (metadata fetched for exactly the enumerated IDs via `cat-file --batch`; parents from raw headers, not `%P`), §4.2 (parent ordering, `is_root`/`is_merge`), §8 (shallow boundary is not a root).

**Inputs / Outputs:** Input: an ordered tuple of commit IDs. Output: a mapping from commit ID to parsed raw commit record, identical in membership to the requested set.

**Interfaces:**
- Consumes: `parse_batch_stream`, `RawCommitObject` (Task 6); `GitAdapter.run` (Tasks 8-10).
- Produces: `GitAdapter.fetch_commit_objects(repository_path: Path, commit_ids: Sequence[str]) -> dict[str, RawCommitObject]`.

- [ ] **Step 1: Scaffolding.** Add `fetch_commit_objects` returning `{}`.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_adapter.py`:
  ```python
  def test_fetch_returns_exactly_the_requested_commits(git_repo):
      repo = git_repo()
      first = repo.commit({"a.txt": b"1\n"})
      second = repo.commit({"a.txt": b"2\n"})
      objects = adapter().fetch_commit_objects(repo.path, [first, second])
      assert set(objects) == {first, second}
      assert objects[first].parent_ids == ()
      assert objects[second].parent_ids == (first,)

  def test_shallow_boundary_keeps_real_parent_and_is_not_root(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      repo.commit({"a.txt": b"2\n"})
      shallow = repo.shallow_clone(depth=1)
      objects = adapter().fetch_commit_objects(shallow.path, [shallow.head])
      boundary = objects[shallow.head]
      assert len(boundary.parent_ids) == 1
      assert boundary.parent_ids[0] != shallow.head

   def test_fetch_handles_input_larger_than_pipe_capacity(git_repo):
       # many commit IDs exceed the OS pipe buffer; the writer thread must
       # feed them without deadlock and stdin must be closed reliably
       repo = git_repo()
       ids = []
       for i in range(2000):
           ids.append(repo.commit({"a.txt": f"{i}\n".encode()}))
       objects = adapter().fetch_commit_objects(repo.path, ids)
       assert set(objects) == set(ids)
       assert len(objects) == 2000
  ```
  And in `tests/test_git_history_parser.py`:
  ```python
  def test_root_commit_has_no_parents():
      raw = _batch(b"author A <a@e.com> 1 +0000\ncommitter A <a@e.com> 1 +0000\n\nmsg\n")
      commit = parse_batch_stream(raw, ["a" * 40])["a" * 40]
      assert commit.parent_ids == ()
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_adapter.py tests/test_git_history_parser.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`Failed` (the stub returns an empty mapping, so the lookup raises `KeyError` — a legitimate behavioural RED). No `ImportError`.
- [ ] **Step 4: Minimal GREEN.** Implement `fetch_commit_objects` to run `git cat-file --batch` with the requested object IDs written to stdin, one per line, each terminated by `\n`, by passing them as `stdin_data` to `run_raw` (Task 9) — never by writing to the pipe directly and never via `subprocess` in this method. The writer thread from Task 9 owns stdin; it closes stdin reliably and swallows `BrokenPipeError`, so a payload larger than the OS pipe buffer (many thousands of commit IDs) cannot deadlock the command. Concurrent stdout/stderr draining from Task 9 prevents pipe deadlock on the output side. `fetch_commit_objects` must tolerate a child that exits early: stdin writes that raise `BrokenPipeError` are ignored, and the command's exit status/stderr are still surfaced by `run_raw`. Feed the raw stdout bytes to `parse_batch_stream` with the same requested ID sequence so the parser can detect a missing object. Return the mapping. The command must NOT use `git log`, `%P`, or any human-readable format (§7: "No use of `git log --pretty` human format"). Do not sort, filter, or re-order the returned records. On timeout or cumulative-output overflow, `run_raw` terminates and reaps the child and raises the typed error; `fetch_commit_objects` does not catch it. The raw records retain their `author_header_present`/`committer_header_present` flags for Task 19.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_adapter.py tests/test_git_history_parser.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm the stdin payload contains ONLY the requested object IDs and no shell metacharacters. Confirm `git log` is not used to obtain parents (the raw object's `parent` lines are authoritative). Confirm the command uses `cat-file --batch` with no `--filters` and no `--textconv`.
- [ ] **Step 8: Commit.** `git add backend/git_history/adapter.py backend/git_history/parser.py tests/test_git_history_adapter.py tests/test_git_history_parser.py` then `git commit -m "feat: fetch authoritative commit metadata via cat-file batch"`.
- [ ] **Step 9: Independent review checklist.** [ ] The fetched set equals the requested set exactly. [ ] Parent IDs come from raw headers, in original order. [ ] A shallow boundary keeps its real parent ID and is not a root. [ ] A missing object raises `GitParseError`. [ ] No `git log` usage. [ ] No textconv/filters flags.
- [ ] **Step 10: Acceptance evidence.** GREEN output and the recorded stdin payload for a two-commit request.

---

### Task 14: Root, First-Parent, and Unavailable-Baseline Resolution

**Prerequisites:** Tasks 11, 13.

**Files:**
- Modify: `backend/git_history/adapter.py`
- Test: `tests/test_git_history_adapter.py`

**Spec references:** §6.4 (tree resolution: the commit tree comes from the raw `tree` header; the first-parent tree uses exactly ONE `rev-parse --verify --end-of-options <commit>^1^{tree}`; root commits use the fixed empty tree; unavailable baseline handling), §14 (at most one tree-resolution command per non-root commit), §4.5 (`commits_with_unavailable_baseline`).

**Inputs / Outputs:** Input: a commit ID and its raw commit record. Output: the comparison baseline tree ID, or an explicit "unavailable" marker.

**Interfaces:**
- Consumes: `RawCommitObject` (Task 6); `GitAdapter.run_raw` (Tasks 8-9); `GitCommandError` (Task 1).
- Produces: `BaselineResolution` — a frozen, slotted dataclass with fields `baseline_tree: str | None`, `available: bool`; plus `GitAdapter.resolve_baseline(repository_path: Path, commit_id: str, raw: RawCommitObject) -> BaselineResolution` and module constants `EMPTY_TREE_SHA1` / `EMPTY_TREE_SHA256` re-exported from `invariants.py`.

- [ ] **Step 1: Scaffolding.** Add `BaselineResolution` and `resolve_baseline` returning `BaselineResolution(None, False)`.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_adapter.py`:
  ```python
  def test_root_commit_uses_the_empty_tree_without_a_command(git_repo, monkeypatch):
      repo = git_repo()
      root = repo.commit({"a.txt": b"1\n"})
      # fetch the raw record FIRST, so the spy below cannot interfere
      raw = adapter().fetch_commit_objects(repo.path, [root])[root]
      calls = []
      original = adapter_module.GitAdapter.run_raw
      def spy(self, args, repository_path, **kw):
          calls.append(list(args))
          return original(self, args, repository_path, **kw)
      monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
      result = adapter().resolve_baseline(repo.path, root, raw)
      assert result.available is True
      assert result.baseline_tree == EMPTY_TREE_SHA1
      # a root commit needs NO tree-resolution command at all
      assert not any("rev-parse" in " ".join(a) for a in calls)

  def test_non_root_commit_resolves_first_parent_tree_with_one_command(git_repo, monkeypatch):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      second = repo.commit({"a.txt": b"2\n"})
      raw = adapter().fetch_commit_objects(repo.path, [second])[second]
      calls = []
      original = adapter_module.GitAdapter.run_raw
      def spy(self, args, repository_path, **kw):
          calls.append(list(args))
          return original(self, args, repository_path, **kw)
      # spy on run_raw: `resolve_baseline` MUST use run_raw (never `run`) so
      # the exit status survives
      monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
      result = adapter().resolve_baseline(repo.path, second, raw)
      assert result.available is True
      rev_parse_calls = [c for c in calls if "rev-parse" in c]
      assert len(rev_parse_calls) == 1
      # the first-parent revision is ONE argument: "<sha>^1^{tree}"
      assert sum(1 for a in rev_parse_calls[0] if a.endswith("^1^{tree}")) == 1

  def test_shallow_boundary_reports_an_unavailable_baseline(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      repo.commit({"a.txt": b"2\n"})
      shallow = repo.shallow_clone(depth=1)
      raw = adapter().fetch_commit_objects(shallow.path, [shallow.head])[shallow.head]
      result = adapter().resolve_baseline(shallow.path, shallow.head, raw)
      assert result.available is False
      assert result.baseline_tree is None

  def test_first_parent_resolution_uses_exactly_one_revision_argument(git_repo, monkeypatch):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      second = repo.commit({"a.txt": b"2\n"})
      seen = []
      original = adapter_module.GitAdapter.run_raw
      def spy(self, args, repository_path, **kw):
          seen.append(list(args))
          return original(self, args, repository_path, **kw)
      monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
      raw = adapter().fetch_commit_objects(repo.path, [second])[second]
      adapter().resolve_baseline(repo.path, second, raw)
      assert seen, "no Git command was issued; the assertion below would be vacuous"
      for args in seen:
          if "rev-parse" in args and "--verify" in args:
              revisions = [a for a in args if a.endswith("^{tree}")]
              assert len(revisions) == 1, args
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`Failed` (the stub reports unavailable for every commit).
- [ ] **Step 4: Minimal GREEN.** Implement `resolve_baseline` to branch on the RAW record, never on the command outcome: if `raw.parent_ids` is empty (a genuine root), return the empty-tree constant for the repository's object format (40-hex commit IDs -> SHA-1 constant, 64-hex -> SHA-256 constant) with `available=True` and issue NO command. Otherwise run EXACTLY ONE command `git rev-parse --verify --end-of-options <commit_id>^1^{tree}` through `run_raw` (never `run`, so the exit status survives) and return `BaselineResolution(tree_id, True)` when the status is 0. If the status is 128, return `BaselineResolution(None, False)` — the documented non-fatal "baseline unavailable" outcome (the exit status, not the stderr text, is the discriminator, because a root commit, a shallow boundary, and an unknown object ID all print `fatal: Needed a single revision`). Any other status (or a timeout, oversize output, or termination by signal) raises `GitCommandError`.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm the resolution command passes EXACTLY ONE revision argument (assert `rev_parse_calls[0]` contains one `^1^{tree}` operand and no second revision). Confirm the commit's own tree is NEVER resolved by a command — it comes from `raw.tree_id`. Confirm the empty-tree constants are the fixed literals, never computed at runtime.
- [ ] **Step 8: Commit.** `git add backend/git_history/adapter.py tests/test_git_history_adapter.py` then `git commit -m "feat: resolve commit baselines with single first-parent resolution"`.
- [ ] **Step 9: Independent review checklist.** [ ] Root commits issue zero resolution commands. [ ] Non-root commits issue exactly one. [ ] One revision argument only. [ ] Root detection comes from raw parent lines, never from a command failure. [ ] Unavailable baseline is non-fatal and reported distinctly. [ ] Other failures still raise `GitCommandError`. [ ] Empty-tree constants are literals.
- [ ] **Step 10: Acceptance evidence.** GREEN output and the recorded command lists for the root, non-root, and shallow cases.

---

### Task 15: Root/Linear/Merge File-Change Extraction

**Prerequisites:** Tasks 7, 9, 10, 13, 14.

**Files:**
- Modify: `backend/git_history/adapter.py`
- Test: `tests/test_git_history_adapter.py`

**Spec references:** §6.4 (two-tree comparison, root empty-tree baseline, first-parent baseline, merge first-parent semantics), §6.2/§4.5 (merge commits trigger `MERGE_SEMANTICS_PARTIAL`), §9 (merge first-parent proof).

**Inputs / Outputs:** Input: a baseline tree ID and a commit tree ID. Output: the parsed change records for that commit.

**Interfaces:**
- Consumes: `parse_name_status`, `RawFileChange` (Task 7); `GitAdapter.run` (Tasks 8-10); `RawCommitObject` (Task 6); `GitAdapter.fetch_commit_objects` (Task 13); `GitAdapter.resolve_baseline` (Task 14).
- Produces: `GitAdapter.diff_trees(repository_path: Path, baseline_tree: str, commit_tree: str, *, find_renames: bool, find_copies: bool) -> tuple[RawFileChange, ...]`, `GitAdapter.commit_tree_id(raw: RawCommitObject) -> str`, and `GitAdapter.changes_for_commit(repository_path: Path, commit_id: str, *, detect_renames: bool = True, detect_copies: bool = False) -> tuple[RawFileChange, ...]`.

- [ ] **Step 1: Scaffolding.** Add `diff_trees`, `commit_tree_id`, and `changes_for_commit` to `adapter.py`, with `diff_trees` returning `()`, `commit_tree_id` returning `""`, and `changes_for_commit` returning `()`.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_adapter.py`:
  ```python
  def test_root_commit_reports_every_file_as_added(git_repo):
      repo = git_repo()
      root = repo.commit({"a.txt": b"1\n", "b.txt": b"2\n"})
      changes = adapter().changes_for_commit(repo.path, root)
      assert {c.new_path for c in changes} == {"a.txt", "b.txt"}
      assert all(c.change_type is GitChangeType.ADDED for c in changes)

  def test_linear_commit_reports_modification(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      second = repo.commit({"a.txt": b"2\n"})
      changes = adapter().changes_for_commit(repo.path, second)
      assert [(c.change_type, c.new_path) for c in changes] == [(GitChangeType.MODIFIED, "a.txt")]

  def test_merge_reports_only_first_parent_changes(git_repo):
      repo = git_repo()
      base = repo.commit({"a.txt": b"1\n"})
      repo.branch("side", base)
      repo.commit({"b.txt": b"2\n"})      # committed on MAIN before the merge
      repo.checkout("side")
      repo.commit({"c.txt": b"3\n"})      # committed on the SIDE branch
      repo.checkout("main")
      merge = repo.merge("side")
      paths = {c.new_path for c in adapter().changes_for_commit(repo.path, merge)}
      # The FIRST PARENT of the merge is main's HEAD, which already contains
      # b.txt. The first-parent diff therefore reports what the merge brought
      # in from the side branch (c.txt) and NOT b.txt.
      assert "c.txt" in paths
      assert "b.txt" not in paths

  def test_binary_file_is_reported_as_a_change(git_repo):
      repo = git_repo()
      repo.commit({"bin.dat": bytes(range(256))})
      repo.commit({"bin.dat": bytes(reversed(range(256)))})
      changes = adapter().changes_for_commit(repo.path, repo.head)
      assert [c.change_type for c in changes] == [GitChangeType.MODIFIED]
      assert changes[0].new_path == "bin.dat"

  def test_deleted_file_is_reported_as_a_deletion(git_repo):
      repo = git_repo()
      repo.commit({"gone.txt": b"1\n", "kept.txt": b"1\n"})
      repo.delete("gone.txt")
      changes = adapter().changes_for_commit(repo.path, repo.head)
      assert [(c.change_type, c.old_path, c.new_path) for c in changes] == [
          (GitChangeType.DELETED, "gone.txt", None)]

  def test_filename_with_spaces_and_unicode_is_extracted(git_repo):
      repo = git_repo()
      repo.commit({"a file with spaces.txt": b"1\n", "caf\u00e9.py": b"2\n"})
      changes = adapter().changes_for_commit(repo.path, repo.head)
      assert {c.new_path for c in changes} == {"a file with spaces.txt", "caf\u00e9.py"}
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`Failed` (the stub returns no changes).
- [ ] **Step 4: Minimal GREEN.** Implement `commit_tree_id(raw)` to return `raw.tree_id` (the commit's own tree, with NO extra Git command). Implement `diff_trees` to run `git diff-tree --no-commit-id -r -z --no-ext-diff --no-textconv --no-color --name-status <baseline_tree> <commit_tree>` plus the rename/copy flags selected in Task 16, and return `parse_name_status(raw_stdout)`. Implement `changes_for_commit(repository_path, commit_id, *, detect_renames=True, detect_copies=False)` as the convenience composition used by the tests: fetch the raw record, resolve the baseline, and when the baseline is available run the two-tree diff; when it is unavailable return an empty tuple (no fabricated changes). The two-tree form is mandatory — never `git diff-tree <commit>` alone, because that silently omits merge commits.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm the diff command always includes `--no-ext-diff --no-textconv --no-color`. Confirm the baseline and commit tree are passed as two separate arguments to the two-tree form. Confirm a commit with an unavailable baseline produces ZERO changes rather than an empty-tree comparison.
- [ ] **Step 8: Commit.** `git add backend/git_history/adapter.py tests/test_git_history_adapter.py` then `git commit -m "feat: extract file changes via two-tree first-parent diff"`.
- [ ] **Step 9: Independent review checklist.** [ ] Two-tree diff form only. [ ] Root commit uses the empty-tree baseline. [ ] Merge reports first-parent changes only. [ ] Unavailable baseline yields zero changes. [ ] `--no-ext-diff --no-textconv` always present. [ ] Commit tree taken from the raw record.
- [ ] **Step 10: Acceptance evidence.** GREEN output and the recorded diff command arguments for a root, a linear, and a merge commit.

---

### Task 16: Rename/Copy Similarity and Option Combinations

**Prerequisites:** Task 15.

**Files:**
- Modify: `backend/git_history/adapter.py`
- Test: `tests/test_git_history_adapter.py`

**Spec references:** §6.4 (conditional rename/copy flags), §6.5 (similarity is Git's score; `detect_renames=False` yields delete+add; `detect_copies=False` yields add), §9 (copy source is lineage, not a modification).

**Inputs / Outputs:** Input: the `detect_renames` and `detect_copies` options. Output: the correct diff command and the resulting change records.

**Interfaces:**
- Consumes: `GitAdapter.diff_trees` (Task 15).
- Produces: `GitAdapter.build_diff_flags(find_renames: bool, find_copies: bool) -> list[str]` and the option-aware `changes_for_commit(repository_path, commit_id, *, detect_renames: bool, detect_copies: bool)`.

- [ ] **Step 1: Scaffolding.** Add `build_diff_flags` returning `[]` to `adapter.py`. The `changes_for_commit` keyword arguments `detect_renames` / `detect_copies` are ALREADY declared in Task 15's Interfaces (with the defaults `True` / `False`); this task only makes them IN EFFECT, so there is nothing to add to the signature here.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_adapter.py`:
  ```python
  def test_flag_combinations():
      assert adapter().build_diff_flags(True, False) == ["--find-renames"]
      assert adapter().build_diff_flags(False, False) == []
      assert adapter().build_diff_flags(True, True) == ["--find-renames", "--find-copies-harder"]
      assert adapter().build_diff_flags(False, True) == ["--no-renames", "--find-copies-harder"]

  def test_rename_is_reported_with_similarity(git_repo):
      repo = git_repo()
      repo.commit({"old.txt": b"line one\nline two\nline three\n"})
      renamed = repo.commit_rename("old.txt", "new.txt")
      changes = adapter().changes_for_commit(repo.path, renamed, detect_renames=True, detect_copies=False)
      assert changes[0].change_type is GitChangeType.RENAMED
      assert 0 <= changes[0].similarity <= 100

  def test_copy_source_is_unchanged_and_recorded_as_old_path(git_repo):
      repo = git_repo()
      repo.commit({"src.txt": b"payload\n" * 20})
      copied = repo.commit_copy("src.txt", "dst.txt")
      changes = adapter().changes_for_commit(repo.path, copied, detect_renames=True, detect_copies=True)
      copy = [c for c in changes if c.change_type is GitChangeType.COPIED]
      assert copy and copy[0].old_path == "src.txt" and copy[0].new_path == "dst.txt"

  def test_renames_disabled_yields_delete_and_add(git_repo):
      repo = git_repo()
      repo.commit({"old.txt": b"line one\nline two\nline three\n"})
      renamed = repo.commit_rename("old.txt", "new.txt")
      changes = adapter().changes_for_commit(repo.path, renamed, detect_renames=False, detect_copies=False)
      kinds = {c.change_type for c in changes}
      assert GitChangeType.RENAMED not in kinds
      # Git reports the rename as a delete plus an add
      assert {GitChangeType.DELETED, GitChangeType.ADDED} <= kinds
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`Failed` (the stub returns no flags and no rename detection).
- [ ] **Step 4: Minimal GREEN.** Implement `build_diff_flags` exactly as §6.4/§6.5 prescribe: `detect_renames=True, detect_copies=False` -> `["--find-renames"]`; `False, False` -> `[]`; `True, True` -> `["--find-renames", "--find-copies-harder"]`; `False, True` -> `["--no-renames", "--find-copies-harder"]`. Thread the flags through `changes_for_commit` into `diff_trees`. Do NOT normalize the similarity score client-side — record Git's integer as reported.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_adapter.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm `--find-copies-harder` (not plain `--find-copies`) is used, so copies of unchanged files are detected. Confirm no client-side rename/copy synthesis: the analyzer records what Git reports and never reclassifies an add as a rename.
- [ ] **Step 8: Commit.** `git add backend/git_history/adapter.py tests/test_git_history_adapter.py` then `git commit -m "feat: honor rename and copy detection option combinations"`.
- [ ] **Step 9: Independent review checklist.** [ ] All four flag combinations exact. [ ] Similarity is Git's score, unmodified. [ ] Copy source preserved as `old_path`. [ ] Renames disabled yields delete+add from Git. [ ] No client-side reclassification.
- [ ] **Step 10: Acceptance evidence.** GREEN output and the recorded flag list for each of the four combinations.


---

### Task 17: Deterministic Commit and Path Indexes

**Prerequisites:** Tasks 3, 4, 5.

**Files:**
- Create: `backend/git_history/analyzer.py`
- Test: `tests/test_git_history_analyzer.py`

**Spec references:** §4.5 (`commits_by_id`, `changes_by_commit`, `changes_by_path`, `commits_touching_path`; copy source is NOT a touched path), §7 (ordering), §14 (indexes are O(1) average).

**Inputs / Outputs:** Input: commits and changes. Output: the four deterministic path/commit indexes.

**Interfaces:**
- Consumes: `GitCommit`, `GitFileChange`, `GitChangeType` (Tasks 3, 4, 2).
- Produces (from `backend.git_history.analyzer`): `build_commits_by_id(commits: Sequence[GitCommit]) -> dict[str, GitCommit]`; `build_changes_by_commit(commits: Sequence[GitCommit], changes: Sequence[GitFileChange]) -> dict[str, tuple[GitFileChange, ...]]` — the result MUST contain an entry for EVERY commit in `commits` (an empty tuple for a commit that produced no changes), because spec §6.4 requires `changes_by_commit[commit_id]` to be an empty tuple for a commit with an unavailable baseline, and §4.5 requires that to be distinguishable from a genuinely unchanged commit; `build_changes_by_path(changes: Sequence[GitFileChange], commits_by_id: Mapping[str, GitCommit]) -> dict[str, tuple[GitFileChange, ...]]`; `build_commits_touching_path(changes: Sequence[GitFileChange]) -> dict[str, tuple[str, ...]]`; and `touched_paths(commit_id: str, changes: Sequence[GitFileChange]) -> tuple[str, ...]` — the shared "genuinely touched" rule.

- [ ] **Step 1: Scaffolding.** Create `analyzer.py` with the five functions returning empty dicts / empty tuples.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_analyzer.py`:
  ```python
  def test_touched_paths_excludes_copy_source():
      copy = _change(commit_id="a" * 40, change_type=GitChangeType.COPIED,
                     old_path="src.py", new_path="dst.py")
      assert touched_paths("a" * 40, [copy]) == ("dst.py",)

  def test_touched_paths_includes_both_paths_for_a_rename():
      rename = _change(commit_id="a" * 40, change_type=GitChangeType.RENAMED,
                       old_path="old.py", new_path="new.py")
      assert touched_paths("a" * 40, [rename]) == ("new.py", "old.py")

  def test_changes_by_path_includes_copy_source():
      copy = _change(commit_id="a" * 40, change_type=GitChangeType.COPIED,
                     old_path="src.py", new_path="dst.py")
      index = build_changes_by_path([copy], {})
      assert set(index) == {"src.py", "dst.py"}

  def test_commits_touching_path_excludes_copy_source():
      copy = _change(commit_id="a" * 40, change_type=GitChangeType.COPIED,
                     old_path="src.py", new_path="dst.py")
      index = build_commits_touching_path([copy])
      assert "src.py" not in index
      assert index["dst.py"] == ("a" * 40,)

  def test_indexes_are_sorted_and_deduplicated():
      changes = [_change(commit_id="b" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py"),
                 _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py")]
      index = build_commits_touching_path(changes)
      assert index["x.py"] == ("a" * 40, "b" * 40)

  def test_changes_by_commit_has_an_entry_for_every_commit():
      commits = _commits(["a" * 40, "b" * 40])
      index = build_changes_by_commit(commits, [])
      assert set(index) == {"a" * 40, "b" * 40}
      assert index["a" * 40] == ()

  def test_changes_within_a_commit_are_sorted_by_type_then_path():
      changes = [
          _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="z.py"),
          _change(commit_id="a" * 40, change_type=GitChangeType.ADDED, new_path="b.py"),
          _change(commit_id="a" * 40, change_type=GitChangeType.ADDED, new_path="a.py"),
      ]
      index = build_changes_by_commit(_commits(["a" * 40]), changes)
      assert [(c.change_type.value, c.new_path) for c in index["a" * 40]] == [
          ("added", "a.py"), ("added", "b.py"), ("modified", "z.py")]

  def test_changes_by_path_falls_back_when_the_commit_is_unknown():
      change = _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py")
      index = build_changes_by_path([change], {})
      assert index["x.py"] == (change,)
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_analyzer.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`Failed` (the stubs return empty results).
- [ ] **Step 4: Minimal GREEN.** Implement the "genuinely touched" rule in ONE place (`touched_paths`): for a `COPIED` change only `new_path` counts; for every other change type `old_path` (when present) and `new_path` count; the result is a sorted tuple of unique paths. `build_commits_by_id` maps each commit ID to its commit. `build_changes_by_commit` SEEDS the result with an empty tuple for EVERY commit, then groups the changes by `commit_id` and sorts each group by `(change_type value, new_path or old_path)` — spec §7: *"Changes within a commit sorted by `(change_type value, new_path or old_path)`"*. Seeding is required by spec §6.4, which states that `changes_by_commit[commit_id]` is an empty tuple for a commit with an unavailable baseline. `build_changes_by_path` keys on BOTH `old_path` and `new_path` where present (including copy sources) and orders each list by `(committer_timestamp desc, commit_id asc)` of the containing commit, then change order; when the containing commit is absent from `commits_by_id`, fall back to sorting by commit ID ascending and then change order (a deterministic total order, never an exception). `build_commits_touching_path` keys on genuinely touched paths only and stores sorted unique commit IDs.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_analyzer.py tests/test_git_history_models.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm no index is built from a path that is only a copy SOURCE. Confirm the functions are pure: they take inputs and return new dicts, never mutating a caller's list.
- [ ] **Step 8: Commit.** `git add backend/git_history/analyzer.py tests/test_git_history_analyzer.py` then `git commit -m "feat: build deterministic commit and path indexes"`.
- [ ] **Step 9: Independent review checklist.** [ ] One shared implementation of the touched-path rule. [ ] Copy source excluded from touched paths but present in `changes_by_path`. [ ] All indexes sorted and deduplicated. [ ] Functions are pure. [ ] Ordering matches §7.
- [ ] **Step 10: Acceptance evidence.** GREEN output plus the recorded index contents for a copy and a rename case.

---

### Task 18: Co-Change Pair Accounting and Budgets

**Prerequisites:** Task 17.

**Files:**
- Modify: `backend/git_history/analyzer.py`
- Test: `tests/test_git_history_analyzer.py`

**Spec references:** §14 (global 10,000,000 unique-pair budget; new-pair contribution; budget tested BEFORE mutation; atomic per-commit omission; the two symmetric index entries are ONE budget unit; the 1,000-touched-path per-commit cap), §4.5 (pair normalization, symmetry, `co_change_incomplete_paths` inference rules), §9 (copy source is not a partner).

**Inputs / Outputs:** Input: commits and changes. Output: the `co_changed_paths` index, the `co_change_incomplete_paths` tuple, and whether the budget was exceeded.

**Interfaces:**
- Consumes: `touched_paths` (Task 17); `GitCommit`, `GitFileChange` (Tasks 3, 4).
- Produces: `CoChangeResult` — a frozen, slotted dataclass with fields `co_changed_paths: dict[str, tuple[str, ...]]`, `incomplete_paths: tuple[str, ...]`, `limit_exceeded: bool`; plus module constants `CO_CHANGE_PAIR_BUDGET = 10_000_000` and `CO_CHANGE_PER_COMMIT_PATH_CAP = 1000` (the PRODUCTION values — tests inject smaller ones by monkeypatching these module attributes, so the production caps are never lowered to make a test fast); plus `build_co_change_index(commits: Sequence[GitCommit], changes: Sequence[GitFileChange], *, per_commit_path_cap: int | None = None, global_pair_budget: int | None = None) -> CoChangeResult`, where `None` means "use the module constant".

- [ ] **Step 1: Scaffolding.** Add `CoChangeResult` and `build_co_change_index` returning an empty result with `limit_exceeded=False`, PLUS the two module constants `CO_CHANGE_PAIR_BUDGET = 10_000_000` and `CO_CHANGE_PER_COMMIT_PATH_CAP = 1000` — they must exist in the scaffold so the constants test's RED is an assertion failure, never an `AttributeError`.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_analyzer.py`:
  ```python
  def test_pair_is_counted_once_across_commits():
      changes = [
          _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py"),
          _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="y.py"),
          _change(commit_id="b" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py"),
          _change(commit_id="b" * 40, change_type=GitChangeType.MODIFIED, new_path="y.py"),
      ]
      result = build_co_change_index(_commits(["a" * 40, "b" * 40]), changes)
      assert result.co_changed_paths["x.py"] == ("y.py",)
      assert result.co_changed_paths["y.py"] == ("x.py",)

  def test_index_is_symmetric():
      changes = [_change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py"),
                 _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="y.py")]
      result = build_co_change_index(_commits(["a" * 40]), changes)
      assert result.co_changed_paths, "the index is empty; the symmetry loop would be vacuous"
      for left, partners in result.co_changed_paths.items():
          for right in partners:
              assert left in result.co_changed_paths[right]

  def test_budget_overflow_omits_the_commit_atomically():
      changes = [_change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py"),
                 _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="y.py")]
      result = build_co_change_index(_commits(["a" * 40]), changes, global_pair_budget=0)
      assert result.limit_exceeded is True
      assert result.co_changed_paths == {}
      assert set(result.incomplete_paths) == {"x.py", "y.py"}

  def test_budget_counts_new_pairs_only():
      changes = [_change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py"),
                 _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="y.py"),
                 _change(commit_id="b" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py"),
                 _change(commit_id="b" * 40, change_type=GitChangeType.MODIFIED, new_path="y.py")]
      # the second commit introduces ZERO new pairs, so a budget of 1 still fits
      result = build_co_change_index(_commits(["a" * 40, "b" * 40]), changes, global_pair_budget=1)
      assert result.limit_exceeded is False
      assert result.co_changed_paths["x.py"] == ("y.py",)

  def test_per_commit_path_cap_omits_the_commit():
      paths = [f"f{i}.py" for i in range(5)]
      changes = [_change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path=p) for p in paths]
      result = build_co_change_index(_commits(["a" * 40]), changes, per_commit_path_cap=3)
      assert result.limit_exceeded is True
      assert result.co_changed_paths == {}
      assert set(result.incomplete_paths) == set(paths)

  def test_budget_exhaustion_across_many_commits_keeps_earlier_pairs():
      # three commits, each introducing THREE new pairs; a budget of 5 fits
      # the first commit and omits the second ATOMICALLY
      changes = []
      for i in range(3):
          for j in range(3):
              changes.append(_change(commit_id=f"{i}" * 40,
                                     change_type=GitChangeType.MODIFIED,
                                     new_path=f"f{i}_{j}.py"))
      result = build_co_change_index(_commits([f"{i}" * 40 for i in range(3)]),
                                     changes, global_pair_budget=5)
      assert result.limit_exceeded is True
      # the FIRST commit's pairs were materialized and remain published
      assert result.co_changed_paths["f0_0.py"] == ("f0_1.py", "f0_2.py")
      # the SECOND commit was omitted wholesale - no partial partners
      assert "f1_0.py" not in result.co_changed_paths
      assert set(result.incomplete_paths) >= {f"f1_{j}.py" for j in range(3)}

  def test_copy_source_is_never_a_co_change_partner():
      changes = [_change(commit_id="a" * 40, change_type=GitChangeType.COPIED,
                         old_path="src.py", new_path="dst.py"),
                 _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="other.py")]
      result = build_co_change_index(_commits(["a" * 40]), changes)
      assert "src.py" not in result.co_changed_paths
      assert result.co_changed_paths["dst.py"] == ("other.py",)

  def test_module_constants_hold_the_production_values():
      assert analyzer_module.CO_CHANGE_PAIR_BUDGET == 10_000_000
      assert analyzer_module.CO_CHANGE_PER_COMMIT_PATH_CAP == 1000
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_analyzer.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`Failed` (the stub returns an empty result).
- [ ] **Step 4: Minimal GREEN.** Implement `build_co_change_index` to process commits in inventory order (newest-first: `(committer_timestamp desc, commit_id asc)`). Resolve the effective caps as `per_commit_path_cap` if not `None` else `CO_CHANGE_PER_COMMIT_PATH_CAP`, and `global_pair_budget` if not `None` else `CO_CHANGE_PAIR_BUDGET`. Both constants are read lazily from the module at call time (never captured as default arguments), so a test that monkeypatches `analyzer_module.CO_CHANGE_PAIR_BUDGET` or `analyzer_module.CO_CHANGE_PER_COMMIT_PATH_CAP` is honored. For each commit: compute its genuinely touched paths via `touched_paths`; if the count exceeds the per-commit cap, omit the commit entirely, add all its touched paths to `incomplete_paths`, and set `limit_exceeded=True`; otherwise compute the candidate unordered pairs normalized as `(min, max)`, subtract the already-materialized pair set to obtain the NEW pairs, and test `len(new_pairs) <= remaining_budget` BEFORE mutating anything. If the test fails, omit the commit atomically, add all its touched paths to `incomplete_paths`, and set `limit_exceeded=True`. If it passes, add the new pairs to the materialized set and add the commit's partners to the symmetric index (each path maps to the sorted tuple of its co-touched paths in that commit, excluding itself). The two symmetric index entries produced by one pair are ONE budget unit. Return the sorted `incomplete_paths` tuple.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_analyzer.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm the budget is never raised above `10_000_000` by a caller of the production path (the parameter exists only for test injection; the analyzer always passes the default). Confirm a skipped commit leaves NO partial partners in the index.
- [ ] **Step 8: Commit.** `git add backend/git_history/analyzer.py tests/test_git_history_analyzer.py` then `git commit -m "feat: account co-change pairs with atomic budget omission"`.
- [ ] **Step 9: Independent review checklist.** [ ] Pairs normalized and deduplicated across commits. [ ] Contribution counts NEW pairs only. [ ] Budget tested before mutation. [ ] Overflow omission atomic. [ ] Per-commit cap enforced. [ ] Index symmetric. [ ] Copy source never a partner. [ ] Production budget constant unchanged.
- [ ] **Step 10: Acceptance evidence.** GREEN output plus the recorded pair counts for the new-pairs-only case.

---

### Task 19: Full Analyzer Orchestration and Completeness Reasons

**Prerequisites:** Tasks 12, 13, 14, 15, 16, 17, 18.

**Files:**
- Modify: `backend/git_history/analyzer.py`
- Test: `tests/test_git_history_analyzer.py`

**Spec references:** §3 (public `analyze` behavior), §4.5 (inventory), §11 (incomplete-reason aggregation and `history_is_complete`), §6.2 (truncation, merge exclusion, path filter), §6.4 (unavailable baseline), §5 (missing metadata), §12 (Module 1/2 integration).

**Inputs / Outputs:** Input: the public `analyze` arguments. Output: a complete `GitHistoryInventory`.

**Interfaces:**
- Consumes: `GitAdapter` (Tasks 8-16), the index builders (Tasks 17-18), the models (Tasks 3-5), `FileInventory` (Module 2, OPTIONAL). The optional Module 2 type is imported under `typing.TYPE_CHECKING` and only used for ANNOTATION, so Module 7 has no RUNTIME dependency on Module 2 (and none at all on Module 1).
- Produces: `GitHistoryAnalyzer` with `__init__(self, *, git_executable: str = "git", command_timeout_seconds: float = 30.0, max_output_bytes: int = 50 * 1024 * 1024, max_commits: int | None = None) -> None` and `analyze(self, repository_path: str | Path, *, repository_namespace: str | None = None, file_inventory: FileInventory | None = None, revision: str = "HEAD", max_commits: int | None = None, include_merges: bool = True, detect_renames: bool = True, detect_copies: bool = False, path_filter: Sequence[str] = (), include_commit_messages: bool = True) -> GitHistoryInventory`; plus the module constant `MAX_CHANGES = 1_000_000` (the evidence cap — exceeding it raises `GitOutputTooLargeError` and returns no partial inventory, per spec §14/§15).

- [ ] **Step 1: Scaffolding.** Add `GitHistoryAnalyzer` with the exact constructor and `analyze` signatures above; `analyze` returns an empty inventory placeholder. The module constant `MAX_CHANGES = 1_000_000` MUST also exist in the scaffold, so the change-cap test's RED is an assertion failure (the cap raising) and never an `AttributeError` from `monkeypatch.setattr`.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_analyzer.py`:
  ```python
  def test_analyze_returns_commits_and_changes(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      repo.commit({"a.txt": b"2\n", "b.txt": b"2\n"})
      inv = GitHistoryAnalyzer().analyze(repo.path)
      assert inv.commit_count == 2 and inv.change_count == 3
      assert inv.history_is_complete is True
      assert inv.incomplete_reasons == ()

  def test_truncation_sets_max_commits_reached(git_repo):
      repo = git_repo()
      for i in range(3):
          repo.commit({"a.txt": f"{i}\n".encode()})
      inv = GitHistoryAnalyzer().analyze(repo.path, max_commits=2)
      assert GitIncompleteReason.MAX_COMMITS_REACHED in inv.incomplete_reasons
      assert inv.truncated_by_limit is True
      assert inv.history_is_complete is False

  def test_path_filter_records_reason_and_keeps_full_changes(git_repo):
      repo = git_repo()
      repo.commit({"keep.txt": b"1\n", "other.txt": b"1\n"})
      inv = GitHistoryAnalyzer().analyze(repo.path, path_filter=("keep.txt",))
      assert GitIncompleteReason.PATH_FILTER_APPLIED in inv.incomplete_reasons
      selected = inv.commits[0]
      paths = {c.new_path for c in inv.changes_by_commit[selected.commit_id]}
      assert paths == {"keep.txt", "other.txt"}

  def test_shallow_history_reports_reasons_and_no_fabricated_changes(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      repo.commit({"a.txt": b"2\n"})
      shallow = repo.shallow_clone(depth=1)
      inv = GitHistoryAnalyzer().analyze(shallow.path)
      assert GitIncompleteReason.SHALLOW_HISTORY in inv.incomplete_reasons
      assert GitIncompleteReason.PARENT_UNAVAILABLE in inv.incomplete_reasons
      assert inv.commits_with_unavailable_baseline == (shallow.head,)
      assert inv.shallow_boundary_commit_ids == (shallow.head,)
      assert inv.changes_by_commit[shallow.head] == ()

  def test_linked_worktree_of_a_shallow_clone_shares_the_shallow_boundary(git_repo):
      # the linked worktree of a SHALLOW clone must still expose the shallow
      # boundary through --git-common-dir
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      repo.commit({"a.txt": b"2\n"})
      shallow = repo.shallow_clone(depth=1)
      worktree = shallow.linked_worktree("wt")
      inv = GitHistoryAnalyzer().analyze(worktree)
      assert GitIncompleteReason.SHALLOW_HISTORY in inv.incomplete_reasons
      assert inv.shallow_boundary_commit_ids == (shallow.head,)
      assert inv.commits_with_unavailable_baseline == (shallow.head,)

  def test_merge_sets_merge_semantics_partial(git_repo):
      repo = git_repo()
      base = repo.commit({"a.txt": b"1\n"})
      repo.branch("side", base)
      repo.commit({"b.txt": b"2\n"})
      repo.checkout("side")
      repo.commit({"c.txt": b"3\n"})
      repo.checkout("main")
      repo.merge("side")
      inv = GitHistoryAnalyzer().analyze(repo.path)
      assert GitIncompleteReason.MERGE_SEMANTICS_PARTIAL in inv.incomplete_reasons

  def test_merges_excluded_sets_reason(git_repo):
      repo = git_repo()
      base = repo.commit({"a.txt": b"1\n"})
      repo.branch("side", base)
      repo.commit({"b.txt": b"2\n"})
      repo.checkout("side")
      repo.commit({"c.txt": b"3\n"})
      repo.checkout("main")
      repo.merge("side")
      inv = GitHistoryAnalyzer().analyze(repo.path, include_merges=False)
      assert GitIncompleteReason.MERGES_EXCLUDED in inv.incomplete_reasons

  def test_empty_repository_returns_empty_inventory(git_repo):
      repo = git_repo()
      inv = GitHistoryAnalyzer().analyze(repo.path)
      assert inv.commits == () and inv.changes == ()
      assert inv.history_is_complete is True

  def test_detached_head_is_recorded_but_not_incomplete(git_repo):
      repo = git_repo()
      head = repo.commit({"a.txt": b"1\n"})
      repo.detach(head)
      inv = GitHistoryAnalyzer().analyze(repo.path)
      assert inv.detached_head is True
      assert GitIncompleteReason.SHALLOW_HISTORY not in inv.incomplete_reasons

  def test_scope_records_the_analysis_parameters(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      inv = GitHistoryAnalyzer().analyze(repo.path, revision="HEAD",
                                         path_filter=("b.txt", "a.txt", "b.txt"),
                                         include_merges=False, detect_copies=True)
      assert inv.scope.revision == "HEAD"
      assert inv.scope.path_filter == ("a.txt", "b.txt")   # deduplicated AND sorted
      assert inv.scope.include_merges is False
      assert inv.scope.detect_copies is True
      assert inv.scope.include_commit_messages is True
      assert inv.git_version is not None and inv.git_version.startswith("git version")

  def test_messages_can_be_suppressed(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"}, message="secret message")
      inv = GitHistoryAnalyzer().analyze(repo.path, include_commit_messages=False)
      assert inv.commits[0].message is None
      assert inv.commit_count == 1

  def test_a_commit_with_metadata_does_not_record_missing_metadata(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      inv = GitHistoryAnalyzer().analyze(repo.path)
      # a normal commit has metadata, so the reason must NOT be present
      assert GitIncompleteReason.MISSING_METADATA not in inv.incomplete_reasons

  def test_an_absent_header_records_missing_metadata(git_repo, monkeypatch):
      # Git always writes author/committer headers, so force the fetched raw
      # record to report an actually absent header and prove the reason is
      # driven by the presence flag, not by empty/zero sentinels.
      from backend.git_history import adapter as adapter_module
      from backend.git_history.parser import RawCommitObject

      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})

      original = adapter_module.GitAdapter.fetch_commit_objects

      def fake(self, repository_path, commit_ids):
          raw = original(self, repository_path, commit_ids)
          for cid in list(raw):
              r = raw[cid]
              raw[cid] = RawCommitObject(
                  commit_id=r.commit_id,
                  tree_id=r.tree_id,
                  parent_ids=r.parent_ids,
                  author_name="",
                  author_email="",
                  author_timestamp=0,
                  author_timezone="+00:00",
                  author_header_present=False,
                  committer_name=r.committer_name,
                  committer_email=r.committer_email,
                  committer_timestamp=r.committer_timestamp,
                  committer_timezone=r.committer_timezone,
                  committer_header_present=r.committer_header_present,
                  message_bytes=r.message_bytes,
                  encoding=r.encoding,
              )
          return raw

      monkeypatch.setattr(adapter_module.GitAdapter, "fetch_commit_objects", fake)
      inv = GitHistoryAnalyzer().analyze(repo.path)
      assert GitIncompleteReason.MISSING_METADATA in inv.incomplete_reasons
      assert inv.commits[0].author_name == ""
      assert inv.commits[0].author_timestamp == 0
      assert inv.commits[0].author_timezone == "+00:00"

  def test_empty_namespace_is_a_configuration_error(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      with pytest.raises(GitConfigurationError):
          GitHistoryAnalyzer().analyze(repo.path, repository_namespace="")

  def test_invalid_max_commits_is_a_configuration_error(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      with pytest.raises(GitConfigurationError):
          GitHistoryAnalyzer().analyze(repo.path, max_commits=0)

  def test_change_evidence_cap_is_enforced(monkeypatch, git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n", "b.txt": b"1\n"})   # TWO changes
      monkeypatch.setattr(analyzer_module, "MAX_CHANGES", 1)
      with pytest.raises(GitOutputTooLargeError):
          GitHistoryAnalyzer().analyze(repo.path)

  def test_genuinely_unchanged_commit_is_distinguishable_from_a_missing_baseline(git_repo):
      # an EMPTY commit (no file changes) is NOT an unavailable baseline
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      repo.commit_empty()
      inv = GitHistoryAnalyzer().analyze(repo.path)
      empty_commit = [c for c in inv.commits if c.commit_id == repo.head][0]
      assert inv.changes_by_commit[empty_commit.commit_id] == ()
      assert empty_commit.commit_id not in inv.commits_with_unavailable_baseline

  def test_commits_with_identical_timestamps_are_ordered_deterministically(git_repo):
      repo = git_repo()
      first = repo.commit({"a.txt": b"1\n"})
      stamp = repo.head_timestamp()
      second = repo.commit_with_timestamp({"a.txt": b"2\n"}, stamp)
      inv = GitHistoryAnalyzer().analyze(repo.path)
      # equal committer timestamps are broken by commit_id ASCENDING
      assert inv.commits[0].committer_timestamp == inv.commits[1].committer_timestamp
      assert [c.commit_id for c in inv.commits] == sorted([first, second])
  ```
  The cap semantics are "raise when the accumulated count EXCEEDS the cap" (2 > 1 raises; exactly 1 would NOT raise), matching spec §14 and the analogous "exactly N is never falsely truncated" rule for the 100,000-commit cap.
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_analyzer.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError`/`Failed` (the placeholder returns an empty inventory, so indexing it raises `IndexError` — a legitimate behavioural RED). No `ImportError`.
- [ ] **Step 4: Minimal GREEN.** Implement `analyze` to: validate the constructor arguments (`command_timeout_seconds > 0`, `max_output_bytes > 0`, nonempty `git_executable`, positive `max_commits` when not `None`, else `GitConfigurationError`); validate `repository_path` resolves to an existing directory, else `GitRepositoryError`; validate `repository_namespace` is a nonempty string when not `None` (an empty string raises `GitConfigurationError`); when `file_inventory` is supplied, take the namespace from it when none was given and check that its `repository_path` resolves to the same root, else `GitInventoryMismatchError`; build the adapter; inspect the repository; handle the unborn-HEAD case by returning an empty inventory with `history_is_complete=True`; resolve the revision; enumerate commits; fetch the raw commit objects; build `GitCommit` records (setting `message=None` when `include_commit_messages=False`, and recording `MISSING_METADATA` when `RawCommitObject.author_header_present` or `RawCommitObject.committer_header_present` is `False` - i.e. when the header was actually absent, not when its parsed name/email is empty or its timestamp is 0; a present but unusual valid header is accepted and does NOT record `MISSING_METADATA`; a present malformed header never reaches this point because `parse_batch_stream` raises `GitParseError`); extract changes per commit via the adapter, recording `PARENT_UNAVAILABLE` (and `SHALLOW_HISTORY` when the repository is shallow) and appending the commit ID to `commits_with_unavailable_baseline` when the baseline is unavailable, and raising `GitOutputTooLargeError` when the accumulated change count EXCEEDS `MAX_CHANGES` (a count exactly equal to the cap is allowed); `MAX_CHANGES` is read from the MODULE at call time, never captured as a default argument, so a test that patches `analyzer_module.MAX_CHANGES` is honored; the analyzer MUST reuse the raw records already fetched by `fetch_commit_objects` and call `resolve_baseline` and `diff_trees` directly — it must NOT call the per-commit convenience helper `changes_for_commit` (which re-fetches the commit object and would turn the single `cat-file --batch` into O(commits) batch calls, violating spec §14's "one cat-file batch" bound); sort commits by `(committer_timestamp desc, commit_id asc)`; build the four indexes and the co-change index; aggregate `incomplete_reasons` as a sorted tuple of the reasons that actually applied; compute `shallow_boundary_commit_ids` from the `shallow` file intersected with the reachable commit set — the file is read through `GitAdapter.read_shallow_boundary_ids(git_common_dir)` (Task 8, which owns `adapter.py`), using the `git_common_dir` from `GitRepositoryContext` (resolved against the validated repository path, because it may be relative); construct the `GitHistoryScope` with `revision`, the DEDUPLICATED AND SORTED `path_filter` tuple, the effective `max_commits`, `include_merges`, `detect_renames`, `detect_copies`, and `include_commit_messages`; and assemble the final `GitHistoryInventory` with `scope`, `git_version` (from `GitAdapter.git_version()`), and `history_is_complete = not incomplete_reasons`.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_analyzer.py tests/test_git_history_adapter.py tests/test_git_history_parser.py tests/test_git_history_models.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm the analyzer never mutates the analyzed repository: record the repository's `.git` directory state (file list plus a hash of `HEAD`) before and after `analyze` and assert they are unchanged. Confirm `analyze` raises rather than returning a partial inventory when a command fails (§8).
- [ ] **Step 8: Commit.** `git add backend/git_history/analyzer.py tests/test_git_history_analyzer.py` then `git commit -m "feat: orchestrate full git history analysis"`.
- [ ] **Step 9: Independent review checklist.** [ ] Every incomplete reason is produced by the right condition. [ ] `history_is_complete` derived from the reasons. [ ] Detached HEAD recorded but not incomplete. [ ] Unborn HEAD returns an empty, complete inventory. [ ] Shallow boundary yields zero fabricated changes. [ ] Path filter keeps the full change set of a selected commit. [ ] No repository mutation. [ ] No partial inventory on failure.
- [ ] **Step 10: Acceptance evidence.** GREEN output plus the recorded reason set for the shallow, truncated, path-filtered, and merge scenarios.

---

### Task 20: Convenience API, Exports, and Model Integration

**Prerequisites:** Task 19.

**Files:**
- Modify: `backend/git_history/analyzer.py` (add the module-level convenience function)
- Modify: `backend/git_history/__init__.py` (full public exports)
- Test: `tests/test_git_history_api.py`

**Spec references:** §3 (public API and `__all__` surface), §2 (upstream contract compatibility), §4 (public exports).

**Inputs / Outputs:** Input: the public call forms. Output: a stable public surface.

**Interfaces:**
- Consumes: `GitHistoryAnalyzer` (Task 19); `FileInventory` (Module 2, optional import).
- Produces: `analyze_git_history(repository_path: str | Path, *, repository_namespace: str | None = None, file_inventory: FileInventory | None = None, revision: str = "HEAD", max_commits: int | None = None, include_merges: bool = True, detect_renames: bool = True, detect_copies: bool = False, path_filter: Sequence[str] = (), include_commit_messages: bool = True, git_executable: str = "git", command_timeout_seconds: float = 30.0, max_output_bytes: int = 50 * 1024 * 1024) -> GitHistoryInventory`; and `backend.git_history.__all__` containing exactly NINETEEN names: `GitHistoryAnalyzer`, `analyze_git_history`, `GitChangeType`, `GitIncompleteReason`, `GitErrorCode`, `GitCommit`, `GitFileChange`, `GitHistoryScope`, `GitHistoryInventory` (9 public names) plus the ten exception classes `GitHistoryError`, `GitUnavailableError`, `GitRepositoryError`, `GitRevisionError`, `GitCommandError`, `GitCommandTimeoutError`, `GitOutputTooLargeError`, `GitParseError`, `GitInventoryMismatchError`, `GitConfigurationError`.

- [ ] **Step 1: Scaffolding.** Add `analyze_git_history` to `analyzer.py` returning `None`. In `backend/git_history/__init__.py`, import the nineteen public names (so `from backend.git_history import GitHistoryAnalyzer, analyze_git_history` resolves) but leave `__all__ = []` — the exports are the behavior this task pins, so the RED must be the missing `__all__` entries, not an ImportError at collection time.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_api.py`:
  ```python
  def test_public_exports_are_importable():
      import backend.git_history as public
      for name in ("GitHistoryAnalyzer", "analyze_git_history", "GitChangeType",
                   "GitIncompleteReason", "GitErrorCode", "GitCommit", "GitFileChange",
                   "GitHistoryScope", "GitHistoryInventory", "GitHistoryError",
                   "GitUnavailableError", "GitRepositoryError", "GitRevisionError",
                   "GitCommandError", "GitCommandTimeoutError", "GitOutputTooLargeError",
                   "GitParseError", "GitInventoryMismatchError", "GitConfigurationError"):
          assert name in public.__all__, name
          assert hasattr(public, name)

  def test_convenience_function_matches_the_class(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      direct = GitHistoryAnalyzer().analyze(repo.path)
      convenience = analyze_git_history(repo.path)
      assert direct.commit_ids == convenience.commit_ids

  def test_convenience_function_rejects_invalid_configuration(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      with pytest.raises(GitConfigurationError):
          analyze_git_history(repo.path, command_timeout_seconds=0)
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_api.py -q -m "not integration and not gemini_live" -rs`
  Expected: FAIL with `AssertionError` (the name is not in `__all__`) and `AttributeError`/`Failed` (the scaffolded convenience function returns `None`, so the call has no `commit_ids`).
- [ ] **Step 4: Minimal GREEN.** Implement `analyze_git_history` as a thin wrapper that constructs `GitHistoryAnalyzer(git_executable=..., command_timeout_seconds=..., max_output_bytes=...)` and delegates to `analyze(...)`, forwarding every remaining keyword. Fill `backend/git_history/__init__.py` with the imports and an explicit `__all__` list containing exactly the nineteen names above. Do NOT export `GitAdapter`, `GitHistoryParser`, or any internal helper.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_api.py tests/test_git_history_analyzer.py -q -m "not integration and not gemini_live" -rs` — expected: PASS.
- [ ] **Step 7: Security/contract checks.** Confirm no upstream module was modified: `git diff --name-only` must not list `backend/repository_loader/`, `backend/file_scanner/`, `backend/code_parser/`, `backend/code_chunker/`, `backend/embedding_vector_store/`, or `backend/code_graph/`. Confirm `requirements.txt` is unchanged. Confirm no Module 6 identity namespace is reused.
- [ ] **Step 8: Commit.** `git add backend/git_history/analyzer.py backend/git_history/__init__.py tests/test_git_history_api.py` then `git commit -m "feat: expose git history analyzer public api"`.
- [ ] **Step 9: Independent review checklist.** [ ] `__all__` contains exactly the §3 public surface. [ ] The convenience function forwards every argument. [ ] Internal modules are not exported. [ ] No upstream module touched. [ ] `requirements.txt` unchanged.
- [ ] **Step 10: Acceptance evidence.** GREEN output and the recorded `__all__` list.


---

### Task 21: Module 1/2/6 Integration and README Usage

**Prerequisites:** Task 20.

**Files:**
- Modify: `README.md` (add a Module 7 usage section)
- Modify: `backend/git_history/analyzer.py` (only if a test in this task exposes a gap; ADDITIVE per the preflight ownership rules)
- Test: `tests/test_git_history_integration.py`

**Spec references:** §12 (Module 1, Module 2, Module 6 integration; file-level association only), §2 (upstream contracts), §3 (`file_inventory` handling).

**Inputs / Outputs:** Input: a real `RepositoryInfo` (Module 1) and an optional `FileInventory` (Module 2). Output: a verified file-level association with Module 6 graph nodes and documented usage.

**Interfaces:**
- Consumes: `GitHistoryAnalyzer` (Task 20); `RepositoryInfo` (Module 1, `backend.repository_loader.models`); `FileInventory` (Module 2, `backend.file_scanner.models`); `CodeGraphInventory` (Module 6, `backend.code_graph.models`).
- Produces: the README section and the integration test module. Module 7 gains NO production dependency on Module 6 — the association is performed in the TEST by mapping `GitFileChange.new_path` to `CodeGraphInventory.nodes_by_file` keys.

**TDD note for this task:** the `file_inventory` handling is implemented in Task 19, so the behavioral RED for the file-inventory assertions may already be GREEN here. This task's RED evidence is therefore the SENSITIVITY PROBE in Step 4 (a throwaway removal of the handling must make the test fail), NOT a deliberately broken committed test. A test that passes on the first run is a legitimate outcome here; the probe is what proves the assertion bites.

- [ ] **Step 1: Scaffolding.** Create `tests/test_git_history_integration.py` containing the REAL assertions below (no `pytest.fail` placeholders — a placeholder is not a behavioral RED and would violate the TDD convention). No README change yet.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_integration.py`:
  ```python
  def test_analyze_accepts_a_real_repository_info_path(git_repo):
      from backend.repository_loader.models import RepositoryInfo
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      info = RepositoryInfo(
          success=True, owner="acme", repository_name="demo",
          repo_url="https://github.com/acme/demo.git", local_path=str(repo.path),
          branch="main", current_commit=repo.head, total_files=1,
          repository_size_bytes=1, reused_existing_clone=True)
      inv = GitHistoryAnalyzer().analyze(info.local_path, revision=info.current_commit)
      assert inv.commit_count == 1

  def test_file_inventory_supplies_the_namespace(git_repo):
      from backend.file_scanner.models import FileInventory
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      inventory = FileInventory(str(repo.path), 1, 1, 0, (), (), (), "acme/demo")
      inv = GitHistoryAnalyzer().analyze(repo.path, file_inventory=inventory)
      assert inv.repository_namespace == "acme/demo"

  def test_mismatched_inventory_raises(git_repo, tmp_path):
      from backend.file_scanner.models import FileInventory
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      inventory = FileInventory(str(tmp_path), 0, 0, 0, (), (), (), "acme/demo")
      with pytest.raises(GitInventoryMismatchError):
          GitHistoryAnalyzer().analyze(repo.path, file_inventory=inventory)

  def test_file_level_association_with_module_6_nodes(git_repo):
      from backend.code_graph.models import CodeGraphInventory, GraphNode, GraphNodeKind
      from backend.code_parser.models import ParsedLanguage, ParseStatus
      repo = git_repo()
      repo.commit({"src/app.py": b"x = 1\n"})
      inv = GitHistoryAnalyzer().analyze(repo.path)
      # build a REAL (minimal) Module 6 graph inventory and associate the
      # history with it by FILE PATH ONLY
      node = GraphNode(
          node_id="code-graph:file:repo:src/app.py",
          node_kind=GraphNodeKind.FILE,
          repository_namespace="repo",
          relative_path="src/app.py",
          language=ParsedLanguage.PYTHON,
          parse_status=ParseStatus.SUCCESS,
          symbol_kind=None, name=None, qualified_name=None, location=None,
      )
      graph = CodeGraphInventory(
          repository_namespace="repo",
          nodes=(node,), edges=(), unresolved_references=(),
          outgoing_edges={}, incoming_edges={},
          nodes_by_file={"src/app.py": (node,)},
          nodes_by_qualified_name={},
      )
      changed_paths = {c.new_path for c in inv.changes if c.new_path}
      associated = sorted(changed_paths & set(graph.nodes_by_file))
      assert associated == ["src/app.py"]
      # file-level only: no symbol-level attribution is claimed
      assert all(n.node_kind is GraphNodeKind.FILE
                 for n in graph.nodes_by_file["src/app.py"])

  def test_path_filter_selects_commits_and_keeps_the_full_change_set(git_repo):
      repo = git_repo()
      repo.commit({"keep.txt": b"1\n", "other.txt": b"1\n"})
      repo.commit({"other.txt": b"2\n"})
      inv = GitHistoryAnalyzer().analyze(repo.path, path_filter=("keep.txt",))
      assert inv.commit_count == 1
      selected = inv.commits[0]
      paths = {c.new_path for c in inv.changes_by_commit[selected.commit_id]}
      assert paths == {"keep.txt", "other.txt"}
      assert GitIncompleteReason.PATH_FILTER_APPLIED in inv.incomplete_reasons
  ```
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_integration.py -q -m "not integration and not gemini_live" -rs`
  Expected: every test PASSES on the first run, because Task 19 already implemented the `file_inventory` handling. Record this outcome. If any test FAILS, that failure is the genuine RED: fix it in the OWNING task (Task 19 for the analyzer behavior) and re-run that task's focused suite.
- [ ] **Step 4: Minimal GREEN.** Add the README section titled "Module 7 — Git History Analyzer" with a short, runnable example that uses `analyze_git_history(...)` and prints the commit count and the co-change partners of one path. Then perform the SENSITIVITY PROBE: in a THROWAWAY copy of `backend/git_history/analyzer.py` (outside the repository, never committed), remove the `file_inventory` namespace/mismatch handling and confirm `test_file_inventory_supplies_the_namespace` and `test_mismatched_inventory_raises` FAIL; restore the file and confirm they pass again. Record the probe output as the RED evidence for this task. If a probe shows a test passing with the behavior removed, that test is vacuous and must be strengthened here.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_integration.py tests/test_git_history_api.py tests/test_git_history_analyzer.py -q -m "not integration and not gemini_live" -rs` — expected: PASS. Also run the Module 1 and Module 2 suites: `... -m pytest tests/test_repository_loader.py tests/test_file_scanner.py -q -m "not integration and not gemini_live" -rs` — expected: PASS with no new failures.
- [ ] **Step 7: Security/contract checks.** Confirm Module 7's production code imports NOTHING from `backend.code_graph`, `backend.code_parser`, `backend.code_chunker`, or `backend.embedding_vector_store`: `Select-String -Path backend/git_history/*.py -Pattern "code_graph|code_parser|code_chunker|embedding_vector_store"` returns no hits. Only `backend.file_scanner.models` (the optional `FileInventory` type) and `backend.repository_loader.models` (not required at all) may be referenced. Confirm the README example contains no network call and no credential.
- [ ] **Step 8: Commit.** `git add README.md tests/test_git_history_integration.py backend/git_history/analyzer.py` then `git commit -m "feat: integrate git history analyzer with module 1 and 2 context"`.
- [ ] **Step 9: Independent review checklist.** [ ] `file_inventory` supplies the namespace when none is given. [ ] Mismatched namespace or path raises `GitInventoryMismatchError`. [ ] Changed paths are not required to be in the inventory. [ ] No production coupling to Module 6. [ ] README example is accurate and offline. [ ] Module 1 and Module 2 suites still pass.
- [ ] **Step 10: Acceptance evidence.** GREEN output, the Module 1/2 regression output, and the quoted README section.

---

### Task 22: Adversarial Git Security Tests

**Prerequisites:** Tasks 8-20.

**Files:**
- Create: `tests/test_git_history_security.py`
- Modify: none (the `install_hook`, `add_replacement_for_head`, and `head_message` helpers are already implemented by Task 8, which owns the whole `git_repo` fixture contract)
- Test: `tests/test_git_history_security.py`

**Spec references:** §13 (security and privacy), §6.1 (adapter safety), §11 (operational failures raise), §15 (error mapping).

**Inputs / Outputs:** Input: hostile repositories and hostile environments. Output: proof that the analyzer refuses to escape its boundary.

**Interfaces:**
- Consumes: the public `GitHistoryAnalyzer` and `GitAdapter` (Tasks 8-20); the `git_repo` fixture (Task 8).
- Produces: `tests/test_git_history_security.py` only — NO production change is expected in this task. If a test fails, the fix belongs to the task that owns the production behavior, and this task records the finding and re-runs that task's suite.

**TDD note for this task:** the security guards are implemented by Tasks 8-20, so these tests may pass on their first run. The RED evidence for this task is the SENSITIVITY PROBE in Step 4 (a throwaway removal of each guard must make its test fail). A `pytest.fail("guard not implemented")` placeholder is NOT used: a guaranteed-failing placeholder is not a behavioral RED, and no committed test may be deliberately broken to manufacture one.

- [ ] **Step 1: Scaffolding.** Create `tests/test_git_history_security.py` containing the REAL assertions below — no placeholders. Define the shared detector ONCE at module level, so the real test and the negative control cannot drift apart:
  ```python
  FORBIDDEN_SUBCOMMANDS = frozenset({
      "clone", "fetch", "pull", "push", "checkout", "reset", "rebase",
      "merge", "commit", "tag", "gc", "prune", "filter-branch", "replace",
  })

  def _forbidden_subcommand(args: list[str]) -> str | None:
      """Return the forbidden sub-command in args, or None."""
      return args[0] if args and args[0] in FORBIDDEN_SUBCOMMANDS else None
  ```
  No production change.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_security.py`:
  ```python
  def test_no_forbidden_git_subcommand_is_ever_invoked(git_repo, monkeypatch):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      seen = []
      original = adapter_module.GitAdapter.run_raw
      def spy(self, args, repository_path, **kw):
          seen.append(list(args))
          return original(self, args, repository_path, **kw)
      monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
      GitHistoryAnalyzer().analyze(repo.path)
      assert seen, "no Git command was issued; the assertion below would be vacuous"
      for args in seen:
          assert _forbidden_subcommand(args) is None, args

  def test_forbidden_subcommand_detector_actually_detects():
      # NEGATIVE CONTROL: the SAME helper used above must flag a forbidden call
      assert _forbidden_subcommand(["clone", "https://example.invalid/x.git"]) == "clone"
      assert _forbidden_subcommand(["push", "origin", "main"]) == "push"
      assert _forbidden_subcommand(["cat-file", "--batch"]) is None

  def test_analyzed_repository_is_not_mutated(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      before = _repo_state(repo.path)
      GitHistoryAnalyzer().analyze(repo.path)
      assert _repo_state(repo.path) == before

  def test_hooks_path_is_overridden_to_an_empty_directory(git_repo, monkeypatch):
      # The guard is asserted DIRECTLY: every spawned command must carry
      # `-c core.hooksPath=<empty dir>`. A behavioural hook test cannot work
      # here: no read-only Git command ever triggers post-commit, so the hook
      # would never fire and the assertion would be vacuous.
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      repo.install_hook("post-commit", 'echo ran')
      spawned = []
      original_popen = adapter_module.subprocess.Popen
      def spy(*args, **kwargs):
          argv = kwargs.get("args")
          if argv is None:
              argv = args[0] if args else []
          spawned.append(list(argv))
          return original_popen(*args, **kwargs)
      monkeypatch.setattr(adapter_module.subprocess, "Popen", spy)
      GitHistoryAnalyzer().analyze(repo.path)
      assert spawned
      for argv in spawned:
          assert any(a.startswith("core.hooksPath=") for a in argv), argv
          hooks = [a for a in argv if a.startswith("core.hooksPath=")]
          assert hooks and Path(hooks[0].split("=", 1)[1]).is_dir()

  def test_only_allowed_git_subcommands_are_invoked(git_repo, monkeypatch):
      # the §6.1 allow-list, enforced positively (not only a denylist)
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      seen = []
      original = adapter_module.GitAdapter.run_raw
      def spy(self, args, repository_path, **kw):
          seen.append(list(args))
          return original(self, args, repository_path, **kw)
      monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
      GitHistoryAnalyzer().analyze(repo.path)
      allowed = {"--version", "rev-parse", "symbolic-ref", "rev-list",
                 "cat-file", "diff-tree"}
      assert seen
      for args in seen:
          assert args[0] in allowed, args

  def test_textconv_and_external_diff_are_disabled(git_repo, monkeypatch):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      seen = []
      original = adapter_module.GitAdapter.run_raw
      def spy(self, args, repository_path, **kw):
          seen.append(list(args))
          return original(self, args, repository_path, **kw)
      monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
      GitHistoryAnalyzer().analyze(repo.path)
      diff_calls = [a for a in seen if a[0] == "diff-tree"]
      assert diff_calls, "no diff-tree command was issued; the assertion below would be vacuous"
      for args in diff_calls:
          assert "--no-ext-diff" in args and "--no-textconv" in args

  def test_replacement_objects_are_ignored(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n", "b.txt": b"2\n"})
      original_message = repo.head_message()
      repo.add_replacement_for_head("REPLACEMENT message")
      inv = GitHistoryAnalyzer().analyze(repo.path)
      assert inv.commits[0].message == original_message
      assert "REPLACEMENT message" not in (inv.commits[0].message or "")

  def test_replacement_refs_are_neither_read_nor_modified(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      repo.add_replacement_for_head("REPLACEMENT message")
      before = _repo_state(repo.path)
      GitHistoryAnalyzer().analyze(repo.path)
      assert _repo_state(repo.path) == before

  def test_error_messages_do_not_leak_environment(git_repo, monkeypatch, tmp_path):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      monkeypatch.setenv("MY_SECRET_TOKEN", "s3cr3t")
      # point at a REAL command failure (a Git process actually runs) so the
      # environment is genuinely in play when the message is produced
      with pytest.raises(GitRevisionError) as excinfo:
          GitHistoryAnalyzer().analyze(repo.path, revision="no-such-revision")
      assert "s3cr3t" not in str(excinfo.value)

  def test_revision_option_injection_is_rejected(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      with pytest.raises(GitRevisionError):
          GitHistoryAnalyzer().analyze(repo.path, revision="--upload-pack=evil")

  def test_revision_containing_nul_is_rejected(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      with pytest.raises(GitRevisionError):
          GitHistoryAnalyzer().analyze(repo.path, revision="HEAD\x00")

  def test_lazy_fetch_is_disabled_and_no_filter_is_requested(git_repo, monkeypatch, tmp_path):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      seen = []
      original = adapter_module.GitAdapter.run_raw
      def spy(self, args, repository_path, **kw):
          seen.append(list(args))
          return original(self, args, repository_path, **kw)
      monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
      GitHistoryAnalyzer().analyze(repo.path)
      for args in seen:
          assert "--filter" not in args
          assert "--depth" not in args
          assert "promisor" not in args
      # the environment that reached the child disables lazy fetching
      # (built in a SCRATCH directory: no helper file may be written inside
      # the analysed repository)
      env = adapter_module.build_git_environment(Path(tmp_path))
      assert env["GIT_NO_LAZY_FETCH"] == "1"

  def test_hostile_environment_variables_do_not_reach_the_child(git_repo, monkeypatch):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      monkeypatch.setenv("GIT_DIR", "C:/attacker")
      monkeypatch.setenv("GIT_WORK_TREE", "C:/attacker")
      monkeypatch.setenv("GIT_OBJECT_DIRECTORY", "C:/attacker")
      monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", "C:/attacker")
      monkeypatch.setenv("GIT_PROXY_COMMAND", "evil")
      monkeypatch.setenv("GIT_SSH", "evil")
      monkeypatch.setenv("GIT_ASKPASS", "evil")
      monkeypatch.setenv("GIT_TRACE", "1")
      monkeypatch.setenv("HTTPS_PROXY", "http://attacker")
      seen_env = []
      original = adapter_module.GitAdapter.run_raw
      def spy(self, args, repository_path, **kw):
          seen_env.append(dict(self.environment))
          return original(self, args, repository_path, **kw)
      monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
      inv = GitHistoryAnalyzer().analyze(repo.path)
      assert inv.commit_count == 1
      for env in seen_env:
          for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY",
                       "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PROXY_COMMAND",
                       "GIT_SSH", "GIT_ASKPASS", "GIT_TRACE", "HTTPS_PROXY"):
              assert name not in env
  ```
  The security test file records the environment actually handed to the child (via the adapter's own environment attribute) rather than re-deriving it, so the assertion is about what the process really received.
  Add the module-level helper `_repo_state(path) -> tuple` (a sorted listing of the `.git` directory with each file's SIZE and a content HASH — deliberately NOT mtime, because an incidental mtime update inside `.git` would make the before/after comparison flaky) to `tests/test_git_history_security.py` itself (per the TDD Convention table). The `install_hook`, `add_replacement_for_head`, and `head_message` fixture helpers are ALREADY implemented by Task 8, which owns `tests/conftest.py`; this task must NOT modify that file.
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_security.py -q -m "not integration and not gemini_live" -rs`
  Expected: the tests PASS on the first run, because Tasks 8-20 already implement the guards. Record this outcome. If any test FAILS, that failure is the genuine RED: fix it in the OWNING task and re-run that task's focused suite. Never weaken an assertion to make a test pass.
- [ ] **Step 4: Minimal GREEN.** No production change is expected. Perform the SENSITIVITY PROBE for each guard, in a THROWAWAY copy of the production file (outside the repository, never committed): remove `GIT_NO_REPLACE_OBJECTS=1` from the environment and confirm `test_replacement_objects_are_ignored` FAILS; remove the forbidden-subcommand guard and confirm `test_no_forbidden_git_subcommand_is_ever_invoked` FAILS; remove `--no-ext-diff --no-textconv` and confirm `test_textconv_and_external_diff_are_disabled` FAILS; remove the `core.hooksPath` override and confirm `test_hooks_path_is_overridden_to_an_empty_directory` FAILS; drop `GIT_NO_LAZY_FETCH=1` and confirm `test_lazy_fetch_is_disabled_and_no_filter_is_requested` FAILS. Restore every file and confirm the suite is GREEN again. Record the probe output. If a probe shows a test still passing with its guard removed, that test is VACUOUS and must be strengthened here before the task can close.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_security.py tests -q -m "not integration and not gemini_live" -rs` — expected: PASS with no new failures anywhere.
- [ ] **Step 7: Security/contract checks.** Confirm the analysis environment cannot reach the network: assert that no invoked Git command contains `remote`, `http`, `https`, or a `://` URL, and that `GIT_TERMINAL_PROMPT=0` and `GIT_NO_LAZY_FETCH=1` are present in the environment handed to every command. Confirm no `shell=True`, no `os.system`, no `eval`/`exec` anywhere in `backend/git_history/`.
- [ ] **Step 8: Commit.** `git add tests/test_git_history_security.py` plus any production file actually changed, then `git commit -m "test: prove git history analyzer security boundaries"`.
- [ ] **Step 9: Independent review checklist.** [ ] Every guard fails before the fix and passes after. [ ] No guard is vacuous (each asserts a concrete observable). [ ] The repository state is byte-identical before and after analysis. [ ] The replacement-object test asserts the ORIGINAL message. [ ] No environment value appears in an error message. [ ] No network-capable Git command is invoked. [ ] No `pytest.fail` placeholder remains.
- [ ] **Step 10: Acceptance evidence.** GREEN output, the recorded before/after repository-state hashes, and the recorded argument list proving no forbidden subcommand was invoked.

---

### Task 23: Scaling, Determinism, and Sensitivity Probes

**Prerequisites:** Tasks 19, 20, 22.

**Files:**
- Create: `tests/test_git_history_scaling.py`
- Test: `tests/test_git_history_scaling.py`

**Spec references:** §14 (operation counts, bounded tree resolutions, co-change budgets), §7 (deterministic ordering), §4.5 (immutability), §16 (performance tests).

**Inputs / Outputs:** Input: synthetic repositories of controlled size. Output: deterministic operation-count and budget evidence, plus sensitivity probes proving the guards actually bite.

**Interfaces:**
- Consumes: `GitHistoryAnalyzer`, `GitAdapter` (Tasks 8-20); the `git_repo` fixture (Task 8); `build_co_change_index` and the module constants `CO_CHANGE_PAIR_BUDGET` / `CO_CHANGE_PER_COMMIT_PATH_CAP` (Task 18).
- Produces: `tests/test_git_history_scaling.py` only. Production limits are NEVER lowered to make a test fast; large-budget scenarios use test-only injection through the `global_pair_budget` / `per_commit_path_cap` parameters and through monkeypatching the module constants.

**TDD note for this task:** the operation counts and budgets are implemented by Tasks 14, 18, 19, so these tests may pass on their first run. The RED evidence for this task is the SENSITIVITY PROBE in Step 4 (a throwaway break of each measured property must make its test fail). A deliberately broken committed test is NOT acceptable.

- [ ] **Step 1: Scaffolding.** Create the test module containing the REAL assertions below; no production change.
- [ ] **Step 2: Write the failing tests** in `tests/test_git_history_scaling.py`:
  ```python
  def test_git_command_count_is_linear_in_commits(git_repo, monkeypatch):
      def count_for(n):
          repo = git_repo()
          for i in range(n):
              repo.commit({"a.txt": f"{i}\n".encode()})
          seen = []
          original = adapter_module.GitAdapter.run_raw
          def spy(self, args, repository_path, **kw):
              seen.append(list(args))
              return original(self, args, repository_path, **kw)
          with monkeypatch.context() as m:
              m.setattr(adapter_module.GitAdapter, "run_raw", spy)
              GitHistoryAnalyzer().analyze(repo.path)
          return seen
      seen = count_for(5)
      diff_calls = [a for a in seen if a[0] == "diff-tree"]
      rev_parse_tree_calls = [a for a in seen if a[0] == "rev-parse"
                              and any(x.endswith("^{tree}") for x in a)]
      assert len(diff_calls) == 5
      assert len(rev_parse_tree_calls) == 4      # one per NON-ROOT commit
      # the cost grows LINEARLY with the commit count: doubling the commits
      # doubles the per-commit commands and adds no fixed per-commit extras
      bigger = count_for(10)
      assert len([a for a in bigger if a[0] == "diff-tree"]) == 10
      assert len([a for a in bigger if a[0] == "rev-parse"
                  and any(x.endswith("^{tree}") for x in a)]) == 9

  def test_command_count_does_not_grow_with_symbols(git_repo, monkeypatch):
      # the command count must be INDEPENDENT of how many symbols the source
      # contains: a 1-symbol repo and a 50-symbol repo cost the same.
      # `git_repo` is a FACTORY: the two calls below create TWO DISTINCT
      # repositories in separate sub-directories of tmp_path.
      small = git_repo()
      small.commit({"a.py": b"def f():\n    pass\n"})
      large = git_repo()
      large.commit({"a.py": b"".join(f"def f{i}():\n    pass\n".encode()
                                     for i in range(50))})
      assert small.path != large.path
      def count(repo):
          seen = []
          original = adapter_module.GitAdapter.run_raw
          def spy(self, args, repository_path, **kw):
              seen.append(list(args))
              return original(self, args, repository_path, **kw)
          with monkeypatch.context() as m:
              m.setattr(adapter_module.GitAdapter, "run_raw", spy)
              GitHistoryAnalyzer().analyze(repo.path)
          return len(seen)
      assert count(small) == count(large)

  def test_repeat_analysis_is_deterministic(git_repo):
      repo = git_repo()
      for i in range(4):
          repo.commit({"a.txt": f"{i}\n".encode(), "b.txt": f"{i}\n".encode()})
      first = GitHistoryAnalyzer().analyze(repo.path)
      second = GitHistoryAnalyzer().analyze(repo.path)
      assert first.commit_ids == second.commit_ids
      assert first.co_changed_paths == second.co_changed_paths
      assert first.changes == second.changes

  def test_published_indexes_cannot_be_mutated(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n"})
      inv = GitHistoryAnalyzer().analyze(repo.path)
      with pytest.raises(TypeError):
          inv.co_changed_paths["x.py"] = ("y.py",)

  def test_duplicate_pairs_do_not_grow_the_budget(monkeypatch, git_repo):
      # 20 commits that all touch the SAME pair: ONE unique pair in total, so
      # a budget of exactly 1 pair must NOT overflow
      repo = git_repo()
      for i in range(20):
          repo.commit({"a.txt": f"{i}\n".encode(), "b.txt": f"{i}\n".encode()})
      monkeypatch.setattr(analyzer_module, "CO_CHANGE_PAIR_BUDGET", 1)
      inv = GitHistoryAnalyzer().analyze(repo.path)
      assert inv.co_changed_paths["a.txt"] == ("b.txt",)
      # the single unique pair fits the budget: no overflow, no incomplete paths
      assert GitIncompleteReason.CO_CHANGE_LIMIT_EXCEEDED not in inv.incomplete_reasons
      assert inv.co_change_incomplete_paths == ()

  def test_global_pair_budget_overflow_is_reported_atomically(monkeypatch, git_repo):
      # a 5-path commit contributes 10 pairs; a budget of 5 must omit it wholly
      repo = git_repo()
      repo.commit({f"f{i}.txt": f"{i}\n".encode() for i in range(5)})
      monkeypatch.setattr(analyzer_module, "CO_CHANGE_PAIR_BUDGET", 5)
      inv = GitHistoryAnalyzer().analyze(repo.path)
      assert GitIncompleteReason.CO_CHANGE_LIMIT_EXCEEDED in inv.incomplete_reasons
      assert inv.history_is_complete is False
      assert inv.co_changed_paths == {}                 # atomic: no partial partners
      assert set(inv.co_change_incomplete_paths) == {f"f{i}.txt" for i in range(5)}
      # the ordinary indexes are NOT affected by the omission
      assert inv.change_count == 5
      assert inv.changes_by_path
      assert inv.changes_by_commit[inv.commits[0].commit_id]

  def test_production_budget_constants_are_unchanged():
      assert analyzer_module.CO_CHANGE_PAIR_BUDGET == 10_000_000
      assert analyzer_module.CO_CHANGE_PER_COMMIT_PATH_CAP == 1000

  def test_published_index_lookup_is_a_direct_hit(git_repo):
      repo = git_repo()
      repo.commit({"a.txt": b"1\n", "b.txt": b"1\n"})
      inv = GitHistoryAnalyzer().analyze(repo.path)
      # the index is a dict-backed mapping: a lookup returns the SAME object
      # that was published, with no copy and no scan
      commit = inv.commits[0]
      assert inv.commits_by_id[commit.commit_id] is commit
      assert inv.commits_by_id[commit.commit_id] is inv.commits_by_id[commit.commit_id]
      assert type(inv.commits_by_id).__name__ == "mappingproxy"
      with pytest.raises(KeyError):
          inv.commits_by_id["no-such-commit"]
  ```
  The published index's backing store is a plain `dict`, so membership is O(1) average; this test proves the lookup is a direct hit, not a scan, and that a missing key raises rather than scanning.
- [ ] **Step 3: Run RED.** `... -m pytest tests/test_git_history_scaling.py -q -m "not integration and not gemini_live" -rs`
  Expected: the tests PASS on the first run, because Tasks 14/18/19 already implement the measured properties. Record this outcome. If any test FAILS, that failure is the genuine RED: fix it in the OWNING task and re-run that task's focused suite.
- [ ] **Step 4: Minimal GREEN.** No production change is expected. Perform the SENSITIVITY PROBE for each measured property, in THROWAWAY copies (never committed): make `resolve_baseline` issue a resolution command for a ROOT commit and confirm `test_git_command_count_is_linear_in_commits` FAILS; make `build_co_change_index` count a duplicate pair twice and confirm `test_duplicate_pairs_do_not_grow_the_budget` FAILS; make the overflow path materialize a partial commit and confirm `test_global_pair_budget_overflow_is_reported_atomically` FAILS; issue one extra command per symbol and confirm `test_command_count_does_not_grow_with_symbols` FAILS. Restore every file and confirm the suite is GREEN. Record the probe output. A probe that still passes proves a vacuous test, which must be strengthened here.
- [ ] **Step 5: Run GREEN.** Same command as Step 3. Expected: all tests PASS.
- [ ] **Step 6: Neighboring regression.** `... -m pytest tests/test_git_history_scaling.py tests -q -m "not integration and not gemini_live" -rs` — expected: PASS with no new failures.
- [ ] **Step 7: Security/contract checks.** Confirm the production constants are unchanged after this task: the source must still read `1000` for the per-commit path cap and `10_000_000` for the global pair budget. Confirm no temporary fault injection, no weakened assertion, and NO CHANGE to the production constant VALUES in `backend/git_history/analyzer.py` is left in the committed test file. (A test may still monkeypatch the module attribute at runtime — that is the sanctioned injection seam — but the production source value must remain `10_000_000` / `1000`.)
- [ ] **Step 8: Commit.** `git add tests/test_git_history_scaling.py` then `git commit -m "test: prove git history analyzer scaling and determinism"`.
- [ ] **Step 9: Independent review checklist.** [ ] Operation counts are deterministic, not timing-based. [ ] Exactly one tree-resolution command per non-root commit and none for a root. [ ] No per-symbol or per-reference commands. [ ] Repeat analysis is byte-identical. [ ] Published indexes are immutable. [ ] Large-budget scenarios use injection, not weakened production limits. [ ] The sensitivity probe is not committed.
- [ ] **Step 10: Acceptance evidence.** GREEN output plus the recorded command counts for the 5-commit repository and the recorded budget-overflow evidence.

---

### Task 24: Complete Regression Verification and Whole-Module Review

**Prerequisites:** Tasks 1-23.

**Files:**
- Create: `docs/superpowers/verification/module-7-git-history-analyzer.md`
- Modify: none in production or test code.

**Spec references:** §20 (acceptance criteria), §16 (offline testing strategy), §13 (security), §12 (upstream compatibility).

**Inputs / Outputs:** Input: the complete Module 7 implementation. Output: the verification evidence document and a whole-module review verdict.

**Interfaces:**
- Consumes: every Module 7 suite and every upstream suite.
- Produces: `docs/superpowers/verification/module-7-git-history-analyzer.md`.

- [ ] **Step 1: Scaffolding.** Create the verification document with these section headings, in this order (`# Module 7 — Git History Analyzer: Verification Evidence`, `## Result`, `## Environment`, `## Task approval status`, `## Implementation commit IDs (Module 7)`, `## Verification commands and results`, `## Scaling observations`, `## Security evidence`, `## Scope audit`, `## Whole-module review verdict`, `## Chroma flake ruling`, `## Graph integration evidence`, `## Conclusion`) and placeholder rows for every command — including a row for the `git log --oneline <base-sha>..HEAD` listing, so no mandated command has no home.
- [ ] **Step 2: Write the failing check.** The RED here is the acceptance gate itself: run every gate command and record its ACTUAL output. Any gate that fails is a RED that must be fixed (in the owning task) before this task can close. Do not record a passing result that was not observed.
- [ ] **Step 3: Run the full verification battery** from the worktree root, recording each command's exact output and exit code:
  ```powershell
  $env:PYTHONPATH = "<worktree-root>"
  $py = "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe"
  & $py -m pytest tests -q -k "git_history" -m "not integration and not gemini_live" -rs
  & $py -m pytest tests/test_repository_loader.py -q -m "not integration and not gemini_live" -rs
  & $py -m pytest tests/test_file_scanner.py -q -m "not integration and not gemini_live" -rs
  & $py -m pytest tests/test_code_parser_dependencies.py tests/test_code_parser.py -q -m "not integration and not gemini_live" -rs
  & $py -m pytest tests/test_code_chunker.py -q -m "not integration and not gemini_live" -rs
  & $py -m pytest tests/test_embedding_models.py tests/test_embedding_documents.py tests/test_embedding_validation.py tests/test_embedding_providers.py tests/test_embedding_retry.py tests/test_gemini_embedding_provider.py tests/test_gemini_live_safety.py tests/test_vector_store_contract.py tests/test_chroma_vector_store.py tests/test_chroma_integrity_deletion.py tests/test_semantic_synchronization.py tests/test_semantic_search.py tests/test_embedding_security.py tests/test_embedding_scaling.py tests/test_embedding_integration.py tests/capability/test_module5_dependency_capabilities.py -q -m "not integration and not gemini_live" -rs
  & $py -m pytest tests/test_code_graph_builder.py tests/test_code_graph_determinism.py tests/test_code_graph_ecmascript.py tests/test_code_graph_identities.py tests/test_code_graph_java.py tests/test_code_graph_models.py tests/test_code_graph_partial_parses.py tests/test_code_graph_python.py tests/test_code_graph_scaling.py tests/test_code_graph_security.py tests/test_code_graph_validation.py -q -m "not integration and not gemini_live" -rs
  & $py -m pytest tests/test_code_graph_integration.py -q -rs
  & $py -m pytest -q -m "not integration and not gemini_live" -rs
  & $py -m compileall -q backend tests
  & $py -m pip check
  git diff --check
  git status --porcelain
  git log --oneline <base-sha>..HEAD
  ```
- [ ] **Step 4: Record the results** in the verification document, including the ACTUAL pass/skip/deselect counts observed. The historical baseline `2387 passed, 4 skipped, 2 deselected` is NOT a prediction; the Module 7 suites add to it, so the final count will differ. State both the pre-Module-7 baseline and the measured final count.
- [ ] **Step 5: Apply the Chroma flake policy.** If and only if a ChromaDB persisted-HNSW failure appears AND it matches the known narrow intermittent signature (`tests/test_chroma_integrity_deletion.py::test_delete_repository_index_removes_incomplete_abandoned_candidates[True-1]`, "Nothing found on disk"): record the original failure, rerun that exact node in a FRESH process with a fresh `--basetemp`, and rerun the full affected suite only if the isolated rerun passes. Require a clean final run. No production retry, xfail, suppression, or weakened assertion is permitted. Every other deterministic failure is a blocker that must be fixed in the owning task.
- [ ] **Step 6: Scope audit.** Run `git diff --name-only <base-sha> HEAD` and confirm the change set is confined to `backend/git_history/`, the new `tests/test_git_history_*.py` files, `tests/conftest.py`, `README.md`, and the verification/plan documents. Confirm `requirements.txt` and `pytest.ini` are unchanged and that no Module 1-6 production file was modified. Confirm no Module 8-10 content exists.
- [ ] **Step 7: Dependency audit.** Confirm `git diff <base-sha> HEAD -- requirements.txt` is empty and that `backend/git_history/` imports only the standard library plus the optional `backend.file_scanner.models` type.
- [ ] **Step 8: Independent whole-module review.** Dispatch a fresh-context reviewer over the complete Module 7 diff with the approved specification as the contract. Require explicit verdicts for architecture compliance, specification coverage, security posture, resource bounds, determinism, and scope discipline, and an explicit Critical/Important/Minor/Nit classification. Resolve every Critical and Important finding and re-review.
- [ ] **Step 9: Write the Conclusion** section stating the measured result, the review verdict, the known-flake ruling, and that the module awaits a separate user-approved integration phase.
- [ ] **Step 10: Commit.** `git add docs/superpowers/verification/module-7-git-history-analyzer.md` then `git commit -m "docs: record git history analyzer verification evidence"`.
- [ ] **Step 11: Independent review checklist.** [ ] Every gate command's actual output is recorded. [ ] The Module 7 suites pass. [ ] Every upstream suite passes. [ ] The full offline suite passes. [ ] `compileall` and `pip check` pass. [ ] `git diff --check` is clean. [ ] The scope audit is confined. [ ] The whole-module review is approved with no Critical/Important findings. [ ] The Chroma ruling is recorded per policy.
- [ ] **Step 12: Acceptance evidence.** The completed verification document, the measured final counts, the scope-audit listing, and the review verdict.


---

## Preflight Audit - Task Dependencies and Shared-File Ownership

This section is the preflight the plan must pass BEFORE Task 1 begins. It exists so that a reviewer can confirm no task depends on an interface a later task creates, and that no two tasks fight over one file.

### Interfaces produced and consumed

| Produced by | Interface | First consumed by | Consumed also by (a task marked `(own tests)` exercises the interface in its OWN test file) |
|---|---|---|---|
| 1 | `backend.git_history.exceptions` (10 classes) | 2 | 1 (own tests), 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 19, 20, 22 |
| 2 | `models.GitChangeType`, `models.GitIncompleteReason`, `models.GitErrorCode` | 4 | 2 (own tests), 4, 5, 7, 18, 19, 20 |
| 2 | `identities.compute_change_id` | 4 | 4 (own tests) |
| 2 | `invariants.require_object_id` / `require_timezone` / `require_tuple` / `snapshot_mapping` | 3 | 4, 5, 6, 12, 13 |
| 2 | `invariants.EMPTY_TREE_SHA1` / `EMPTY_TREE_SHA256` | 14 | — |
| 3 | `models.GitCommit` | 4 | 3 (own tests), 5, 17, 18, 19 |
| 3 | `models.GitHistoryScope` | 5 | 19, 20 |
| 4 | `models.GitFileChange` | 5 | 4 (own tests), 17, 18, 19 |
| 5 | `models.GitHistoryInventory` | 19 | 5 (own tests), 20, 21 |
| 6 | `parser.RawCommitObject`, `parser.parse_batch_stream`, `parser.normalize_timezone` | 13 | 13 (own tests), 14, 15 |
| 7 | `parser.RawFileChange`, `parser.parse_name_status` | 15 | 7 (own tests), 15 |
| 8 | `adapter.GitAdapter` (constructor + `run` + `run_raw`), `adapter.CommandResult`, `adapter.build_git_environment`, `adapter.read_shallow_boundary_ids`, `adapter.MINIMUM_GIT_VERSION` | 9 | 8 (own tests), 10, 11, 12, 13, 14, 15, 16, 19, 22 |
| 8 | `tests/conftest.py::git_repo` fixture | 9 | 10, 11, 12, 13, 14, 15, 16, 19, 20, 21, 22, 23 |
| 9 | `adapter.terminate_process`, bounded `run_raw` | 10 | 9 (own tests), 11, 12, 13, 14, 15, 16 |
| 10 | `adapter.consume_output_budget`, `adapter.check_aggregate_deadline` | 19 | 10 (own tests) |
| 11 | `adapter.GitRepositoryContext`, `git_version`, `inspect_repository`, `resolve_revision`, `is_detached_head`, `is_unborn_head` | 12 | 11 (own tests), 13, 14, 19 |
| 12 | `adapter.CommitSelection`, `adapter.enumerate_commits` | 19 | 12 (own tests) |
| 13 | `adapter.fetch_commit_objects` | 14 | 13 (own tests), 15, 19 |
| 14 | `adapter.BaselineResolution`, `adapter.resolve_baseline` | 15 | 14 (own tests), 19 |
| 15 | `adapter.diff_trees`, `adapter.commit_tree_id` | 16 | 15 (own tests), 19 |
| 15 | `adapter.changes_for_commit` (a per-commit convenience helper; the analyzer does NOT use it, per Task 19) | 16 | 15 (own tests), 16 |
| 16 | `adapter.build_diff_flags` | 19 | 16 (own tests) |
| 17 | `analyzer.build_commits_by_id` / `build_changes_by_commit` / `build_changes_by_path` / `build_commits_touching_path` / `touched_paths` | 18 | 17 (own tests), 19 |
| 18 | `analyzer.CoChangeResult`, `analyzer.build_co_change_index`, `analyzer.CO_CHANGE_PAIR_BUDGET`, `analyzer.CO_CHANGE_PER_COMMIT_PATH_CAP` | 19 | 18 (own tests), 23 |
| 19 | `analyzer.GitHistoryAnalyzer`, `analyzer.MAX_CHANGES` | 20 | 19 (own tests), 21, 22, 23 |
| 20 | `analyzer.analyze_git_history`, `backend.git_history.__all__` | 21 | 20 (own tests), 22, 23 |
| 21 | README Module 7 section | 24 | - |
| 24 | `docs/superpowers/verification/module-7-git-history-analyzer.md` | - (the final gate) | - |

### Circular-dependency check

No cycle exists. Every interface is produced by a strictly lower-numbered task than its first consumer, except the fixture extensions, which are additive and backward-compatible:

- `tests/conftest.py` is created in Task 8, which implements the ENTIRE `git_repo` fixture contract (every builder method, including `is_unborn`, `install_hook`, `add_replacement_for_head`, and `head_message`). No later task modifies it: the file has exactly one owner, so there is no possibility of two tasks implementing the same helper.
- `backend/git_history/models.py` is extended by Tasks 3, 4, 5 in that order. Each task ADDS new names and never edits the names added by an earlier task, so the file has no conflicting ownership.
- `backend/git_history/parser.py` is created in Task 6 and extended by Tasks 7 and 13, in that order, additively.
- `backend/git_history/adapter.py` is created in Task 8 and extended by Tasks 9-16, in that order, additively.
- `backend/git_history/analyzer.py` is created in Task 17 and extended by Tasks 18, 19, 20, in that order, additively.

### Shared-file ownership rules (binding)

1. A task may ADD to a file it does not own ONLY when that file's owner task has already landed, and only by adding new names.
2. A task may EDIT existing names in a file it does not own ONLY with an explicit, recorded justification in the task's review note.
3. `README.md` is owned by Task 21; no other task may edit it.
4. `tests/conftest.py` is owned by Task 8 alone, which implements the whole fixture contract; no other task may modify it.
5. `backend/git_history/__init__.py` is created in Task 1 and its public exports are written in Task 20; no other task may edit it.
6. If a task's work requires editing another task's file, that task's owner must be complete and the edit must be justified in the commit message.

### Execution order

Tasks execute in numeric order 1 -> 24. No parallelism is required or permitted within a single file. Tasks 5, 7, 11, 14, 17, 18 are pure or near-pure and could in principle be reviewed in parallel with their neighbors, but the numeric order is the default and any deviation must preserve the ownership rules above.

---

## Verification Matrix

Every normative contract in the specification, and the task that proves it.

| Spec contract | Proving task(s) |
|---|---|
| §1 purpose and dependency position (Module 1 required, Module 2 optional, no Module 3-6 dependency) | 21, 24 |
| §4 umbrella (all public models are frozen, slotted dataclasses; collections are tuples) | 2, 3, 4, 5 |
| §6 umbrella (`GitAdapter` is the only component that touches `subprocess`; no external Git library dependency) | 8, 20, 24 |
| §2 upstream contracts unchanged | 21, 24 |
| §3 public API shape and defaults | 20 |
| §3 revision validation, unborn HEAD, detached HEAD | 11, 19 |
| §3 `max_commits` override (the exact-boundary test covers the user-supplied limit) | 12, 19 |
| §3 the 100,000-commit internal hard cap (code-level invariant; the lookahead arithmetic is proven by the exact-boundary test, the 100,000 magnitude itself is not executed in a test) | 12 |
| §3 `path_filter` validation and commit-selection-only semantics | 12, 19, 21 |
| §3 `file_inventory` namespace and path agreement | 21 |
| §3 `include_commit_messages=False` | 19 |
| §3 empty namespace / invalid `max_commits` rejected | 19 |
| §4.1 enum members and values | 2 |
| §4.2 `GitCommit` invariants, parent order, timezone, negative epochs | 3, 6, 19 |
| §4.3 `GitFileChange` invariants and canonical `change_id` (including unforgeability) | 2, 4 |
| §4.4 `GitHistoryScope` (populated with the deduplicated, sorted path filter) | 3, 19 |
| §4.5 `GitHistoryInventory` fields, counts, completeness | 5, 19 |
| §4.5 immutable MappingProxyType snapshots | 5, 23 |
| §4.5 copy-source exclusion from touched paths | 17, 18 |
| §4.5 `co_change_incomplete_paths` inference rules | 18, 23 |
| §4.5 missing baseline vs genuinely unchanged commit | 19 |
| §4.6 exception hierarchy | 1, 2, 3, 4, 5 |
| §5 evidence-vs-causality boundary (no inference) | 18, 19 |
| §6.1 allowed commands, environment, temp helpers, no `cwd` | 8, 11, 22 |
| §6.1 option-injection defense (`--end-of-options`, literal pathspecs) | 11, 12, 22 |
| §6.2 enumeration, lookahead, `--no-merges`, literal pathspec | 12 |
| §6.2 shallow detection, `--git-common-dir` resolution, linked worktree | 11, 19 |
| §6.3 raw metadata, parents from raw headers | 6, 13 |
| §6.4 commit tree from the raw object; ONE first-parent resolution; NEVER two revision args | 11, 14, 22 |
| §6.4 root empty-tree baseline; first-parent baseline; merge semantics | 14, 15, 19 |
| §6.4 unavailable baseline (no fabricated changes) | 14, 19, 21 |
| §6.4 exit-status classification of a failed resolution | 14 |
| §6.4 NUL-delimited record grammar | 7 |
| §6.5 rename/copy similarity and flag combinations | 16 |
| §7 deterministic ordering, including the intra-commit change order | 17, 18, 23 |
| §7.1 strict UTF-8, encoding headers, and error content (byte offset / offending hex) | 6, 7 |
| §8 DAG semantics, shallow boundary is not a root | 13, 19 |
| §9 copy source is lineage, not a modification | 4, 16, 17, 18 |
| §10 change identity, no Module 6 namespace reuse | 2, 20 |
| §11 incomplete reasons and `history_is_complete` (all eight reasons at the analyzer level, including `MISSING_METADATA` via Task 19's positive and negative tests; the parser-level presence flags in Task 6 provide the underlying evidence) | 6, 19, 23 |
| §12 Module 1 / Module 2 / Module 6 integration | 21 |
| §13 security posture (no network, no mutation, no hooks, no textconv, no replacement objects, no lazy fetch) | 8, 22 |
| §13 Git >= 2.29 version floor (an older Git raises `GitUnavailableError`) | 11 |
| §14 per-command timeout, aggregate timeout, per-command output cap | 9, 10 |
| §14 cumulative output budget | 10 |
| §14 1,000,000-change evidence cap | 19 |
| §14 global co-change pair budget and atomic omission | 18, 23 |
| §14 per-commit 1,000-path co-change cap | 18, 23 |
| §14 operation counts and bounded tree resolutions | 23 |
| §15 error classification | 1, 9, 10, 11, 19 |
| §16 offline testing strategy (root, linear, merge, rename, copy, delete, binary, spaces/unicode, shallow, worktree, path filter, replacement refs, limits, budgets, determinism) | 12, 13, 14, 15, 16, 18, 19, 21, 22, 23 |
| §16 the global co-change pair budget (one oversized commit that overflows, plus a many-commits duplicate-pair case that does NOT) | 18, 23 |
| §17 explicit exclusions (no Modules 8-10) | 24 |
| §18 alternatives and tradeoffs (no new dependency) | 24 |
| §19 resolved design decisions | 24 |
| §20 acceptance criteria | 24 |

---

## Completion Checklist

The module is complete only when EVERY box below is checked and the evidence is recorded.

**Per task**
- [ ] The task's RED failed on the intended behavior (an assertion), never on an import or environment error.
- [ ] The minimal GREEN implementation satisfies the task's tests and adds no speculative public names.
- [ ] The focused GREEN command passes.
- [ ] The neighboring regression commands pass with no new failures.
- [ ] The security/contract checks in the task pass.
- [ ] The task is committed with its specified message.
- [ ] An independent fresh-context review approved the task with no Critical or Important findings.
- [ ] Every review finding was corrected and re-reviewed.

**Module level**
- [ ] All 24 tasks closed with recorded evidence.
- [ ] The verification document `docs/superpowers/verification/module-7-git-history-analyzer.md` records ACTUAL measured results.
- [ ] Every command in the Task 24 battery was run and its output recorded.
- [ ] The full offline repository suite passes.
- [ ] `compileall`, `pip check`, and `git diff --check` pass.
- [ ] `requirements.txt` and `pytest.ini` are unchanged.
- [ ] No Module 1-6 production file was modified.
- [ ] No Modules 8-10 functionality exists.
- [ ] The independent whole-module review is approved with no Critical or Important findings.
- [ ] The Chroma flake ruling is recorded per the established policy.
- [ ] The module is NOT merged and NOT pushed; it awaits a separate user-approved integration phase.
