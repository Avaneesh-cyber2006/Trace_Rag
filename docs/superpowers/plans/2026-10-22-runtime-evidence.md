# Runtime Evidence Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Module 8's ingestion-only, bytes-in runtime evidence pipeline: three bounded format adapters, deterministic identities and fingerprints, optional line-containment association with a namespace-matched Module 6 graph, and an immutable `RuntimeEvidenceInventory` with explicit provenance, completeness, and privacy limits.

**Architecture:** Public frozen models live in `backend/runtime_evidence/models.py`, configuration in `config.py`, and canonical hashing in `identities.py`. Internal line, JSON-structural-scan, JSON-event, stack, source-reference, and association modules produce validated drafts; `ingestor.py` owns envelopes, deduplication, record budgets, statuses, counters, ordering, and error sanitization; `indexes.py` builds and validates immutable snapshots. Only the public Module 6 package (`CodeGraphInventory`, `GraphNode`, `GraphNodeKind`) is imported from upstream.

**Tech Stack:** Python 3.11+, standard library only (`dataclasses`, `enum`, `types.MappingProxyType`, `collections.abc`, `hashlib`, `json`, `re`, `logging`), pytest 8+, no network, Git, database, vector store, LLM, subprocess, or filesystem access at ingestion time.

**Spec:** `docs/superpowers/specs/2026-10-22-runtime-evidence-design.md` (the normative contract; every task cites its sections and exact values). This document is the implementation plan only and creates no production code or tests.

## Global Constraints

These are binding on every task and come from the specification:

- Ingestion-only, bytes-in. Never execute, import, evaluate, compile, instrument, attach to, profile, or inspect analyzed code; never start a subprocess; never read or write a file, environment variable, credential, or temporary workspace; never access network, Git, a database, ChromaDB/Foyer, an embedding provider, or an LLM.
- The only direct project dependency is the public Module 6 package, imported exactly as `from backend.code_graph import CodeGraphInventory, GraphNode, GraphNodeKind`. Do not import Module 6 internals or Modules 1-5, 7, 9, or 10.
- Do not modify Modules 1-7 contracts, production behavior, tests, dependencies, `requirements.txt`, or `pytest.ini`. The pre-existing Module 5 ChromaDB/Foyer manifest-read failure remains separate and is never waived, masked, or counted as a Module 8 regression.
- No persistence, cache, global registry, background worker, thread pool, async queue, wall-clock budget, new dependency, Modules 9-10 behavior, merge, push, fetch, rebase, reset, or clean.
- Public models are `@dataclass(frozen=True, slots=True)`; public collections are tuples; published indexes are `MappingProxyType` snapshots copied from local dictionaries.
- Enums derive from `str, Enum` and serialize by value. Public strings are Unicode scalar-only. Never use `surrogatepass`, replacement decoding, lossy decoding, or Unicode normalization.
- Canonical hashes are lowercase SHA-256 over UTF-8 JSON arrays with `ensure_ascii=False` and `separators=(",", ":")`. IDs never contain source names, absolute/machine paths, graph IDs, Module 8 timestamps, random values, or `hash()`.
- Artifact identity is content-addressed. Event and frame identity are occurrence-based. Event fingerprints are grouping keys, never identities.
- No absolute path, UNC path, drive path, raw source string, raw artifact bytes, source name, namespace, declared claim, trace/span ID, free text, tag, function text, graph content, or parser exception may appear in a public `repr`, normal log, or fatal exception message.
- V1 formats are exactly `TEXT_LOG`, `JSON_EVENTS` (document or JSONL), and `STACK_TRACE_TEXT`; `STACK_TRACE_TEXT` requires a known declared language.
- `RuntimeEvidenceConfig` defaults are exactly: `max_artifacts=64`, `max_namespace_bytes=256`, `max_artifact_bytes=8_388_608`, `max_submitted_input_bytes=67_108_864`, `max_total_input_bytes=67_108_864`, `max_events=50_000`, `max_json_nodes=1_000_000`, `max_json_number_bytes=1_024`, `max_frames_per_event=128`, `max_total_frames=200_000`, `max_issues=10_000`, `max_line_bytes=65_536`, `max_source_reference_bytes=1_024`, `max_message_bytes=8_192`, `max_error_message_bytes=8_192`, `max_stack_bytes=65_536`, `max_frame_function_bytes=1_024`, `max_tags=32`, `max_tag_key_bytes=128`, `max_tag_value_bytes=512`, `retain_event_text=True`.
- Config integers are positive exact `int` values, never `bool`; `retain_event_text` is an exact `bool`; enforce `max_artifact_bytes <= max_total_input_bytes <= max_submitted_input_bytes` and `max_frames_per_event <= max_total_frames`.
- Fatal errors are exactly the seven-class hierarchy and return no partial inventory. Recoverable artifact outcomes are `COMPLETE`, `PARTIAL`, `FAILED`, or `SKIPPED` as specified.
- Every task uses strict RED/GREEN TDD, focused regression, security/contract checks, one commit, and a fresh-context review checklist. A RED failure on import, syntax, or environment is invalid; scaffold the names first and rerun.
- Tests are offline and synthetic. Execute in the existing Module 8 worktree only: `C:\Users\avane\Desktop\Studies\Projects\Trace_Rag_Module8_Worktree`, branch `codex/module-8-runtime-evidence`.
- The execution base is the reviewed plan commit, whose direct parent MUST be the approved specification commit `9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa`. The upstream baseline is `6f25511af8fa1615342a6be3083b09ac3e73d4be`.
- Never run tests against the primary repository, inside any worktree as a basetemp, or with pytest cache files. No live Gemini or networked tests.
- No merge or push. Implementation completion stops on the feature branch for a separate user-approved integration phase.

## Review Focus

These are the inputs and conditions the spec implies but that no single task's happy-path tests obviously exercise. Each line is pinned by tests in the named task.

1. **A malformed but balanced whole JSON document with one usable object line must recover as JSONL, not become a document failure** (T11). Invalid escapes, missing commas, trailing commas, and a valid scalar/array whole candidate must fall back per line; only a syntactically valid whole object/array may select document mode.
2. **An over-budget valid whole document must fail as a document, while JSONL budgets are per line** (T9, T11). Do not fall back to JSONL for a valid over-budget document and do not compare aggregate JSONL nodes to `max_json_nodes`.
3. **Metadata-only mode must validate first and project before text truncation and fingerprinting** (T10, T20). It omits only the seven specified free-text classes, retains structural stack frames/paths/associations/IDs, sets `text_truncated=False` for policy omission, and still rejects over-limit non-truncatable fields.
4. **Unpaired surrogates and Unicode scalar rules must never escape as `UnicodeEncodeError` or a malformed hash** (T3, T6, T10, T12, T20). Valid pairs become one scalar; unpaired field values are `INVALID_EVENT_SCHEMA`, unpaired sources are invalid sources, and unpaired stack text is `STACK_UNPARSEABLE`.
5. **Frame association must respect exclusive `end_line`/`end_column` and index only FILE/SYMBOL primaries** (T14, T15, T21, T22). A line at `end_line` belongs only when `end_column > 0`; `AMBIGUOUS_SYMBOL` has no primary; `frames_by_node` excludes file candidates of symbol frames and all ambiguous/unassociated frames.

## Execution Boundaries

- Worktree: `C:\Users\avane\Desktop\Studies\Projects\Trace_Rag_Module8_Worktree`.
- Branch: `codex/module-8-runtime-evidence`.
- Spec commit and execution-base parent: `9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa`; upstream baseline `6f25511af8fa1615342a6be3083b09ac3e73d4be`.
- Plan commit is the reviewed document at `docs/superpowers/plans/2026-10-22-runtime-evidence.md`, created by the execution base setup step below; its direct parent MUST be `9803be4f...`. Verify with `git rev-parse HEAD^` before Task 1.
- [ ] **Execution base setup (before Task 1):** stage only `docs/superpowers/plans/2026-10-22-runtime-evidence.md`; commit with `docs: plan module 8 runtime evidence implementation`; verify `git rev-parse HEAD^` equals `9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa`, `git status --short` is empty, and `git show --name-only --pretty=format: HEAD` lists only the plan. Task 1 starts only after this commit.
- Every task starts from a clean worktree and changes only its declared files. No task touches the primary repository or any other worktree.
- The implementation must preserve all pre-existing untracked artifacts and registered worktrees. Never clean, reset, stash, or remove them.
- Production caps are never weakened for tests. Small tests construct explicit small `RuntimeEvidenceConfig` values; do not monkeypatch defaults.

## Test Environment and Canonical Command

All future runs use Windows PowerShell 5, from the Module 8 worktree root. `<RT-TEST>` means exactly this command, with the selector substituted and a fresh GUID basetemp outside every repository and worktree:

```powershell
$env:PYTHONPATH = "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag_Module8_Worktree"
$env:RT_BASETEMP = Join-Path $env:TEMP ("runtime-evidence-pytest-" + [guid]::NewGuid().ToString("N"))
& "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe" -m pytest <SELECTOR> -q -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
```

Expected output for GREEN: exit code 0 and no failed/error tests. The basetemp is never inside either repository or any worktree. Never add `-x` to the full-suite gate; focused RED runs may add `-x` only after the selector.

## TDD and Review Convention

Each task follows this order:

1. **Scaffold** exact names/signatures with behavior left unimplemented, so the first test fails on a named assertion rather than an import.
2. **Write the failing test** using the task's exact values and assertions.
3. **Run RED** with `<RT-TEST>`. Expected: the named assertion fails. Import, collection, syntax, or environment errors mean the scaffold is wrong.
4. **Implement the minimum GREEN** behavior. No speculative public names.
5. **Run GREEN**, then focused neighboring/regression selectors.
6. **Run security/contract checks** named by the task and inspect `git diff --name-only HEAD~1`.
7. **Commit only the task files**, with the exact message given.
8. **Fresh-context independent review** against the task checklist. Fix and re-review any Critical/Important finding before the next task.
9. Record GREEN, regression, security, and diff-name-only evidence in the task review note.

Test-file-local builders (`_config`, `_artifact`, `_request`, `_event`, `_frame`, `_issue`, `_artifact_record`, `_inventory`) MUST pass overrides through verbatim and MUST NOT pre-validate, so deliberately invalid values reach the production constructor. Import production classes from their owning module until Task 18 fills the public package. Do not add a `tests/__init__.py`, modify `tests/conftest.py`, or depend on pytest plugins beyond pytest itself.

## Task Map

| # | Task | Produces |
|---|---|---|
| 1 | Exception hierarchy | `backend/runtime_evidence/exceptions.py`; package marker |
| 2 | Config and scalar validation | `config.py`, `validation.py` |
| 3 | Enums and input models | `models.py` enums, `RuntimeTag`, `RuntimeArtifact`, `RuntimeEvidenceRequest` |
| 4 | Output record models | `models.py` frame/event/issue/artifact-record values |
| 5 | Inventory and immutable indexes | `models.py` inventory, `indexes.py` |
| 6 | Canonical identities | `identities.py` |
| 7 | Physical lines and `TEXT_LOG` | `lines.py`, `drafts.py`, `text_adapter.py` |
| 8 | Stack grammars | `stack_parser.py` |
| 9 | JSON structural scanner | `json_scanner.py` |
| 10 | JSON event schema and metadata projection | `json_adapter.py` (normalization) |
| 11 | JSON document/JSONL selection and recovery | `json_adapter.py` (artifact adapter) |
| 12 | `STACK_TRACE_TEXT` blocks and budgets | `stack_parser.py` (artifact adapter) |
| 13 | Source-reference normalization | `source_references.py` |
| 14 | Graph validation and lookup index | `association.py` (graph index) |
| 15 | Frame path matching and line association | `association.py` (frame resolution) |
| 16 | Ingestion pipeline and artifact records | `ingestor.py` |
| 17 | Event, frame, and issue budgets | `ingestor.py` (budget state) |
| 18 | Public exports, API, logging, sanitization | `__init__.py`, `ingestor.py` |
| 19 | Deterministic ordering and repeated runs | ordering code and tests |
| 20 | Security, privacy, and redacted representations | redacted `__repr__` methods and tests |
| 21 | Real Module 2 -> 3 -> 6 integration | integration tests and fixes |
| 22 | Scaling and resource-operation gates | scaling tests and fixes |
| 23 | README, regression matrix, verification document | `README.md`, verification doc |
| 24 | Independent final audit and closure | final audit evidence and any approved corrections |

## File Ownership Map

| File | Owner | Later modifiers |
|---|---|---|
| `backend/runtime_evidence/__init__.py` | 1 (marker) | 18 (exports) |
| `backend/runtime_evidence/exceptions.py` | 1 | - |
| `backend/runtime_evidence/config.py` | 2 | - |
| `backend/runtime_evidence/validation.py` | 2 | 7, 19 |
| `backend/runtime_evidence/models.py` | 3 | 4, 5, 20 |
| `backend/runtime_evidence/lines.py` | 7 | 11, 12 |
| `backend/runtime_evidence/drafts.py` | 7 | - |
| `backend/runtime_evidence/text_adapter.py` | 7 | 17 |
| `backend/runtime_evidence/stack_parser.py` | 8 | 10, 12 |
| `backend/runtime_evidence/json_scanner.py` | 9 | 22 |
| `backend/runtime_evidence/json_adapter.py` | 10 | 11, 17, 22 |
| `backend/runtime_evidence/source_references.py` | 13 | - |
| `backend/runtime_evidence/association.py` | 14 | 15, 21, 22 |
| `backend/runtime_evidence/indexes.py` | 5 | 19 |
| `backend/runtime_evidence/identities.py` | 6 | - |
| `backend/runtime_evidence/ingestor.py` | 16 | 17, 18, 19, 20, 21, 22 |
| `tests/test_runtime_evidence_models.py` | 1 | 2, 3, 4, 5, 18, 20 |
| `tests/test_runtime_evidence_validation.py` | 2 | 3, 16, 17 |
| `tests/test_runtime_evidence_identities.py` | 6 | 11, 12, 19 |
| `tests/test_runtime_evidence_text.py` | 7 | 17 |
| `tests/test_runtime_evidence_json.py` | 9 | 10, 11, 17 |
| `tests/test_runtime_evidence_stack.py` | 8 | 12, 17 |
| `tests/test_runtime_evidence_association.py` | 13 | 14, 15 |
| `tests/test_runtime_evidence_determinism.py` | 19 | - |
| `tests/test_runtime_evidence_security.py` | 20 | - |
| `tests/test_runtime_evidence_integration.py` | 21 | - |
| `tests/test_runtime_evidence_scaling.py` | 22 | - |
| `README.md` | 23 | - |
| `docs/superpowers/verification/module-8-runtime-evidence.md` | 23 | 24 |

## Dependency Graph

```text
1 -> 2 -> 3 -> 4 -> 5
          \         \
           6 -> 7 -> 8 -> 9 -> 10 -> 11
                     \           /
                      12 -------/
13 -> 14 -> 15 -> 16 -> 17 -> 18 -> 19 -> 20 -> 21 -> 22 -> 23 -> 24
```

Tasks 9-12 may be developed after 7 and 8; Task 13 depends on 3/4; Task 14 consumes 3/4 plus public Module 6; Task 15 consumes 6, 13, 14; Task 16 consumes 5-15. Numeric order is the required execution order.

## Deferred Minor-Finding Coverage

Every deferred Minor from the approved design review is pinned here and in a task test:

| Minor finding / edge | Owning test |
|---|---|
| JSONL diagnostics when no line qualifies as an object | T11 `test_jsonl_without_a_qualifying_object_reports_each_line_and_fails` |
| Issue lines are one-based | T7 `test_invalid_utf8_nul_and_overlong_lines_are_skipped_with_one_based_lines`, T11 `test_jsonl_failure_precedence_and_one_based_lines`, T19 issue ordering |
| Failure precedence `LINE_TOO_LONG` -> `NUL_BYTE` -> `INVALID_UTF8` -> JSON validation | T7 `test_line_failure_precedence`, T11 `test_jsonl_failure_precedence_and_one_based_lines` |
| Every public mapping key and value is scalar-only | T5 `test_inventory_rejects_surrogate_index_key_or_value`, T4 mapping-index constructors |
| Multibyte namespace boundaries use UTF-8 bytes, not characters | T2 `test_multibyte_byte_limits_are_utf8_bytes_not_characters`, T3 `test_request_namespace_exact_multibyte_limit_and_surrogates`, T14 `test_namespace_multibyte_at_limit_and_mismatch` |
| Numeric-looking strings are not coerced | T10 `test_log_event_and_duration_are_exact_strings` and `test_numeric_strings_do_not_satisfy_int_fields_and_bool_is_not_int` |
| Structural-budget failures use the fixed `MALFORMED_JSON` message | T10 `test_structural_budget_messages_are_fixed` |
| Malformed-but-balanced whole document falls back to JSONL | T11 `test_malformed_balanced_documents_fall_back_to_jsonl` |
| Invalid escapes, missing commas, trailing commas | same T11 test plus `test_balanced_invalid_document_with_recoverable_object_line` |
| Over-budget whole document never falls back | T9 `test_scanner_does_not_materialize_json`, T11 `test_over_budget_document_fails_whole_and_does_not_fall_back` |
| JSONL lines are independently budgeted | T11 `test_aggregate_jsonl_nodes_may_exceed_document_budget` |
| Issue sentinel copies first dropped issue's artifact/event/line | T17 `test_issue_limit_sentinel_points_to_first_dropped_issue` |
| Module 5 failure remains a separate exception | T23 regression matrix and verification document |

## Acceptance Gates

The module is complete only when all gates pass:

1. All 11 Module 8 test files and all focused tests pass offline.
2. Public exports match the exact 28-name contract in T18; no internal parser/index/helper leaks.
3. The three formats, canonical digests, association matrix, record budgets, statuses, counters, indexes, and fatal/recoverable semantics match the specification.
4. Security and privacy tests prove no filesystem, subprocess, network, Git, database, LLM, unsafe-deserialization, logging, repr, exception, or raw-byte leak.
5. A real Module 2 -> 3 -> 6 pipeline associates synthetic runtime frames without source rereads or upstream edits.
6. Scaling tests show linear byte/record behavior and basename-bucket/line-sweep association, not all-pairs scans.
7. The full offline suite, `compileall`, `pip check`, and `git diff --check` pass, except that the separately tracked pre-existing Module 5 ChromaDB/Foyer failure may remain; any remaining failure must be a documented baseline subset with the same call site/signature.
8. Independent whole-branch review has no Critical or Important findings; Task 24 records both PASS verdicts.
9. README documents the public contract and the verification document records commands and evidence.
10. No Modules 1-7 production change, no new dependency, no Modules 9-10 work, no persistence, no merge, and no push.

---

### Task 1: Exception Hierarchy

**Prerequisites:** Execution base setup commit (the reviewed plan commit with parent `9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa`).

**Files:**
- Create: `backend/runtime_evidence/__init__.py` (docstring only; public exports land in T18)
- Create: `backend/runtime_evidence/exceptions.py`
- Test: `tests/test_runtime_evidence_models.py`

**Spec references:** §5, §13.1, §13.3.

**Interfaces:**
- Consumes: nothing.
- Produces: `RuntimeEvidenceError`, `RuntimeEvidenceConfigurationError`, `RuntimeEvidenceValidationError`, `RuntimeEvidenceLimitError`, `RuntimeEvidenceGraphError`, `RuntimeEvidenceInvariantError`, `RuntimeEvidenceIngestionError`.
- Hierarchy: every specific class inherits directly from `RuntimeEvidenceError`; `RuntimeEvidenceError` inherits `Exception`. No class carries content, path, parser, or error-code attributes.

- [ ] **Step 1: Scaffold** the package marker and all seven classes, initially each inheriting directly from `Exception`.
- [ ] **Step 2: Write the failing tests** in `tests/test_runtime_evidence_models.py`:

```python
from backend.runtime_evidence.exceptions import (
    RuntimeEvidenceConfigurationError, RuntimeEvidenceError,
    RuntimeEvidenceGraphError, RuntimeEvidenceIngestionError,
    RuntimeEvidenceInvariantError, RuntimeEvidenceLimitError,
    RuntimeEvidenceValidationError,
)


def test_all_fatal_errors_descend_from_runtime_evidence_error():
    assert RuntimeEvidenceError.__bases__ == (Exception,)
    for cls in (RuntimeEvidenceConfigurationError, RuntimeEvidenceValidationError,
                RuntimeEvidenceLimitError, RuntimeEvidenceGraphError,
                RuntimeEvidenceInvariantError, RuntimeEvidenceIngestionError):
        assert cls.__bases__ == (RuntimeEvidenceError,), cls.__name__


def test_exception_message_is_exact_and_not_rewritten():
    err = RuntimeEvidenceGraphError("runtime evidence graph is invalid")
    assert str(err) == "runtime evidence graph is invalid"
    assert err.args == ("runtime evidence graph is invalid",)
```

- [ ] **Step 3: RED.** Run `<RT-TEST> tests/test_runtime_evidence_models.py -x`. Expected: `test_all_fatal_errors_descend_from_runtime_evidence_error` fails with `AssertionError` because the flat scaffold does not inherit from `RuntimeEvidenceError`.
- [ ] **Step 4: GREEN.** Re-parent the six specific classes to `RuntimeEvidenceError`. Add one-line docstrings. Do not add default messages, `__init__`, raw cause storage, or fields.
- [ ] **Step 5: GREEN command.** Same selector. Expected: both tests pass.
- [ ] **Step 6: Regression/security checks.** `git diff --name-only HEAD~1` lists only the three task files. `Select-String` finds no `open\(|subprocess|os\.|Path\(|socket|json|logging` in `exceptions.py`.
- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/__init__.py backend/runtime_evidence/exceptions.py tests/test_runtime_evidence_models.py`; `git commit -m "feat: add runtime evidence exception hierarchy"`.
- [ ] **Step 8: Independent review checklist.** [ ] Exactly seven classes. [ ] Exact hierarchy. [ ] No content-bearing attributes. [ ] RED was an assertion, not import/environment. [ ] No upstream file changed.

---

### Task 2: Configuration and Scalar Validation

**Prerequisites:** T1.

**Files:**
- Create: `backend/runtime_evidence/config.py`
- Create: `backend/runtime_evidence/validation.py`
- Test: `tests/test_runtime_evidence_models.py`, `tests/test_runtime_evidence_validation.py`

**Spec references:** §4, §5, §6.1, §9.1, §10.1, §12, §13.1.

**Interfaces:**
- Consumes: the T1 exceptions.
- Produces from `config.py`: `RuntimeEvidenceConfig` with the exact field order and defaults in Global Constraints; `__post_init__` raises `RuntimeEvidenceConfigurationError` on invalid values. T2 owns exception behavior only; T18 owns the fixed, content-free ERROR logging at the configuration validation boundary.
- Produces from `validation.py`:
  - `utf8_size(value: str) -> int`
  - `require_scalar_text(value: object, field_name: str, error_type: type[Exception], *, allow_empty: bool = True, max_bytes: int | None = None, reject_nul: bool = True) -> str`
  - `require_exact_bool(value: object, field_name: str, error_type: type[Exception]) -> bool`
  - `require_positive_int(value: object, field_name: str, error_type: type[Exception]) -> int`
  - `require_nonnegative_int(value: object, field_name: str, error_type: type[Exception]) -> int`
  - `require_sha256_digest(value: object, field_name: str, error_type: type[Exception]) -> str`
  - `require_relative_posix_path(value: object, field_name: str, error_type: type[Exception]) -> str`
  - `truncate_utf8_text(value: str, max_bytes: int) -> tuple[str, bool]`
- `require_relative_posix_path` rejects empty, leading `/`, `^[A-Za-z]:`, backslash, NUL, C0, surrogates, and empty/`.`/`..` components. It does not resolve paths and does not normalize case.
- `truncate_utf8_text` is code-point safe: when over the bound, reserve three bytes for `…`; if no code point plus the ellipsis fits, return `("", True)`. Never split a UTF-8 sequence.
- `require_scalar_text` checks exact Unicode scalar values by rejecting every code point in `U+D800..U+DFFF`; it never repairs or encodes with `surrogatepass`.
- `RuntimeEvidenceConfig.__post_init__` enforces exact `int` (not `bool`) positivity for all 20 integer limits, exact `bool` for `retain_event_text`, and all three cross-field relations.

- [ ] **Step 1: Scaffold** `RuntimeEvidenceConfig` with the exact fields but no validation; scaffold all helpers returning their input (or `("", False)` for truncation).
- [ ] **Step 2: Write failing tests.** In `tests/test_runtime_evidence_models.py`:

```python
import pytest
from dataclasses import FrozenInstanceError, fields
from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.exceptions import RuntimeEvidenceConfigurationError


def test_config_defaults_are_exact():
    cfg = RuntimeEvidenceConfig()
    assert cfg.max_artifacts == 64
    assert cfg.max_namespace_bytes == 256
    assert cfg.max_artifact_bytes == 8_388_608
    assert cfg.max_submitted_input_bytes == 67_108_864
    assert cfg.max_total_input_bytes == 67_108_864
    assert cfg.max_events == 50_000
    assert cfg.max_json_nodes == 1_000_000
    assert cfg.max_json_number_bytes == 1_024
    assert cfg.max_frames_per_event == 128
    assert cfg.max_total_frames == 200_000
    assert cfg.max_issues == 10_000
    assert cfg.max_line_bytes == 65_536
    assert cfg.max_source_reference_bytes == 1_024
    assert cfg.max_message_bytes == 8_192
    assert cfg.max_error_message_bytes == 8_192
    assert cfg.max_stack_bytes == 65_536
    assert cfg.max_frame_function_bytes == 1_024
    assert cfg.max_tags == 32
    assert cfg.max_tag_key_bytes == 128
    assert cfg.max_tag_value_bytes == 512
    assert cfg.retain_event_text is True


def test_config_field_order_is_exact():
    assert [field.name for field in fields(RuntimeEvidenceConfig)] == [
        "max_artifacts", "max_namespace_bytes", "max_artifact_bytes",
        "max_submitted_input_bytes", "max_total_input_bytes", "max_events",
        "max_json_nodes", "max_json_number_bytes", "max_frames_per_event",
        "max_total_frames", "max_issues", "max_line_bytes",
        "max_source_reference_bytes", "max_message_bytes",
        "max_error_message_bytes", "max_stack_bytes", "max_frame_function_bytes",
        "max_tags", "max_tag_key_bytes", "max_tag_value_bytes",
        "retain_event_text"]


@pytest.mark.parametrize("field", [
    "max_artifacts", "max_namespace_bytes", "max_artifact_bytes",
    "max_submitted_input_bytes", "max_total_input_bytes", "max_events",
    "max_json_nodes", "max_json_number_bytes", "max_frames_per_event",
    "max_total_frames", "max_issues", "max_line_bytes",
    "max_source_reference_bytes", "max_message_bytes",
    "max_error_message_bytes", "max_stack_bytes", "max_frame_function_bytes",
    "max_tags", "max_tag_key_bytes", "max_tag_value_bytes",
])
def test_config_rejects_bool_and_nonpositive_integers(field):
    with pytest.raises(RuntimeEvidenceConfigurationError):
        RuntimeEvidenceConfig(**{field: True})
    with pytest.raises(RuntimeEvidenceConfigurationError):
        RuntimeEvidenceConfig(**{field: 0})
    with pytest.raises(RuntimeEvidenceConfigurationError):
        RuntimeEvidenceConfig(**{field: -1})


def test_config_rejects_cross_field_violations():
    with pytest.raises(RuntimeEvidenceConfigurationError):
        RuntimeEvidenceConfig(max_artifact_bytes=11, max_total_input_bytes=10,
                              max_submitted_input_bytes=10)
    with pytest.raises(RuntimeEvidenceConfigurationError):
        RuntimeEvidenceConfig(max_artifact_bytes=10,
                              max_total_input_bytes=11,
                              max_submitted_input_bytes=10)
    with pytest.raises(RuntimeEvidenceConfigurationError):
        RuntimeEvidenceConfig(max_frames_per_event=3, max_total_frames=2)


def test_config_is_frozen_slotted_and_requires_exact_bool():
    cfg = RuntimeEvidenceConfig()
    with pytest.raises(FrozenInstanceError):
        cfg.max_events = 1
    assert not hasattr(cfg, "__dict__")
    with pytest.raises(RuntimeEvidenceConfigurationError):
        RuntimeEvidenceConfig(retain_event_text=1)
```

In `tests/test_runtime_evidence_validation.py`:

```python
import pytest
from backend.runtime_evidence.validation import (
    require_relative_posix_path, require_scalar_text, truncate_utf8_text,
    utf8_size,
)


def test_scalar_validation_rejects_every_surrogate_and_nul():
    for bad in ("\ud800", "\udfff", "a\ud800b", "\ud83d\ude00", "a\x00b"):
        with pytest.raises(ValueError):
            require_scalar_text(bad, "field", ValueError)
    assert require_scalar_text("😀", "field", ValueError) == "😀"


def test_scalar_validation_rejects_non_strings():
    for bad in (None, 1, b"x", object()):
        with pytest.raises(ValueError):
            require_scalar_text(bad, "field", ValueError)


def test_multibyte_byte_limits_are_utf8_bytes_not_characters():
    assert utf8_size("é") == 2
    assert require_scalar_text("é" * 128, "namespace", ValueError,
                               max_bytes=256) == "é" * 128
    with pytest.raises(ValueError):
        require_scalar_text("é" * 128 + "a", "namespace", ValueError,
                            max_bytes=256)


@pytest.mark.parametrize("bad", ["", "/a", "C:/a", "C:a", r"a\b",
                                 "a//b", "a/./b", "a/../b", "a/", "a\x00b",
                                 "a\x01b", "a\tb", "a\ud800b"])
def test_relative_posix_path_rejects_unsafe_forms(bad):
    with pytest.raises(ValueError):
        require_relative_posix_path(bad, "path", ValueError)


def test_relative_posix_path_accepts_canonical_components():
    assert require_relative_posix_path("pkg/app.py", "path", ValueError) == "pkg/app.py"


def test_text_truncation_is_codepoint_safe_and_reserves_ellipsis():
    assert truncate_utf8_text("abc", 3) == ("abc", False)
    assert truncate_utf8_text("abc😀", 5) == ("ab…", True)
    assert truncate_utf8_text("😀x", 4) == ("", True)
    assert truncate_utf8_text("x", 1) == ("x", False)
```

- [ ] **Step 3: RED.** Run `<RT-TEST> tests/test_runtime_evidence_models.py tests/test_runtime_evidence_validation.py -x`. Expected: assertion failures for defaults/booleans/surrogates/truncation; no import error.
- [ ] **Step 4: GREEN.** Implement config validation and helpers exactly. Use an explicit ASCII whitespace set only where a task says to trim; do not trim in `require_scalar_text`. Reject `type(value) is bool` before integer checks. `truncate_utf8_text` must encode a candidate prefix and stop before exceeding `max_bytes - 3`; build the output as prefix plus `…`.
- [ ] **Step 5: GREEN command.** Same selector. Expected: all pass.
- [ ] **Step 6: Regression/security checks.** Sweep `config.py` and `validation.py` for I/O/network/importlib/subprocess/eval/exec/compile; all absent. Confirm no default differs from Global Constraints. `git diff --name-only HEAD~1` lists only four task-owned files.
- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/config.py backend/runtime_evidence/validation.py tests/test_runtime_evidence_models.py tests/test_runtime_evidence_validation.py`; `git commit -m "feat: add runtime evidence configuration and validation"`.
- [ ] **Step 8: Independent review checklist.** [ ] All 21 defaults exact (20 integers plus one boolean). [ ] Field order exact. [ ] Bool rejected for every one of the 20 integer fields. [ ] Non-string scalar inputs rejected. [ ] All three cross-field relations. [ ] Unsafe path forms including single backslash, C0/tab, and surrogates rejected. [ ] UTF-8 byte limits tested with multibyte input. [ ] Truncation never splits a scalar and never exceeds the cap. [ ] No I/O or normalization. [ ] RED assertion evidence recorded.

---

### Task 3: Enums and Input Models

**Prerequisites:** T2.

**Files:**
- Create: `backend/runtime_evidence/models.py` (all enums; `RuntimeTag`, `RuntimeArtifact`, `RuntimeEvidenceRequest`)
- Test: `tests/test_runtime_evidence_models.py`, `tests/test_runtime_evidence_validation.py`

**Spec references:** §4, §5, §6.1, §6.2, §6.3, §13.1.

**Interfaces:**
- Consumes: config and validation helpers (T2); exceptions (T1).
- Produces all ten public enums with exact members/values from §6.1: `RuntimeArtifactFormat`, `RuntimeArtifactStatus`, `RuntimeEventKind`, `RuntimeFrameLanguage`, `CaptureCompleteness`, `GraphBinding`, `PathMatchBasis`, `FrameAssociation`, `RuntimeIncompleteReason`, `RuntimeIssueCode` (15 members).
- Produces `RuntimeTag(key: str, value: str)`.
- Produces `RuntimeArtifact(source_name: str, format: RuntimeArtifactFormat, content: bytes, declared_language: RuntimeFrameLanguage | None = None)`.
- Produces `RuntimeEvidenceRequest(repository_namespace: str, artifacts: tuple[RuntimeArtifact, ...], declared_revision: str | None = None, declared_captured_at: str | None = None, declared_capture_completeness: CaptureCompleteness = CaptureCompleteness.UNKNOWN)`.
- All three are frozen/slotted. Input constructors raise `RuntimeEvidenceValidationError` for invalid caller values, including non-exact enum values for `format`, `declared_language`, and `declared_capture_completeness`.
- `source_name` rules: nonempty, <=128 UTF-8 bytes, no `/`, `\`, `:`, NUL, C0, or leading/trailing ASCII whitespace. `repository_namespace`: nonempty, opaque, case-sensitive, not trimmed, <=`max_namespace_bytes`-equivalent construction limit of 256 bytes (the model has no config argument; use the V1 contract limit). Claims: revision <=256 bytes, capture time <=128 bytes, no NUL, scalar-only, no normalization.
- `STACK_TRACE_TEXT` requires known language: not `None` and not `UNKNOWN`.
- Request artifacts must be an exact tuple of exact `RuntimeArtifact` instances; content must be exact `bytes`, not `bytearray`/`memoryview`. Zero artifacts is allowed.
- `RuntimeArtifact.__repr__` and `RuntimeEvidenceRequest.__repr__` are deferred to T20; until then no test asserts them.

- [ ] **Step 1: Scaffold** all enum classes with placeholder values, and the three dataclasses without validation.
- [ ] **Step 2: Write failing tests.** In `tests/test_runtime_evidence_models.py`:

```python
import pytest
from dataclasses import FrozenInstanceError, fields
from backend.runtime_evidence.exceptions import RuntimeEvidenceValidationError
from backend.runtime_evidence.models import (
    CaptureCompleteness, FrameAssociation, GraphBinding, PathMatchBasis,
    RuntimeArtifact, RuntimeArtifactFormat, RuntimeArtifactStatus,
    RuntimeEventKind, RuntimeEvidenceRequest, RuntimeFrameLanguage,
    RuntimeIncompleteReason, RuntimeIssueCode, RuntimeTag,
)


def test_enum_members_and_values_are_exact():
    assert [x.value for x in RuntimeArtifactFormat] == [
        "text_log", "json_events", "stack_trace_text"]
    assert [x.value for x in RuntimeArtifactStatus] == [
        "complete", "partial", "failed", "skipped"]
    assert [x.value for x in RuntimeEventKind] == ["log", "error", "span"]
    assert [x.value for x in RuntimeFrameLanguage] == [
        "python", "java", "javascript", "typescript", "tsx", "unknown"]
    assert [x.value for x in CaptureCompleteness] == [
        "complete", "partial", "unknown"]
    assert [x.value for x in GraphBinding] == [
        "not_provided", "namespace_matched_unversioned"]
    assert [x.value for x in PathMatchBasis] == [
        "exact_relative_path", "unverified_relative_path", "unique_path_suffix",
        "unique_basename", "external_or_non_file", "ambiguous", "unmatched"]
    assert [x.value for x in FrameAssociation] == [
        "not_evaluated", "unassociated", "file", "symbol", "ambiguous_symbol"]
    assert [x.value for x in RuntimeIncompleteReason] == [
        "artifact_failed", "artifact_partial", "artifact_skipped",
        "event_limit_reached", "frame_limit_reached", "issue_limit_reached",
        "text_truncated"]
    assert [x.value for x in RuntimeIssueCode] == [
        "artifact_too_large", "invalid_utf8", "nul_byte", "line_too_long",
        "malformed_json", "duplicate_json_key", "invalid_event_schema",
        "stack_language_missing", "stack_too_large", "stack_unparseable",
        "invalid_source_location", "text_truncated", "frame_limit_reached",
        "event_limit_reached", "issue_limit_reached"]


def _artifact(**kw):
    values = dict(source_name="ci.log", format=RuntimeArtifactFormat.TEXT_LOG,
                  content=b"hello\n", declared_language=None)
    values.update(kw)
    return RuntimeArtifact(**values)


def _request(**kw):
    values = dict(repository_namespace="repo", artifacts=(_artifact(),))
    values.update(kw)
    return RuntimeEvidenceRequest(**values)


def test_input_models_are_frozen_and_slotted():
    artifact = _artifact()
    with pytest.raises(FrozenInstanceError):
        artifact.source_name = "x"
    assert not hasattr(artifact, "__dict__")
    assert not hasattr(_request(), "__dict__")


def test_input_model_field_order_is_exact():
    assert [field.name for field in fields(RuntimeTag)] == ["key", "value"]
    assert [field.name for field in fields(RuntimeArtifact)] == [
        "source_name", "format", "content", "declared_language"]
    assert [field.name for field in fields(RuntimeEvidenceRequest)] == [
        "repository_namespace", "artifacts", "declared_revision",
        "declared_captured_at", "declared_capture_completeness"]


@pytest.mark.parametrize("name", ["", " ci.log", "ci.log ", "a/b", r"a\b",
                                  "a:b", "a\x00b", "a\x01b", "a\tb",
                                  "a\ud800b"])
def test_source_name_rules(name):
    with pytest.raises(RuntimeEvidenceValidationError):
        _artifact(source_name=name)


def test_input_scalar_fields_require_exact_strings():
    for bad in (None, 1, b"x", object()):
        with pytest.raises(RuntimeEvidenceValidationError):
            _artifact(source_name=bad)
        with pytest.raises(RuntimeEvidenceValidationError):
            _request(repository_namespace=bad)
        with pytest.raises(RuntimeEvidenceValidationError):
            RuntimeTag(bad, "v")
        with pytest.raises(RuntimeEvidenceValidationError):
            RuntimeTag("k", bad)
    for bad in (1, b"x", object()):
        with pytest.raises(RuntimeEvidenceValidationError):
            _request(declared_revision=bad)
        with pytest.raises(RuntimeEvidenceValidationError):
            _request(declared_captured_at=bad)


def test_exact_bytes_and_stack_language_rules():
    with pytest.raises(RuntimeEvidenceValidationError):
        _artifact(content=bytearray(b"x"))
    with pytest.raises(RuntimeEvidenceValidationError):
        _artifact(content=memoryview(b"x"))
    with pytest.raises(RuntimeEvidenceValidationError):
        _artifact(format=RuntimeArtifactFormat.STACK_TRACE_TEXT,
                  declared_language=None)
    with pytest.raises(RuntimeEvidenceValidationError):
        _artifact(format=RuntimeArtifactFormat.STACK_TRACE_TEXT,
                  declared_language=RuntimeFrameLanguage.UNKNOWN)
    assert _artifact(format=RuntimeArtifactFormat.STACK_TRACE_TEXT,
                     declared_language=RuntimeFrameLanguage.PYTHON)


def test_format_and_declared_language_require_exact_enums():
    for bad in ("text_log", "python", 1, object()):
        with pytest.raises(RuntimeEvidenceValidationError):
            _artifact(format=bad)
    for bad in ("python", "text_log", 1, object()):
        with pytest.raises(RuntimeEvidenceValidationError):
            _artifact(declared_language=bad)
    assert _artifact(format=RuntimeArtifactFormat.TEXT_LOG,
                     declared_language=RuntimeFrameLanguage.PYTHON
                     ).declared_language is RuntimeFrameLanguage.PYTHON


def test_request_namespace_exact_multibyte_limit_and_surrogates():
    assert _request(repository_namespace="é" * 128).repository_namespace == "é" * 128
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(repository_namespace="é" * 128 + "a")
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(repository_namespace="")
    for bad in ("\ud800", "\udfff", "\ud83d\ude00"):
        with pytest.raises(RuntimeEvidenceValidationError):
            _request(repository_namespace=bad)
        with pytest.raises(RuntimeEvidenceValidationError):
            _request(declared_revision=bad)
        with pytest.raises(RuntimeEvidenceValidationError):
            _request(declared_captured_at=bad)


def test_request_claims_are_opaque_and_not_trimmed():
    request = _request(declared_revision="  rev  ", declared_captured_at="when\x01")
    assert request.declared_revision == "  rev  "
    assert request.declared_captured_at == "when\x01"
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(declared_captured_at="a\x00b")


def test_source_name_and_claim_byte_boundaries():
    assert _artifact(source_name="a" * 128).source_name == "a" * 128
    with pytest.raises(RuntimeEvidenceValidationError):
        _artifact(source_name="a" * 129)
    assert _artifact(source_name="é" * 64).source_name == "é" * 64
    with pytest.raises(RuntimeEvidenceValidationError):
        _artifact(source_name="é" * 64 + "a")
    assert _request(declared_revision="é" * 128).declared_revision == "é" * 128
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(declared_revision="é" * 128 + "a")
    assert _request(declared_captured_at="é" * 64).declared_captured_at == "é" * 64
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(declared_captured_at="é" * 64 + "a")


def test_capture_completeness_requires_exact_enum():
    assert _request(declared_capture_completeness=CaptureCompleteness.PARTIAL
                    ).declared_capture_completeness is CaptureCompleteness.PARTIAL
    for bad in ("partial", 1, object()):
        with pytest.raises(RuntimeEvidenceValidationError):
            _request(declared_capture_completeness=bad)


def test_request_requires_exact_tuple_and_artifacts():
    class _TupleSubclass(tuple):
        pass
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(artifacts=[_artifact()])
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(artifacts=_TupleSubclass((_artifact(),)))
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(artifacts=(object(),))
    assert _request(artifacts=()).artifacts == ()


def test_tag_is_scalar_only():
    assert RuntimeTag("k", "v").value == "v"
    for key, value in (("k\ud800", "v"), ("k", "v\ud800"),
                       ("k\x00", "v"), ("k", "v\x00")):
        with pytest.raises(RuntimeEvidenceValidationError):
            RuntimeTag(key, value)
```

Add the multibyte graph/request boundary test to `tests/test_runtime_evidence_association.py` in T14; this task covers the request side.

- [ ] **Step 3: RED.** Run `<RT-TEST> tests/test_runtime_evidence_models.py -x`. Expected: enum/default/validation assertions fail. No import error.
- [ ] **Step 4: GREEN.** Implement exact enum values and constructors. Use `require_scalar_text` and `require_relative_posix_path`-independent source-name checks. `RuntimeTag` raises `RuntimeEvidenceValidationError`. Do not add config-dependent bounds to models beyond the fixed namespace/claim/source limits.
- [ ] **Step 5: GREEN.** Same selector, then `<RT-TEST> tests/test_runtime_evidence_validation.py -q`. Expected: all pass.
- [ ] **Step 6: Security/contract checks.** No I/O; `RuntimeArtifact` must not expose content in `__str__`/`__repr__` beyond Python's default at this stage (T20 pins redaction). No normalization of claims or namespace. Verify `STACK_TRACE_TEXT` rejects both missing and `UNKNOWN`.
- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/models.py tests/test_runtime_evidence_models.py tests/test_runtime_evidence_validation.py`; `git commit -m "feat: add runtime evidence enums and input models"`.
- [ ] **Step 8: Independent review checklist.** [ ] All 10 enums/55 members exact. [ ] Three input models frozen/slotted. [ ] Input field order exact. [ ] Exact bytes (not `bytearray`/`memoryview`) and exact tuple (not list/subclass). [ ] Non-string scalar fields rejected. [ ] Non-exact `format`/`declared_language` enum values, including value-equal strings, rejected. [ ] Multibyte UTF-8 boundaries. [ ] Every surrogate code point rejected in inputs. [ ] Source-name punctuation/whitespace/C0/tab rules exact. [ ] Tag key/value scalar/NUL rules exact. [ ] Claims opaque and unnormalized. [ ] No repr test yet (owned by T20).

---

### Task 4: Output Record Models

**Prerequisites:** T3.

**Files:**
- Modify: `backend/runtime_evidence/models.py`
- Test: `tests/test_runtime_evidence_models.py`

**Spec references:** §6.3, §8.3, §10.2, §10.3, §12, §13.1.

**Interfaces:**
- Consumes: T1 exceptions, T2 scalar/path/hash helpers, T3 enums and `RuntimeTag`.
- Produces `RuntimeStackFrame`, `RuntimeEvent`, `RuntimeEvidenceIssue`, `RuntimeArtifactRecord` with the exact §6.3 fields and field order.
- Produces internal `ISSUE_MESSAGES: Mapping[RuntimeIssueCode, str]` and `issue_message(code: RuntimeIssueCode) -> str` using the exact 15 messages in §6.3. These are not public-package exports introduced in this task.
- All four models are frozen/slotted and raise `RuntimeEvidenceInvariantError` on invalid direct construction.
- Frame invariants: two lowercase SHA-256 IDs; nonnegative ordinal; exact enums; scalar/NUL-free text; `line` is a positive exact integer or `None`; `column` is a nonnegative exact integer or `None`; canonical relative path or `None`; source hash present iff path present; path basis is one of `EXACT_RELATIVE_PATH`, `UNIQUE_PATH_SUFFIX`, `UNIQUE_BASENAME`, `UNVERIFIED_RELATIVE_PATH`, `AMBIGUOUS`, or `UNMATCHED` when a path is present, and one of `EXTERNAL_OR_NON_FILE`, `AMBIGUOUS`, or `UNMATCHED` when it is absent; `EXACT_RELATIVE_PATH`/`UNIQUE_PATH_SUFFIX`/`UNIQUE_BASENAME`/`UNVERIFIED_RELATIVE_PATH` require a path and `EXTERNAL_OR_NON_FILE` requires none; the association/path-basis matrix is exact: `NOT_EVALUATED` allows only `UNVERIFIED_RELATIVE_PATH` with a path, `UNMATCHED` without a path, or `EXTERNAL_OR_NON_FILE` without a path; `UNASSOCIATED` allows only `UNMATCHED` or `AMBIGUOUS` with or without a path, or `EXTERNAL_OR_NON_FILE` without a path; `FILE`, `SYMBOL`, and `AMBIGUOUS_SYMBOL` allow only a verified path basis (`EXACT_RELATIVE_PATH`, `UNIQUE_PATH_SUFFIX`, or `UNIQUE_BASENAME`) with a path; candidate IDs unique/sorted/nonempty strings; primary contained; association shape exactly `NOT_EVALUATED|UNASSOCIATED -> no candidates and no primary`, `FILE -> exactly one candidate, the primary`, `SYMBOL -> exactly two candidates with one primary`, `AMBIGUOUS_SYMBOL -> at least three candidates (FILE plus at least two symbols) with no primary`.
- Event invariants: SHA IDs; nonnegative sequence; positive `source_line` or `None`; exact enums; scalar/NUL-free optional text; `duration_ms_text` matches `^(0|[1-9][0-9]*)(\.[0-9]+)?$`; tags are a tuple of `RuntimeTag`, sorted by key with unique keys; frames are a tuple whose `event_id` equals the event and whose ordinals equal positions; both truncation flags are exact bools.
- Issue invariants: SHA artifact ID; nonnegative optional event sequence; positive optional source line; message exactly `ISSUE_MESSAGES[code]`.
- Artifact-record invariants: SHA IDs; each source name satisfies T3's source-name rules (nonempty, <=128 UTF-8 bytes, scalar-only, no NUL/C0/separators/edge whitespace); source names are a nonempty tuple in `(casefold(), original)` order with no exact duplicates; format/language compatibility; nonnegative size/counters; exact bool flags.
- No `__repr__` work in this task; T20 owns redacted representations.

- [ ] **Step 1: Scaffold** the four dataclasses with exact fields and no validation; add the issue-message mapping with placeholder strings.
- [ ] **Step 2: Write failing tests** in `tests/test_runtime_evidence_models.py`. Add local `_frame(...)`, `_event(...)`, `_issue(...)`, and `_artifact_record(...)` builders with valid defaults:

```python
from dataclasses import FrozenInstanceError, fields
from backend.runtime_evidence.exceptions import RuntimeEvidenceInvariantError
from backend.runtime_evidence.models import (
    FrameAssociation, GraphBinding, ISSUE_MESSAGES, PathMatchBasis,
    RuntimeArtifactFormat, RuntimeArtifactRecord, RuntimeArtifactStatus,
    RuntimeEvent, RuntimeEventKind, RuntimeEvidenceIssue,
    RuntimeFrameLanguage, RuntimeIssueCode, RuntimeStackFrame,
)

A = "a" * 64


def _frame(**kw):
    values = dict(frame_id=A, event_id="b" * 64, ordinal=0,
                  language=RuntimeFrameLanguage.UNKNOWN, function_text=None,
                  relative_path=None, line=None, column=None,
                  source_reference_sha256=None,
                  path_match_basis=PathMatchBasis.UNMATCHED,
                  association=FrameAssociation.UNASSOCIATED,
                  primary_node_id=None, candidate_node_ids=())
    values.update(kw)
    return RuntimeStackFrame(**values)


def _verified_frame(**kw):
    values = dict(relative_path="pkg/a.py", source_reference_sha256=A,
                  path_match_basis=PathMatchBasis.EXACT_RELATIVE_PATH)
    values.update(kw)
    return _frame(**values)


def _event(**kw):
    values = dict(event_id=A, event_fingerprint="c" * 64, artifact_id="d" * 64,
                  sequence=0, source_line=1, kind=RuntimeEventKind.LOG,
                  timestamp_text=None, level="INFO", message="hello",
                  logger=None, operation=None, trace_id=None, span_id=None,
                  parent_span_id=None, duration_ms_text=None, status=None,
                  error_type=None, error_message=None, tags=(), frames=(),
                  text_truncated=False, frames_truncated=False)
    values.update(kw)
    return RuntimeEvent(**values)


def test_output_model_field_order_and_frozen_slotted():
    assert [field.name for field in fields(RuntimeStackFrame)] == [
        "frame_id", "event_id", "ordinal", "language", "function_text",
        "relative_path", "line", "column", "source_reference_sha256",
        "path_match_basis", "association", "primary_node_id",
        "candidate_node_ids"]
    assert [field.name for field in fields(RuntimeEvent)] == [
        "event_id", "event_fingerprint", "artifact_id", "sequence",
        "source_line", "kind", "timestamp_text", "level", "message", "logger",
        "operation", "trace_id", "span_id", "parent_span_id",
        "duration_ms_text", "status", "error_type", "error_message", "tags",
        "frames", "text_truncated", "frames_truncated"]
    assert [field.name for field in fields(RuntimeEvidenceIssue)] == [
        "code", "message", "artifact_id", "event_sequence", "source_line"]
    assert [field.name for field in fields(RuntimeArtifactRecord)] == [
        "artifact_id", "source_names", "format", "declared_language",
        "content_sha256", "size_bytes", "status", "event_count",
        "frame_count", "duplicate_count", "text_truncated",
        "frames_truncated", "issue_count"]
    issue = RuntimeEvidenceIssue(
        RuntimeIssueCode.INVALID_UTF8,
        ISSUE_MESSAGES[RuntimeIssueCode.INVALID_UTF8], A, None, 1)
    for record, field_name in ((_frame(), "frame_id"), (_event(), "event_id"),
                               (issue, "code"),
                               (_artifact_record(), "artifact_id")):
        assert not hasattr(record, "__dict__")
        with pytest.raises(FrozenInstanceError):
            setattr(record, field_name, None)


def test_issue_messages_are_exact_and_issue_validates_them():
    expected = {
        RuntimeIssueCode.ARTIFACT_TOO_LARGE:
            "artifact exceeds the configured byte limit",
        RuntimeIssueCode.INVALID_UTF8: "line is not valid UTF-8",
        RuntimeIssueCode.NUL_BYTE: "NUL byte is not allowed",
        RuntimeIssueCode.LINE_TOO_LONG:
            "line exceeds the configured byte limit",
        RuntimeIssueCode.MALFORMED_JSON:
            "line or document is not valid JSON",
        RuntimeIssueCode.DUPLICATE_JSON_KEY:
            "JSON object contains a duplicate key",
        RuntimeIssueCode.INVALID_EVENT_SCHEMA:
            "event does not match the supported schema",
        RuntimeIssueCode.STACK_LANGUAGE_MISSING:
            "stack cannot be parsed without a known language",
        RuntimeIssueCode.STACK_TOO_LARGE:
            "stack exceeds the configured byte limit",
        RuntimeIssueCode.STACK_UNPARSEABLE:
            "stack contains no recognized frame",
        RuntimeIssueCode.INVALID_SOURCE_LOCATION:
            "source location is invalid",
        RuntimeIssueCode.TEXT_TRUNCATED:
            "text was truncated to the configured limit",
        RuntimeIssueCode.FRAME_LIMIT_REACHED: "frame limit was reached",
        RuntimeIssueCode.EVENT_LIMIT_REACHED: "event limit was reached",
        RuntimeIssueCode.ISSUE_LIMIT_REACHED:
            "additional issues were omitted",
    }
    assert dict(ISSUE_MESSAGES) == expected
    with pytest.raises(RuntimeEvidenceInvariantError):
        RuntimeEvidenceIssue(RuntimeIssueCode.INVALID_UTF8, "wrong", A, None, 1)


def test_frame_shape_and_path_hash_pairing():
    assert _verified_frame(association=FrameAssociation.FILE,
                           candidate_node_ids=("f",), primary_node_id="f")
    assert _frame(line=1, column=0)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(line=0)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(column=-1)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.FILE,
                        candidate_node_ids=("",), primary_node_id="")
    assert _frame(relative_path="pkg/a.py", source_reference_sha256=A,
                  path_match_basis=PathMatchBasis.UNMATCHED,
                  association=FrameAssociation.UNASSOCIATED)
    assert _frame(relative_path="pkg/a.py", source_reference_sha256=A,
                  path_match_basis=PathMatchBasis.AMBIGUOUS,
                  association=FrameAssociation.UNASSOCIATED)
    assert _frame(relative_path=None, source_reference_sha256=None,
                  path_match_basis=PathMatchBasis.UNMATCHED,
                  association=FrameAssociation.UNASSOCIATED)
    assert _frame(relative_path=None, source_reference_sha256=None,
                  path_match_basis=PathMatchBasis.AMBIGUOUS,
                  association=FrameAssociation.UNASSOCIATED)
    assert _frame(relative_path=None, source_reference_sha256=None,
                  path_match_basis=PathMatchBasis.EXTERNAL_OR_NON_FILE,
                  association=FrameAssociation.UNASSOCIATED)
    assert _frame(relative_path="pkg/a.py", source_reference_sha256=A,
                  path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
                  association=FrameAssociation.NOT_EVALUATED)
    assert _frame(relative_path=None, source_reference_sha256=None,
                  path_match_basis=PathMatchBasis.UNMATCHED,
                  association=FrameAssociation.NOT_EVALUATED)
    assert _frame(relative_path=None, source_reference_sha256=None,
                  path_match_basis=PathMatchBasis.EXTERNAL_OR_NON_FILE,
                  association=FrameAssociation.NOT_EVALUATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.FILE, candidate_node_ids=(),
                        primary_node_id=None)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(association=FrameAssociation.FILE,
               candidate_node_ids=("f",), primary_node_id="f")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(association=FrameAssociation.SYMBOL,
               candidate_node_ids=("a", "f"), primary_node_id="a")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(association=FrameAssociation.AMBIGUOUS_SYMBOL,
               candidate_node_ids=("a", "b", "f"))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
               association=FrameAssociation.FILE,
               candidate_node_ids=("f",), primary_node_id="f")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.EXACT_RELATIVE_PATH,
               association=FrameAssociation.UNASSOCIATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNIQUE_PATH_SUFFIX,
               association=FrameAssociation.NOT_EVALUATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=None,
               path_match_basis=PathMatchBasis.EXACT_RELATIVE_PATH,
               association=FrameAssociation.UNASSOCIATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path=None, source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNMATCHED,
               association=FrameAssociation.UNASSOCIATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path=None, source_reference_sha256=None,
               path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
               association=FrameAssociation.UNASSOCIATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.EXTERNAL_OR_NON_FILE,
               association=FrameAssociation.UNASSOCIATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
               association=FrameAssociation.UNASSOCIATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNMATCHED,
               association=FrameAssociation.NOT_EVALUATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.AMBIGUOUS,
               association=FrameAssociation.NOT_EVALUATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path=None, source_reference_sha256=None,
               path_match_basis=PathMatchBasis.AMBIGUOUS,
               association=FrameAssociation.NOT_EVALUATED)


def test_frame_candidate_order_and_primary_containment():
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.SYMBOL,
                        candidate_node_ids=("z", "a"), primary_node_id="z")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.SYMBOL,
                        candidate_node_ids=("a", "z"), primary_node_id="x")
    assert _verified_frame(association=FrameAssociation.SYMBOL,
                           candidate_node_ids=("a", "z"), primary_node_id="z")


def test_verified_association_candidate_shapes():
    assert _verified_frame(association=FrameAssociation.AMBIGUOUS_SYMBOL,
                           candidate_node_ids=("a", "b", "f"))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.AMBIGUOUS_SYMBOL,
                        candidate_node_ids=("a", "f"))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.AMBIGUOUS_SYMBOL,
                        candidate_node_ids=("a", "b", "f"),
                        primary_node_id="a")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.FILE,
                        candidate_node_ids=("a", "f"), primary_node_id="a")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.FILE,
                        candidate_node_ids=("f",), primary_node_id=None)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.SYMBOL,
                        candidate_node_ids=("f",), primary_node_id="f")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.SYMBOL,
                        candidate_node_ids=("a", "b", "f"), primary_node_id="a")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
               association=FrameAssociation.NOT_EVALUATED,
               candidate_node_ids=("f",), primary_node_id="f")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
               association=FrameAssociation.NOT_EVALUATED,
               candidate_node_ids=("f",), primary_node_id=None)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
               association=FrameAssociation.NOT_EVALUATED,
               candidate_node_ids=(), primary_node_id="f")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNMATCHED,
               association=FrameAssociation.UNASSOCIATED,
               candidate_node_ids=("f",), primary_node_id="f")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNMATCHED,
               association=FrameAssociation.UNASSOCIATED,
               candidate_node_ids=("f",), primary_node_id=None)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNMATCHED,
               association=FrameAssociation.UNASSOCIATED,
               candidate_node_ids=(), primary_node_id="f")


def test_event_forbids_duplicate_or_unsorted_tags_and_bad_frame_ordinals():
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(tags=(RuntimeTag("b", "1"), RuntimeTag("a", "2")))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(tags=(RuntimeTag("a", "1"), RuntimeTag("a", "2")))
    bad = _frame(ordinal=1, event_id=A)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(frames=(bad,))


def test_output_tuple_fields_require_exact_tuples():
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(tags=list(_event().tags))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(frames=list(_event().frames))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(source_names=list(_artifact_record().source_names))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(candidate_node_ids=list(_frame().candidate_node_ids))


@pytest.mark.parametrize("duration", ["-1", "1.", ".1", "01", "1e3", " 1", "1 "])
def test_event_duration_is_a_nonnegative_decimal_string(duration):
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(duration_ms_text=duration)


def test_artifact_record_source_name_order_and_language_rule():
    record = _artifact_record(source_names=("A.log", "a.log"))
    assert record.source_names == ("A.log", "a.log")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(source_names=("b.log", "a.log"))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(source_names=("a.log", "A.log"))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(source_names=("ci.log", "ci.log"))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(source_names=())
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(format=RuntimeArtifactFormat.STACK_TRACE_TEXT,
                         declared_language=None)
    assert _artifact_record(source_names=("a" * 128,))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(source_names=("a" * 129,))
    assert _artifact_record(source_names=("é" * 64,))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(source_names=("é" * 64 + "a",))
    for bad in ("", " ci.log", "ci.log ", "a/b", r"a\b", "a:b",
                "a\x00b", "a\x01b", "a\tb", "a\ud800b"):
        with pytest.raises(RuntimeEvidenceInvariantError):
            _artifact_record(source_names=(bad,))
```

`_artifact_record` defaults: `artifact_id=A`, `source_names=("ci.log",)`, `format=TEXT_LOG`, `declared_language=None`, `content_sha256="e"*64`, `size_bytes=6`, `status=COMPLETE`, counts zero, flags false.

Add a parametrized invalid-field matrix that passes `None`, wrong enum types, `True` where a count is expected, uppercase/short hashes, `\ud800`, and NUL into every text/counter/hash field and asserts `RuntimeEvidenceInvariantError`. This proves local validation, not just the examples above.

- [ ] **Step 3: RED.** `<RT-TEST> tests/test_runtime_evidence_models.py -x`. Expected: issue mapping and invariant assertions fail. No import error.
- [ ] **Step 4: GREEN.** Implement `__post_init__` validation with only local/T2 rules. Do not enforce config-dependent maxima; those belong to the parser/ingestor. Sort nothing in constructors; reject wrong order.
- [ ] **Step 5: GREEN.** Same selector. Expected: all pass.
- [ ] **Step 6: Security/contract checks.** Ensure error messages are fixed field templates and never include the rejected value. Confirm no file/network/subprocess imports. Confirm direct construction of a mismatched issue message raises `RuntimeEvidenceInvariantError`.
- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/models.py tests/test_runtime_evidence_models.py`; `git commit -m "feat: add runtime evidence output record models"`.
- [ ] **Step 8: Independent review checklist.** [ ] Field names/types/order exact. [ ] Output records frozen/slotted. [ ] All 15 issue messages byte-exact. [ ] Frame association shape/candidate ordering/source-hash pairing (`FILE` one, `SYMBOL` two, `AMBIGUOUS_SYMBOL` at least three, no candidates/primary for `NOT_EVALUATED`/`UNASSOCIATED`), nonempty candidate IDs, positive `line`, and nonnegative `column`. [ ] Event tag/frame ordering and duration text. [ ] Exact tuple fields (`tags`, `frames`, `source_names`, `candidate_node_ids`). [ ] Artifact source-name ordering, duplicates, C0/tab, 128-byte boundary, single-backslash rejection, and language rule. [ ] No constructor normalizes or truncates. [ ] No sensitive value appears in invariant messages.

---

### Task 5: Inventory, Immutable Indexes, and Cross-Model Invariants

**Prerequisites:** T4.

**Files:**
- Modify: `backend/runtime_evidence/models.py` (add `RuntimeEvidenceInventory`)
- Create: `backend/runtime_evidence/indexes.py`
- Test: `tests/test_runtime_evidence_models.py`

**Spec references:** §6.4, §7.3, §8.1-§8.4, §12, §13.1.

**Interfaces:**
- Consumes: all T3/T4 records; validation helpers.
- Produces `RuntimeEvidenceInventory` with the exact §6.4 fields/order and `SCHEMA_VERSION = "tracerag-runtime-evidence-v1"`.
- Produces from `indexes.py`: internal `InventoryIndexes` (five `MappingProxyType` fields in inventory order) and:
  - `build_inventory_indexes(artifacts: tuple[RuntimeArtifactRecord, ...], events: tuple[RuntimeEvent, ...], frames: tuple[RuntimeStackFrame, ...]) -> InventoryIndexes`
  - `validate_inventory(inventory: RuntimeEvidenceInventory) -> None`
- `__post_init__` validates the top-level namespace as a nonempty scalar-only string; claim strings as scalar-only and NUL-free or `None`; exact `CaptureCompleteness` and `GraphBinding` enum types; exact bools for every flag; exact nonnegative integers (never bools) for every counter; exact tuple shapes for authoritative records and reasons; unique/sorted/derived incomplete reasons; and every index. It copies each index into a fresh dict and exposes `MappingProxyType`; values are tuples. It rejects non-mappings, non-string keys, non-tuple values, surrogate keys/values, abnormal ordering, and indexes that differ from the exact filter of authoritative tuples.
- `validate_inventory` revalidates the same top-level scalar, enum, bool, counter, and tuple rules, then enforces every §8.3 formula derivable without the effective config, plus event/frame chronology, the full T4 frame invariants including the exact association/path-basis matrix (even for constructor-bypassed frame values), source-path pairing, issue ordering/deduplication, sentinel/truncation consistency, derived incomplete reasons, and index exactness. The config-dependent ordinary-issue cap and `max_issues + 1` total bound are enforced by the ingestor in T17, because the inventory does not retain config. It also enforces §8.1 ordering everywhere; T19 owns tests for shuffled parse inputs but the constructor contract is fixed here.
- The five indexes: `events_by_artifact` includes every artifact including empties; `events_by_trace_id` every non-null trace; `events_by_fingerprint` every fingerprint; `frames_by_file` every non-null path including unverified/ambiguous-suffix paths; `frames_by_node` only FILE/SYMBOL primaries.
- Index keys are lexically sorted; values preserve global authoritative order. `frames` is exactly the event-order/ordinal flattening.

- [ ] **Step 1: Scaffold** the inventory dataclass, empty index builder, and no-op validator.
- [ ] **Step 2: Write failing tests.** Add a builder `_inventory(...)` with valid one-artifact/one-event/one-frame defaults and helpers that recompute indexes. Tests:

```python
from dataclasses import FrozenInstanceError, fields
from types import MappingProxyType
from backend.runtime_evidence.indexes import build_inventory_indexes


def test_inventory_field_order_schema_and_frozen_slotted():
    inventory = _inventory()
    inventory_type = type(inventory)
    assert inventory_type.SCHEMA_VERSION == "tracerag-runtime-evidence-v1"
    assert [field.name for field in fields(inventory_type)] == [
        "repository_namespace", "declared_revision", "declared_captured_at",
        "declared_capture_completeness", "graph_binding", "event_text_retained",
        "artifacts", "events", "frames", "issues", "submitted_artifacts",
        "duplicate_artifacts", "total_input_bytes", "total_events",
        "total_frames", "text_truncated", "events_truncated",
        "frames_truncated", "issues_truncated", "ingestion_complete",
        "incomplete_reasons", "events_by_artifact", "events_by_trace_id",
        "events_by_fingerprint", "frames_by_file", "frames_by_node"]
    assert not hasattr(inventory, "__dict__")
    with pytest.raises(FrozenInstanceError):
        inventory.repository_namespace = "x"


def test_inventory_indexes_are_exact_filters_and_read_only():
    inv = _inventory()
    assert isinstance(inv.events_by_artifact, MappingProxyType)
    assert inv.events_by_artifact == { _artifact_record().artifact_id: (inv.events[0],) }
    for mapping in (inv.events_by_artifact, inv.events_by_trace_id,
                    inv.events_by_fingerprint, inv.frames_by_file,
                    inv.frames_by_node):
        with pytest.raises(TypeError):
            mapping["x"] = ()


def test_inventory_counter_formulas_and_derived_completeness():
    inv = _inventory()
    assert inv.submitted_artifacts == len(inv.artifacts) + inv.duplicate_artifacts
    assert inv.total_events == len(inv.events) == sum(a.event_count for a in inv.artifacts)
    assert inv.total_frames == len(inv.frames) == sum(len(e.frames) for e in inv.events)
    assert inv.ingestion_complete == (inv.incomplete_reasons == ())


def test_inventory_rejects_wrong_count_and_index_snapshot():
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(total_events=2)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(events_by_artifact={})


def test_inventory_authoritative_tuples_are_exact_tuples():
    inventory = _inventory()
    for field in ("artifacts", "events", "frames", "issues",
                  "incomplete_reasons"):
        with pytest.raises(RuntimeEvidenceInvariantError):
            _inventory(**{field: list(getattr(inventory, field))})


def test_inventory_rejects_surrogate_index_key_or_value():
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(events_by_trace_id={"\ud800": ( _event(), )})


def test_inventory_requires_derived_incomplete_reasons_and_flags():
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(incomplete_reasons=(RuntimeIncompleteReason.TEXT_TRUNCATED,))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(text_truncated=True, incomplete_reasons=())


def test_inventory_rejects_invalid_top_level_scalars_enums_bools_and_counts():
    for bad in ("", "\ud800"):
        with pytest.raises(RuntimeEvidenceInvariantError):
            _inventory(repository_namespace=bad)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(declared_revision="rev\x00")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(declared_captured_at="\ud800")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(declared_capture_completeness="unknown")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(graph_binding="not_provided")
    for field in ("event_text_retained", "text_truncated", "events_truncated",
                  "frames_truncated", "issues_truncated", "ingestion_complete"):
        with pytest.raises(RuntimeEvidenceInvariantError):
            _inventory(**{field: 1})
    for field in ("submitted_artifacts", "duplicate_artifacts",
                  "total_input_bytes", "total_events", "total_frames"):
        with pytest.raises(RuntimeEvidenceInvariantError):
            _inventory(**{field: True})
        with pytest.raises(RuntimeEvidenceInvariantError):
            _inventory(**{field: -1})
```

Add negative cases for: artifact ordering, event sequence gap, frame offset mismatch, frame event mismatch, issue order, duplicate issue key, artifact issue count, primary absent from candidates, `event_text_retained` not exact bool, `frames_by_node` containing a symbol's file candidate, index value reordering, duplicate candidates, mapping with a list value, and an event in an index but absent from `events`. Each must raise `RuntimeEvidenceInvariantError`. Also forge a frame with `object.__new__(RuntimeStackFrame)` plus `object.__setattr__` (valid IDs/ordinal, `relative_path="pkg/a.py"`, matching source hash, `UNVERIFIED_RELATIVE_PATH`, `UNASSOCIATED`), place it in an otherwise valid inventory built through `object.__new__(RuntimeEvidenceInventory)` with copied fields and matching event/index updates, and call `validate_inventory` directly; it must reject the association/path-basis matrix. Repeat with `NOT_EVALUATED` + `UNMATCHED` and a path.

Add this explicit constructor-bypassed shape test:

```python
from backend.runtime_evidence.indexes import validate_inventory
from backend.runtime_evidence.models import (
    FrameAssociation, PathMatchBasis, RuntimeEvent, RuntimeEvidenceInventory,
    RuntimeStackFrame,
)


def _inventory_with_bypassed_frame(association, candidates, primary):
    base = _inventory()
    valid_frame = base.frames[0]
    valid_event = base.events[0]
    forged = object.__new__(RuntimeStackFrame)
    for field in fields(RuntimeStackFrame):
        object.__setattr__(forged, field.name, getattr(valid_frame, field.name))
    object.__setattr__(forged, "event_id", valid_event.event_id)
    object.__setattr__(forged, "relative_path", "pkg/a.py")
    object.__setattr__(forged, "source_reference_sha256", A)
    object.__setattr__(forged, "path_match_basis",
                       PathMatchBasis.EXACT_RELATIVE_PATH)
    object.__setattr__(forged, "line", 1)
    object.__setattr__(forged, "association", association)
    object.__setattr__(forged, "candidate_node_ids", candidates)
    object.__setattr__(forged, "primary_node_id", primary)
    forged_event = object.__new__(RuntimeEvent)
    for field in fields(RuntimeEvent):
        object.__setattr__(forged_event, field.name,
                           getattr(valid_event, field.name))
    object.__setattr__(forged_event, "frames", (forged,))
    indexes = build_inventory_indexes(base.artifacts, (forged_event,), (forged,))
    inventory = object.__new__(RuntimeEvidenceInventory)
    for field in fields(RuntimeEvidenceInventory):
        value = getattr(base, field.name)
        if field.name == "events":
            value = (forged_event,)
        elif field.name == "frames":
            value = (forged,)
        elif field.name in ("events_by_artifact", "events_by_trace_id",
                            "events_by_fingerprint", "frames_by_file",
                            "frames_by_node"):
            value = getattr(indexes, field.name)
        object.__setattr__(inventory, field.name, value)
    return inventory


def test_validate_inventory_rejects_bypassed_frame_shapes():
    for association, candidates, primary in (
        (FrameAssociation.AMBIGUOUS_SYMBOL, ("a", "f"), None),
        (FrameAssociation.FILE, ("a", "f"), "a"),
    ):
        inventory = _inventory_with_bypassed_frame(
            association, candidates, primary)
        with pytest.raises(RuntimeEvidenceInvariantError):
            validate_inventory(inventory)
```

- [ ] **Step 3: RED.** `<RT-TEST> tests/test_runtime_evidence_models.py -x`. Expected: mapping/type/counter assertions fail.
- [ ] **Step 4: GREEN.** Implement index building by filtering authoritative tuples once; sort mapping keys only. Implement `__post_init__` snapshotting and exact comparison through `build_inventory_indexes`. Validate all formulas and orderings with fixed messages. Do not retain a graph object.
- [ ] **Step 5: GREEN.** Same selector. Expected: all pass.
- [ ] **Step 6: Security/contract checks.** Confirm `frames_by_node` uses only `primary_node_id` and that no index is mutable. Confirm no graph reference or graph content field exists.
- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/models.py backend/runtime_evidence/indexes.py tests/test_runtime_evidence_models.py`; `git commit -m "feat: add runtime evidence inventory and indexes"`.
- [ ] **Step 8: Independent review checklist.** [ ] Field order exact and `SCHEMA_VERSION` exact. [ ] Inventory frozen/slotted. [ ] All index filters and empty-key rules. [ ] Exact tuple shapes for authoritative records and reasons. [ ] Constructor-bypassed frame candidate shapes (including `FILE` and `AMBIGUOUS_SYMBOL`) rejected by `validate_inventory`. [ ] Tuple/mapping immutability after attempted mutation. [ ] Issue count/order/dedup invariants. [ ] Incomplete reasons derived and sorted. [ ] No graph retained. [ ] Direct invalid construction raises `RuntimeEvidenceInvariantError`.

---

### Task 6: Canonical Identities and Fingerprints

**Prerequisites:** T4 (uses frame/tag records for fingerprint material).

**Files:**
- Create: `backend/runtime_evidence/identities.py`
- Test: `tests/test_runtime_evidence_identities.py`

**Spec references:** §4, §7.1, §10.1.

**Interfaces:**
- Consumes: enums/models plus `RuntimeEvidenceInvariantError`; `hashlib.sha256`.
- Produces from `identities.py`:
  - `artifact_id(repository_namespace: str, artifact_format: RuntimeArtifactFormat, declared_language: RuntimeFrameLanguage | None, content_sha256: str) -> str`
  - `event_id(repository_namespace: str, artifact_id: str, sequence: int) -> str`
  - `frame_id(repository_namespace: str, event_id: str, ordinal: int) -> str`
  - `source_reference_sha256(relative_path: str) -> str`
  - `event_fingerprint(*, repository_namespace: str, kind: RuntimeEventKind, timestamp_text: str | None, level: str | None, message: str | None, logger: str | None, operation: str | None, trace_id: str | None, span_id: str | None, parent_span_id: str | None, duration_ms_text: str | None, status: str | None, error_type: str | None, error_message: str | None, tags: tuple[RuntimeTag, ...], frames: tuple[RuntimeStackFrame, ...]) -> str`
- Canonical serialization is `json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")`; no sort_keys (array order is normative), no `surrogatepass`, no normalization. On non-scalar material raise `RuntimeEvidenceInvariantError("invalid runtime evidence identity material")` without the value.
- Tags are sorted by key and frames by ordinal before fingerprint payload construction. `relative_path` is `None` for non-path frames; source hash is included as given.
- Pinned test vectors (use exactly these):
  - `content_sha256 = sha256(b"hello\\n").hexdigest() = 5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03`
  - artifact ID `09f8483cbc155fd3e52bf5d82de4af300c99ffac76212b1f2f73f739c7f89993`
  - event ID `c34329c46e8733344d378749ce8f3244b367a9be403f4b8b1b8f321eabbf93ec`
  - frame ID `8abaeb036ef874fb9c03e109cc7883ddf0ec09d9cb6fc846448d27f264a4010d`
  - source-reference hash for `pkg/app.py` `815183491aa22012907fbd76531faaeaab8c5b829f7206b49c7838928994a0a9`
  - fingerprint for namespace `repo`, kind `log`, level `INFO`, message `hello`, all other fields `None`, empty tags/frames: `af9aeed9c7f817b89c5caa7a062d591875a091907b93fa8aa0ab8b02d024ef82`

- [ ] **Step 1: Scaffold** each function returning `""`.
- [ ] **Step 2: Write failing tests**:

```python
import hashlib
import json
import pytest
from backend.runtime_evidence.exceptions import RuntimeEvidenceInvariantError
from backend.runtime_evidence.identities import (
    artifact_id, event_id, event_fingerprint, frame_id, source_reference_sha256,
)
from backend.runtime_evidence.models import (
    FrameAssociation, PathMatchBasis, RuntimeArtifactFormat, RuntimeEventKind,
    RuntimeFrameLanguage, RuntimeStackFrame, RuntimeTag,
)


CONTENT_SHA = hashlib.sha256(b"hello\n").hexdigest()
ARTIFACT = "09f8483cbc155fd3e52bf5d82de4af300c99ffac76212b1f2f73f739c7f89993"
EVENT = "c34329c46e8733344d378749ce8f3244b367a9be403f4b8b1b8f321eabbf93ec"
FRAME = "8abaeb036ef874fb9c03e109cc7883ddf0ec09d9cb6fc846448d27f264a4010d"


def _frame(ordinal):
    return RuntimeStackFrame(
        frame_id=("1" if ordinal == 0 else "2") * 64,
        event_id="c" * 64,
        ordinal=ordinal,
        language=RuntimeFrameLanguage.UNKNOWN,
        function_text=None,
        relative_path=None,
        line=None,
        column=None,
        source_reference_sha256=None,
        path_match_basis=PathMatchBasis.UNMATCHED,
        association=FrameAssociation.UNASSOCIATED,
        primary_node_id=None,
        candidate_node_ids=(),
    )


def test_pinned_artifact_event_and_frame_ids():
    assert artifact_id("repo", RuntimeArtifactFormat.TEXT_LOG, None, CONTENT_SHA) == ARTIFACT
    assert event_id("repo", ARTIFACT, 0) == EVENT
    assert frame_id("repo", EVENT, 0) == FRAME


def test_pinned_source_reference_hash_and_fingerprint():
    assert source_reference_sha256("pkg/app.py") == (
        "815183491aa22012907fbd76531faaeaab8c5b829f7206b49c7838928994a0a9")
    assert event_fingerprint(
        repository_namespace="repo", kind=RuntimeEventKind.LOG,
        timestamp_text=None, level="INFO", message="hello", logger=None,
        operation=None, trace_id=None, span_id=None, parent_span_id=None,
        duration_ms_text=None, status=None, error_type=None,
        error_message=None, tags=(), frames=()) == (
        "af9aeed9c7f817b89c5caa7a062d591875a091907b93fa8aa0ab8b02d024ef82")


def test_identity_payloads_are_exact_canonical_arrays():
    payload = ["tracerag-runtime-artifact-v1", "repo", "text_log", None, CONTENT_SHA]
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert artifact_id("repo", RuntimeArtifactFormat.TEXT_LOG, None, CONTENT_SHA) == expected


def test_unicode_is_preserved_and_lone_surrogates_fail_without_encoding_error():
    assert source_reference_sha256("pkg/é😀.py") == hashlib.sha256(
        json.dumps(["tracerag-runtime-source-reference-v1", "pkg/é😀.py"],
                   ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    with pytest.raises(RuntimeEvidenceInvariantError):
        source_reference_sha256("pkg/\ud800.py")


def _fingerprint(tags, frames):
    return event_fingerprint(
        repository_namespace="repo", kind=RuntimeEventKind.LOG,
        timestamp_text=None, level=None, message=None, logger=None,
        operation=None, trace_id=None, span_id=None, parent_span_id=None,
        duration_ms_text=None, status=None, error_type=None,
        error_message=None, tags=tags, frames=frames,
    )


def test_fingerprint_sorts_tags_and_frames_without_changing_ids():
    tags_forward = (RuntimeTag("a", "1"), RuntimeTag("b", "2"))
    tags_reverse = tuple(reversed(tags_forward))
    frames_forward = (_frame(0), _frame(1))
    frames_reverse = tuple(reversed(frames_forward))

    assert _fingerprint(tags_forward, frames_forward) == _fingerprint(
        tags_reverse, frames_reverse)
    assert artifact_id("repo", RuntimeArtifactFormat.TEXT_LOG, None,
                       CONTENT_SHA) == ARTIFACT
    assert event_id("repo", ARTIFACT, 0) == EVENT
    assert frame_id("repo", EVENT, 0) == FRAME
```

Also add tests that: timestamp bytes only change artifact content hash (not event/frame IDs); graph-independent IDs are identical across calls; `null` optionals serialize as `null`; event IDs distinguish adjacent sequences; frame IDs distinguish adjacent ordinals; fingerprint includes the repository namespace; fingerprint excludes artifact ID/sequence/source line/path basis/association/candidates/flags; no `hash()` or clock is called; `event_fingerprint` rejects a lone surrogate in a field; and the exact canonical JSON payload is asserted for every one of the five identity families.

- [ ] **Step 3: RED.** `<RT-TEST> tests/test_runtime_evidence_identities.py -x`. Expected: empty-string digest assertion failures.
- [ ] **Step 4: GREEN.** Implement a single private canonical-digest helper used by all five functions. Build payloads exactly in §7.1/§10.1 order. Sort fingerprint tags/frames; do not sort other payload arrays. Catch only encoding/type errors and raise the fixed invariant error from `None`.
- [ ] **Step 5: GREEN.** Same selector. Expected: all pinned digests match.
- [ ] **Step 6: Security/contract checks.** `Select-String` finds no `surrogatepass`, time, random, uuid, os, pathlib, logging, or graph import in `identities.py`. Confirm source names and absolute paths never appear in payloads.
- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/identities.py tests/test_runtime_evidence_identities.py`; `git commit -m "feat: add canonical runtime evidence identities"`.
- [ ] **Step 8: Independent review checklist.** [ ] Five pinned vectors exact. [ ] Canonical separators/ensure_ascii/order exact. [ ] No sort_keys or extra transformations. [ ] Tags/frames canonical for fingerprints only. [ ] Valid Unicode preserved; unpaired surrogate fails closed. [ ] IDs contain no source name/order/timestamp/graph input. [ ] No clock/random/hash builtin.

---

### Task 7: Physical Lines, Drafts, and `TEXT_LOG`

**Prerequisites:** T2, T3, T4.

**Files:**
- Create: `backend/runtime_evidence/lines.py`
- Create: `backend/runtime_evidence/drafts.py`
- Create: `backend/runtime_evidence/text_adapter.py`
- Test: `tests/test_runtime_evidence_text.py`

**Spec references:** §6.3, §8.2, §9.1, §9.2, §12.

**Interfaces:**
- `lines.py`:
  - `@dataclass(frozen=True, slots=True) PhysicalLine(number: int, raw: bytes, blank: bool)`
  - `@dataclass(frozen=True, slots=True) LineDecodeResult(text: str | None, issue: RuntimeIssueCode | None)`
  - `split_physical_lines(content: bytes) -> tuple[PhysicalLine, ...]`
  - `decode_physical_line(line: PhysicalLine, max_line_bytes: int) -> LineDecodeResult`
- `split_physical_lines` removes at most one leading UTF-8 BOM from the first line only, splits on LF, drops the final empty split element only when the content ends in LF, removes one trailing CR from every line, and marks blank after those normalizations. Empty content yields `()`. A trailing CR is not retained; a lone CR elsewhere is.
- `decode_physical_line` order is: blank -> `(None, None)` even if overlong; overlong nonblank -> `(None, LINE_TOO_LONG)`; raw NUL -> `(None, NUL_BYTE)`; strict UTF-8 failure -> `(None, INVALID_UTF8)`; otherwise `(text, None)`. Decoded text may contain control characters other than NUL.
- `drafts.py` (internal only):
  - `FrameDraft(language: RuntimeFrameLanguage, function_text: str | None, source_text: str | None, line: int | None, column: int | None)`
  - `IssueDraft(code: RuntimeIssueCode, event_sequence: int | None, source_line: int | None)`
  - `EventDraft(sequence, source_line, kind, timestamp_text, level, message, logger, operation, trace_id, span_id, parent_span_id, duration_ms_text, status, error_type, error_message, tags, frames, text_truncated, frames_truncated)`
  - `ArtifactParseResult(events: tuple[EventDraft, ...], issues: tuple[IssueDraft, ...], text_truncated: bool, frames_truncated: bool, empty_input: bool, event_limit_hit: bool)`
  - All are frozen/slotted with tuple fields. They are not public-package exports.
- `text_adapter.parse_text_log(content: bytes, config: RuntimeEvidenceConfig, max_new_events: int) -> ArtifactParseResult`
- A nonblank valid line becomes `LOG`, exact bounded message, `source_line=physical number`, next sequence. No field guessing. Blank-only/empty content sets `empty_input=True` and yields no events. All-invalid nonblank content sets `empty_input=False`. Invalid lines create one issue with their one-based line and no event. Truncation creates `TEXT_TRUNCATED` for the event's sequence and line. `max_new_events` is the remaining global capacity; if reached, stop before emitting the next event and set `event_limit_hit=True`.

- [ ] **Step 1: Scaffold** the three modules with exact signatures, empty-line results, empty drafts, and no parsing behavior.
- [ ] **Step 2: Write failing tests** in `tests/test_runtime_evidence_text.py` using a small `_config(**kw)` builder:

```python
from backend.runtime_evidence.models import (
    RuntimeArtifactFormat, RuntimeEventKind, RuntimeIssueCode,
)
from backend.runtime_evidence.text_adapter import parse_text_log


def _config(**kw):
    from backend.runtime_evidence.config import RuntimeEvidenceConfig
    values = dict(max_message_bytes=32, max_line_bytes=32)
    values.update(kw)
    return RuntimeEvidenceConfig(**values)


def test_lf_crlf_blank_bom_and_source_lines():
    result = parse_text_log(b"\xef\xbb\xbfone\r\n\r\n \t\r\ntwo\n", _config(), 100)
    assert [(e.sequence, e.source_line, e.message) for e in result.events] == [
        (0, 1, "one"), (1, 4, "two")]
    assert result.empty_input is False


def test_blank_only_and_empty_are_complete_shaped_results():
    for content in (b"", b"\n\t\n  \r\n"):
        result = parse_text_log(content, _config(), 10)
        assert result.events == ()
        assert result.empty_input is True
        assert result.issues == ()


def test_overlong_blank_line_is_ignored_without_issue():
    result = parse_text_log(b" " * 40 + b"\n", _config(max_line_bytes=8), 10)
    assert result.events == () and result.issues == () and result.empty_input is True


def test_invalid_utf8_nul_and_overlong_lines_are_skipped_with_one_based_lines():
    result = parse_text_log(b"ok\n\xff\nno\x00pe\n" + b"x" * 40 + b"\nend\n",
                            _config(max_line_bytes=32), 10)
    assert [e.source_line for e in result.events] == [1, 5]
    assert [(i.code, i.source_line, i.event_sequence) for i in result.issues] == [
        (RuntimeIssueCode.INVALID_UTF8, 2, None),
        (RuntimeIssueCode.NUL_BYTE, 3, None),
        (RuntimeIssueCode.LINE_TOO_LONG, 4, None),
    ]
    assert result.empty_input is False
```

`test_line_failure_precedence` uses `b"\x00" + b"x" * 40` and asserts only `LINE_TOO_LONG`; a within-limit NUL+invalid-UTF8 line asserts `NUL_BYTE`; a within-limit invalid-UTF8 line asserts `INVALID_UTF8`.

`test_controls_and_lone_cr_are_retained_but_nul_is_not` asserts `b"a\rb\x01c\n"` yields message `"a\rb\x01c"`; the NUL line is skipped.

`test_message_truncation_is_codepoint_safe` uses `max_message_bytes=5`, message `"abc😀"`, expects `"ab…"`, event/result `text_truncated=True`, issue code `TEXT_TRUNCATED` with sequence 0 and line 1.

`test_text_log_never_guesses_fields_or_frames` ingests `b"2026-01-01 INFO Traceback (most recent call last):\n"` and asserts `kind is LOG`, `level/timestamp_text/error_type is None`, `frames == ()`, message exact.

`test_event_limit_stops_at_capacity` passes `max_new_events=1` to three valid lines and asserts one event, `event_limit_hit=True`; passing `3` asserts three events and `event_limit_hit=False`.

`test_chunked_utf8_is_decoded_only_as_complete_lines` ingests a valid multibyte character, then a lone continuation byte on the next line, and asserts recovery with line 2 `INVALID_UTF8`.

Also cover UTF-16 BOM input: a bare UTF-16 BOM (`b"\xff\xfe"`) yields `INVALID_UTF8`, while a UTF-16LE payload of ASCII text contains NUL bytes and yields `NUL_BYTE` before UTF-8 decoding. Also cover a single trailing CR, an empty line at EOF, and issue sequence `None` for all line-level failures.

- [ ] **Step 3: RED.** `<RT-TEST> tests/test_runtime_evidence_text.py -x`. Expected: assertion failures for events/lines/issues, no import error.
- [ ] **Step 4: GREEN.** Implement the line splitter/decode precedence exactly, then parse nonblank decoded lines. Use `truncate_utf8_text`; only mark truncation when the boolean is true. Maintain local sequence and stop at `max_new_events` capacity. Do not read files or infer structured fields.
- [ ] **Step 5: GREEN.** Same selector. Expected: all pass.
- [ ] **Step 6: Regression/contract checks.** No I/O/subprocess/network imports. Verify blankness happens before length/NUL/UTF-8 and source lines are one-based. Verify `empty_input` means original content is empty/blank, not "no events".
- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/lines.py backend/runtime_evidence/drafts.py backend/runtime_evidence/text_adapter.py tests/test_runtime_evidence_text.py`; `git commit -m "feat: add bounded text log adapter"`.
- [ ] **Step 8: Independent review checklist.** [ ] BOM/CR/LF semantics exact. [ ] Blank-before-length. [ ] Precedence exact. [ ] One-based issue lines. [ ] No guessing. [ ] UTF-8 truncation safe. [ ] Capacity stops exactly. [ ] Draft tuples immutable.

---

### Task 8: Fixed Stack Grammars

**Prerequisites:** T7 (line/draft helpers) and T3/T4.

**Files:**
- Create: `backend/runtime_evidence/stack_parser.py`
- Test: `tests/test_runtime_evidence_stack.py`

**Spec references:** §9.3 (`error.stack`), §9.4 grammars, §14.3.

**Interfaces:**
- `@dataclass(frozen=True, slots=True) StackParseResult(frames: tuple[FrameDraft, ...], message: str | None, recognized_frame_count: int, function_truncated: bool, frame_limit_hit: bool)`
- `parse_stack_source(text: str, language: RuntimeFrameLanguage, *, max_frames: int, max_function_bytes: int) -> StackParseResult`
- Grammar constants are fixed compiled module regexes; no regex is assembled from artifact text. Supported:
  - Python: `File "source", line N, in function`, including leading indentation; snippets are indented non-frame lines.
  - Java: `at qualified.method(Source.java:N)` or `at qualified.method(Source.java)`; `Native Method` and `Unknown Source` are recognized frames with no source.
  - JavaScript/TypeScript/TSX: `at function (source:N:C)` or `at source:N:C`.
- Java/JS continuation `... N more` is ignored. Chained headers such as `Caused by:`, `Suppressed:`, and `During handling...` remain message lines.
- Python message is the unindented non-frame lines joined by LF; indented code snippets are ignored. Java/JS message is every non-frame/non-continuation line joined by LF. `message=None` if no message lines.
- Raw source text stays in `FrameDraft.source_text` for later normalization. The parser never drops a recognized frame for an unsafe/over-limit source.
- Function text is bounded with `truncate_utf8_text`; any truncation sets `function_truncated=True`. Recognized frames beyond `max_frames` are omitted and set `frame_limit_hit=True`. `recognized_frame_count` counts all recognized frames before the cap. Frames preserve reported order; language is copied into every draft.
- The parser does not handle NUL, surrogates, byte caps, events, or issues; callers own those rules.

- [ ] **Step 1: Scaffold** `StackParseResult`, `parse_stack_source` returning an empty result, and placeholder regex constants.
- [ ] **Step 2: Write failing tests** in `tests/test_runtime_evidence_stack.py`:

```python
import pytest
from backend.runtime_evidence.models import RuntimeFrameLanguage
from backend.runtime_evidence.stack_parser import parse_stack_source


def test_python_grammar_and_messages():
    text = (
        "Traceback (most recent call last):\n"
        '  File "pkg/app.py", line 12, in run\n'
        "    helper()\n"
        "ValueError: bad\n"
        "During handling of the above exception, another exception occurred:\n"
        '  File "pkg/other.py", line 3, in helper\n'
    )
    result = parse_stack_source(text, RuntimeFrameLanguage.PYTHON,
                                max_frames=10, max_function_bytes=100)
    assert [(f.source_text, f.line, f.function_text) for f in result.frames] == [
        ("pkg/app.py", 12, "run"), ("pkg/other.py", 3, "helper")]
    assert result.message == (
        "Traceback (most recent call last):\n"
        "ValueError: bad\n"
        "During handling of the above exception, another exception occurred:")


@pytest.mark.parametrize("language", [RuntimeFrameLanguage.JAVA,
                                      RuntimeFrameLanguage.JAVASCRIPT,
                                      RuntimeFrameLanguage.TYPESCRIPT,
                                      RuntimeFrameLanguage.TSX])
def test_java_and_v8_grammars(language):
    if language is RuntimeFrameLanguage.JAVA:
        text = ("Caused by: java.lang.IllegalStateException: x\n"
                "\tat a.b.C.run(C.java:7)\n"
                "\tat a.b.C.other(C.java)\n"
                "\tat a.b.C.native(Native Method)\n"
                "\t... 12 more")
    else:
        text = ("Error: x\n"
                "    at run (webpack:///src/app.js:4:9)\n"
                "    at src/other.js:8:2\n")
    result = parse_stack_source(text, language, max_frames=10,
                                max_function_bytes=100)
    assert len(result.frames) == (3 if language is RuntimeFrameLanguage.JAVA else 2)
    assert result.message.startswith(("Caused by:", "Error:"))
    assert all("... 12 more" not in (f.function_text or "") for f in result.frames)
```

Additional tests:
- `test_java_native_and_unknown_source_are_no_source_frames`.
- `test_function_is_none_when_v8_omits_it`.
- `test_python_indented_snippets_are_ignored` including a snippet containing the word `File`.
- `test_no_recognized_frames_returns_empty_and_unparseable_input`.
- `test_columns_are_exact_for_v8_and_absent_for_python_java`.
- `test_function_truncation_is_reported_and_codepoint_safe`.
- `test_frame_limit_omits_extra_and_counts_all_recognized`.
- `test_raw_source_is_not_normalized_or_rejected_by_grammar` with `C:\repo\a.py`, `%ZZ`, NUL, and over-limit source; parser retains raw source text and no issue.
- `test_suppressed_and_during_handling_headers_stay_in_same_message`.
- `test_regexes_are_module_constants` by inspecting module-level compiled patterns and confirming no `re.compile` call on an artifact value (source audit plus a monkeypatched compile in T20).

- [ ] **Step 3: RED.** `<RT-TEST> tests/test_runtime_evidence_stack.py -x`. Expected: empty parser result assertions fail.
- [ ] **Step 4: GREEN.** Implement fixed regexes and line classification. Use `.split("\n")` and preserve line text. Treat a single trailing CR as part of the line only if the caller did not normalize it; do not trim arbitrary text. Parse V8 by suffix `:line:column` then optional `function (` prefix. Do not infer function names from Python/Java frames.
- [ ] **Step 5: GREEN.** Same selector. Expected: all pass.
- [ ] **Step 6: Contract checks.** No `eval`, subprocess, importlib, filesystem, or dynamic regex. No source normalization in this file. Confirm all five supported languages share exactly the documented grammar families.
- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/stack_parser.py tests/test_runtime_evidence_stack.py`; `git commit -m "feat: add fixed stack trace grammars"`.
- [ ] **Step 8: Independent review checklist.** [ ] Grammar forms exact. [ ] Python/Java/V8 message/continuation behavior exact. [ ] Raw sources retained. [ ] Function truncation and frame cap exact. [ ] No dynamic regex. [ ] No path or Unicode policy leaked into grammar layer.

---

### Task 9: Bounded JSON Structural Scanner

**Prerequisites:** T2 and T4.

**Files:**
- Create: `backend/runtime_evidence/json_scanner.py`
- Test: `tests/test_runtime_evidence_json.py`

**Spec references:** §9.3, §14.3.

**Interfaces:**
- `class JsonScanError(ValueError): ...` (internal; fixed message, no artifact text).
- `@dataclass(frozen=True, slots=True) JsonScanResult(node_count: int, max_number_token_bytes: int, top_level_kind: str)` where `top_level_kind` is exactly `"object"`, `"array"`, or `"scalar"`.
- `scan_json_structure(text: str) -> JsonScanResult`
- The scanner validates the complete JSON grammar iteratively: one value, only JSON whitespace around it, objects/arrays, commas/colons, strings/escapes, literals, and numbers. It rejects `NaN`, `Infinity`, `-Infinity`, raw control characters in strings, actual surrogate code points in the input text, and incomplete escapes. Escaped `\uXXXX` sequences are syntactically accepted even when unpaired so field-level policy can handle them later.
- Node counting: each object, each array, each object-key occurrence (including duplicates), and each scalar string/number/true/false/null. Punctuation is not a node. Number-token bytes run from optional `-` through exponent, excluding whitespace.
- It does not enforce node/number budgets, detect duplicate keys, materialize values, call `json.loads`, or recurse on input depth. The caller compares the returned counts.
- `JsonScanError` messages are fixed and contain no source text.

- [ ] **Step 1: Scaffold** `JsonScanResult`, `JsonScanError`, and a scanner returning zero counts.
- [ ] **Step 2: Write failing tests** in `tests/test_runtime_evidence_json.py`:

```python
import json

import pytest
from backend.runtime_evidence.json_scanner import JsonScanError, scan_json_structure


@pytest.mark.parametrize("text, nodes, kind, number_bytes", [
    ("{}", 1, "object", 0),
    ('{"a":1}', 3, "object", 1),
    ("[{}]", 2, "array", 0),
    ('{"a":{}}', 3, "object", 0),
    ('[ "x" ]', 2, "array", 0),
    ('{"a":true}', 3, "object", 0),
    ('{"a":false}', 3, "object", 0),
    ("[null]", 2, "array", 0),
    ('{"\\u0061":-1.2e+3}', 3, "object", 7),
    ('{"a":1,"a":2}', 5, "object", 2),
    ("7", 1, "scalar", 1),
])
def test_exact_node_and_number_token_counts(text, nodes, kind, number_bytes):
    result = scan_json_structure(text)
    assert (result.node_count, result.top_level_kind,
            result.max_number_token_bytes) == (nodes, kind, number_bytes)


def test_braces_quotes_and_backslashes_inside_strings_are_not_structure():
    result = scan_json_structure('{"a":"} ] \\\\ \\" {", "b":[1]}')
    assert result.top_level_kind == "object"
    assert result.node_count == 6


@pytest.mark.parametrize("bad", ['{"a":}', '{"a":1,}', '{"a" 1}',
                                 '"\\x"', '"\\u12"', "[1,]",
                                 '{"a":1} trailing', "NaN", "Infinity",
                                 '{"a":"\x01"}'])
def test_invalid_syntax_is_rejected_without_artifact_text(bad):
    with pytest.raises(JsonScanError) as caught:
        scan_json_structure(bad)
    assert "\\x01" not in str(caught.value)


def test_scanner_does_not_materialize_json(monkeypatch):
    monkeypatch.setattr(json, "loads", lambda *a, **k: pytest.fail("materialized"))
    assert scan_json_structure('{"a":[1,2,3]}').node_count == 6


def test_deep_nesting_is_iterative_and_unpaired_escape_is_syntax_only():
    deep = "[" * 5000 + "0" + "]" * 5000
    assert scan_json_structure(deep).node_count == 5001
    assert scan_json_structure('"\\ud800"').node_count == 1
```

Also test exact JSON whitespace, empty input rejection, top-level trailing whitespace, duplicate keys counted twice, escaped key counts as one key, malformed numbers (`01`, `1.`, `.1`, `+1`, `1e`), lowercase-only literals, max number token including sign/dot/exponent, array/object punctuation, and actual `\ud800` in the input text rejected. The fenced `test_scanner_does_not_materialize_json` pins the no-materialization contract; also monkeypatch `json.JSONDecoder` when auditing that no decoder is constructed.

- [ ] **Step 3: RED.** `<RT-TEST> tests/test_runtime_evidence_json.py -x`. Expected: counts/kind/rejection assertions fail.
- [ ] **Step 4: GREEN.** Implement an iterative scanner over `str` with an explicit stack for containers and a small state machine/indices for strings/numbers. Count nodes at token start. Validate exactly four hex digits after `\u`. Raise the fixed `JsonScanError` for every grammar failure. Do not call regexes, `json.loads`, `eval`, or recursion.
- [ ] **Step 5: GREEN.** Same selector. Expected: all pass.
- [ ] **Step 6: Security/contract checks.** No materialization, no dynamic regex, no recursion on input depth, no artifact text in errors. The scanner must not enforce budgets (caller owns precedence).
- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/json_scanner.py tests/test_runtime_evidence_json.py`; `git commit -m "feat: add bounded json structural scanner"`.
- [ ] **Step 8: Independent review checklist.** [ ] Complete grammar, not delimiter balance. [ ] Exact node/number counts for all pinned forms. [ ] Strings/escapes handled. [ ] Deep input iterative. [ ] NaN/infinities rejected. [ ] No materialization or budget side effects. [ ] Errors sanitized.

---

### Task 10: JSON Event Schema and Metadata-Only Projection

**Prerequisites:** T4, T6, T8, T9.

**Files:**
- Create: `backend/runtime_evidence/json_adapter.py` (schema normalization only)
- Test: `tests/test_runtime_evidence_json.py`

**Spec references:** §6.3, §9.3, §12, §14.5.

**Interfaces:**
- `@dataclass(frozen=True, slots=True) NormalizedEvent(draft: EventDraft | None, issues: tuple[IssueDraft, ...])`
- `normalize_event_object(value: object, *, sequence: int, source_line: int | None, config: RuntimeEvidenceConfig, declared_language: RuntimeFrameLanguage | None) -> NormalizedEvent`
- The returned draft uses the exact `EventDraft` fields from T7. An invalid event returns `draft=None` and one `INVALID_EVENT_SCHEMA` issue whose `event_sequence` is `None` and whose `source_line` is the JSONL line or `None` for document mode. Frame-level/truncation issues belong to the valid event and use its sequence and source line.
- Schema support is exactly §9.3. Required fields: `kind`; `message` for log; `operation` for span; `message` or `error` for error. An `error` object is invalid on log/span. `error.frames` (even empty) suppresses `error.stack`. Unknown keys/values are ignored entirely, including surrogate/NUL inspection.
- Non-truncatable strings are bounded exactly: timestamp 128, level 32, logger 256, operation 256, trace/span/parent IDs 256, duration 64, status 32, error type 256 UTF-8 bytes. Over-limit values invalidate the event. `duration_ms` is an exact string matching `^(0|[1-9][0-9]*)(\.[0-9]+)?$` and stays text; its 64-byte bound is checked before schema acceptance.
- `message`, `error.message`, and frame `function` are truncatable. Over-limit truncations set event `text_truncated`, add one `TEXT_TRUNCATED` issue, and use `truncate_utf8_text`; no truncation of any other string.
- Tags: JSON object, <=`max_tags`, string-only values, key/value UTF-8 bounds, scalar-only and NUL-free; sorted by key. No nested values, numbers, or booleans. Over-limit/tag type errors invalidate the event rather than truncating.
- Explicit frames: only under `error`, array of objects, <=`max_frames_per_event`; extra frames omitted, event `frames_truncated=True`, one `FRAME_LIMIT_REACHED` issue. Non-object element or non-string `function`/`source` or non-integer/bool `line`/`column` invalidates the event. A NUL/surrogate/over-limit source is retained raw for T13 to mark invalid source, not an event invalidation. `line<1` and `column<0` become `None` with `INVALID_SOURCE_LOCATION`; other integers stay exact. Explicit frames use declared language when known, else `UNKNOWN`.
- `error.stack` rules: if explicit frames key is absent, check language first. Missing/`UNKNOWN` language: retain event, no frames, issue `STACK_LANGUAGE_MISSING`; do not size-check. Known language and over `max_stack_bytes`: retain event, set both text/frames truncation, issues `STACK_TOO_LARGE` and `TEXT_TRUNCATED`, no frames. Known language and within cap: NUL or unpaired surrogate or no recognized frame => `STACK_UNPARSEABLE`; otherwise frames from `parse_stack_source`, plus `TEXT_TRUNCATED`/`FRAME_LIMIT_REACHED` when the parser flags them.
- Metadata-only (`retain_event_text=False`): perform every schema/type/required/NUL/surrogate/tag/non-truncatable bound check first, then set `message`, `error_type`, `error_message`, `logger`, `operation`, tags, and frame function text to `None`/empty. Do this before truncation and fingerprinting. Stack parsing still runs. Omitted text never sets `text_truncated` or `TEXT_TRUNCATED`. Structural stack-size truncation still does.
- A NUL or unpaired surrogate in any other retained event string or frame function invalidates the event; valid surrogate pairs are accepted; a NUL in `error.stack` makes it unparseable.

- [ ] **Step 1: Scaffold** `NormalizedEvent`, `normalize_event_object` returning `NormalizedEvent(None, ())`, and helpers.
- [ ] **Step 2: Write failing tests.** In `tests/test_runtime_evidence_json.py` add:

```python
from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.json_adapter import normalize_event_object
from backend.runtime_evidence.models import (
    RuntimeEventKind, RuntimeFrameLanguage, RuntimeIssueCode,
)


def _config(**kw):
    values = dict(max_tags=4, max_tag_key_bytes=8, max_tag_value_bytes=8,
                  max_message_bytes=16, max_error_message_bytes=16,
                  max_stack_bytes=128, max_frames_per_event=4,
                  max_frame_function_bytes=12, retain_event_text=True)
    values.update(kw)
    return RuntimeEvidenceConfig(**values)


def _norm(value, **kw):
    values = dict(sequence=0, source_line=None, config=_config(),
                  declared_language=None)
    values.update(kw)
    return normalize_event_object(value, **values)


def test_log_event_and_duration_are_exact_strings():
    result = _norm({"kind": "log", "message": "hello", "duration_ms": "001"},
                   source_line=7)
    assert result.draft is None
    result = _norm({"kind": "log", "message": "hello", "duration_ms": "1.20"},
                   source_line=7)
    assert result.draft.message == "hello"
    assert result.draft.duration_ms_text == "1.20"
    assert result.draft.source_line == 7


def test_required_fields_and_error_only_on_error():
    for value in ({"kind": "log"},
                  {"kind": "span"},
                  {"kind": "error"},
                  {"kind": "log", "message": "x", "error": {}},
                  {"kind": "span", "operation": "op", "error": {}}):
        assert _norm(value).draft is None
    assert _norm({"kind": "error"}).draft is None
    assert _norm({"kind": "error", "error": {}}).draft is not None


def test_numeric_strings_do_not_satisfy_int_fields_and_bool_is_not_int():
    for line in ("1", True, 1.0):
        assert _norm({"kind": "error", "message": "x",
                      "error": {"frames": [{"line": line}]}}).draft is None


def test_numeric_metadata_never_becomes_duration_text():
    result = _norm({"kind": "log", "message": "x", "duration_ms": 1})
    assert result.draft is None


def test_duration_bound_is_exact_utf8_bytes():
    assert _norm({"kind": "log", "message": "x",
                  "duration_ms": "1" * 64}).draft.duration_ms_text == "1" * 64
    assert _norm({"kind": "log", "message": "x",
                  "duration_ms": "1" * 65}).draft is None


def test_tags_are_sorted_and_metadata_only_omits_them():
    result = _norm({"kind": "log", "message": "x",
                    "tags": {"b": "2", "a": "1"}})
    assert [t.key for t in result.draft.tags] == ["a", "b"]
    projected = _norm({"kind": "log", "message": "x",
                       "tags": {"a": "1"}},
                      config=_config(retain_event_text=False)).draft
    assert projected.message is None and projected.tags == ()
    assert projected.text_truncated is False
    assert all(i.code is not RuntimeIssueCode.TEXT_TRUNCATED
               for i in _norm({"kind": "log", "message": "x" * 100},
                              config=_config(retain_event_text=False)).issues)


def test_explicit_frames_precede_stack_and_retain_invalid_locations():
    value = {"kind": "error", "error": {
        "frames": [{"function": "f", "source": "a.py", "line": 0,
                    "column": -2}],
        "stack": "this would be unparseable"}}
    result = _norm(value)
    assert len(result.draft.frames) == 1
    assert result.draft.frames[0].line is None
    assert result.draft.frames[0].column is None
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.INVALID_SOURCE_LOCATION]


def test_stack_language_precedes_size_and_parse():
    big = "x" * 200
    result = _norm({"kind": "error", "error": {"stack": big}},
                   declared_language=None)
    assert result.draft is not None and result.draft.frames == ()
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.STACK_LANGUAGE_MISSING]


def test_stack_size_truncation_sets_both_flags_and_issues():
    result = _norm({"kind": "error",
                    "error": {"stack": '  File "a.py", line 1, in f\n' + "x" * 200}},
                   declared_language=RuntimeFrameLanguage.PYTHON)
    assert result.draft.frames == ()
    assert result.draft.text_truncated is True
    assert result.draft.frames_truncated is True
    assert [i.code for i in result.issues] == [
        RuntimeIssueCode.STACK_TOO_LARGE, RuntimeIssueCode.TEXT_TRUNCATED]


def test_unknown_keys_are_not_inspected_for_nul_or_surrogates():
    value = {"kind": "log", "message": "x", "unknown": "\x00\ud800"}
    assert _norm(value).draft is not None
```

Add tests for every field/type/bound, tag count/type and surrogate/NUL keys/values, frame non-object/non-string/bool fields, per-event frame cap, frame function truncation, message/error-message truncation, stack no-frame/NUL/surrogate/valid-pair cases, explicit empty frames suppressing stack, unknown stack ignored when frames exist, tags required list? (tags is object), metadata projection retaining trace/span IDs/status/timestamps/levels and association-relevant frame fields, and a metadata-only over-limit non-truncatable field still invalidating.

`test_structural_budget_messages_are_fixed` asserts a normalization/schema issue for an oversized structural input will use `issue_message`, never parser text or the numeric value. Budget selection itself is T11.

- [ ] **Step 3: RED.** `<RT-TEST> tests/test_runtime_evidence_json.py -x`. Expected: empty-result assertions fail.
- [ ] **Step 4: GREEN.** Implement field validators in spec order: type/required -> scalar/NUL -> bounds -> tags -> frames/stack -> truncation/projection -> flags/issues. Never coerce numbers to strings or bool to int. Preserve tags sorted by key and frames reported order. Use a local issue helper that always derives the fixed message.
- [ ] **Step 5: GREEN.** Same selector. Expected: all pass.
- [ ] **Step 6: Security/contract checks.** Confirm unknown values are never traversed. Confirm metadata-only never truncates omitted fields and still parses stack. Confirm fixed issue messages and no raw field text in errors.
- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/json_adapter.py tests/test_runtime_evidence_json.py`; `git commit -m "feat: add bounded json event schema normalization"`.
- [ ] **Step 8: Independent review checklist.** [ ] Every §9.3 field/type/required rule. [ ] Error-only-on-error. [ ] Stack-language/size/parse precedence. [ ] Explicit frames precedence. [ ] Metadata projection order. [ ] Tags bounds/types/sort. [ ] NUL/surrogate routing. [ ] Numeric strings unchanged. [ ] Fixed messages.

---

### Task 11: JSON Document/JSONL Selection and Recoverable Parsing

**Prerequisites:** T7, T9, T10.

**Files:**
- Modify: `backend/runtime_evidence/lines.py` (add `strip_utf8_bom(content: bytes) -> bytes`)
- Modify: `backend/runtime_evidence/json_adapter.py`
- Test: `tests/test_runtime_evidence_json.py`

**Spec references:** §9.1, §9.3, §12, §13.2, §17.5.

**Interfaces:**
- `parse_json_events(content: bytes, config: RuntimeEvidenceConfig, declared_language: RuntimeFrameLanguage | None, max_new_events: int) -> ArtifactParseResult`
- Internal test seam: `json_adapter.py` imports standard-library `json` at module scope and materializes through `json.loads` only (using local guarded-hook arguments). T22 patches `json_adapter.json.loads` to count document and JSONL materializations.
- Document parsing calls `json.loads` only after strict UTF-8 decode, complete structural scan, and budget checks. Materialization uses a private `object_pairs_hook` that raises on any duplicate key and `parse_constant` that rejects NaN/Infinity. Recursion, decoder, numeric-conversion, and parser-limit failures become recoverable `MALFORMED_JSON`, never fatal.
- Selection order:
  1. Empty/blank-only content -> JSONL empty/complete result, `empty_input=True`.
  2. Strip one BOM and strict-decode all content.
  3. Run `scan_json_structure`; only a syntactically valid, exact one-value top-level object or array selects document mode.
  4. Any decode/scan failure, top-level scalar, or trailing token selects JSONL mode.
  5. For a selected document, enforce `max_json_nodes`/`max_json_number_bytes` before `json.loads`. Over-budget is a whole-document `MALFORMED_JSON` failure with no JSONL fallback.
  6. Duplicate keys in a selected document fail the whole document with `DUPLICATE_JSON_KEY`; they never split into lines. JSONL duplicate keys fail only that line.
- Document mode: object -> one event attempt; array -> one event attempt per element in order; `source_line=None`; non-object array elements are invalid-event issues with `event_sequence=None`/`source_line=None` and produce no event. Events use sequence order among emitted events.
- JSONL mode: split physical lines, skip blank before line budget, apply overlong/NUL/strict-UTF-8 checks in that order, then structural syntax/budget, duplicate, shape/schema. Overlong/nonblank -> `LINE_TOO_LONG`; raw NUL -> `NUL_BYTE`; invalid UTF-8 -> `INVALID_UTF8`; syntax/budget/resource -> `MALFORMED_JSON`; valid non-object -> `INVALID_EVENT_SCHEMA`; malformed line issues carry one-based physical `source_line`.
- JSONL node budgets are independently per line; aggregate node counts are never compared with `max_json_nodes`. A failed line never stops later lines. `max_new_events` stops only before emitting another valid event and sets `event_limit_hit=True`.
- `ArtifactParseResult.text_truncated`/`frames_truncated` are the OR of emitted event flags. `empty_input` is true only when the original content has no nonblank physical line. Per-artifact status is computed by T16 from events, issues, and flags, not here.
- No document/JSONL selector argument exists.

- [ ] **Step 1: Scaffold** `parse_json_events` returning an empty result and `strip_utf8_bom`.
- [ ] **Step 2: Write failing tests.** Add:

```python
from backend.runtime_evidence.json_adapter import parse_json_events


def test_single_line_object_is_document_mode_with_no_line_provenance():
    result = parse_json_events(b'{"kind":"log","message":"x"}', _config(), None, 10)
    assert len(result.events) == 1
    assert result.events[0].source_line is None
    assert result.empty_input is False


def test_document_array_empty_array_and_non_object_elements():
    result = parse_json_events(
        b'[{"kind":"log","message":"a"}, 3, {"kind":"log","message":"b"}]',
        _config(), None, 10)
    assert [e.message for e in result.events] == ["a", "b"]
    assert [(i.code, i.event_sequence, i.source_line) for i in result.issues] == [
        (RuntimeIssueCode.INVALID_EVENT_SCHEMA, None, None)]
    empty = parse_json_events(b"[]", _config(), None, 10)
    assert empty.events == () and empty.empty_input is False


def test_empty_and_blank_only_jsonl_are_complete_shaped():
    for content in (b"", b"\n \t\n"):
        result = parse_json_events(content, _config(), None, 10)
        assert result.events == () and result.empty_input is True


def test_malformed_balanced_documents_fall_back_to_jsonl():
    for bad in (b'{"kind":"log","message":"ok","x":"\\x"}\n',
                b'{"kind":"log","message":"ok" "x":1}\n',
                b'{"kind":"log","message":"ok",}\n'):
        result = parse_json_events(bad, _config(), None, 10)
        assert result.events == ()
        assert [(i.code, i.source_line) for i in result.issues] == [
            (RuntimeIssueCode.MALFORMED_JSON, 1)]


def test_balanced_invalid_document_with_recoverable_object_line():
    content = (b'{"kind":"log","message":"ok",}\n'
               b'{"kind":"log","message":"saved"}\n')
    result = parse_json_events(content, _config(), None, 10)
    assert [e.message for e in result.events] == ["saved"]
    assert result.events[0].source_line == 2
    assert [(i.code, i.source_line) for i in result.issues] == [
        (RuntimeIssueCode.MALFORMED_JSON, 1)]


def test_over_budget_document_fails_whole_and_does_not_fall_back():
    content = b'[' + b",".join([b"0"] * 8) + b"]"
    result = parse_json_events(content, _config(max_json_nodes=5), None, 10)
    assert result.events == ()
    assert [(i.code, i.source_line) for i in result.issues] == [
        (RuntimeIssueCode.MALFORMED_JSON, None)]


def test_aggregate_jsonl_nodes_may_exceed_document_budget():
    content = b"\n".join([b'{"kind":"log","message":"x"}'] * 5)
    result = parse_json_events(content, _config(max_json_nodes=5), None, 10)
    assert len(result.events) == 5


def test_over_budget_jsonl_line_recovers_later_event():
    content = (b'[0,1,2,3,4,5]\n'
               b'{"kind":"log","message":"saved"}\n')
    result = parse_json_events(content, _config(max_json_nodes=5), None, 10)
    assert [e.message for e in result.events] == ["saved"]
    assert [(i.code, i.source_line) for i in result.issues] == [
        (RuntimeIssueCode.MALFORMED_JSON, 1)]


def test_jsonl_failure_precedence_and_one_based_lines():
    content = (b'{"kind":"log","message":"ok"}\n'
               b'\x00' + b"x" * 40 + b'\n'
               b'no\x00\n'
               b'\xff\n'
               b'{"kind":"log"}\n')
    result = parse_json_events(content, _config(max_line_bytes=32), None, 10)
    assert [(i.code, i.source_line) for i in result.issues] == [
        (RuntimeIssueCode.LINE_TOO_LONG, 2),
        (RuntimeIssueCode.NUL_BYTE, 3),
        (RuntimeIssueCode.INVALID_UTF8, 4),
        (RuntimeIssueCode.INVALID_EVENT_SCHEMA, 5),
    ]
```

`test_invalid_utf8_whole_candidate_recovers_valid_object_line` builds an invalid UTF-8 line plus a valid object line, asserts the valid event and one-based `INVALID_UTF8`, then removes the object line and asserts no events. `test_jsonl_without_a_qualifying_object_reports_each_line_and_fails` uses scalar/array/malformed lines and asserts `INVALID_EVENT_SCHEMA` for valid scalars/arrays and `MALFORMED_JSON` for malformed lines, all one-based. `test_duplicate_key_precedence` asserts a document array with one duplicate fails the whole document (no events) while a JSONL duplicate skips only its line. `test_recoverable_jsonl_syntax_and_resource_failures` covers trailing commas/missing commas/invalid escapes/NaN/Infinity/oversized numbers and deep recursion with `MALFORMED_JSON`. `test_top_level_scalar_document_is_not_selected` passes a lone scalar and asserts JSONL `INVALID_EVENT_SCHEMA`, not document failure. `test_event_limit_hit` asserts capacity behavior and no later event.

- [ ] **Step 3: RED.** `<RT-TEST> tests/test_runtime_evidence_json.py -x`. Expected: empty results/issues fail.
- [ ] **Step 4: GREEN.** Implement selection in the exact order above. For document mode, check scan counts before loading, then load once under the guarded hooks. For JSONL, reuse `split_physical_lines`/`decode_physical_line`, scan each decoded line, budget it, then guard-load. Build normalization branches with `normalize_event_object`; do not catch fatal allocator/keyboard errors. Convert only known malformed/parser exceptions to issues.
- [ ] **Step 5: GREEN.** Same selector. Expected: all pass.
- [ ] **Step 6: Contract checks.** Verify document mode never falls back after budget/duplicate, JSONL aggregate nodes are not summed, document mode has `source_line=None`, and every line issue is one-based. Verify no raw parser exception is chained into an issue/public message.
- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/lines.py backend/runtime_evidence/json_adapter.py tests/test_runtime_evidence_json.py`; `git commit -m "feat: add json document and jsonl adapter"`.
- [ ] **Step 8: Independent review checklist.** [ ] Selection rules/fallback exact. [ ] Over-budget no fallback. [ ] Duplicate precedence. [ ] JSONL independent budgets. [ ] Failure precedence. [ ] Document array element scope. [ ] Single-line object `source_line=None`. [ ] No fatal parser exceptions. [ ] Empty/blank/empty-array complete shapes.

---

### Task 12: `STACK_TRACE_TEXT` Blocks and Budgets

**Prerequisites:** T7, T8.

**Files:**
- Modify: `backend/runtime_evidence/stack_parser.py`
- Test: `tests/test_runtime_evidence_stack.py`

**Spec references:** §6.2, §9.1, §9.4, §12, §13.2.

**Interfaces:**
- `parse_stack_trace_text(content: bytes, config: RuntimeEvidenceConfig, declared_language: RuntimeFrameLanguage | None, max_new_events: int) -> ArtifactParseResult`
- `declared_language` must be one of the five known languages. Missing/`UNKNOWN` is impossible after request validation; if called directly it raises `RuntimeEvidenceValidationError` with the fixed message `stack trace text requires a known declared language`.
- Use `split_physical_lines`. A block is a maximal sequence of nonblank physical lines; blank lines separate blocks and are excluded. Each block starts at its first physical line. Empty/blank-only content yields no events, no issues, `empty_input=True`.
- Block byte size is `sum(len(raw) for nonblank lines) + (line_count - 1)` using LF separators after CR/BOM normalization. If the block is over `max_stack_bytes`, select only the longest prefix of complete lines whose cumulative measured bytes (including separators between selected lines) fit. No complete line beginning after the cap is processed. Such a block always produces one event, even with an empty prefix/`message=None`/no frames; set event/result `text_truncated` and `frames_truncated`, report `STACK_TOO_LARGE` and `TEXT_TRUNCATED`, and do not add `STACK_UNPARSEABLE`.
- For non-over-cap blocks, apply line checks before decoding. Overlong/NUL/invalid-UTF-8 lines are skipped with one-based issues. If every nonblank line is unusable, the block produces no event. A block with at least one usable line always produces one event. If it recognizes no frame, retain the event with `STACK_UNPARSEABLE`.
- Event: kind `ERROR`, `message` from non-frame lines joined by LF then bounded to `max_message_bytes` (truncation adds `TEXT_TRUNCATED`), `source_line` first line, frames parsed in reported order. Parser function truncation adds `TEXT_TRUNCATED`; per-event frame cap adds `FRAME_LIMIT_REACHED`; both set the matching event/result flags. All frame-level issues carry the block event's sequence/source line.
- Event capacity stops before another event; `event_limit_hit=True`. Blocks after capacity are not parsed. `empty_input=False` whenever any nonblank physical line exists, even if all are invalid.
- The adapter never normalizes sources, associates frames, builds IDs, or computes statuses.

- [ ] **Step 1: Scaffold** the function returning an empty result.
- [ ] **Step 2: Write failing tests** in `tests/test_runtime_evidence_stack.py`:

```python
import pytest

from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.exceptions import RuntimeEvidenceValidationError
from backend.runtime_evidence.models import (
    RuntimeEventKind, RuntimeFrameLanguage, RuntimeIssueCode,
)
from backend.runtime_evidence.stack_parser import parse_stack_trace_text


def _config(**kw):
    values = dict(max_stack_bytes=256, max_frames_per_event=8,
                  max_message_bytes=64, max_frame_function_bytes=64,
                  max_line_bytes=256)
    values.update(kw)
    return RuntimeEvidenceConfig(**values)


@pytest.mark.parametrize("language", [None, RuntimeFrameLanguage.UNKNOWN])
def test_stack_trace_text_requires_known_declared_language(language):
    with pytest.raises(
            RuntimeEvidenceValidationError,
            match="^stack trace text requires a known declared language$"):
        parse_stack_trace_text(b"", _config(), language, 10)


def test_one_block_is_one_error_event_with_source_line():
    content = (b"Traceback (most recent call last):\n"
               b'  File "a.py", line 3, in f\n'
               b"ValueError: x\n")
    result = parse_stack_trace_text(content, _config(),
                                    RuntimeFrameLanguage.PYTHON, 10)
    assert len(result.events) == 1
    event = result.events[0]
    assert event.kind is RuntimeEventKind.ERROR
    assert event.source_line == 1
    assert len(event.frames) == 1
    assert event.message == "Traceback (most recent call last):\nValueError: x"


def test_blank_line_separates_independent_blocks():
    content = (b'  File "a.py", line 1, in f\n'
               b"\n"
               b'  File "b.py", line 2, in g\n')
    result = parse_stack_trace_text(content, _config(),
                                    RuntimeFrameLanguage.PYTHON, 10)
    assert [(e.source_line, len(e.frames)) for e in result.events] == [(1, 1), (3, 1)]


def test_over_cap_block_retains_one_event_even_with_empty_prefix():
    content = b"x" * 40 + b"\n" + b"second\n"
    result = parse_stack_trace_text(content, _config(max_stack_bytes=10),
                                    RuntimeFrameLanguage.PYTHON, 10)
    assert len(result.events) == 1
    event = result.events[0]
    assert event.message is None and event.frames == ()
    assert event.text_truncated and event.frames_truncated
    assert [(i.code, i.source_line) for i in result.issues] == [
        (RuntimeIssueCode.STACK_TOO_LARGE, 1),
        (RuntimeIssueCode.TEXT_TRUNCATED, 1)]


def test_over_cap_prefix_processes_only_complete_lines():
    content = (b'  File "a.py", line 1, in f\n'
               b'  File "b.py", line 2, in g\n'
               b'  File "c.py", line 3, in h\n')
    cap = 30  # first frame line plus LF, second does not fit
    result = parse_stack_trace_text(content, _config(max_stack_bytes=cap),
                                    RuntimeFrameLanguage.PYTHON, 10)
    assert len(result.events) == 1
    assert [f.source_text for f in result.events[0].frames] == ["a.py"]
    assert result.events[0].frames_truncated is True


def test_unusable_block_produces_no_event_but_mixed_blocks_recover():
    content = b"\xff\n\n" + b'  File "a.py", line 1, in f\n'
    result = parse_stack_trace_text(content, _config(),
                                    RuntimeFrameLanguage.PYTHON, 10)
    assert len(result.events) == 1
    assert [(i.code, i.source_line) for i in result.issues] == [
        (RuntimeIssueCode.INVALID_UTF8, 1)]
    empty = parse_stack_trace_text(b"\xff\n", _config(),
                                   RuntimeFrameLanguage.PYTHON, 10)
    assert empty.events == () and empty.empty_input is False


def test_usable_block_without_frames_retains_event_and_reports_unparseable():
    result = parse_stack_trace_text(b"Error: no frames\n", _config(),
                                    RuntimeFrameLanguage.JAVASCRIPT, 10)
    assert len(result.events) == 1
    assert result.events[0].frames == ()
    assert [(i.code, i.source_line, i.event_sequence) for i in result.issues] == [
        (RuntimeIssueCode.STACK_UNPARSEABLE, 1, 0)]
```

Add tests for: blank-only/empty complete shape; LF vs CRLF byte measurement; a line that itself exceeds `max_line_bytes`; NUL line skipped; no event when all lines unusable; frame cap and function truncation (with issue and event flags); message truncation; multiple blocks preserving order; event cap stopping later blocks; chained `Caused by` remaining one event; valid surrogate not possible in bytes (invalid UTF-8 line handling); source line after skipped invalid lines; and the case where prefix ends exactly at the cap (accepted, not truncated).

- [ ] **Step 3: RED.** `<RT-TEST> tests/test_runtime_evidence_stack.py -x`. Expected: empty-result assertions fail.
- [ ] **Step 4: GREEN.** Build blocks from `PhysicalLine`s. Measure raw bytes before decoding. Compute the longest prefix, decode only selected lines with `decode_physical_line`, convert line issues, then call `parse_stack_source` on the LF-joined usable lines. Ensure over-cap never reports `STACK_UNPARSEABLE`. Enforce message/function text limits and capacity.
- [ ] **Step 5: GREEN.** Same selector. Expected: all pass.
- [ ] **Step 6: Contract checks.** No filesystem/source reads; no path normalization; no event/frame IDs; block bytes use normalized lines only; issues one-based. Confirm over-cap event is never dropped and no later block is parsed after capacity.
- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/stack_parser.py tests/test_runtime_evidence_stack.py`; `git commit -m "feat: add stack trace text adapter"`.
- [ ] **Step 8: Independent review checklist.** [ ] Block/blank semantics. [ ] Exact byte cap/prefix. [ ] Over-cap precedence and one retained event. [ ] No-usable vs unusable. [ ] `STACK_UNPARSEABLE` only for usable non-over-cap blocks. [ ] Frame/message/function limits. [ ] Source lines one-based. [ ] Capacity. [ ] No association/IDs/status.

---

### Task 13: Source-Reference Normalization

**Prerequisites:** T3/T4 and T7 drafts.

**Files:**
- Create: `backend/runtime_evidence/source_references.py`
- Test: `tests/test_runtime_evidence_association.py`

**Spec references:** §10.1, §14.4.

**Interfaces:**
- Internal enum `SourceReferenceKind(str, Enum)`: `NONE`, `RELATIVE`, `BASENAME`, `ABSOLUTE`, `EXTERNAL`, `INVALID`.
- `@dataclass(frozen=True, slots=True) NormalizedSource(kind: SourceReferenceKind, relative_path: str | None, components: tuple[str, ...], basename: str | None)`
- `normalize_source_reference(source: str | None, max_bytes: int) -> NormalizedSource`
- `source=None` -> `NONE`; every invalid form -> `INVALID` with no path/components. The result never retains the raw source or a machine path.
- Exact steps:
  1. Strip surrounding ASCII whitespace. Reject empty, NUL/C0, CR/LF, surrogates, or UTF-8 size over `max_bytes`.
  2. Replace `\` with `/`.
  3. Leading `//` -> `EXTERNAL`.
  4. Leading `/` -> `ABSOLUTE`.
  5. `^[A-Za-z]:/` -> `ABSOLUTE` drive path after removing the drive; `^[A-Za-z]:` -> `INVALID` drive-relative.
  6. Scheme detection `^[A-Za-z][A-Za-z0-9+.-]*:`:
     - `file:` rejects an empty remainder (`file:`), `file:relative`, and bare `file://`. A remainder starting with `//` and a nonempty host (`file://host/...`) is `EXTERNAL`. A remainder starting with `///` has an empty authority: remove exactly the first two slashes, leaving the third slash as the start of the absolute path (`file:///X...` becomes `/X...`). A remainder starting with one `/` is already an absolute path. Percent-decode path bytes with every `%` required as `%HH`, strict UTF-8 decode, normalize `\` to `/` again, reject raw/decoded query/fragment markers, remove one leading `/` before a drive prefix, and reclassify through the UNC/absolute/drive/relative rules. Thus `file:/C:/...` and `file:///C:/...` are drive `ABSOLUTE`; `file:///home/...` is `ABSOLUTE`; `file:////server/...` becomes UNC `EXTERNAL`; and `file:/` or `file:///` has an empty absolute root and is `INVALID`. Never retain a drive/UNC/absolute `file:` path.
     - `webpack:` accepts only `webpack://`; remove scheme plus exactly two leading slashes, then if the remainder starts `//` mark `EXTERNAL`; if it starts `/`, remove exactly one more slash and reclassify through UNC/drive-absolute/drive-relative/absolute/relative rules. Strip repeated `./` from a relative remainder and reject empty.
     - Every other scheme is `EXTERNAL`.
  7. Absolute: split off root/drive, reject empty/`.`/`..` components, keep components/basename for transient suffix matching. Relative: strip repeated `./`; reject empty/`.`/`..`/empty components. No resolution.
  8. Relative no `/` -> `BASENAME`; relative with components -> `RELATIVE` with normalized `relative_path` and components.
- Matching is case-sensitive; no Unicode normalization or case folding. A bare basename is never stored as a path. Percent decoding is path decoding only; no filesystem access/urllib opener.

- [ ] **Step 1: Scaffold** enum/result and a normalizer that maps every non-`None` source to `INVALID`.
- [ ] **Step 2: Write failing tests** in `tests/test_runtime_evidence_association.py`:

```python
import pytest
from backend.runtime_evidence.source_references import (
    SourceReferenceKind, normalize_source_reference,
)


@pytest.mark.parametrize("source, kind, path, components", [
    (None, SourceReferenceKind.NONE, None, ()),
    ("pkg/app.py", SourceReferenceKind.RELATIVE, "pkg/app.py", ("pkg", "app.py")),
    ("pkg//", SourceReferenceKind.INVALID, None, ()),
    ("./pkg/app.py", SourceReferenceKind.RELATIVE, "pkg/app.py", ("pkg", "app.py")),
    ("././pkg/app.py", SourceReferenceKind.RELATIVE, "pkg/app.py",
     ("pkg", "app.py")),
    ("app.py", SourceReferenceKind.BASENAME, None, ("app.py",)),
    ("/home/u/app.py", SourceReferenceKind.ABSOLUTE, None, ("home", "u", "app.py")),
    (r"C:\repo\pkg\app.py", SourceReferenceKind.ABSOLUTE, None,
     ("repo", "pkg", "app.py")),
    (r"C:\repo/pkg\app.py", SourceReferenceKind.ABSOLUTE, None,
     ("repo", "pkg", "app.py")),
    ("C:foo", SourceReferenceKind.INVALID, None, ()),
    ("//server/share/a.py", SourceReferenceKind.EXTERNAL, None, ()),
    ("https://example/a.py", SourceReferenceKind.EXTERNAL, None, ()),
    ("webpack:///src/app.js", SourceReferenceKind.RELATIVE, "src/app.js",
     ("src", "app.js")),
    ("webpack:///./src/app.js", SourceReferenceKind.RELATIVE, "src/app.js",
     ("src", "app.js")),
    ("webpack://C:/repo/a.py", SourceReferenceKind.ABSOLUTE, None,
     ("repo", "a.py")),
    ("webpack:///C:/repo/a.py", SourceReferenceKind.ABSOLUTE, None,
     ("repo", "a.py")),
    ("webpack://C:foo", SourceReferenceKind.INVALID, None, ()),
    ("webpack:////server/share", SourceReferenceKind.EXTERNAL, None, ()),
    ("file:///C:/repo/a.py", SourceReferenceKind.ABSOLUTE, None,
     ("repo", "a.py")),
    ("file:/C:/repo/a.py", SourceReferenceKind.ABSOLUTE, None,
     ("repo", "a.py")),
    ("file://host/share/a.py", SourceReferenceKind.EXTERNAL, None, ()),
    ("file:////server/share/a.py", SourceReferenceKind.EXTERNAL, None, ()),
    ("file:/home/u/a.py", SourceReferenceKind.ABSOLUTE, None,
     ("home", "u", "a.py")),
    ("file:/a%5Cb.py", SourceReferenceKind.ABSOLUTE, None, ("a", "b.py")),
    ("file:///home/u/a%20b.py", SourceReferenceKind.ABSOLUTE, None,
     ("home", "u", "a b.py")),
])
def test_normalization_matrix(source, kind, path, components):
    result = normalize_source_reference(source, 1024)
    assert (result.kind, result.relative_path, result.components) == (
        kind, path, components)


@pytest.mark.parametrize("source", [
    "", "   ", "a//b", "a/./b", "../a", "a/../b", "a/",
    "a\x00b", "a\x01b", "a\ud800b", "file:/a?x",
    "file:/a#x", "file:/bad%2", "file:/bad%ZZ", "file:/bad%FF",
    "file:", "file:/a/%2e%2e/b.py", "file:/", "file://", "file:///", "file:relative",
    "webpack:foo", "webpack://",
])
def test_rejected_sources_are_invalid_without_retaining_text(source):
    result = normalize_source_reference(source, 1024)
    assert result == normalize_source_reference("", 1024)
    assert result.kind is SourceReferenceKind.INVALID


def test_long_surrogate_and_controls_are_invalid_but_interior_tab_is_control():
    assert normalize_source_reference("x" * 1025, 1024).kind is SourceReferenceKind.INVALID
    assert normalize_source_reference("  a/b.py  ", 10).relative_path == "a/b.py"
    assert normalize_source_reference("a\tb.py", 10).kind is SourceReferenceKind.INVALID


def test_case_and_unicode_are_never_folded():
    assert normalize_source_reference("Pkg/É.py", 1024).relative_path == "Pkg/É.py"


def test_source_reference_exact_byte_limit_is_accepted():
    at_limit = "é/" + "b" * 1021
    assert len(at_limit.encode("utf-8")) == 1024
    result = normalize_source_reference(at_limit, 1024)
    assert (result.kind, result.relative_path, result.components) == (
        SourceReferenceKind.RELATIVE, at_limit, ("é", "b" * 1021))
    assert normalize_source_reference(at_limit + "c", 1024).kind is (
        SourceReferenceKind.INVALID)
```

The matrix pins the required exact forms: `%2e%2e` traversal is `INVALID`;
`%5C` is decoded before backslash normalization and reclassified;
`file:`, `file:/`, `file://`, and `file:///` are `INVALID` (`file:/` and
`file:///` reduce to an empty absolute root, whose empty component is invalid);
`file:///home/...` keeps the third slash as the absolute path start and
`file:////server/...` is UNC `EXTERNAL`; `file:relative` is `INVALID`; repeated leading `./`
and `webpack:///./...` are stripped only for a relative remainder; mixed-slash
and `webpack:` drive paths are `ABSOLUTE`; and `file://host/a` and
`webpack:////server/share` remain `EXTERNAL`. The at-limit test above proves
the source-reference byte bound is inclusive on UTF-8 bytes and that one byte
over is rejected.

- [ ] **Step 3: RED.** `<RT-TEST> tests/test_runtime_evidence_association.py -x`. Expected: normalization matrix failures; no import error.
- [ ] **Step 4: GREEN.** Implement fixed classification helpers and percent decoding manually. Ensure `file:` and `webpack:` paths are reclassified exactly, never forced relative. Invalid forms carry no components/path. Do not use `Path`, `os`, `urlopen`, or filesystem calls.
- [ ] **Step 5: GREEN.** Same selector. Expected: all pass.
- [ ] **Step 6: Security/contract checks.** No raw source retained in `NormalizedSource`; no absolute/UNC/drive path in `relative_path`; no `..` resolution; no case/Unicode folding; no filesystem/urllib calls.
- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/source_references.py tests/test_runtime_evidence_association.py`; `git commit -m "feat: add source reference normalization"`.
- [ ] **Step 8: Independent review checklist.** [ ] All step rules and reclassification. [ ] Drive not mistaken for scheme. [ ] `file:`/`webpack:` edge matrix. [ ] Percent/control/surrogate limits. [ ] Basename vs relative. [ ] No leak/resolution/folding. [ ] Exact at-limit acceptance and over-limit rejection.

---

## Actual Module 6 Interface Check

The plan uses only the public Module 6 package. The current committed shapes are:

```python
from backend.code_graph import CodeGraphInventory, GraphNode, GraphNodeKind
```

- `CodeGraphInventory` fields: `repository_namespace`, `nodes`, `edges`,
  `unresolved_references`, `outgoing_edges`, `incoming_edges`,
  `nodes_by_file`, `nodes_by_qualified_name`.
- `GraphNode` fields: `node_id`, `node_kind`, `repository_namespace`,
  `relative_path`, `language`, `parse_status`, `symbol_kind`, `name`,
  `qualified_name`, `location`.
- `GraphNodeKind` is exactly `FILE = "file"` and `SYMBOL = "symbol"`.
- `GraphNode.location` is a Module 3 `SourceLocation` value at runtime, but
  Module 8 must not import Module 3. Graph validation therefore checks the six
  documented, structurally required integer attributes
  (`start_byte`, `end_byte`, `start_line`, `start_column`, `end_line`,
  `end_column`) without importing the class.
- Module 8 must not use `nodes_by_file`, `nodes_by_qualified_name`, `edges`,
  `unresolved_references`, or any Module 6 internal module. Association is built
  from `nodes` only.
- Tests construct graph fixtures with the public `CodeGraphInventory`,
  `GraphNode`, and `GraphNodeKind`; placeholder `language`, `parse_status`,
  `symbol_kind`, and a local six-field location object are sufficient because
  those fields are outside Module 8's association contract. Task 21 exercises
  real Module 2 -> 3 -> 6 values.

## Specification-to-Task Coverage Matrix

| Specification area | Owning tasks |
|---|---|
| §5 public API, exports, redacted repr, instance isolation | T3, T18, T19, T20 |
| §6.1 enums/config/defaults/relations | T2, T3 |
| §6.2 request/artifact/source names/claims/dedup rules | T2, T3, T16 |
| §6.3 records, issue messages, local invariants | T4, T5, T10, T12 |
| §6.4 inventory/schema version/indexes | T5, T19 |
| §7.1 canonical identities/fingerprint | T6 |
| §7.2 provenance | T4, T5, T16 |
| §7.3 dedup/non-dedup/issues | T11, T16, T17, T19 |
| §8.1 ordering | T5, T16, T17, T19 |
| §8.2 statuses/completeness | T7, T10, T11, T12, T16, T17 |
| §8.3 counters/invariants | T5, T16, T17, T19 |
| §8.4 indexes | T5, T15, T19, T22 |
| §9.1 decoding/line/BOM | T7, T11, T12 |
| §9.2 TEXT_LOG | T7 |
| §9.3 JSON/JSONL/schema/stack | T9, T10, T11 |
| §9.4 STACK_TRACE_TEXT | T8, T12 |
| §10.1 source normalization | T13, T15 |
| §10.2 line/column association | T10, T12, T15 |
| §10.3 graph validation/association | T14, T15 |
| §11 ingestion algorithm | T16, T17, T18 |
| §12 resource bounds/scaling | T2, T7, T9-12, T16, T17, T22 |
| §13 fatal/recoverable semantics | T1, T16, T18, T20 |
| §14 security/privacy | T20 |
| §15 integration and import direction | T18, T20, T21 |
| §16 logging | T18, T20 |
| §17.1-§17.11 test strategy | all test-owning tasks |
| §17.12 acceptance gates | T23, T24 |
| §18 non-goals | T23, T24 |
| §20 package layout/README | T1-T18, T23 |
| §21 review target | T24 |

---

### Task 14: Graph Validation and Lookup Index

**Prerequisites:** T3, T4, T13.

**Files:**
- Create: `backend/runtime_evidence/association.py` (graph index types and validation)
- Test: `tests/test_runtime_evidence_association.py`

**Spec references:** §5, §10.3, §12, §14.4, §17.3, §17.7.

**Interfaces:**
- Consumes: `CodeGraphInventory`, `GraphNode`, `GraphNodeKind` from the public Module 6 package; `RuntimeEvidenceConfig`; `require_scalar_text`, `require_relative_posix_path`; `RuntimeEvidenceGraphError`.
- Produces internal frozen/slotted values:
  - `GraphFileEntry(node_id: str, relative_path: str, components: tuple[str, ...])`
  - `GraphSymbolEntry(node_id: str, start_line: int, last_covered_line: int)`
  - `AssociationIndex(files_by_path: Mapping[str, GraphFileEntry], files_by_basename: Mapping[str, tuple[GraphFileEntry, ...]], symbols_by_file: Mapping[str, tuple[GraphSymbolEntry, ...]])`
  - `build_association_index(graph: object, *, repository_namespace: str, max_namespace_bytes: int) -> AssociationIndex | None`
- `None` graph returns `None`. A supplied value must be an exact `CodeGraphInventory`; `type(graph.nodes) is tuple`; every node must be an exact `GraphNode`; every ID is a nonempty scalar string and unique; namespace is nonempty scalar UTF-8 <= `max_namespace_bytes` and equal to the request namespace; every node namespace equals the request namespace.
- Node validation: `node_kind` is exactly `FILE` or `SYMBOL`; every `relative_path` passes canonical relative POSIX validation; FILE nodes have `location is None` and unique paths; SYMBOL nodes have a structurally valid location and exactly one FILE node for the path.
- Structural location validation checks exact integer (not `bool`) `start_byte`, `end_byte`, `start_line`, `start_column`, `end_line`, `end_column`, with `0 <= start_byte <= end_byte`, positive lines, `start_line <= end_line`, nonnegative columns, and same-line `start_column <= end_column`.
- `last_covered_line = end_line` when `end_column > 0`, else `end_line - 1`; it may be zero. Symbols are sorted by `(start_line, last_covered_line, node_id)`; basename buckets are sorted by `(relative_path, node_id)`; both mappings are lexically ordered and their tuples are immutable.
- Every failure raises `RuntimeEvidenceGraphError` with a fixed, content-free message. Graph edges, unresolved references, and public graph mappings are ignored and never revalidated.

- [ ] **Step 1: Scaffold** the three internal values and `build_association_index` returning an empty index for every input.

- [ ] **Step 2: Write failing tests** in `tests/test_runtime_evidence_association.py`:

```python
from types import MappingProxyType, SimpleNamespace

import pytest
import backend.runtime_evidence.association as association
from backend.code_graph import CodeGraphInventory, GraphNode, GraphNodeKind
from backend.runtime_evidence.association import build_association_index
from backend.runtime_evidence.exceptions import RuntimeEvidenceGraphError


def _loc(start_line=1, end_line=1, start_column=0, end_column=0):
    return SimpleNamespace(
        start_byte=0, end_byte=10, start_line=start_line,
        start_column=start_column, end_line=end_line, end_column=end_column)


def _node(node_id, kind, path, *, namespace="repo", location=None):
    return GraphNode(
        node_id=node_id, node_kind=kind, repository_namespace=namespace,
        relative_path=path, language="python", parse_status="success",
        symbol_kind=None, name=None, qualified_name=None, location=location)


def _graph(nodes, *, namespace="repo"):
    return CodeGraphInventory(
        namespace, tuple(nodes), (), (),
        MappingProxyType({}), MappingProxyType({}),
        MappingProxyType({}), MappingProxyType({}))


def test_indexes_exact_file_and_symbol_buckets():
    graph = _graph([
        _node("file-main", GraphNodeKind.FILE, "pkg/main.py"),
        _node("sym-b", GraphNodeKind.SYMBOL, "pkg/main.py",
              location=_loc(8, 20, 0, 0)),
        _node("sym-a", GraphNodeKind.SYMBOL, "pkg/main.py",
              location=_loc(1, 7, 0, 0)),
    ])
    index = build_association_index(graph, repository_namespace="repo",
                                    max_namespace_bytes=256)
    assert index.files_by_path["pkg/main.py"].node_id == "file-main"
    assert [f.node_id for f in index.files_by_basename["main.py"]] == ["file-main"]
    assert [(s.node_id, s.start_line, s.last_covered_line)
            for s in index.symbols_by_file["pkg/main.py"]] == [
        ("sym-a", 1, 6), ("sym-b", 8, 19)]


def test_namespace_multibyte_at_limit_and_mismatch():
    namespace = "é" * 128
    graph = _graph([], namespace=namespace)
    assert build_association_index(
        graph, repository_namespace=namespace, max_namespace_bytes=256)
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace=namespace + "a",
                                max_namespace_bytes=256)
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(graph, repository_namespace=namespace,
                                max_namespace_bytes=255)


def test_graph_failures_are_fixed_and_content_free():
    sentinel = "SENTINEL-PATH"
    graph = _graph([_node("f", GraphNodeKind.FILE, "../" + sentinel)])
    with pytest.raises(RuntimeEvidenceGraphError) as caught:
        build_association_index(graph, repository_namespace="repo",
                                max_namespace_bytes=256)
    assert sentinel not in str(caught.value)
    with pytest.raises(RuntimeEvidenceGraphError):
        build_association_index(object(), repository_namespace="repo",
                                max_namespace_bytes=256)


def test_graph_paths_reject_c0_tab_and_surrogates():
    for path in ("a\x01b", "a\tb", "a\ud800b"):
        graph = _graph([_node("f", GraphNodeKind.FILE, path)])
        with pytest.raises(RuntimeEvidenceGraphError):
            build_association_index(graph, repository_namespace="repo",
                                    max_namespace_bytes=256)
```

Also add tests for: empty graph; duplicate node IDs; empty/surrogate node ID; node namespace mismatch; duplicate FILE paths; FILE node with a location; SYMBOL with `None` or malformed location; SYMBOL path with no FILE; bool/non-int/negative location fields; zero/negative lines; line order and same-line column order; symbol with `end_column=0` producing `last_covered_line=end_line-1`; and over-limit node count not relevant (no node cap). Add a constructor-bypassed `CodeGraphInventory` built with `object.__new__(CodeGraphInventory)` and `object.__setattr__`, with valid namespace, empty edges/unresolved tuples, empty mapping proxies, and `nodes=[_node("file-main", GraphNodeKind.FILE, "pkg/main.py")]` (a list); `build_association_index` must raise `RuntimeEvidenceGraphError` before iterating. Use `object.__new__(GraphNode)` plus `object.__setattr__` only for enum/tuple-shape cases the public constructor rejects. The result must be immutable (`index.files_by_path` is `MappingProxyType`).

- [ ] **Step 3: RED.** Run `<RT-TEST> tests/test_runtime_evidence_association.py -x`. Expected: index/validation assertion failures; no import error.

- [ ] **Step 4: GREEN.** Check `type(graph.nodes) is tuple` before iterating, then implement one validation pass over `graph.nodes`, building dictionaries in linear time, then snapshot them with `MappingProxyType`. Use `require_relative_posix_path(..., error_type=RuntimeEvidenceGraphError)` and a local `_valid_location` helper. Never read `nodes_by_file`, edges, or source files.

- [ ] **Step 5: GREEN command.** Same selector. Expected: all tests pass.

- [ ] **Step 6: Security/contract checks.** Import only `CodeGraphInventory`, `GraphNode`, and `GraphNodeKind` from `backend.code_graph`; no Module 1-5, 7, 9, 10 import. Confirm no filesystem/network/subprocess calls and no sentinel path in any error. Confirm the index is immutable and has no graph reference.

- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/association.py tests/test_runtime_evidence_association.py`; `git commit -m "feat: validate module 6 graphs for association"`.

- [ ] **Step 8: Independent review checklist.** [ ] Exact public graph types and exact tuple `graph.nodes`. [ ] Namespace byte boundary and equality. [ ] IDs/paths/files/symbols validation, including C0/tab/surrogate paths. [ ] Structural location validation. [ ] Inclusive line interval computation. [ ] Deterministic immutable buckets. [ ] No graph internals/edges/source reads. [ ] Fixed content-free errors. [ ] RED/GREEN evidence recorded.

---

### Task 15: Frame Source Matching and Line Association

**Prerequisites:** T6, T7, T13, T14.

**Files:**
- Modify: `backend/runtime_evidence/association.py` (frame association)
- Test: `tests/test_runtime_evidence_association.py`

**Spec references:** §6.3, §8.4, §10.1-§10.3, §12, §17.7.

**Interfaces:**
- Consumes: `FrameDraft`; `normalize_source_reference`; `frame_id`, `source_reference_sha256`; `RuntimeEvidenceConfig`; `AssociationIndex`, `GraphFileEntry`, `GraphSymbolEntry`; `IssueDraft`.
- Produces:
  - `@dataclass(frozen=True, slots=True) AssociationResult(frames: tuple[RuntimeStackFrame, ...], issues: tuple[IssueDraft, ...])`
  - `associate_frames(frames: tuple[FrameDraft, ...], *, event_id: str, event_sequence: int, source_line: int | None, repository_namespace: str, config: RuntimeEvidenceConfig, graph_index: AssociationIndex | None) -> AssociationResult`
  - Internal `match_symbols_by_line(frame_lines: tuple[int, ...], symbols: tuple[GraphSymbolEntry, ...]) -> tuple[tuple[GraphSymbolEntry, ...], ...]` used by the association pass; it sorts frame lines once and sweeps symbols by `start_line`/`last_covered_line`, never comparing every symbol with every frame.
- Matching rules are exactly §10.1/§10.3:
  - no graph: every frame has `association=NOT_EVALUATED`, no candidates/primary; a normalized relative candidate (including an unmatched relative reference) retains its path with `UNVERIFIED_RELATIVE_PATH`; a basename-only or absolute reference retains no path and is `UNMATCHED`; an external reference is `EXTERNAL_OR_NON_FILE` with no path; no-source/invalid is `UNMATCHED` with no path.
  - graph: exact relative path -> `EXACT_RELATIVE_PATH`; unique component-wise suffix -> `UNIQUE_PATH_SUFFIX`; multiple relative-suffix matches -> `AMBIGUOUS` with the normalized relative path retained; no relative-suffix match -> `UNMATCHED` with the normalized relative path retained. A basename-only reference is `UNIQUE_BASENAME` when exactly one graph path has that basename, `AMBIGUOUS` with no path for multiple matches, and `UNMATCHED` with no path for none. A local absolute reference is `UNIQUE_PATH_SUFFIX` with the resolved graph path, `AMBIGUOUS` with no path for multiple suffix matches, and `UNMATCHED` with no path for none. An external/non-file reference is `EXTERNAL_OR_NON_FILE` with no path; no-source/invalid is `UNMATCHED` with no path. Only a path that resolves to a graph FILE can associate; line containment chooses FILE/SYMBOL/AMBIGUOUS_SYMBOL. No line -> FILE. Unknown path -> UNASSOCIATED.
  - Verified path matches (`EXACT_RELATIVE_PATH`, `UNIQUE_PATH_SUFFIX`, `UNIQUE_BASENAME`) retain the resolved relative path and `source_reference_sha256`. A relative candidate under `UNVERIFIED_RELATIVE_PATH` (no graph), `AMBIGUOUS` (multiple relative-suffix matches), or `UNMATCHED` (no relative-suffix match) also retains the normalized relative path and hash as an unverified candidate. Ambiguous basename or ambiguous absolute-suffix matches, external/non-file, invalid, and no-source results retain no path.
  - A line `< 1` becomes `None` with issue `INVALID_SOURCE_LOCATION`; a negative column becomes `None` with the same issue. A frame with an invalid source gets no path, `UNMATCHED`, and one `INVALID_SOURCE_LOCATION` issue.
  - Candidate IDs are unique and sorted; `SYMBOL` has FILE + one symbol with the symbol primary; `AMBIGUOUS_SYMBOL` has FILE + all containing symbols with no primary; `FILE` has only the file node.
- `frame_id` uses the event ID and ordinal. `source_reference_sha256` is present exactly when a path is retained. No raw absolute/UNC/drive path is ever stored.

- [ ] **Step 1: Scaffold** `AssociationResult`, `match_symbols_by_line`, and `associate_frames` returning unattached drafts with no association.

- [ ] **Step 2: Write failing tests.** Add:

```python
from backend.runtime_evidence.association import (
    AssociationIndex, GraphFileEntry, GraphSymbolEntry, associate_frames,
)
from backend.runtime_evidence.drafts import FrameDraft, IssueDraft
from backend.runtime_evidence.models import (
    FrameAssociation, PathMatchBasis, RuntimeFrameLanguage, RuntimeIssueCode,
)


def _draft(source=None, line=None, column=None, function="f"):
    return FrameDraft(RuntimeFrameLanguage.PYTHON, function, source, line,
                      column)


def _assoc(drafts, graph_index, *, namespace="repo", sequence=0,
           source_line=1, config=None):
    from backend.runtime_evidence.config import RuntimeEvidenceConfig
    return associate_frames(
        tuple(drafts), event_id="e" * 64, event_sequence=sequence,
        source_line=source_line, repository_namespace=namespace,
        config=config or RuntimeEvidenceConfig(), graph_index=graph_index)


def _index(files, symbols=()):
    by_path = {f.relative_path: f for f in files}
    by_base = {}
    for f in files:
        by_base.setdefault(f.components[-1], []).append(f)
    by_file = {}
    for s in symbols:
        by_file.setdefault(s[0], []).append(s[2])
    return AssociationIndex(
        MappingProxyType(by_path),
        MappingProxyType({k: tuple(v) for k, v in by_base.items()}),
        MappingProxyType({k: tuple(v) for k, v in by_file.items()}))


def test_no_graph_relative_candidate_is_unverified_and_has_hash():
    result = _assoc([_draft("pkg/app.py", 10)], None)
    frame = result.frames[0]
    assert frame.path_match_basis is PathMatchBasis.UNVERIFIED_RELATIVE_PATH
    assert frame.association is FrameAssociation.NOT_EVALUATED
    assert frame.relative_path == "pkg/app.py"
    assert frame.source_reference_sha256
    assert frame.candidate_node_ids == ()


def test_exact_file_and_symbol_association_and_line_sweep():
    file = GraphFileEntry("file", "pkg/app.py", ("pkg", "app.py"))
    one = GraphSymbolEntry("sym", 10, 20)
    index = _index([file], [(file.relative_path, file, one)])
    result = _assoc([_draft("pkg/app.py", 15)], index)
    frame = result.frames[0]
    assert frame.association is FrameAssociation.SYMBOL
    assert frame.primary_node_id == "sym"
    assert frame.candidate_node_ids == ("file", "sym")
    file_only = _assoc([_draft("pkg/app.py", 21)], index).frames[0]
    assert file_only.association is FrameAssociation.FILE
    assert file_only.primary_node_id == "file"
    assert file_only.candidate_node_ids == ("file",)


def test_exclusive_end_line_and_ambiguous_symbols():
    file = GraphFileEntry("file", "pkg/app.py", ("pkg", "app.py"))
    a = GraphSymbolEntry("a", 10, 19)  # end_line 20, end_column 0
    b = GraphSymbolEntry("b", 15, 20)
    index = _index([file], [(file.relative_path, file, a),
                            (file.relative_path, file, b)])
    at_19 = _assoc([_draft("pkg/app.py", 19)], index).frames[0]
    assert at_19.association is FrameAssociation.AMBIGUOUS_SYMBOL
    assert at_19.primary_node_id is None
    assert at_19.candidate_node_ids == ("a", "b", "file")
    at_20 = _assoc([_draft("pkg/app.py", 20)], index).frames[0]
    assert at_20.association is FrameAssociation.SYMBOL
    assert at_20.primary_node_id == "b"


def test_relative_retention_and_resolved_absolute_and_basename_paths():
    file = GraphFileEntry("file", "pkg/app.py", ("pkg", "app.py"))
    index = _index([file])

    unmatched = _assoc([_draft("other/app.py", 1)], index).frames[0]
    assert unmatched.path_match_basis is PathMatchBasis.UNMATCHED
    assert unmatched.relative_path == "other/app.py"
    assert unmatched.source_reference_sha256
    assert unmatched.association is FrameAssociation.UNASSOCIATED

    absolute = _assoc([_draft("/repo/pkg/app.py", 1)], index).frames[0]
    assert absolute.path_match_basis is PathMatchBasis.UNIQUE_PATH_SUFFIX
    assert absolute.relative_path == "pkg/app.py"
    assert absolute.association is FrameAssociation.FILE

    basename = _assoc([_draft("app.py", 1)], index).frames[0]
    assert basename.path_match_basis is PathMatchBasis.UNIQUE_BASENAME
    assert basename.relative_path == "pkg/app.py"
    assert basename.association is FrameAssociation.FILE

    ambiguous_index = _index([
        GraphFileEntry("file-a", "one/src/app.py", ("one", "src", "app.py")),
        GraphFileEntry("file-b", "two/src/app.py", ("two", "src", "app.py")),
    ])
    ambiguous = _assoc([_draft("src/app.py", 1)], ambiguous_index).frames[0]
    assert ambiguous.path_match_basis is PathMatchBasis.AMBIGUOUS
    assert ambiguous.relative_path == "src/app.py"
    assert ambiguous.source_reference_sha256
    assert ambiguous.association is FrameAssociation.UNASSOCIATED
    assert ambiguous.candidate_node_ids == ()
```

Add tests for: `match_symbols_by_line` boundary lines and repeated lines; unique suffix (`src/app.py` against `pkg/src/app.py`); ambiguous suffix; no suffix; unique/ambiguous/unmatched basename; absolute POSIX and drive suffix; external; invalid/no source; `line=0`, `column=-1`; candidate sorting when file ID sorts after symbol; columns not used; no graph absolute/basename path absence; source hash presence exactly with path; `source_line` passed unchanged into issues; and an invalid source producing one `INVALID_SOURCE_LOCATION` issue.

- [ ] **Step 3: RED.** `<RT-TEST> tests/test_runtime_evidence_association.py -x`. Expected: association/path/ID assertions fail; no import error.

- [ ] **Step 4: GREEN.** Normalize each source once and resolve its path/basis first. Group line-bearing frames by resolved file path, sort their lines, and use one symbol sweep per file. Then build `RuntimeStackFrame` values with `frame_id`, source hash, sorted candidates, and issue drafts. Do not use function names for association and do not re-run source normalization.

- [ ] **Step 5: GREEN command.** Same selector. Expected: all pass.

- [ ] **Step 6: Security/contract checks.** Confirm no absolute/drive/UNC/raw source is stored; no filesystem/Path/urllib use; no source hashes without paths; no graph mutation; no function-name matching; no all-pairs loop. Inspect `match_symbols_by_line` operation shape.

- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/association.py tests/test_runtime_evidence_association.py`; `git commit -m "feat: associate runtime frames by line containment"`.

- [ ] **Step 8: Independent review checklist.** [ ] Exact §10.1/§10.3 bases. [ ] NOT_EVALUATED vs UNASSOCIATED. [ ] Path retention/hash rules. [ ] Exclusive end-line semantics. [ ] File-only and candidate-complete symbol results. [ ] Invalid source/line/column issues. [ ] Sorted unique candidates/primary containment. [ ] Deterministic sweep, no name matching. [ ] No machine-path leak. [ ] RED/GREEN evidence recorded.

---

### Task 16: Ingestion Pipeline and Artifact Records

**Prerequisites:** T5, T6, T7-T15.

**Files:**
- Create: `backend/runtime_evidence/ingestor.py`
- Test: `tests/test_runtime_evidence_models.py`, `tests/test_runtime_evidence_validation.py`
- Test: `tests/test_runtime_evidence_text.py`, `tests/test_runtime_evidence_json.py`
- Test: `tests/test_runtime_evidence_stack.py`, `tests/test_runtime_evidence_association.py`

**Spec references:** §6.2-§6.4, §7.2-§7.3, §8.1-§8.3, §9, §11, §12, §13, §17.1, §17.3.

**Interfaces:**
- Consumes all prior public records, adapters, association, indexes, identities, and `CodeGraphInventory`.
- Produces:
  - `RuntimeEvidenceIngestor.__init__(self, config: RuntimeEvidenceConfig | None = None) -> None`
  - `RuntimeEvidenceIngestor.ingest(self, request: RuntimeEvidenceRequest, graph: CodeGraphInventory | None = None) -> RuntimeEvidenceInventory`
  - Internal `_PreparedArtifact(artifact_id: str, source_names: tuple[str, ...], artifact: RuntimeArtifact, content_sha256: str, duplicate_count: int)` as the canonical deduplicated work item.
  - Internal `_parse_artifact(artifact: RuntimeArtifact, config: RuntimeEvidenceConfig, max_new_events: int) -> ArtifactParseResult`, dispatching only the three declared formats.
  - Internal helpers for envelope validation, source-name conflict detection, canonical sorting, event assembly, issue projection, status derivation, and inventory construction. Their names may be chosen locally but their behavior is fixed below; T17 modifies only the budget/issue-count portions.
- Constructor: `None` means `RuntimeEvidenceConfig()`; any non-`None` value must be an exact `RuntimeEvidenceConfig`, otherwise `RuntimeEvidenceConfigurationError`. No mutable instance state beyond the frozen config.
- Internal hashing seam: `ingestor.py` imports standard-library `hashlib` at module scope and computes content digests only through `hashlib.sha256(content).hexdigest()`. This is the seam patched by the envelope-precedence test.
- `ingest` order is exactly §11 steps 1-11:
  1. Validate exact request/artifact types, namespace effective config bound, exact tuple/bytes, formats, languages, and claims. Request-constructor validation is not bypassed.
  2. Reject submitted-artifact count `> max_artifacts` and submitted bytes `> max_submitted_input_bytes` before hashing.
  3. Hash each submission only after the count/submitted checks; deduplicate by `(format, declared_language, content_sha256)`, retain all distinct source names sorted by `(casefold(), original)`, and count extra submissions.
  4. A source name used with two different dedup keys is `RuntimeEvidenceValidationError` with a fixed message.
  5. Enforce unique total bytes `<= max_total_input_bytes` (sum of one size per deduplicated group).
  6. Build the optional graph index and enforce graph namespace equality before artifact IDs/ordering.
  7. Compute artifact IDs, sort by primary `(casefold(), original)` then `artifact_id`, and process in that order.
  8. Per artifact: if size `> max_artifact_bytes`, emit a `SKIPPED` record with `ARTIFACT_TOO_LARGE`; otherwise dispatch the declared adapter, then normalize/associate frames, compute IDs/fingerprints, and build events/issues.
  9. Project issues to records, deduplicate by `(code, artifact_id, event_sequence, source_line)` keeping first parse-order occurrence, and sort by artifact order, event sequence (absent last), source line (absent last), `code.value`, fixed message.
  10. Build `RuntimeArtifactRecord`s, the flattened frames tuple, and all five indexes.
  11. Copy request claims unchanged, set `event_text_retained=config.retain_event_text`, set graph binding, derive statuses/counters/incomplete reasons, and return the immutable inventory.
- Status baseline for T16 (before T17 budgets): `SKIPPED` only for per-artifact oversize; with no events, zero issues means `COMPLETE`, any issue means `FAILED`; with events, any issue or truncation flag means `PARTIAL`, else `COMPLETE`. Empty `TEXT_LOG`, blank-only input, empty JSON array, and empty/blank JSONL are `COMPLETE`.
- Event assembly uses each draft's zero-based sequence and `artifact_id`, and each frame draft's declared language; `event_fingerprint` is computed from the final event fields/frames after association. Frame drafts are replaced by `RuntimeStackFrame` values. `event_id = event_id(namespace, artifact_id, sequence)`. All records are frozen and all collections are tuples.
- A fatal error returns no inventory. Unexpected internal exceptions are not converted to evidence and will be sanitized in T18.
- T16 must not implement event/frame/issue global budgets (T17), public logging (T18), or redacted `__repr__` (T20).

- [ ] **Step 1: Scaffold** `RuntimeEvidenceIngestor` with an exact request/graph signature, a config-validating constructor, and `ingest` returning a minimal empty inventory only for an empty request. Scaffold `_PreparedArtifact` and `_parse_artifact` with format dispatch names but no parsing.

- [ ] **Step 2: Write failing tests.** In `tests/test_runtime_evidence_validation.py` add:

```python
from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.ingestor import RuntimeEvidenceIngestor
from backend.runtime_evidence.models import (
    RuntimeArtifact, RuntimeArtifactFormat, RuntimeArtifactStatus,
    RuntimeEvidenceRequest, RuntimeFrameLanguage, RuntimeIssueCode,
)


def _artifact(source="ci.log", content=b"ok\n", fmt=RuntimeArtifactFormat.TEXT_LOG,
              language=None):
    return RuntimeArtifact(source, fmt, content, language)


def _request(*artifacts, namespace="repo"):
    return RuntimeEvidenceRequest(namespace, tuple(artifacts))


def test_dedup_retains_all_names_and_counts_duplicate_submissions():
    inv = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("b.log", b"same\n"),
        _artifact("A.log", b"same\n"),
        _artifact("A.log", b"same\n"),
    ))
    assert len(inv.artifacts) == 1
    assert inv.artifacts[0].source_names == ("A.log", "b.log")
    assert inv.artifacts[0].duplicate_count == 2
    assert inv.submitted_artifacts == 3
    assert inv.total_input_bytes == 5
    assert inv.artifacts[0].status is RuntimeArtifactStatus.COMPLETE


def test_same_source_name_with_different_content_is_fatal():
    with pytest.raises(RuntimeEvidenceValidationError):
        RuntimeEvidenceIngestor().ingest(_request(
            _artifact("ci.log", b"one\n"), _artifact("ci.log", b"two\n")))


def test_envelopes_precede_hashing(monkeypatch):
    import backend.runtime_evidence.ingestor as ingestor
    def forbidden(*args, **kwargs):
        raise AssertionError("hash was reached")
    monkeypatch.setattr(ingestor.hashlib, "sha256", forbidden)
    with pytest.raises(RuntimeEvidenceLimitError):
        RuntimeEvidenceIngestor(
            RuntimeEvidenceConfig(max_artifacts=1)).ingest(_request(
                _artifact("a", b"a"), _artifact("b", b"b")))
    with pytest.raises(RuntimeEvidenceLimitError):
        RuntimeEvidenceIngestor(RuntimeEvidenceConfig(
            max_artifact_bytes=3, max_total_input_bytes=3,
            max_submitted_input_bytes=3)).ingest(
                _request(_artifact("a", b"abcd")))
```

Also add tests for: zero artifacts; namespace exactly at the effective config limit and one byte over; unique-total limit after duplicate collapse; wrong request/graph types; source-name order with casefold ties; canonical artifact/event/frame order under shuffled submissions; complete/partial/failed/skipped statuses (mixed invalid text, all-invalid text, empty text, empty JSON array, blank JSONL); exact event and frame counts; issue dedup and `issue_count == len(issues carrying artifact_id)`; `declared_*` copied unchanged; `event_text_retained`; `graph_binding` `NOT_PROVIDED` and `NAMESPACE_MATCHED_UNVERSIONED`; graph namespace mismatch; event/frame IDs stable; frame path/hash/association from a stack artifact; per-artifact oversized skip; and no events for skipped artifacts.

In `tests/test_runtime_evidence_models.py` add a small pipeline test that asserts inventory counters equal the authoritative tuples (`submitted_artifacts`, `total_input_bytes`, `total_events`, `total_frames`) and that all five indexes are immutable snapshots.

- [ ] **Step 3: RED.** Run `<RT-TEST> tests/test_runtime_evidence_validation.py tests/test_runtime_evidence_models.py -x`. Expected: dedup/status/counter assertions fail; no import error.

- [ ] **Step 4: GREEN.** Implement the exact order above. Cache each content SHA once per submission, build groups keyed by `(format, language, sha)`, then discard per-submission temporary data except the group’s exact bytes/content hash and names. Use `dataclasses.replace` for final artifact issue counts. Build events from drafts with `identities.event_id`, frames from `associate_frames`, and fingerprints from `identities.event_fingerprint`. Do not add budgets or logging yet.

- [ ] **Step 5: GREEN command.** Run `<RT-TEST> tests/test_runtime_evidence_validation.py tests/test_runtime_evidence_models.py tests/test_runtime_evidence_text.py tests/test_runtime_evidence_json.py tests/test_runtime_evidence_stack.py tests/test_runtime_evidence_association.py -q`. Expected: all pass.

- [ ] **Step 6: Regression/security checks.** Confirm the submitted-byte check performs no hashing or adapter call; confirm no artifact bytes remain in the inventory; inspect `git diff --name-only HEAD~1` and confirm only the declared ingestor/test files changed. Run the T1-T15 focused tests.

- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/ingestor.py tests/test_runtime_evidence_models.py tests/test_runtime_evidence_validation.py tests/test_runtime_evidence_text.py tests/test_runtime_evidence_json.py tests/test_runtime_evidence_stack.py tests/test_runtime_evidence_association.py`; `git commit -m "feat: add runtime evidence ingestion pipeline"`.

- [ ] **Step 8: Independent review checklist.** [ ] Exact §11 order. [ ] Envelopes before hash/parse. [ ] Dedup names/duplicates/conflict. [ ] Canonical artifact/event/frame order. [ ] Status/counter/issue-count correctness. [ ] Claims/graph binding/event-text flag. [ ] Immutable inventory and indexes. [ ] No global budgets/logging/repr work yet. [ ] No bytes retained or upstream changes. [ ] RED/GREEN evidence recorded.

---

### Task 17: Event, Frame, and Issue Budgets

**Prerequisites:** T16.

**Files:**
- Modify: `backend/runtime_evidence/ingestor.py` (budget accounting and final counters)
- Test: `tests/test_runtime_evidence_validation.py`
- Test: `tests/test_runtime_evidence_json.py`, `tests/test_runtime_evidence_stack.py`

**Spec references:** §8.1-§8.3, §11 step 9, §12, §13.2, §17.3.

**Interfaces:**
- Consumes: T16 `RuntimeEvidenceIngestor` and internal artifact-processing helpers.
- Produces internal `_BudgetState` (dataclass or equivalent local object) with counters/flags:
  `events_retained`, `frames_retained`, `ordinary_issues`, `events_truncated`, `frames_truncated`, `issues_truncated`, `event_limit_issue_emitted`.
- Event-budget behavior:
  - Before parsing an artifact, if `events_retained == config.max_events`, mark it `SKIPPED` without parsing. Set `events_truncated`; emit exactly one `EVENT_LIMIT_REACHED` issue on the first such artifact, and none on later skipped artifacts.
  - If the remaining capacity would be exceeded while parsing, emit only the capacity-fitting prefix of events. Mark the current artifact `PARTIAL` when at least one event was emitted, set `events_truncated`, and emit exactly one `EVENT_LIMIT_REACHED` issue on that artifact. Do not emit a second event-limit issue on later skipped artifacts.
  - Exact consumption with no later artifact sets no flag and produces no issue.
  - Per-artifact oversize check precedes event-budget skipping; an oversized artifact is `ARTIFACT_TOO_LARGE`, not `EVENT_LIMIT_REACHED`.
- Frame-budget behavior:
  - The adapter’s per-event `max_frames_per_event` cap runs first and may already set flags/issues.
  - Before retaining an event’s frames, retain only the prefix allowed by `max_total_frames`. If frames are dropped, set that event’s and artifact’s `frames_truncated`, set inventory `frames_truncated`, mark the artifact `PARTIAL`, and emit exactly one `FRAME_LIMIT_REACHED` issue on the first affected event. Later affected events are flagged without additional issues. Later events and artifacts continue.
  - Exact consumption without dropping a frame sets no flag.
- Issue-budget behavior:
  - Deduplicate by `(code, artifact_id, event_sequence, source_line)` before budget accounting, keeping the first in canonical parse order.
  - Sort unique issues by the §8.1 key, retain at most `max_issues` ordinary issues, and if any are dropped set `issues_truncated=True` and append exactly one `ISSUE_LIMIT_REACHED` sentinel copying the first dropped issue’s `artifact_id`, `event_sequence`, and `source_line`.
  - Sort the final tuple again by the same key with the sentinel included; the sentinel is subject to the same deterministic ordering but not deduplicated away.
  - Recompute every artifact `issue_count` from the final retained issue tuple, including a sentinel assigned to that artifact. Dropping diagnostic issues never changes an artifact status by itself.
- After all processing, recompute `total_events`, `total_frames`, truncation flags, incomplete reasons, and counters from the final authoritative tuples. The inventory invariant `len(issues) <= max_issues + 1` holds.

- [ ] **Step 1: Scaffold** `_BudgetState` with all fields but no enforcement; route all event/frame/issue insertion points through it without changing T16 outputs.

- [ ] **Step 2: Write failing tests** in `tests/test_runtime_evidence_validation.py`:

```python
def test_event_budget_exact_end_has_no_loss():
    inv = RuntimeEvidenceIngestor(
        RuntimeEvidenceConfig(max_events=1)).ingest(
            _request(_artifact("one.log", b"one\n")))
    assert inv.total_events == 1
    assert inv.events_truncated is False
    assert inv.incomplete_reasons == ()


def test_event_budget_skips_later_artifact_with_one_issue():
    inv = RuntimeEvidenceIngestor(
        RuntimeEvidenceConfig(max_events=1)).ingest(_request(
            _artifact("a.log", b"one\n"), _artifact("b.log", b"two\n")))
    assert [a.status for a in inv.artifacts] == [
        RuntimeArtifactStatus.COMPLETE, RuntimeArtifactStatus.SKIPPED]
    assert inv.events_truncated is True
    assert [i.code for i in inv.issues] == [RuntimeIssueCode.EVENT_LIMIT_REACHED]
    assert inv.issues[0].artifact_id == inv.artifacts[1].artifact_id


def test_event_budget_overflow_marks_current_partial():
    inv = RuntimeEvidenceIngestor(
        RuntimeEvidenceConfig(max_events=1)).ingest(
            _request(_artifact("a.log", b"one\ntwo\n")))
    assert inv.total_events == 1
    assert inv.artifacts[0].status is RuntimeArtifactStatus.PARTIAL
    assert inv.events_truncated is True
    assert [i.code for i in inv.issues] == [RuntimeIssueCode.EVENT_LIMIT_REACHED]


def test_frame_budget_keeps_later_events_and_flags_every_affected_event():
    content = (b'  File "a.py", line 1, in f\n\n'
               b'  File "b.py", line 2, in g\n')
    inv = RuntimeEvidenceIngestor(RuntimeEvidenceConfig(
        max_frames_per_event=1, max_total_frames=1)).ingest(_request(
            _artifact("trace.txt", content,
                      RuntimeArtifactFormat.STACK_TRACE_TEXT,
                      RuntimeFrameLanguage.PYTHON)))
    assert inv.total_events == 2
    assert inv.total_frames == 1
    assert inv.frames_truncated is True
    assert inv.artifacts[0].status is RuntimeArtifactStatus.PARTIAL
    assert [e.frames_truncated for e in inv.events] == [False, True]
    assert [i.code for i in inv.issues] == [RuntimeIssueCode.FRAME_LIMIT_REACHED]


def test_issue_limit_sentinel_points_to_first_dropped_issue():
    content = (b'{"kind":"log","message":"one"}\n'
               b'{"kind":"log"}\n'
               b'{"kind":"log"}\n')
    inv = RuntimeEvidenceIngestor(RuntimeEvidenceConfig(max_issues=1)).ingest(
        _request(_artifact("a.jsonl", content,
                           RuntimeArtifactFormat.JSON_EVENTS)))
    assert [issue.code for issue in inv.issues] == [
        RuntimeIssueCode.INVALID_EVENT_SCHEMA,
        RuntimeIssueCode.ISSUE_LIMIT_REACHED]
    sentinel = inv.issues[-1]
    assert sentinel.artifact_id == inv.artifacts[0].artifact_id
    assert sentinel.event_sequence is None
    assert sentinel.source_line == 3
    assert inv.issues_truncated is True
    assert inv.artifacts[0].issue_count == 2
```

Also add tests for: exact `max_total_frames` with no flag; global frame prefix truncation with adapter per-event cap already set; issue equality at a positive `max_issues` (for example `max_issues=2` with two ordinary issues and no sentinel); one-over positive limit (for example `max_issues=2` with three ordinary issues: two retained plus exactly one sentinel, `len(issues)==3`); first-dropped sentinel artifact/event/source coordinates (the named test above); issue dedup before accounting (`max_issues=1`, two identical issue keys -> one issue, no truncation); `max_issues=0` rejected by `RuntimeEvidenceConfigurationError` (there is no sentinel-only zero mode); final issue ordering; sentinel `issue_count`; event-limit issue not duplicated when a partial current artifact is followed by skipped artifacts; oversized later artifact precedence over event-budget skipping; and statuses/flags/reasons after each budget.

- [ ] **Step 3: RED.** Run `<RT-TEST> tests/test_runtime_evidence_validation.py tests/test_runtime_evidence_stack.py -x`. Expected: budget/counter/sentinel assertions fail; no import error.

- [ ] **Step 4: GREEN.** Thread the local `_BudgetState` through canonical artifact processing. Pass only remaining event capacity to adapters (`max_new_events`); apply the frame prefix immediately after association; deduplicate/sort issues once, apply the issue cap/sentinel, sort again, and rebuild artifact records with final issue counts. Recompute all derived inventory fields last. No timing, threads, or global state.

- [ ] **Step 5: GREEN command.** Run `<RT-TEST> tests/test_runtime_evidence_validation.py tests/test_runtime_evidence_json.py tests/test_runtime_evidence_stack.py -q`. Expected: all pass.

- [ ] **Step 6: Contract checks.** Confirm event-budget exhaustion never drops frames/events silently; frame-budget exhaustion never skips later artifacts; issue-budget drops only diagnostics; exact limits do not set flags; `sum(a.issue_count)==len(issues)`; ordinary issues `<= max_issues`; at most one sentinel. No wall-clock or mutable cross-call state.

- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/ingestor.py tests/test_runtime_evidence_validation.py tests/test_runtime_evidence_json.py tests/test_runtime_evidence_stack.py`; `git commit -m "feat: enforce runtime evidence record budgets"`.

- [ ] **Step 8: Independent review checklist.** [ ] Event exact/overflow/skip precedence. [ ] Byte-limit precedence. [ ] Frame global/per-event order and later-event continuation. [ ] Issue dedup/sentinel/cap/order/counts. [ ] Statuses and incomplete reasons. [ ] Deterministic counters. [ ] No secondary state or timing. [ ] RED/GREEN evidence recorded.

---

### Task 18: Public Exports, Convenience API, Logging, and Sanitization

**Prerequisites:** T17.

**Files:**
- Modify: `backend/runtime_evidence/__init__.py` (explicit public exports)
- Modify: `backend/runtime_evidence/config.py` (configuration ERROR logging)
- Modify: `backend/runtime_evidence/ingestor.py` (logging and fatal wrapper)
- Test: `tests/test_runtime_evidence_models.py`
- Test: `tests/test_runtime_evidence_security.py`

**Spec references:** §5, §13.1, §15, §16, §17.1, §17.9.

**Interfaces:**
- Consumes: `RuntimeEvidenceIngestor`, all public models/enums/config/exceptions.
- Produces exactly these 28 public names in `backend.runtime_evidence.__all__`:
  `RuntimeEvidenceIngestor`, `ingest_runtime_evidence`,
  `RuntimeEvidenceConfig`, all ten enums (`RuntimeArtifactFormat`,
  `RuntimeArtifactStatus`, `RuntimeEventKind`, `RuntimeFrameLanguage`,
  `CaptureCompleteness`, `GraphBinding`, `PathMatchBasis`, `FrameAssociation`,
  `RuntimeIncompleteReason`, `RuntimeIssueCode`), the eight non-config public
  dataclasses (`RuntimeTag`, `RuntimeArtifact`, `RuntimeEvidenceRequest`,
  `RuntimeStackFrame`, `RuntimeEvent`, `RuntimeEvidenceIssue`,
  `RuntimeArtifactRecord`, `RuntimeEvidenceInventory`), and the seven exceptions.
- `ingest_runtime_evidence(request, graph=None, config=None)` constructs an
  isolated `RuntimeEvidenceIngestor(config)` and returns exactly
  `ingestor.ingest(request, graph)`. No caching, registry, or shared instance.
- `RuntimeEvidenceConfig.__post_init__` wraps its value and cross-field
  validation in `try/except RuntimeEvidenceConfigurationError`: log one fixed,
  content-free ERROR (`runtime evidence configuration failed`) and re-raise
  unchanged.
- `RuntimeEvidenceIngestor.__init__` wraps its exact-config check in
  `try/except RuntimeEvidenceConfigurationError`: log one fixed, content-free
  ERROR (`runtime evidence configuration failed`) and re-raise unchanged. This
  covers the constructor and convenience-API construction boundary for a
  non-exact config; the convenience wrapper does not log the same failure again.
- Module loggers: `logging.getLogger(__name__)` in `ingestor.py` for ingestion
  and in `config.py` for configuration validation. No other production module
  logs.
  - INFO: ingestion start and aggregate artifact/event/frame/issue counts.
  - DEBUG: artifact ID, format, status, event/frame counts, and issue codes.
  - WARNING: each recoverable artifact failure or truncation with artifact ID
    and issue codes.
  - ERROR: fatal configuration, validation, graph, limit, or internal failure,
    using a fixed category token, never `repr(exception)` or its text.
    Configuration value/cross-field failures are logged by `config.py` at
    `RuntimeEvidenceConfig` validation; exact-type rejection is logged by
    `RuntimeEvidenceIngestor.__init__`; validation/limit/graph failures are
    logged by `ingest`; internal failures are logged once before sanitization.
- `ingest` catches only intended fatal `RuntimeEvidenceValidationError`,
  `RuntimeEvidenceLimitError`, and `RuntimeEvidenceGraphError` for logging and
  re-raises them unchanged. Any other `Exception` from internal processing
  (including `RuntimeEvidenceInvariantError`, parser/decoder errors that escaped
  their boundary, and `MemoryError`) is logged once at ERROR with the fixed
  `runtime evidence ingestion failed` token and replaced by
  `RuntimeEvidenceIngestionError("runtime evidence ingestion failed")` with
  `from None`; no partial inventory and no cause is exposed. `KeyboardInterrupt`,
  `SystemExit`, and `GeneratorExit` are not caught.
- No internal helper is re-exported: adapters, parsers, association/index
  helpers, and their submodules stay outside the public API. Imports in
  production remain only standard library plus the three allowed public
  Module 6 names.

- [ ] **Step 1: Scaffold** the explicit `__init__.py` import list and `ingest_runtime_evidence`, plus a module logger. Do not add catches or log calls yet.

- [ ] **Step 2: Write failing tests.** In `tests/test_runtime_evidence_models.py`:

```python
import logging
import backend.runtime_evidence as runtime_evidence

EXPECTED_EXPORTS = {
    "CaptureCompleteness", "FrameAssociation", "GraphBinding", "PathMatchBasis",
    "RuntimeArtifact", "RuntimeArtifactFormat", "RuntimeArtifactRecord",
    "RuntimeArtifactStatus", "RuntimeEvent", "RuntimeEventKind",
    "RuntimeEvidenceConfig", "RuntimeEvidenceIngestionError",
    "RuntimeEvidenceIngestor", "RuntimeEvidenceInventory",
    "RuntimeEvidenceInvariantError", "RuntimeEvidenceIssue",
    "RuntimeEvidenceLimitError", "RuntimeEvidenceRequest",
    "RuntimeEvidenceValidationError", "RuntimeFrameLanguage",
    "RuntimeIncompleteReason", "RuntimeIssueCode", "RuntimeStackFrame",
    "RuntimeTag", "ingest_runtime_evidence", "RuntimeEvidenceGraphError",
    "RuntimeEvidenceConfigurationError", "RuntimeEvidenceError",
}


def test_public_exports_are_exact_and_hide_internals():
    assert set(runtime_evidence.__all__) == EXPECTED_EXPORTS
    assert len(runtime_evidence.__all__) == 28
    for name in EXPECTED_EXPORTS:
        assert getattr(runtime_evidence, name)
    for name in ("parse_text_log", "parse_json_events", "parse_stack_source",
                 "normalize_source_reference", "build_inventory_indexes",
                 "build_association_index", "AssociationIndex"):
        assert not hasattr(runtime_evidence, name)


def test_convenience_api_builds_an_isolated_default_ingestor(monkeypatch):
    import backend.runtime_evidence as package
    seen = {}
    class FakeIngestor:
        def __init__(self, config=None):
            seen["config"] = config
        def ingest(self, request, graph=None):
            seen["request"] = request
            seen["graph"] = graph
            return "RESULT"
    monkeypatch.setattr(package, "RuntimeEvidenceIngestor", FakeIngestor)
    request = _request()
    assert package.ingest_runtime_evidence(request) == "RESULT"
    assert seen["config"] is None and seen["request"] is request


def test_configuration_failure_logs_one_fixed_error(caplog):
    logger = "backend.runtime_evidence.ingestor"
    with caplog.at_level(logging.ERROR, logger=logger):
        with pytest.raises(runtime_evidence.RuntimeEvidenceConfigurationError):
            runtime_evidence.RuntimeEvidenceIngestor(object())
    assert [record.getMessage() for record in caplog.records] == [
        "runtime evidence configuration failed"]
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger=logger):
        with pytest.raises(runtime_evidence.RuntimeEvidenceConfigurationError):
            runtime_evidence.ingest_runtime_evidence(_request(), config=object())
    assert [record.getMessage() for record in caplog.records] == [
        "runtime evidence configuration failed"]
```

In `tests/test_runtime_evidence_security.py`:

```python
import logging
import pytest
import backend.runtime_evidence.ingestor as ingestor
from backend.runtime_evidence import (
    RuntimeArtifact, RuntimeArtifactFormat, RuntimeEvidenceConfig,
    RuntimeEvidenceConfigurationError, RuntimeEvidenceIngestionError,
    RuntimeEvidenceRequest, ingest_runtime_evidence,
)


def _artifact(name="ci.log", content=b"x\n", fmt=RuntimeArtifactFormat.TEXT_LOG):
    return RuntimeArtifact(name, fmt, content)


def _request(*artifacts, namespace="repo"):
    return RuntimeEvidenceRequest(namespace, tuple(artifacts))


def test_config_value_failures_log_one_fixed_error(caplog):
    logger = "backend.runtime_evidence.config"
    with caplog.at_level(logging.ERROR, logger=logger):
        with pytest.raises(RuntimeEvidenceConfigurationError):
            RuntimeEvidenceConfig(max_events=0)
    assert [record.getMessage() for record in caplog.records] == [
        "runtime evidence configuration failed"]
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger=logger):
        with pytest.raises(RuntimeEvidenceConfigurationError):
            RuntimeEvidenceConfig(max_artifact_bytes=10,
                                  max_total_input_bytes=11,
                                  max_submitted_input_bytes=10)
    assert [record.getMessage() for record in caplog.records] == [
        "runtime evidence configuration failed"]


def test_internal_failure_is_sanitized_and_unchained(monkeypatch):
    sentinel = "SENTINEL-INTERNAL-TEXT"
    def explode(*args, **kwargs):
        raise RuntimeError(sentinel)
    monkeypatch.setattr(ingestor, "_parse_artifact", explode)
    with pytest.raises(RuntimeEvidenceIngestionError) as caught:
        ingest_runtime_evidence(_request(_artifact("secret.log", b"x\n")))
    assert str(caught.value) == "runtime evidence ingestion failed"
    assert sentinel not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_logs_have_aggregates_but_no_content(caplog):
    sentinel = "SENTINEL-LOG-CONTENT"
    with caplog.at_level(logging.DEBUG,
                         logger="backend.runtime_evidence.ingestor"):
        inventory = ingest_runtime_evidence(
            _request(_artifact("secret.log", (sentinel + "\n").encode())))
    text = caplog.text
    assert sentinel not in text
    assert "secret.log" not in text
    assert inventory.artifacts[0].event_count == 1
    assert any(record.levelno == logging.INFO for record in caplog.records)
```

Also add tests for: the logger name; one INFO start; DEBUG artifact detail containing IDs/status/counts/issue codes but no claim/source/path/text; WARNING on PARTIAL/FAILED/SKIPPED; ERROR on configuration/validation/limit/graph/internal failures; invalid value and cross-field config failures logging exactly once from `config.py`; exact-type rejection logging exactly once from the constructor; no `repr(exception)`; convenience function forwards explicit graph/config; separate instances keep distinct effective configs; repeated calls do not accumulate state; and no internal export is reachable through `__all__`.

- [ ] **Step 3: RED.** Run `<RT-TEST> tests/test_runtime_evidence_models.py tests/test_runtime_evidence_security.py -x`. Expected: export/sanitization/log assertions fail; no import error.

- [ ] **Step 4: GREEN.** Fill the exact import/export list, implement the convenience function by direct delegation, attach loggers only in `ingestor.py` and `config.py`, wrap `RuntimeEvidenceConfig.__post_init__` validation with the fixed configuration ERROR, log exact-config rejection in the constructor, and wrap parsing/inventory construction with the precise catch structure. Log only fixed categories, IDs, counts, statuses, formats, issue codes, and flags. Re-raise known fatal errors unchanged and sanitize everything else from `None`.

- [ ] **Step 5: GREEN command.** Same selector. Expected: all pass.

- [ ] **Step 6: Security/contract checks.** Confirm exactly 28 exports and zero internal names; confirm log messages never use `record.args` with content; confirm no exception cause/context leak; confirm invalid value and cross-field config failures log exactly once from `config.py`, exact-type rejection logs exactly once from the constructor, and the convenience wrapper does not duplicate either; confirm no logging of source names, namespace, claims, paths, messages, tags, function text, trace/span IDs, parser text, or bytes. Confirm imports are standard library plus only `CodeGraphInventory`, `GraphNode`, `GraphNodeKind`.

- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/__init__.py backend/runtime_evidence/config.py backend/runtime_evidence/ingestor.py tests/test_runtime_evidence_models.py tests/test_runtime_evidence_security.py`; `git commit -m "feat: publish runtime evidence api and sanitized logging"`.

- [ ] **Step 8: Independent review checklist.** [ ] Exact 28-name public boundary. [ ] No internal leaks. [ ] Convenience delegation/isolation. [ ] INFO/DEBUG/WARNING/ERROR content and levels. [ ] Configuration value/cross-field ERROR from `config.py`; exact-type ERROR from the ingestor; validation/graph/limit/internal ERROR categories. [ ] No sensitive content in logs. [ ] Sanitized unchained internal failures. [ ] Known fatal errors preserved. [ ] No partial inventory. [ ] Import direction. [ ] RED/GREEN evidence recorded.

---

### Task 19: Deterministic Ordering and Repeated Runs

**Prerequisites:** T18.

**Files:**
- Modify only if a test exposes an ordering defect: `backend/runtime_evidence/ingestor.py`, `backend/runtime_evidence/indexes.py`, `backend/runtime_evidence/validation.py`
- Test: `tests/test_runtime_evidence_determinism.py`
- Test: `tests/test_runtime_evidence_identities.py` (repeated-occurrence fingerprint case)

**Spec references:** §5, §7.1, §7.3, §8.1-§8.4, §17.2, §17.8.

**Interfaces:**
- No new public or internal production names. T19 proves the ordering, equality, repeated-run, fingerprint-grouping, issue-order, and index-order contracts already produced by T5-T18, and corrects only an ordering implementation defect if one is exposed.
- Permuting artifact submission order must not change any public field, ID, status, counter, issue, index key, or index value ordering. The sole exception is `submitted_artifacts`, which is invariant under permutation, and any caller-owned request object, which is not mutated.
- Two calls on the same `RuntimeEvidenceIngestor` instance and two calls on separate instances with equal effective configs and equal requests must produce equal inventories with distinct index snapshots. No call may retain parse state, normalize into module globals, or reuse a cached mapping.
- Timestamps are opaque text and never participate in ordering. JSON document-array order and JSONL physical-line order are preserved. Event sequence is contiguous per artifact; frame ordinal is contiguous per event.
- Event fingerprints may group equal normalized content, but occurrences remain separate events with distinct IDs. The issue-limit sentinel copies the first dropped issue and is placed by the canonical issue key. Incomplete reasons are unique and sorted by `.value`.
- Index keys are lexically sorted; index values are exact filters of the authoritative tuple in global order. No test in this task may use wall-clock timing or a timing threshold.

- [ ] **Step 1: Scaffold** `tests/test_runtime_evidence_determinism.py` with local builders and a canonical snapshot helper. Do not add production scaffolding: every production name already exists.

```python
import itertools
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum

import pytest

from backend.runtime_evidence import (
    RuntimeArtifact, RuntimeArtifactFormat, RuntimeEvidenceIngestor,
    RuntimeEvidenceRequest, RuntimeFrameLanguage, RuntimeIncompleteReason,
    RuntimeIssueCode,
)


def _artifact(name, content, fmt=RuntimeArtifactFormat.TEXT_LOG, language=None):
    return RuntimeArtifact(name, fmt, content, language)


def _request(*artifacts, namespace="repo"):
    return RuntimeEvidenceRequest(namespace, tuple(artifacts))


def _snapshot(value):
    if is_dataclass(value):
        return {field.name: _snapshot(getattr(value, field.name))
                for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _snapshot(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_snapshot(item) for item in value]
    if value is None or type(value) is bool or isinstance(value, (int, str)):
        return value
    raise AssertionError(f"unexpected public value type: {type(value)!r}")


def _canonical(inventory):
    return json.dumps(
        _snapshot(inventory), ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
```

- [ ] **Step 2: Write failing tests** in `tests/test_runtime_evidence_determinism.py`:

```python
def test_shuffled_artifact_order_is_observation_identical():
    artifacts = (
        _artifact("B.log", b"bravo\n"),
        _artifact("a.log", b"alpha\n"),
        _artifact("C.log", b"charlie\n"),
    )
    snapshots = {
        _canonical(RuntimeEvidenceIngestor().ingest(_request(*permutation)))
        for permutation in itertools.permutations(artifacts)
    }
    assert len(snapshots) == 1


def test_source_name_casefold_ties_are_deterministic():
    inventory = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("b.log", b"lower\n"),
        _artifact("B.log", b"upper\n"),
        _artifact("A.log", b"first\n"),
    ))
    assert [artifact.source_names for artifact in inventory.artifacts] == [
        ("A.log",), ("B.log",), ("b.log",)]
    assert [event.message for event in inventory.events] == [
        "first", "upper", "lower"]


def test_timestamps_and_input_order_are_never_resorted():
    document = (
        b'[{"kind":"log","message":"late",'
        b'"timestamp":"2026-01-01T00:00:00Z"},'
        b'{"kind":"log","message":"early",'
        b'"timestamp":"1999-01-01T00:00:00Z"}]')
    inventory = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("events.json", document, RuntimeArtifactFormat.JSON_EVENTS)))
    assert [event.message for event in inventory.events] == ["late", "early"]
    assert [event.sequence for event in inventory.events] == [0, 1]
    assert all(event.source_line is None for event in inventory.events)

    jsonl = (b'{"kind":"log","message":"one"}\n'
             b'{"kind":"log","message":"two"}\n')
    lines = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("events.jsonl", jsonl, RuntimeArtifactFormat.JSON_EVENTS)))
    assert [(event.message, event.source_line) for event in lines.events] == [
        ("one", 1), ("two", 2)]


def test_repeated_runs_are_byte_identical_and_isolated():
    request = _request(
        _artifact("a.log", b"one\n"),
        _artifact("b.jsonl", b'{"kind":"log","message":"two"}\n',
                  RuntimeArtifactFormat.JSON_EVENTS),
        _artifact("c.txt", b'  File "pkg/app.py", line 1, in f\n',
                  RuntimeArtifactFormat.STACK_TRACE_TEXT,
                  RuntimeFrameLanguage.PYTHON),
    )
    first = RuntimeEvidenceIngestor().ingest(request)
    second = RuntimeEvidenceIngestor().ingest(request)
    assert first == second
    assert first is not second
    assert first.events_by_artifact is not second.events_by_artifact
    assert first.frames_by_file is not second.frames_by_file
    assert _canonical(first) == _canonical(second)


def test_fingerprints_group_occurrences_without_merging_them():
    inventory = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("a.jsonl", b'{"kind":"log","message":"same"}\n',
                  RuntimeArtifactFormat.JSON_EVENTS),
        _artifact("b.jsonl", b'{ "kind": "log", "message": "same" }\n',
                  RuntimeArtifactFormat.JSON_EVENTS),
    ))
    assert len(inventory.artifacts) == 2
    assert len(inventory.events) == 2
    assert inventory.events[0].event_fingerprint == (
        inventory.events[1].event_fingerprint)
    assert inventory.events[0].event_id != inventory.events[1].event_id
    fingerprint = inventory.events[0].event_fingerprint
    assert inventory.events_by_fingerprint[fingerprint] == inventory.events


def test_incomplete_reasons_are_unique_and_sorted_across_scaled_artifacts():
    from backend.runtime_evidence import RuntimeEvidenceConfig
    config = RuntimeEvidenceConfig(
        max_events=3, max_frames_per_event=1, max_total_frames=1,
        max_message_bytes=4, max_issues=8, max_stack_bytes=256,
        max_line_bytes=256)
    inventory = RuntimeEvidenceIngestor(config).ingest(_request(
        _artifact("aa0_bad.log", b"\xff\n"),
        _artifact("aa1_bad.log", b"\xfe\n"),
        _artifact("ab_stack.txt",
                  b'  File "a.py", line 1, in f\n\n'
                  b'  File "b.py", line 2, in g\n',
                  RuntimeArtifactFormat.STACK_TRACE_TEXT,
                  RuntimeFrameLanguage.PYTHON),
        _artifact("ac_long.log", b"abcdef\n"),
        _artifact("ad_skip.log", b"x\n"),
    ))
    assert [artifact.status.value for artifact in inventory.artifacts] == [
        "failed", "failed", "partial", "partial", "skipped"]
    assert inventory.total_events == 3
    assert inventory.total_frames == 1
    assert inventory.events_truncated is True
    assert inventory.frames_truncated is True
    assert inventory.text_truncated is True
    assert inventory.issues_truncated is False
    assert inventory.incomplete_reasons == (
        RuntimeIncompleteReason.ARTIFACT_FAILED,
        RuntimeIncompleteReason.ARTIFACT_PARTIAL,
        RuntimeIncompleteReason.ARTIFACT_SKIPPED,
        RuntimeIncompleteReason.EVENT_LIMIT_REACHED,
        RuntimeIncompleteReason.FRAME_LIMIT_REACHED,
        RuntimeIncompleteReason.TEXT_TRUNCATED,
    )
    assert len(inventory.incomplete_reasons) == len(
        set(inventory.incomplete_reasons))


def test_issue_sentinel_is_last_and_copies_the_first_dropped_issue():
    from backend.runtime_evidence import RuntimeEvidenceConfig
    config = RuntimeEvidenceConfig(max_issues=1, max_message_bytes=4)
    inventory = RuntimeEvidenceIngestor(config).ingest(_request(
        _artifact("a_bad.log", b"\xff\n"),
        _artifact("b_long.log", b"abcdef\n"),
    ))
    assert [issue.code for issue in inventory.issues] == [
        RuntimeIssueCode.INVALID_UTF8, RuntimeIssueCode.ISSUE_LIMIT_REACHED]
    sentinel = inventory.issues[-1]
    assert sentinel.artifact_id == inventory.artifacts[1].artifact_id
    assert sentinel.event_sequence == 0
    assert sentinel.source_line == 1
    assert inventory.issues_truncated is True
    assert [artifact.status.value for artifact in inventory.artifacts] == [
        "failed", "partial"]
    assert sum(artifact.issue_count for artifact in inventory.artifacts) == 2
    assert inventory.artifacts[0].issue_count == 1
    assert inventory.artifacts[1].issue_count == 1


def test_index_keys_and_values_follow_the_authoritative_order():
    inventory = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("empty.log", b""),
        _artifact("events.jsonl",
                  b'{"kind":"span","operation":"op","trace_id":"z"}\n'
                  b'{"kind":"span","operation":"op","trace_id":"a"}\n',
                  RuntimeArtifactFormat.JSON_EVENTS),
        _artifact("trace.txt",
                  b'  File "b.py", line 1, in f\n\n'
                  b'  File "a.py", line 2, in g\n',
                  RuntimeArtifactFormat.STACK_TRACE_TEXT,
                  RuntimeFrameLanguage.PYTHON),
    ))
    assert list(inventory.events_by_artifact) == sorted(
        inventory.events_by_artifact)
    assert list(inventory.events_by_trace_id) == ["a", "z"]
    assert inventory.events_by_trace_id["a"] == (inventory.events[1],)
    assert inventory.events_by_trace_id["z"] == (inventory.events[0],)
    assert list(inventory.events_by_fingerprint) == sorted(
        inventory.events_by_fingerprint)
    assert list(inventory.frames_by_file) == ["a.py", "b.py"]
    assert inventory.frames_by_file["a.py"] == (inventory.frames[1],)
    assert inventory.frames_by_file["b.py"] == (inventory.frames[0],)
    assert dict(inventory.frames_by_node) == {}
    for artifact in inventory.artifacts:
        assert inventory.events_by_artifact[artifact.artifact_id] == tuple(
            event for event in inventory.events
            if event.artifact_id == artifact.artifact_id)
    for mapping in (inventory.events_by_artifact,
                    inventory.events_by_trace_id,
                    inventory.events_by_fingerprint,
                    inventory.frames_by_file,
                    inventory.frames_by_node):
        with pytest.raises(TypeError):
            mapping["x"] = ()
```

Also add to `tests/test_runtime_evidence_identities.py` a case where two distinct artifact contents normalize to equal event fields and assert equal fingerprints but distinct events/IDs, and a case where the same request is ingested twice and every ID tuple is equal. Do not duplicate T6's pinned digest vectors.

- [ ] **Step 3: RED.** Run `<RT-TEST> tests/test_runtime_evidence_determinism.py tests/test_runtime_evidence_identities.py -x`. Expected: a genuine ordering/equality assertion fails only if T16-T18 left a defect; otherwise the new tests pass immediately because they characterize existing behavior. A new test that passes on first run is acceptable here only after a deliberate mutation check: temporarily reverse one artifact sort key or index-value insertion locally, observe the named assertion fail, restore it, and record both runs. An import, collection, syntax, or environment error is invalid.

- [ ] **Step 4: GREEN.** If a test exposed a defect, fix only the ordering or snapshotting code in the declared production files. Never change public fields, error types, statuses, budgets, or parse behavior to make ordering pass. Never sort index values by index key. Never deduplicate events by fingerprint. Never remove the sentinel. If no defect is exposed, make no production change and record the mutation evidence from Step 3.

- [ ] **Step 5: GREEN command.** Run `<RT-TEST> tests/test_runtime_evidence_determinism.py tests/test_runtime_evidence_identities.py tests/test_runtime_evidence_models.py tests/test_runtime_evidence_validation.py -q`. Expected: all pass.

- [ ] **Step 6: Regression/contract checks.** Run the T7-T18 focused selectors. Confirm `_canonical` accepts every public inventory value and raises on bytes, proving no raw bytes are present. Confirm no wall-clock, `time`, `random`, `hash()`, process ID, or global registry appears in the production diff. Inspect `git diff --name-only HEAD~1` and confirm only the declared test files and any genuinely required ordering file changed.

- [ ] **Step 7: Commit.** `git add backend/runtime_evidence tests/test_runtime_evidence_determinism.py tests/test_runtime_evidence_identities.py`; `git commit -m "test: verify runtime evidence determinism and ordering"`.

- [ ] **Step 8: Independent review checklist.** [ ] Artifact permutation invariance. [ ] Casefold/original name order. [ ] Timestamp opacity and input-order preservation. [ ] Repeated-run byte equality and distinct snapshots. [ ] Fingerprint grouping without occurrence loss. [ ] Incomplete-reason uniqueness/order. [ ] Sentinel copies the first dropped issue and is ordered by the canonical issue key. [ ] Index key/value order and immutability. [ ] Mutation check recorded when no production defect existed. [ ] No timing or global state. [ ] RED/GREEN evidence recorded.

---

### Task 20: Security, Privacy, and Redacted Representations

**Prerequisites:** T19.

**Files:**
- Modify: `backend/runtime_evidence/models.py` (one explicit `__repr__` per public non-config dataclass)
- Test: `tests/test_runtime_evidence_models.py`
- Test: `tests/test_runtime_evidence_security.py`

**Spec references:** §5, §6.3, §6.4, §14.1-§14.5, §16, §17.1, §17.9.

**Interfaces:**
- Add an explicit `__repr__` to exactly these classes; `RuntimeEvidenceConfig` is exempt:
  `RuntimeTag`, `RuntimeArtifact`, `RuntimeEvidenceRequest`,
  `RuntimeStackFrame`, `RuntimeEvent`, `RuntimeEvidenceIssue`,
  `RuntimeArtifactRecord`, `RuntimeEvidenceInventory`.
- The only fields a representation may print are:
  - type name;
  - `artifact_id`, `event_id`, `frame_id` (stable record IDs);
  - `format`, `declared_language`, `status`, `kind`, `language`,
    `path_match_basis`, `association`, `graph_binding` (enum values, or `None`);
  - `sequence`, `ordinal`, `source_line`, `line`, `column`, `size_bytes`,
    `event_count`, `frame_count`, `duplicate_count`, `issue_count`,
    `candidate_count`, `artifact_count`, `incomplete_reason_count`,
    `submitted_artifacts`, `duplicate_artifacts`, `total_input_bytes`,
    `total_events`, `total_frames` (counts);
  - `text_truncated`, `frames_truncated`, `issues_truncated`,
    `event_text_retained`, `ingestion_complete` (booleans).
- Use these exact shapes (no quotes around hashes; `None` for absent values):
  - `RuntimeTag()`
  - `RuntimeArtifact(format=<value>, declared_language=<value|None>, size_bytes=<len(content)>)`
  - `RuntimeEvidenceRequest(artifact_count=<n>)`
  - `RuntimeStackFrame(frame_id=<id>, event_id=<id>, ordinal=<n>, language=<value>, line=<n|None>, column=<n|None>, path_match_basis=<value>, association=<value>, candidate_count=<n>)`
  - `RuntimeEvent(event_id=<id>, artifact_id=<id>, sequence=<n>, source_line=<n|None>, kind=<value>, frame_count=<n>, text_truncated=<bool>, frames_truncated=<bool>)`
  - `RuntimeEvidenceIssue(code=<value>, artifact_id=<id>, event_sequence=<n|None>, source_line=<n|None>)`
  - `RuntimeArtifactRecord(artifact_id=<id>, format=<value>, declared_language=<value|None>, size_bytes=<n>, status=<value>, event_count=<n>, frame_count=<n>, duplicate_count=<n>, text_truncated=<bool>, frames_truncated=<bool>, issue_count=<n>)`
  - `RuntimeEvidenceInventory(artifact_count=<n>, event_count=<n>, frame_count=<n>, issue_count=<n>, total_input_bytes=<n>, submitted_artifacts=<n>, duplicate_artifacts=<n>, graph_binding=<value>, event_text_retained=<bool>, ingestion_complete=<bool>, text_truncated=<bool>, events_truncated=<bool>, frames_truncated=<bool>, issues_truncated=<bool>, incomplete_reason_count=<n>)`
- `RuntimeArtifact.__repr__` must not call `repr`/`str` on `content` or `source_name`. No representation may call `repr` on a field that is not allowlisted.
- No public field, constructor, default, error, or parse result changes.

- [ ] **Step 1: Scaffold** the eight `__repr__` methods with only the allowlisted fields and no behavior changes elsewhere.

- [ ] **Step 2: Write failing tests.** In `tests/test_runtime_evidence_models.py` add:

```python
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass

from backend.runtime_evidence import (
    FrameAssociation, PathMatchBasis, RuntimeArtifact, RuntimeArtifactFormat,
    RuntimeEvidenceConfig, RuntimeEvidenceIngestor, RuntimeEvidenceRequest,
    RuntimeTag,
)

SENTINEL = "SENTINEL-REPR-CONTENT"
HOSTILE = "\x1b[31m" + SENTINEL + "\x1b[0m\u202e"


def _privacy_config(**overrides):
    values = dict(max_stack_bytes=512, max_message_bytes=128,
                  max_error_message_bytes=128, max_frame_function_bytes=128,
                  max_line_bytes=512, max_tags=4, max_tag_key_bytes=32,
                  max_tag_value_bytes=64, retain_event_text=True)
    values.update(overrides)
    return RuntimeEvidenceConfig(**values)


def _privacy_artifact(name, content, fmt=RuntimeArtifactFormat.TEXT_LOG,
                      language=None):
    return RuntimeArtifact(name, fmt, content, language)


def _privacy_request(*artifacts, namespace="repo", revision=None, captured=None,
                     completeness=None):
    kwargs = {}
    if revision is not None:
        kwargs["declared_revision"] = revision
    if captured is not None:
        kwargs["declared_captured_at"] = captured
    if completeness is not None:
        kwargs["declared_capture_completeness"] = completeness
    return RuntimeEvidenceRequest(namespace, tuple(artifacts), **kwargs)


def _privacy_inventory(**config_overrides):
    log = {
        "kind": "log",
        "message": SENTINEL + "-message",
        "timestamp": SENTINEL + "-timestamp",
        "level": SENTINEL + "-level",
        "logger": SENTINEL + "-logger",
        "operation": SENTINEL + "-operation",
        "trace_id": SENTINEL + "-trace",
        "span_id": SENTINEL + "-span",
        "parent_span_id": SENTINEL + "-parent",
        "duration_ms": "1.5",
        "status": SENTINEL + "-status",
        "tags": {"secret_key": "secret_value"},
    }
    error = {
        "kind": "error",
        "message": SENTINEL + "-error-message",
        "error": {
            "type": SENTINEL + "-error-type",
            "message": SENTINEL + "-error-detail",
            "frames": [{
                "function": SENTINEL + "-function",
                "source": "pkg/app.py",
                "line": 5,
                "column": 2,
            }],
        },
    }
    content = (b"\xff\n" + (json.dumps(log) + "\n" + json.dumps(error) + "\n").encode())
    return RuntimeEvidenceIngestor(_privacy_config(**config_overrides)).ingest(
        _privacy_request(_privacy_artifact(SENTINEL + "-source.log", content,
                                           RuntimeArtifactFormat.JSON_EVENTS),
                         namespace=SENTINEL + "-namespace",
                         revision=SENTINEL + "-revision",
                         captured=SENTINEL + "-captured"))


def _leaf_values(value):
    if is_dataclass(value):
        for field in fields(value):
            yield from _leaf_values(getattr(value, field.name))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield key
            yield from _leaf_values(item)
    elif isinstance(value, tuple):
        for item in value:
            yield from _leaf_values(item)
    else:
        yield value


def test_every_public_record_owns_an_explicit_redacted_repr():
    inventory = _privacy_inventory()
    artifact = _privacy_artifact(SENTINEL + "-artifact", SENTINEL.encode())
    request = _privacy_request(
        artifact, namespace=SENTINEL + "-request-namespace",
        revision=SENTINEL + "-request-revision",
        captured=SENTINEL + "-request-captured")
    values = (
        artifact, request, RuntimeTag(SENTINEL + "-key", SENTINEL + "-value"),
        inventory, *inventory.artifacts, *inventory.events,
        *inventory.frames, *inventory.issues,
    )
    for value in values:
        assert "__repr__" in type(value).__dict__, type(value).__name__
        assert type(value).__repr__ is not object.__repr__
        text = repr(value)
        assert type(value).__name__ in text
        for forbidden in (SENTINEL, "pkg/app.py", "secret_key", "secret_value",
                          "\x1b", "\u202e", "b'", 'b"'):
            assert forbidden not in text, (type(value).__name__, forbidden)


def test_repr_prints_only_identity_structure_and_counts():
    inventory = _privacy_inventory()
    event = next(item for item in inventory.events if item.kind.value == "log")
    frame = inventory.frames[0]
    record = inventory.artifacts[0]
    issue = inventory.issues[0]
    assert f"event_id={event.event_id}" in repr(event)
    assert f"artifact_id={event.artifact_id}" in repr(event)
    assert "sequence=0" in repr(event)
    assert "kind=log" in repr(event)
    assert "frame_count=0" in repr(event)
    assert f"frame_id={frame.frame_id}" in repr(frame)
    assert "language=unknown" in repr(frame)
    assert "path_match_basis=unverified_relative_path" in repr(frame)
    assert "association=not_evaluated" in repr(frame)
    assert f"artifact_id={record.artifact_id}" in repr(record)
    assert f"status={record.status.value}" in repr(record)
    assert f"size_bytes={record.size_bytes}" in repr(record)
    assert "event_text_retained=True" in repr(inventory)
    assert f"incomplete_reason_count={len(inventory.incomplete_reasons)}" in repr(
        inventory)
    assert f"code={issue.code.value}" in repr(issue)
    assert f"source_line={issue.source_line}" in repr(issue)


def test_inventory_contains_no_bytes_or_raw_artifact_reference():
    inventory = _privacy_inventory()
    leaves = list(_leaf_values(inventory))
    assert not any(isinstance(value, (bytes, bytearray, memoryview))
                   for value in leaves)
    assert all(not isinstance(value, RuntimeArtifact) for value in leaves)


def test_metadata_only_reprs_stay_redacted_and_keep_structure():
    inventory = _privacy_inventory(retain_event_text=False)
    assert inventory.event_text_retained is False
    assert all(event.message is None for event in inventory.events)
    assert all(event.tags == () for event in inventory.events)
    assert all(frame.function_text is None for frame in inventory.frames)
    assert inventory.frames[0].relative_path == "pkg/app.py"
    assert inventory.frames[0].line == 5
    for value in (inventory, *inventory.artifacts, *inventory.events,
                  *inventory.frames):
        assert SENTINEL not in repr(value)
        assert "pkg/app.py" not in repr(value)
```

Also add a direct test for `RuntimeArtifact.__repr__` with a 1 MiB binary body and a source name containing every forbidden sentinel: assert the exact content bytes, `source_name`, `b'`, and `bytearray` never appear. Add a test that hostile message/tag/function text does not appear at all (raw or escaped) in any repr.

In `tests/test_runtime_evidence_security.py` add:

```python
import ast
import builtins
import importlib
import io
import marshal
import os
import pickle
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

import backend.runtime_evidence as runtime_evidence
import backend.runtime_evidence.ingestor as ingestor
from backend.runtime_evidence import (
    RuntimeArtifact, RuntimeArtifactFormat, RuntimeEvidenceConfig,
    RuntimeEvidenceConfigurationError, RuntimeEvidenceIngestor,
    RuntimeEvidenceLimitError, RuntimeEvidenceRequest,
    RuntimeEvidenceValidationError, RuntimeFrameLanguage,
)


def _forbid(*args, **kwargs):
    raise AssertionError("Module 8 crossed the in-memory boundary")


def _patch_io(guard):
    for owner, name in (
        (builtins, "open"), (io, "open"), (os, "open"),
        (os, "scandir"), (os, "listdir"), (os, "remove"), (os, "unlink"),
        (os, "rename"), (os, "replace"), (os, "mkdir"), (os, "makedirs"),
        (os, "rmdir"), (os, "chmod"), (os, "symlink"), (os, "link"),
        (os, "truncate"), (os, "fdopen"),
        (Path, "open"), (Path, "read_text"), (Path, "read_bytes"),
        (Path, "write_text"), (Path, "write_bytes"), (Path, "stat"),
        (Path, "resolve"), (Path, "iterdir"), (Path, "unlink"),
        (Path, "rename"), (Path, "replace"), (Path, "mkdir"),
        (Path, "rmdir"), (Path, "touch"), (Path, "chmod"),
        (Path, "symlink_to"), (Path, "hardlink_to"),
        (subprocess, "run"), (subprocess, "Popen"), (subprocess, "call"),
        (subprocess, "check_call"), (subprocess, "check_output"),
        (socket, "socket"), (socket, "create_connection"),
        (urllib.request, "urlopen"), (importlib, "import_module"),
        (builtins, "eval"), (builtins, "exec"), (builtins, "compile"),
        (pickle, "loads"), (pickle, "load"),
        (marshal, "loads"), (marshal, "load"),
    ):
        guard.setattr(owner, name, _forbid)


def _security_request():
    stack = (
        b'Traceback (most recent call last):\n'
        b'  File "pkg/app.py", line 5, in run\n'
        b'ValueError: ' + b"SENTINEL-SECURITY" + b"\n"
    )
    return RuntimeEvidenceRequest(
        "repo",
        (
            RuntimeArtifact("secret.log", RuntimeArtifactFormat.TEXT_LOG,
                            b"SENTINEL-SECURITY\n"),
            RuntimeArtifact("trace.log", RuntimeArtifactFormat.STACK_TRACE_TEXT,
                            stack, RuntimeFrameLanguage.PYTHON),
        ),
    )


def test_ingestion_performs_no_io_execution_or_network(monkeypatch):
    request = _security_request()
    with monkeypatch.context() as guard:
        _patch_io(guard)
        inventory = RuntimeEvidenceIngestor().ingest(request)
    assert inventory.total_events == 2
    assert inventory.frames[0].relative_path == "pkg/app.py"


def test_fatal_errors_are_fixed_and_do_not_echo_values():
    sentinel = "SENTINEL-ERROR-VALUE"
    with pytest.raises(RuntimeEvidenceValidationError) as caught:
        RuntimeEvidenceRequest(sentinel + "\ud800", ())
    assert sentinel not in str(caught.value)
    assert caught.value.__cause__ is None

    with pytest.raises(RuntimeEvidenceConfigurationError) as caught:
        RuntimeEvidenceIngestor(RuntimeEvidenceConfig(max_artifacts=0))
    assert sentinel not in str(caught.value)

    request = RuntimeEvidenceRequest(
        "repo",
        (RuntimeArtifact(sentinel, RuntimeArtifactFormat.TEXT_LOG, b"a\n"),
         RuntimeArtifact(sentinel + "2", RuntimeArtifactFormat.TEXT_LOG, b"b\n")),
    )
    with pytest.raises(RuntimeEvidenceLimitError) as caught:
        RuntimeEvidenceIngestor(RuntimeEvidenceConfig(max_artifacts=1)).ingest(
            request)
    assert sentinel not in str(caught.value)
    assert caught.value.__cause__ is None
```

Add the following static audit helpers and tests to the same file:

```python
def _module_name_for(path, root):
    return ".".join(path.relative_to(root).with_suffix("").parts)


def _imported_modules(tree, module_name):
    package = module_name.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                yield item.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                keep = len(parts) - (node.level - 1)
                base = parts[:keep]
                if node.module:
                    base.extend(node.module.split("."))
                base_name = ".".join(base)
                yield base_name
                if node.module is None:
                    for item in node.names:
                        if item.name != "*":
                            yield f"{base_name}.{item.name}".strip(".")
            else:
                yield node.module or ""


FORBIDDEN_IMPORT_ROOTS = {
    "os", "pathlib", "subprocess", "socket", "urllib", "http", "requests",
    "httpx", "sqlite3", "chromadb", "git", "pickle", "marshal", "yaml",
    "joblib", "cloudpickle", "dill", "torch", "numpy", "google",
    "backend.code_parser", "backend.file_scanner", "backend.code_chunker",
    "backend.embedding_vector_store", "backend.git_history",
    "backend.repository_loader",
}


def _is_allowed_module(name):
    root = name.split(".", 1)[0]
    if root in sys.stdlib_module_names:
        return root not in FORBIDDEN_IMPORT_ROOTS
    if name == "backend.runtime_evidence" or name.startswith(
            "backend.runtime_evidence."):
        return True
    return name == "backend.code_graph"


def _forbidden_calls(tree):
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".", 1)[0]] = item.name
        elif isinstance(node, ast.ImportFrom):
            base = next(iter(_imported_modules(
                ast.Module(body=[node], type_ignores=[]), "backend.runtime_evidence.audit")), "")
            for item in node.names:
                if item.name != "*":
                    aliases[item.asname or item.name] = (
                        f"{base}.{item.name}".strip("."))
    forbidden_io = {
        "open", "read_text", "read_bytes", "write_text", "write_bytes",
        "iterdir", "glob", "rglob", "resolve", "stat", "scandir", "listdir",
        "walk", "run", "Popen", "popen", "call", "check_call", "check_output",
        "system", "socket", "connect", "create_connection", "urlopen",
        "import_module", "__import__", "eval", "exec", "compile",
        "remove", "unlink", "rename", "mkdir", "makedirs", "rmdir",
        "chmod", "symlink", "link", "truncate", "fdopen",
    }
    unsafe = {"pickle.load", "pickle.loads", "marshal.load", "marshal.loads",
              "yaml.load", "yaml.load_all", "yaml.unsafe_load",
              "jsonpickle.decode"}
    forbidden_replace = {
        "os.replace", "pathlib.Path.replace", "pathlib.PosixPath.replace",
        "pathlib.WindowsPath.replace", "pathlib.PurePath.replace",
        "pathlib.PurePosixPath.replace", "pathlib.PureWindowsPath.replace",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        parts = []
        current = node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        dotted = ".".join(reversed(parts))
        head = dotted.split(".", 1)[0]
        if head in aliases:
            dotted = aliases[head] + dotted[len(head):]
        leaf = dotted.rsplit(".", 1)[-1]
        if dotted in unsafe or dotted in forbidden_replace or dotted in {
                "builtins.eval", "builtins.exec", "builtins.compile"}:
            yield dotted
        elif leaf in forbidden_io and dotted not in {"re.compile"}:
            yield dotted


def _audit_sources(root):
    violations = []
    audited = []
    package = root / "backend" / "runtime_evidence"
    for source_path in sorted(package.rglob("*.py")):
        relative = source_path.relative_to(root)
        audited.append(relative.as_posix())
        module_name = _module_name_for(relative, root)
        tree = ast.parse(source_path.read_text(encoding="utf-8"),
                         filename=str(source_path))
        for imported in _imported_modules(tree, module_name):
            if not _is_allowed_module(imported):
                violations.append(f"{relative}: forbidden import {imported}")
        for call_name in _forbidden_calls(tree):
            violations.append(f"{relative}: forbidden call {call_name}")
    return violations, audited


EXPECTED_AUDITED = [
    "backend/runtime_evidence/__init__.py",
    "backend/runtime_evidence/association.py",
    "backend/runtime_evidence/config.py",
    "backend/runtime_evidence/drafts.py",
    "backend/runtime_evidence/exceptions.py",
    "backend/runtime_evidence/identities.py",
    "backend/runtime_evidence/indexes.py",
    "backend/runtime_evidence/ingestor.py",
    "backend/runtime_evidence/json_adapter.py",
    "backend/runtime_evidence/json_scanner.py",
    "backend/runtime_evidence/lines.py",
    "backend/runtime_evidence/models.py",
    "backend/runtime_evidence/source_references.py",
    "backend/runtime_evidence/stack_parser.py",
    "backend/runtime_evidence/text_adapter.py",
    "backend/runtime_evidence/validation.py",
]


def test_runtime_evidence_sources_stay_in_memory_and_within_dependencies():
    root = Path(__file__).resolve().parents[1]
    violations, audited = _audit_sources(root)
    assert audited == EXPECTED_AUDITED
    assert violations == []


def test_source_audit_detects_a_planted_violation(tmp_path):
    package = tmp_path / "backend" / "runtime_evidence"
    package.mkdir(parents=True)
    (package / "bad.py").write_text(
        "import subprocess\n\n"
        "def damage():\n"
        "    subprocess.run(['git', 'status'])\n",
        encoding="utf-8")
    violations, audited = _audit_sources(tmp_path)
    assert audited == ["backend/runtime_evidence/bad.py"]
    assert any("forbidden import subprocess" in item for item in violations)
    assert any("forbidden call subprocess.run" in item for item in violations)
```

Also add a test that the audit rejects a `backend.code_graph.models` import, a `backend.code_parser.models` import, and a bare `compile`/`eval` call in a synthetic module, and a test that `re.compile` of a fixed module-level pattern is allowed. Add a test that `RuntimeEvidenceConfig` is the only public dataclass without an explicit class-owned `__repr__`.

- [ ] **Step 3: RED.** Run `<RT-TEST> tests/test_runtime_evidence_models.py tests/test_runtime_evidence_security.py -x`. Expected: representation, metadata-only, or audit assertions fail; an import/collection error is invalid.

- [ ] **Step 4: GREEN.** Implement only the eight `__repr__` methods and any test-driven audit correction. Do not add redaction heuristics, secret scanning, filesystem access, or a logging filter. Never mask a forbidden import by changing the audit list to match bad code.

- [ ] **Step 5: GREEN command.** Run `<RT-TEST> tests/test_runtime_evidence_models.py tests/test_runtime_evidence_security.py -q`. Expected: all pass.

- [ ] **Step 6: Security/contract checks.** Confirm every public non-config dataclass owns a class-dict `__repr__`; every repr lacks raw bytes, source names, namespace, claims, trace/span IDs, tags, function text, paths, messages, and graph/index content; metadata-only `text_truncated` remains false; the runtime guard covers every named API; static audit lists exactly the 16 production files; production imports only stdlib plus `backend.code_graph`; and no production file contains a forbidden call. Record the audit output.

- [ ] **Step 7: Commit.** `git add backend/runtime_evidence/models.py tests/test_runtime_evidence_models.py tests/test_runtime_evidence_security.py`; `git commit -m "feat: redact runtime evidence public representations"`.

- [ ] **Step 8: Independent review checklist.** [ ] All eight explicit reprs. [ ] Exact allowlist/no field traversal. [ ] No bytes/source/content/path/tag/trace/namespace/claim leak. [ ] Metadata-only reprs and structural retention. [ ] Hostile/control text inert. [ ] Runtime no-I/O guard. [ ] Fixed content-free fatal errors. [ ] 16-file static audit and planted-violation proof. [ ] Import direction/public Module 6 only. [ ] No redaction claim or new dependency. [ ] RED/GREEN evidence recorded.

---

### Task 21: Real Module 2 → 3 → 6 Integration

**Prerequisites:** T20.

**Files:**
- Create: `tests/test_runtime_evidence_integration.py`
- Modify only if a test exposes an association defect: `backend/runtime_evidence/association.py`, `backend/runtime_evidence/ingestor.py`

**Spec references:** §10.3, §12, §14.4, §15, §17.10.

**Interfaces:**
- No new production names. The test builds a real temporary repository with the public
  Module 2 `FileScanner`, Module 3 `CodeParser`, and Module 6 `build_code_graph`.
  Those imports are test-only; Module 8 production still imports only the public
  Module 6 package.
- The fixture graph is namespace `integration-repo`. The request uses the same
  namespace. A `STACK_TRACE_TEXT` artifact with
  `File "pkg/app.py", line 5, in run` must associate to the real `run` symbol.
- A symbol-free file (`pkg/util.py` containing only the lowercase assignment
  `value = 1`) must associate at `FILE` level with the real file node.
- Ingestion with and without the graph must produce the same artifact, event, and
  frame IDs. Only path basis, association, candidates, primary node, graph
  binding, and graph-derived indexes may differ.
- A namespace mismatch raises `RuntimeEvidenceGraphError` before association and
  leaves the graph unchanged. No source file, scanner, parser, or graph builder
  may run during ingestion; no source API may be called.
- A local consumer may read tuples and mappings directly, without conversion,
  and must not mutate the inventory or graph.

- [ ] **Step 1: Scaffold** the integration test module with local repository and
  request builders. No production scaffolding is needed; every production name
  exists.

```python
import builtins
import copy
import importlib
import io
import os
import socket
import subprocess
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import MappingProxyType

import pytest

from backend.code_graph import CodeGraphInventory, GraphNode, GraphNodeKind, build_code_graph
from backend.code_parser import CodeParser
from backend.code_parser.models import ParseStatus
from backend.file_scanner import FileScanner
from backend.runtime_evidence import (
    FrameAssociation, GraphBinding, PathMatchBasis, RuntimeArtifact,
    RuntimeArtifactFormat, RuntimeEvidenceGraphError, RuntimeEvidenceIngestor,
    RuntimeEvidenceRequest, RuntimeFrameLanguage,
)

NAMESPACE = "integration-repo"
STACK = (
    b"Traceback (most recent call last):\n"
    b'  File "pkg/app.py", line 5, in run\n'
    b"ValueError: boom\n"
)


def _artifact(name, content, fmt=RuntimeArtifactFormat.STACK_TRACE_TEXT,
              language=RuntimeFrameLanguage.PYTHON):
    return RuntimeArtifact(name, fmt, content, language)


def _request(content=STACK, *, namespace=NAMESPACE, name="run.log"):
    return RuntimeEvidenceRequest(namespace, (_artifact(name, content),))


def _write_repository(tmp_path, files):
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _real_graph(tmp_path, files):
    _write_repository(tmp_path, files)
    files_inventory = FileScanner().scan(
        tmp_path, repository_namespace=NAMESPACE)
    parsed = CodeParser().parse_inventory(files_inventory)
    graph = build_code_graph(files_inventory, parsed)
    return graph, parsed


def _file_node(graph, path):
    return next(node for node in graph.nodes
                if node.node_kind is GraphNodeKind.FILE
                and node.relative_path == path)


def _symbol_node(graph, qualified_name):
    return next(node for node in graph.nodes
                if node.node_kind is GraphNodeKind.SYMBOL
                and node.qualified_name == qualified_name)


def _leaf_values(value):
    if is_dataclass(value):
        for field in fields(value):
            yield from _leaf_values(getattr(value, field.name))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield key
            yield from _leaf_values(item)
    elif isinstance(value, tuple):
        for item in value:
            yield from _leaf_values(item)
    else:
        yield value


def _graph_snapshot(graph):
    return (
        graph.repository_namespace,
        graph.nodes,
        graph.edges,
        graph.unresolved_references,
        dict(graph.outgoing_edges),
        dict(graph.incoming_edges),
        dict(graph.nodes_by_file),
        dict(graph.nodes_by_qualified_name),
    )


def _forbid(*args, **kwargs):
    raise AssertionError("Module 8 reread source or crossed the boundary")


def _guard_source_reads(guard):
    for owner, name in (
        (builtins, "open"), (io, "open"), (os, "open"),
        (os, "scandir"), (os, "listdir"), (os, "remove"), (os, "unlink"),
        (os, "rename"), (os, "replace"), (os, "mkdir"), (os, "makedirs"),
        (os, "rmdir"), (os, "chmod"), (os, "symlink"), (os, "link"),
        (os, "truncate"), (os, "fdopen"),
        (Path, "open"), (Path, "read_text"), (Path, "read_bytes"),
        (Path, "write_text"), (Path, "write_bytes"), (Path, "stat"),
        (Path, "resolve"), (Path, "iterdir"), (Path, "unlink"),
        (Path, "rename"), (Path, "replace"), (Path, "mkdir"),
        (Path, "rmdir"), (Path, "touch"), (Path, "chmod"),
        (Path, "symlink_to"), (Path, "hardlink_to"),
        (subprocess, "run"), (subprocess, "Popen"), (subprocess, "call"),
        (subprocess, "check_call"), (subprocess, "check_output"),
        (socket, "socket"), (socket, "create_connection"),
        (importlib, "import_module"),
    ):
        guard.setattr(owner, name, _forbid)
```

- [ ] **Step 2: Write the failing integration tests.**

```python
APP = (
    "def helper():\n"
    "    return 1\n"
    "\n"
    "\n"
    "def run():\n"
    "    helper()\n"
)
UTIL = "value = 1\n"


def test_real_pipeline_associates_exact_relative_symbol(tmp_path):
    graph, parsed = _real_graph(tmp_path, {"pkg/app.py": APP, "pkg/util.py": UTIL})
    assert isinstance(graph, CodeGraphInventory)
    assert parsed.success_files == 2
    assert _symbol_node(graph, "run").parse_status is ParseStatus.SUCCESS

    inventory = RuntimeEvidenceIngestor().ingest(_request(), graph)

    assert len(inventory.frames) == 1
    frame = inventory.frames[0]
    run = _symbol_node(graph, "run")
    file_node = _file_node(graph, "pkg/app.py")
    assert frame.relative_path == "pkg/app.py"
    assert frame.path_match_basis is PathMatchBasis.EXACT_RELATIVE_PATH
    assert frame.association is FrameAssociation.SYMBOL
    assert frame.primary_node_id == run.node_id
    assert frame.candidate_node_ids == tuple(
        sorted((file_node.node_id, run.node_id)))
    assert inventory.frames_by_node[run.node_id] == (frame,)
    assert inventory.graph_binding is GraphBinding.NAMESPACE_MATCHED_UNVERSIONED
    assert inventory.declared_revision is None


def test_symbol_free_file_associates_at_file_level(tmp_path):
    stack = (b"Traceback (most recent call last):\n"
             b'  File "pkg/util.py", line 1, in module\n'
             b"ValueError: boom\n")
    graph, _parsed = _real_graph(tmp_path, {"pkg/util.py": UTIL})
    file_node = _file_node(graph, "pkg/util.py")
    assert not [node for node in graph.nodes
                if node.node_kind is GraphNodeKind.SYMBOL
                and node.relative_path == "pkg/util.py"]

    inventory = RuntimeEvidenceIngestor().ingest(
        _request(stack, name="util.log"), graph)

    frame = inventory.frames[0]
    assert frame.path_match_basis is PathMatchBasis.EXACT_RELATIVE_PATH
    assert frame.association is FrameAssociation.FILE
    assert frame.primary_node_id == file_node.node_id
    assert frame.candidate_node_ids == (file_node.node_id,)
    assert inventory.frames_by_node[file_node.node_id] == (frame,)


def test_namespace_mismatch_fails_before_association_and_keeps_graph(
        tmp_path):
    graph, _parsed = _real_graph(tmp_path, {"pkg/app.py": APP})
    before = _graph_snapshot(graph)
    with pytest.raises(RuntimeEvidenceGraphError) as caught:
        RuntimeEvidenceIngestor().ingest(
            _request(namespace="SENTINEL-OTHER-NAMESPACE"), graph)
    assert "SENTINEL" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert _graph_snapshot(graph) == before


def test_ingestion_does_not_reread_source_or_rebuild_the_graph(
        tmp_path, monkeypatch):
    graph, _parsed = _real_graph(tmp_path, {"pkg/app.py": APP})
    request = _request()
    before = _graph_snapshot(graph)
    with monkeypatch.context() as guard:
        _guard_source_reads(guard)
        inventory = RuntimeEvidenceIngestor().ingest(request, graph)
    assert inventory.frames[0].primary_node_id == _symbol_node(graph, "run").node_id
    assert _graph_snapshot(graph) == before
    assert not [value for value in _leaf_values(inventory)
                if isinstance(value, GraphNode)]


def test_ids_are_graph_independent_and_graph_only_changes_association(tmp_path):
    graph, _parsed = _real_graph(tmp_path, {"pkg/app.py": APP})
    request = _request()
    ingestor = RuntimeEvidenceIngestor()
    without_graph = ingestor.ingest(request)
    with_graph = ingestor.ingest(request, graph)

    assert [a.artifact_id for a in without_graph.artifacts] == [
        a.artifact_id for a in with_graph.artifacts]
    assert [e.event_id for e in without_graph.events] == [
        e.event_id for e in with_graph.events]
    assert [f.frame_id for f in without_graph.frames] == [
        f.frame_id for f in with_graph.frames]
    assert without_graph.frames[0].source_reference_sha256 == (
        with_graph.frames[0].source_reference_sha256)
    assert without_graph.frames[0].relative_path == "pkg/app.py"
    assert without_graph.frames[0].association is FrameAssociation.NOT_EVALUATED
    assert without_graph.frames[0].path_match_basis is (
        PathMatchBasis.UNVERIFIED_RELATIVE_PATH)
    assert with_graph.frames[0].association is FrameAssociation.SYMBOL
    assert without_graph.graph_binding is GraphBinding.NOT_PROVIDED
    assert without_graph.frames_by_node == {}
    assert with_graph.frames_by_node


def test_consumer_reads_without_conversion_or_mutation(tmp_path):
    graph, _parsed = _real_graph(tmp_path, {"pkg/app.py": APP})
    inventory = RuntimeEvidenceIngestor().ingest(_request(), graph)

    def consume(value):
        return (
            value.total_events,
            value.total_frames,
            tuple(event.event_id for event in value.events),
            tuple(frame.frame_id for frame in value.frames),
            tuple(sorted(value.frames_by_node)),
            {key: tuple(frame.frame_id for frame in frames)
             for key, frames in value.frames_by_node.items()},
            {key: tuple(event.event_id for event in events)
             for key, events in value.events_by_fingerprint.items()},
        )

    first = consume(inventory)
    assert consume(inventory) == first
    assert type(inventory.frames_by_node) is MappingProxyType
    assert type(inventory.events_by_fingerprint) is MappingProxyType
    for mapping in (inventory.frames_by_node, inventory.events_by_fingerprint):
        with pytest.raises(TypeError):
            mapping["injected"] = ()
```

- [ ] **Step 3: RED.** Run `<RT-TEST> tests/test_runtime_evidence_integration.py -x`. Expected: exact association, file association, namespace, no-reread, or consumer assertions fail; an upstream fixture failure is not a valid RED for the Module 8 assertion.

- [ ] **Step 4: GREEN.** Fix only a real Module 8 association/orchestration defect. Do not change Module 2, 3, or 6, their tests, or their contracts. Do not read source, call the scanner/parser/builder at ingestion, mutate the graph, or replace exact component matching with prefix/substring matching.

- [ ] **Step 5: GREEN command.** Run `<RT-TEST> tests/test_runtime_evidence_integration.py -q`. Expected: all pass.

- [ ] **Step 6: Contract checks.** Confirm `git diff --name-only HEAD~1` lists only `tests/test_runtime_evidence_integration.py` and, only if a real defect was fixed, `association.py`/`ingestor.py`. Confirm no source reread under the guard, graph immutability, namespace failure with fixed message, graph-independent IDs, exact line containment, and no Module 1-5/7 production import in `backend/runtime_evidence`.

- [ ] **Step 7: Commit.** `git add tests/test_runtime_evidence_integration.py backend/runtime_evidence/association.py backend/runtime_evidence/ingestor.py` (omit unchanged production paths); `git commit -m "test: verify runtime evidence with real module 2 3 6 pipeline"`.

- [ ] **Step 8: Independent review checklist.** [ ] Real Module 2 → 3 → 6 graph. [ ] Exact relative symbol association. [ ] FILE-level symbol-free association. [ ] Namespace mismatch fixed/fail-closed. [ ] No source reread/builder/scanner/parser. [ ] Graph unchanged/no graph objects retained. [ ] Graph-independent IDs. [ ] Consumer reads tuples/mappings without conversion or mutation. [ ] No upstream edits. [ ] RED/GREEN evidence recorded.

---

### Task 22: Scaling and Resource-Operation Gates

**Prerequisites:** T21.

**Files:**
- Create: `tests/test_runtime_evidence_scaling.py`
- Modify only if a test exposes a real complexity defect: `backend/runtime_evidence/json_scanner.py`, `backend/runtime_evidence/json_adapter.py`, `backend/runtime_evidence/association.py`, `backend/runtime_evidence/ingestor.py`

**Spec references:** §8.1, §12, §14.3, §17.11.

**Interfaces:**
- No new public names. All assertions are deterministic operation counts, input/output accounting, or exact structure. No wall-clock timing, `time`, `perf_counter`, timeout, or threshold appears.
- `scan_json_structure` must remain an iterative Python scanner; T22 counts executed scanner source lines with `sys.settrace` and requires growth no worse than linear for 32/64/128-field flat objects.
- `parse_json_events` must materialize a selected document exactly once and an over-budget document zero times. JSONL materializes each qualifying line independently; aggregate JSONL nodes are never compared with `max_json_nodes`.
- `AssociationIndex` constructor stores the supplied mappings without copying, so T22 can wrap `files_by_path`/`files_by_basename` with counting mappings. `associate_frames` must use keyed lookups, never iterate a whole graph bucket map per frame.
- `match_symbols_by_line` must sort frame lines once and sweep sorted symbols in linear passes, never compare every symbol with every frame.
- Record budgets must produce exact counters/flags/sentinel counts at scale; indexes and tuples stay immutable; no raw bytes remain.

- [ ] **Step 1: Scaffold** the scaling test module with counting containers and builders. No production scaffolding is needed.

```python
import json
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import pytest

import backend.runtime_evidence.json_adapter as json_adapter
from backend.runtime_evidence import (
    FrameAssociation, PathMatchBasis, RuntimeArtifact, RuntimeArtifactFormat,
    RuntimeArtifactStatus, RuntimeEvidenceConfig, RuntimeEvidenceIngestor,
    RuntimeEvidenceRequest, RuntimeFrameLanguage, RuntimeIssueCode,
)
from backend.runtime_evidence.association import (
    AssociationIndex, GraphFileEntry, GraphSymbolEntry, associate_frames,
    match_symbols_by_line,
)
from backend.runtime_evidence.drafts import FrameDraft
from backend.runtime_evidence.json_adapter import parse_json_events
from backend.runtime_evidence.json_scanner import scan_json_structure


class CountingMapping(Mapping):
    def __init__(self, source):
        self.source = source
        self.counts = Counter()

    def __getitem__(self, key):
        self.counts["get"] += 1
        return self.source[key]

    def get(self, key, default=None):
        self.counts["get"] += 1
        return self.source.get(key, default)

    def __iter__(self):
        self.counts["iter"] += 1
        for key in self.source:
            self.counts["yield"] += 1
            yield key

    def __len__(self):
        return len(self.source)


class CountingTuple(tuple):
    def __new__(cls, source):
        instance = super().__new__(cls, source)
        instance.counts = Counter()
        return instance

    def __iter__(self):
        self.counts["iter"] += 1
        for item in super().__iter__():
            self.counts["yield"] += 1
            yield item


def _config(**overrides):
    values = dict(max_events=1000, max_json_nodes=10000,
                  max_line_bytes=4096, max_issues=100)
    values.update(overrides)
    return RuntimeEvidenceConfig(**values)


def _artifact(name="a.log", content=b"", fmt=RuntimeArtifactFormat.TEXT_LOG,
              language=None):
    return RuntimeArtifact(name, fmt, content, language)


def _jsonl(count, *, text="x"):
    line = json.dumps({"kind": "log", "message": text}).encode()
    return b"\n".join([line] * count) + b"\n"


def _leaf_values(value):
    from dataclasses import fields, is_dataclass
    if is_dataclass(value):
        for field in fields(value):
            yield from _leaf_values(getattr(value, field.name))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield key
            yield from _leaf_values(item)
    elif isinstance(value, tuple):
        for item in value:
            yield from _leaf_values(item)
    else:
        yield value
```

- [ ] **Step 2: Write the failing scaling tests.**

```python
def _flat_object(fields):
    return "{" + ",".join(f'"k{i}":{i}' for i in range(fields)) + "}"


def _scan_line_events(text):
    count = 0
    previous = sys.gettrace()

    def tracer(frame, event, arg):
        nonlocal count
        if event == "line" and Path(frame.f_code.co_filename).name == (
                "json_scanner.py"):
            count += 1
        return tracer

    sys.settrace(tracer)
    try:
        result = scan_json_structure(text)
    finally:
        sys.settrace(previous)
    return count, result


def test_json_structural_scan_work_is_linear_in_input_fields(monkeypatch):
    monkeypatch.setattr(
        json, "loads",
        lambda *args, **kwargs: pytest.fail("scanner materialized JSON"))
    counts = {}
    for fields in (32, 64, 128):
        count, result = _scan_line_events(_flat_object(fields))
        assert result.node_count == 2 * fields + 1
        counts[fields] = count
    assert counts[32] > 0
    assert counts[64] <= 3 * counts[32]
    assert counts[128] <= 3 * counts[64]


def _counted_loads(monkeypatch):
    calls = []
    real = json_adapter.json.loads

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(json_adapter.json, "loads", counted)
    return calls


def test_document_materializes_once_and_over_budget_materializes_zero(
        monkeypatch):
    document = (b"[" + b",".join(
        [b'{"kind":"log","message":"x"}'] * 20) + b"]")
    calls = _counted_loads(monkeypatch)
    result = parse_json_events(document, _config(), None, 100)
    assert len(result.events) == 20
    assert calls == [1]

    calls.clear()
    over = parse_json_events(document, _config(max_json_nodes=5), None, 100)
    assert over.events == ()
    assert [(issue.code, issue.source_line) for issue in over.issues] == [
        (RuntimeIssueCode.MALFORMED_JSON, None)]
    assert calls == []


def test_jsonl_materializes_each_line_once_and_never_sums_nodes(monkeypatch):
    content = _jsonl(10)
    calls = _counted_loads(monkeypatch)
    result = parse_json_events(content, _config(max_json_nodes=5), None, 100)
    assert len(result.events) == 10
    assert len(calls) == 10


def test_basename_buckets_avoid_a_global_scan_per_frame():
    target = GraphFileEntry("target", "pkg/app.py", ("pkg", "app.py"))
    unrelated = tuple(
        GraphFileEntry(f"f{i}", f"pkg{i}/app{i}.py",
                       (f"pkg{i}", f"app{i}.py"))
        for i in range(500))
    by_path = CountingMapping({target.relative_path: target})
    by_base = CountingMapping({
        entry.components[-1]: (entry,) for entry in unrelated + (target,)})
    by_file = CountingMapping({})
    index = AssociationIndex(by_path, by_base, by_file)
    drafts = tuple(
        FrameDraft(RuntimeFrameLanguage.PYTHON, "f", "app.py", 1, None)
        for _ in range(20))

    result = associate_frames(
        drafts, event_id="e" * 64, event_sequence=0, source_line=1,
        repository_namespace="repo", config=RuntimeEvidenceConfig(),
        graph_index=index)

    assert all(frame.path_match_basis is PathMatchBasis.UNIQUE_BASENAME
               for frame in result.frames)
    assert all(frame.relative_path == "pkg/app.py" for frame in result.frames)
    assert all(frame.association is FrameAssociation.FILE
               for frame in result.frames)
    assert by_path.counts["iter"] == 0
    assert by_base.counts["iter"] == 0
    assert by_base.counts["get"] >= 1


def test_line_sweep_never_compares_all_symbols_with_all_frames():
    symbols = CountingTuple(
        GraphSymbolEntry(f"s{i}", i * 10 + 1, i * 10 + 5)
        for i in range(1000))
    lines = tuple(range(1, 1001, 10))

    matches = match_symbols_by_line(lines, symbols)

    assert len(matches) == len(lines)
    assert all(len(match) == 1 for match in matches)
    assert symbols.counts["yield"] <= 2 * len(symbols) + 10


def test_record_outputs_scale_linearly_without_content_deduplication():
    for count in (20, 40):
        inventory = RuntimeEvidenceIngestor(_config()).ingest(
            RuntimeEvidenceRequest(
                "repo", (_artifact("a.log", b"".join(
                    f"line{i}\n".encode() for i in range(count))),)))
        assert inventory.total_events == count
        assert len(inventory.events) == count
        assert len(inventory.artifacts) == 1


def test_event_budget_at_scale_sets_one_issue_and_exact_counters():
    content = b"".join(f"line{i}\n".encode() for i in range(100))
    inventory = RuntimeEvidenceIngestor(
        _config(max_events=10)).ingest(
        RuntimeEvidenceRequest("repo", (_artifact("a.log", content),)))
    assert inventory.total_events == 10
    assert len(inventory.events) == 10
    assert inventory.events_truncated is True
    assert [issue.code for issue in inventory.issues] == [
        RuntimeIssueCode.EVENT_LIMIT_REACHED]
    assert inventory.artifacts[0].status is RuntimeArtifactStatus.PARTIAL


def test_frame_budget_at_scale_keeps_exact_prefix_and_one_issue():
    event = {
        "kind": "error",
        "message": "x",
        "error": {"frames": [
            {"function": f"f{i}", "source": "pkg/app.py", "line": i + 1}
            for i in range(40)]},
    }
    inventory = RuntimeEvidenceIngestor(_config(
        max_frames_per_event=5, max_total_frames=100,
        max_json_nodes=10000)).ingest(RuntimeEvidenceRequest(
            "repo", (_artifact("a.jsonl", json.dumps(event).encode() + b"\n",
                               RuntimeArtifactFormat.JSON_EVENTS),)))
    assert len(inventory.frames) == 5
    assert len(inventory.events) == 1
    assert inventory.events[0].frames_truncated is True
    assert inventory.frames_truncated is True
    assert [issue.code for issue in inventory.issues] == [
        RuntimeIssueCode.FRAME_LIMIT_REACHED]
    assert [frame.ordinal for frame in inventory.frames] == [0, 1, 2, 3, 4]


def test_issue_budget_at_scale_keeps_exact_ordinary_count_and_sentinel():
    content = b"\xff\n" * 40
    inventory = RuntimeEvidenceIngestor(_config(max_issues=5)).ingest(
        RuntimeEvidenceRequest("repo", (_artifact("a.log", content),)))
    assert len(inventory.issues) == 6
    assert inventory.issues_truncated is True
    assert inventory.issues[-1].code is RuntimeIssueCode.ISSUE_LIMIT_REACHED
    assert sum(issue.code is not RuntimeIssueCode.ISSUE_LIMIT_REACHED
               for issue in inventory.issues) == 5
    assert sum(artifact.issue_count for artifact in inventory.artifacts) == 6


def test_indexes_and_tuples_are_immutable_at_scale():
    inventory = RuntimeEvidenceIngestor(_config()).ingest(
        RuntimeEvidenceRequest("repo", (_artifact("a.log", _jsonl(20)),)))
    for mapping in (inventory.events_by_artifact, inventory.events_by_trace_id,
                    inventory.events_by_fingerprint, inventory.frames_by_file,
                    inventory.frames_by_node):
        with pytest.raises(TypeError):
            mapping["injected"] = ()
    for record in (inventory.artifacts, inventory.events, inventory.frames,
                   inventory.issues):
        with pytest.raises(AttributeError):
            record.append(None)


def test_no_raw_bytes_remain_in_the_returned_inventory():
    inventory = RuntimeEvidenceIngestor(_config()).ingest(
        RuntimeEvidenceRequest(
            "repo",
            (_artifact("a.log", b"SENTINEL-RAW-BYTES\n"),)))
    assert not any(isinstance(value, (bytes, bytearray, memoryview))
                   for value in _leaf_values(inventory))
```

- [ ] **Step 3: RED and mutation evidence.** Run `<RT-TEST> tests/test_runtime_evidence_scaling.py -x`. Expected: any genuine operation-count or accounting assertion fails; a collection/import/environment error is invalid. Because several tests characterize existing linear behavior, they may pass immediately. For each immediately passing operation-count test, temporarily introduce the forbidden shape locally, run the named test to observe failure, restore the code, and record both outputs: for the scanner, rescan the prefix inside the field loop; for association, replace the bucket lookup with iteration over `by_base`; for the line sweep, compare every symbol with every line; for the document loader, materialize before the budget check or call `json.loads` twice; for JSONL, materialize the whole content once before per-line parsing. Do not commit the mutation.

- [ ] **Step 4: GREEN.** Fix only a real complexity defect in the declared production files. Preserve all public behavior and exact results. Do not add caches, memoization keyed by artifact text, lazy global state, or timing-based limits. Do not weaken a counter or change a test to accommodate a global scan.

- [ ] **Step 5: GREEN command.** Run `<RT-TEST> tests/test_runtime_evidence_scaling.py -q`. Expected: all pass.

- [ ] **Step 6: Contract checks.** Confirm no wall-clock/timing API in the test or production diff; scanner node counts exact and materialization call counts exact; over-budget document never materialized; JSONL aggregate budget not enforced; association bucket maps never iterated globally; line sweep only linear passes; record budget flags/counters/sentinel exact; indexes/tuples immutable; no bytes retained. Inspect `git diff --name-only HEAD~1`.

- [ ] **Step 7: Commit.** `git add tests/test_runtime_evidence_scaling.py backend/runtime_evidence/json_scanner.py backend/runtime_evidence/json_adapter.py backend/runtime_evidence/association.py backend/runtime_evidence/ingestor.py` (omit unchanged production paths); `git commit -m "test: gate runtime evidence scaling and resource bounds"`.

- [ ] **Step 8: Independent review checklist.** [ ] No wall-clock/timing. [ ] Scanner linear and non-materializing. [ ] Document one load, over-budget zero, JSONL per-line. [ ] Basename/path bucket keyed lookups. [ ] Per-file line sweep. [ ] Event/frame/issue budget exactness. [ ] Linear output, no content dedup. [ ] Immutability. [ ] No retained bytes. [ ] Mutation evidence recorded. [ ] RED/GREEN evidence recorded.

---

### Task 23: README, Module 1–7 Regressions, Full Offline Suite, and Verification Record

**Prerequisites:** T22.

**Files:**
- Modify: `README.md` (append only; lines 1–585 stay byte-identical)
- Create: `docs/superpowers/verification/module-8-runtime-evidence.md`
- Test: the complete Module 8 suite and the existing Module 1–7 suites; no new test file

**Spec references:** §1, §2, §5, §6, §9, §10, §12–§17, §18, §19, §21.

**Interfaces:**
- Append exactly one `## Module 8 — Runtime Evidence` section after the existing
  Module 7 content. Do not renumber, rewrite, or delete any existing section.
- The section must state the bytes-in boundary, public entry points, the three
  formats, JSON event schema, optional Module 6 association, determinism,
  metadata-only privacy mode, every resource limit/default, fatal errors,
  recoverable statuses/issues, explicit non-goals, and the offline test command.
- It must not claim sandboxing, redaction, execution, instrumentation, causal
  attribution, automatic log collection, or a guarantee that Module 8 sanitizes
  evidence.
- The verification document is evidence, not marketing. It records actual
  commands, environment, counts, RED/GREEN observations, failure signatures,
  scope checks, and the pre-existing Module 5 Chroma exception. It must not
  record a PASS for work not run.
- No production or test code changes in T23. If a test fails, stop and fix it in
  the owning earlier task; do not edit assertions in T23.

- [ ] **Step 1: Append the README section.** Use the editor, not a shell rewrite.
  Append exactly the following content after the last existing line; the block
  is literal Markdown to add, not a wrapper to copy:

````markdown
## Module 8 — Runtime Evidence

Module 8 ingests runtime evidence bytes supplied by the caller and returns a
normalized, deterministic inventory of events, stack frames, issues, and
provenance. It does not discover, tail, watch, execute, instrument, or read
application code or logs.

### Bytes-in contract

`RuntimeArtifact` is an immutable caller-supplied byte string plus a logical
`source_name`, a closed `format`, and an optional declared language. The caller
captures and owns the bytes. Module 8 performs no filesystem, subprocess, Git,
network, database, vector-store, LLM, or code-execution operation.

### Public API

```python
from backend.runtime_evidence import (
    RuntimeArtifact,
    RuntimeArtifactFormat,
    RuntimeEvidenceRequest,
    ingest_runtime_evidence,
)

request = RuntimeEvidenceRequest(
    repository_namespace="github.com/acme/app",
    artifacts=(
        RuntimeArtifact(
            source_name="run-42.log",
            format=RuntimeArtifactFormat.TEXT_LOG,
            content=b"2026-10-23T10:00:00Z INFO started\n",
        ),
    ),
)
inventory = ingest_runtime_evidence(request)
print(inventory.total_events)
print(inventory.artifacts[0].status.value)
```

`RuntimeEvidenceIngestor().ingest(request)` is equivalent. Both entry points
accept an optional Module 6 `CodeGraphInventory` as the second argument.

### Formats

| Format | Input |
|---|---|
| `TEXT_LOG` | UTF-8 text; one event per non-blank physical line |
| `JSON_EVENTS` | one JSON object/array document, or JSONL with one object per non-blank line |
| `STACK_TRACE_TEXT` | one or more UTF-8 Python, Java, JavaScript, TypeScript, or TSX (V8) stack blocks; requires a known declared language |

A whole JSON object or array is document mode and its events have
`source_line=None`. A lone JSON object is therefore not a one-line JSONL
artifact. Use one object per non-blank line when line provenance is required.
Malformed JSONL lines are recoverable per-line issues. A whole-content
candidate that is syntactically malformed or invalid UTF-8 falls back to
per-line JSONL recovery; a syntactically valid document that exceeds the node
or number budget, or contains a duplicate key, fails the whole document
without fallback.

### JSON event schema

Common fields: `kind` (required; `log`, `error`, or `span`), `message`,
`timestamp`, `level`, `logger`, `operation`, `trace_id`, `span_id`,
`parent_span_id`, `duration_ms`, `status`, and `tags`.

| Kind | Required |
|---|---|
| `log` | `message` |
| `error` | `message` or `error` |
| `span` | `operation` |

`error` supports `type`, `message`, `stack`, and `frames`. Explicit frame
objects support `function`, `source`, `line`, and `column`. An explicit
`frames` list takes precedence over `error.stack`; an empty list suppresses
stack parsing. `tags` is an object of string values. All text, tags, frames,
nodes, and numbers are structurally bounded by the configuration.

### Optional Module 6 association

Association is optional, deterministic, and **not causal**. It only matches
normalized source paths to a caller-supplied Module 6 graph with the same
repository namespace. It never reads source files or rebuilds the graph.

```python
from pathlib import Path

from backend.code_graph import build_code_graph
from backend.code_parser import CodeParser
from backend.file_scanner import FileScanner
from backend.runtime_evidence import (
    RuntimeArtifact,
    RuntimeArtifactFormat,
    RuntimeEvidenceRequest,
    RuntimeFrameLanguage,
    ingest_runtime_evidence,
)

namespace = "github.com/acme/app"
files = FileScanner().scan(Path("."), repository_namespace=namespace)
graph = build_code_graph(files, CodeParser().parse_inventory(files))
stack = (
    b"Traceback (most recent call last):\n"
    b'  File "pkg/app.py", line 5, in run\n'
    b"ValueError: boom\n"
)
request = RuntimeEvidenceRequest(
    namespace,
    (RuntimeArtifact("trace.log", RuntimeArtifactFormat.STACK_TRACE_TEXT,
                     stack, RuntimeFrameLanguage.PYTHON),),
)
inventory = ingest_runtime_evidence(request, graph)
for node_id, frames in inventory.frames_by_node.items():
    print(node_id, [frame.ordinal for frame in frames])
```

The graph must use exactly the public Module 6 `CodeGraphInventory` shape and
the same namespace. A mismatch is a fatal `RuntimeEvidenceGraphError`. The graph
is not stored in the inventory and is never mutated. `FILE` association is
file-level; `AMBIGUOUS_SYMBOL` is not a symbol claim.

### Determinism

Artifacts are ordered by primary source name `(casefold(), original)` and then
artifact ID; input artifact order never affects output. Events retain
artifact/sequence order; frames retain event/ordinal order; issues retain the
canonical issue order, and the issue-limit sentinel is ordered by that same
key. Tags sort by key and
index keys sort lexically. Fingerprints are SHA-256 over canonical JSON arrays.
Timestamps are opaque strings and are not parsed or compared. Repeated runs
over equal inputs return equal content.

### Privacy and metadata-only mode

Module 8 **does not redact**. Treat artifact bytes as untrusted. With
`retain_event_text=False`, it omits free-form `message`, `error_type`,
`error_message`, `logger`, `operation`, tags, and frame function text after
schema validation and before truncation/fingerprinting. Timestamps, levels,
status, trace/span IDs, duration, source positions, and structural frames are
retained. Public representations, normal logs, and fatal messages omit raw
bytes, free-form text, tags, paths, source names, namespaces, claims,
trace/span IDs, graph contents, and exception text. The caller must sanitize
evidence when redaction is required.

### Resource limits

All limits are structural. There is no processing-time limit, background
worker, thread pool, or network call.

| Config field | Default |
|---|---:|
| `max_artifacts` | 64 |
| `max_namespace_bytes` | 256 |
| `max_artifact_bytes` | 8,388,608 |
| `max_submitted_input_bytes` | 67,108,864 |
| `max_total_input_bytes` | 67,108,864 |
| `max_events` | 50,000 |
| `max_json_nodes` | 1,000,000 |
| `max_json_number_bytes` | 1,024 |
| `max_frames_per_event` | 128 |
| `max_total_frames` | 200,000 |
| `max_issues` | 10,000 |
| `max_line_bytes` | 65,536 |
| `max_source_reference_bytes` | 1,024 |
| `max_message_bytes` | 8,192 |
| `max_error_message_bytes` | 8,192 |
| `max_stack_bytes` | 65,536 |
| `max_frame_function_bytes` | 1,024 |
| `max_tags` | 32 |
| `max_tag_key_bytes` | 128 |
| `max_tag_value_bytes` | 512 |
| `retain_event_text` | `True` |

### Errors and outcomes

Fatal exceptions are `RuntimeEvidenceError` and its subclasses:
`RuntimeEvidenceConfigurationError`, `RuntimeEvidenceValidationError`,
`RuntimeEvidenceLimitError`, `RuntimeEvidenceGraphError`,
`RuntimeEvidenceInvariantError`, and `RuntimeEvidenceIngestionError`. Fatal
messages are fixed and content-free; no partial inventory is returned.

Recoverable artifact statuses are `COMPLETE`, `PARTIAL`, `FAILED`, and
`SKIPPED`. Per-line, per-event, and per-frame failures are reported as issues
such as `INVALID_UTF8`, `MALFORMED_JSON`, `INVALID_EVENT_SCHEMA`,
`EVENT_LIMIT_REACHED`, `FRAME_LIMIT_REACHED`, and `ISSUE_LIMIT_REACHED`.
`PARTIAL` means usable evidence was retained; `FAILED` means no usable event
was produced; `SKIPPED` means the artifact was not parsed because it exceeded
`max_artifact_bytes` or the event budget was already exhausted when its turn
arrived. A request-envelope failure is fatal and returns no inventory.

### Explicit non-goals

Module 8 does not collect logs, read files, execute or instrument code, attach
to processes, run commands, call Git/GitHub, use ChromaDB or another store, call
an LLM, merge duplicate events by content, infer causality, or implement
Modules 9–10. It provides no sandbox and makes no redaction guarantee.

### Tests

Run the offline Module 8 suite with:

```bash
python -m pytest tests -q -k runtime_evidence
```

Normal verification excludes networked markers:

```bash
python -m pytest tests -q -m "not integration and not gemini_live"
```
````

- [ ] **Step 2: Verify the README append is additive and complete.** Run:

```powershell
$base = "9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa"
$numstat = git diff --numstat $base -- README.md
if ($numstat -notmatch '^\d+\s+0\s+README\.md$') { throw "README changed existing lines: $numstat" }
$readme = Get-Content -LiteralPath README.md -Raw
$marker = '## Module 8'
$markerIndex = $readme.IndexOf($marker)
if ($markerIndex -lt 0) { throw "Missing README module marker: $marker" }
$module8 = $readme.Substring($markerIndex)
@(
  '### Bytes-in contract',
  '### Public API',
  '### Formats',
  '### JSON event schema',
  '### Optional Module 6 association',
  '### Determinism',
  '### Privacy and metadata-only mode',
  '### Resource limits',
  '### Errors and outcomes',
  '### Explicit non-goals',
  '### Tests'
) | ForEach-Object {
  if (-not $module8.Contains($_)) { throw "Missing README heading: $_" }
}
if (-not $module8.Contains('python -m pytest tests -q -k runtime_evidence')) {
  throw "Missing Module 8 focused test command"
}
if (-not $module8.Contains('python -m pytest tests -q -m "not integration and not gemini_live"')) {
  throw "Missing Module 8 offline suite command"
}
if ($module8 -notmatch 'does \*\*not redact\*\*') { throw "Missing no-redaction statement" }
if ($module8 -notmatch 'no sandbox') { throw "Missing no-sandbox statement" }
```

The `0` deletion count is mandatory. A nonzero count means an existing Module
1–7 line was changed; restore it and append again.

- [ ] **Step 3: Create the verification record skeleton.** Create
  `docs/superpowers/verification/module-8-runtime-evidence.md` with these exact
  headings. Fill every placeholder from actual output; never write a result that
  was not observed.

```markdown
# Module 8 — Runtime Evidence Verification

## Status

DRAFT — Task 23 evidence collection complete; Task 24 independent implementation
audit pending. This document does not claim a final PASS in this state.

## Environment

| Field | Value |
|---|---|
| Date (UTC) | <ISO-8601> |
| Worktree | <absolute path> |
| Branch | `codex/module-8-runtime-evidence` |
| Base/spec commit | `9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa` |
| Plan commit | <plan commit SHA> |
| Plan commit parent | `9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa` |
| Upstream baseline | `6f25511af8fa1615342a6be3083b09ac3e73d4be` |
| Python | <`& "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe" --version` output> |
| pytest | <`& "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe" -m pytest --version` output> |
| OS | <Windows version/build> |
| `PYTHONPATH` | <absolute worktree> |
| Basetemp | <fresh absolute path outside repositories/worktrees> |
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

Exact `git diff --cached --name-only
9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa` output after staging both
documentation paths (must equal the post-commit `<base>..HEAD` output):
<output>

Forbidden diff check: <no `backend/code_graph`, `backend/code_parser`,
`backend/file_scanner`, `backend/code_chunker`, `backend/embedding_vector_store`,
`backend/git_history`, `backend/repository_loader`, `requirements.txt`, or spec
change; state the command and output>.

## Task commits and focused test evidence

| Task | Commit | Focused selector | RED observation | GREEN result |
|---|---|---|---|---|
| T1 | <sha> | `tests/test_runtime_evidence_models.py` | <exact> | <exact> |
| T2 | <sha> | `tests/test_runtime_evidence_models.py tests/test_runtime_evidence_validation.py` | <exact> | <exact> |
| T3 | <sha> | `tests/test_runtime_evidence_models.py tests/test_runtime_evidence_validation.py` | <exact> | <exact> |
| T4 | <sha> | `tests/test_runtime_evidence_models.py` | <exact> | <exact> |
| T5 | <sha> | `tests/test_runtime_evidence_models.py` | <exact> | <exact> |
| T6 | <sha> | `tests/test_runtime_evidence_identities.py` | <exact> | <exact> |
| T7 | <sha> | `tests/test_runtime_evidence_text.py` | <exact> | <exact> |
| T8 | <sha> | `tests/test_runtime_evidence_stack.py` | <exact> | <exact> |
| T9 | <sha> | `tests/test_runtime_evidence_json.py` | <exact> | <exact> |
| T10 | <sha> | `tests/test_runtime_evidence_json.py` | <exact> | <exact> |
| T11 | <sha> | `tests/test_runtime_evidence_json.py` | <exact> | <exact> |
| T12 | <sha> | `tests/test_runtime_evidence_stack.py` | <exact> | <exact> |
| T13 | <sha> | `tests/test_runtime_evidence_association.py` | <exact> | <exact> |
| T14 | <sha> | `tests/test_runtime_evidence_association.py` | <exact> | <exact> |
| T15 | <sha> | `tests/test_runtime_evidence_association.py` | <exact> | <exact> |
| T16 | <sha> | `tests/test_runtime_evidence_validation.py tests/test_runtime_evidence_models.py tests/test_runtime_evidence_text.py tests/test_runtime_evidence_json.py tests/test_runtime_evidence_stack.py tests/test_runtime_evidence_association.py` | <exact> | <exact> |
| T17 | <sha> | `tests/test_runtime_evidence_validation.py tests/test_runtime_evidence_json.py tests/test_runtime_evidence_stack.py` | <exact> | <exact> |
| T18 | <sha> | `tests/test_runtime_evidence_models.py tests/test_runtime_evidence_security.py` | <exact> | <exact> |
| T19 | <sha> | `tests/test_runtime_evidence_determinism.py tests/test_runtime_evidence_identities.py` | <exact> | <exact> |
| T20 | <sha> | `tests/test_runtime_evidence_models.py tests/test_runtime_evidence_security.py` | <exact> | <exact> |
| T21 | <sha> | `tests/test_runtime_evidence_integration.py` | <exact> | <exact> |
| T22 | <sha> | `tests/test_runtime_evidence_scaling.py` | <exact> | <exact> |
| T23 | this commit (actual SHA recorded in T24) | full Module 8 + Module 1–7 + full offline suite | documentation/regression only | <exact> |
| T24 | final evidence commit (SHA reported after commit) | independent audit and final rerun | not applicable (audit task) | <exact final rerun result> |

## Test inventory

| File | Collected | Result |
|---|---:|---|
| `tests/test_runtime_evidence_models.py` | <n> | <n passed> |
| `tests/test_runtime_evidence_validation.py` | <n> | <n passed> |
| `tests/test_runtime_evidence_identities.py` | <n> | <n passed> |
| `tests/test_runtime_evidence_text.py` | <n> | <n passed> |
| `tests/test_runtime_evidence_json.py` | <n> | <n passed> |
| `tests/test_runtime_evidence_stack.py` | <n> | <n passed> |
| `tests/test_runtime_evidence_association.py` | <n> | <n passed> |
| `tests/test_runtime_evidence_determinism.py` | <n> | <n passed> |
| `tests/test_runtime_evidence_security.py` | <n> | <n passed> |
| `tests/test_runtime_evidence_integration.py` | <n> | <n passed> |
| `tests/test_runtime_evidence_scaling.py` | <n> | <n passed> |

## Module 8 focused suite

Command:

```powershell
<exact command>
```

Observed:
<exact summary, skips, warnings>

## Determinism, association, and privacy evidence

- Repeated-run equal-content: <result>
- Artifact permutation invariance: <result>
- Input-order preservation: <result>
- Timestamp opacity: <result>
- Fingerprint grouping/occurrence preservation: <result>
- Issue-limit sentinel ordering/copy: <result>
- Real Module 2 → 3 → 6 exact symbol association: <result>
- FILE-only symbol-free association: <result>
- Namespace mismatch/fail-closed: <result>
- No source reread/graph mutation: <result>
- Metadata-only projection before truncation/fingerprint: <result>
- Redacted reprs/log/errors/runtime no-I/O guard: <result>

## Scaling and resource evidence

Record only deterministic counts; no wall-clock timing.

- JSON structural scan line counts (32/64/128 fields): <exact>
- Document `json.loads` calls (valid/over-budget): <exact>
- JSONL per-line `json.loads` calls: <exact>
- Association `files_by_path`/`files_by_basename` iteration counts: <exact>
- `match_symbols_by_line` yielded-symbol counts: <exact>
- Event/frame/issue budget counters/flags/sentinel: <exact>
- Output counts at 20/40 records: <exact>
- Index/tuple immutability: <exact>
- No retained bytes: <exact>
- Mutation-check outputs (restored immediately): <exact>

## Module 1–7 regression evidence

| Module | Selector | Result |
|---|---|---|
| 1 | `tests/test_repository_loader.py` | <exact> |
| 2 | `tests/test_file_scanner.py` | <exact> |
| 3 | `tests/test_code_parser.py tests/test_code_parser_dependencies.py` | <exact> |
| 4 | `tests/test_code_chunker.py` | <exact> |
| 5 | `tests -q -k "embedding or semantic or vector_store or chroma"` | <exact> |
| 6 | `tests -q -k code_graph` | <exact> |
| 7 | `tests -q -k git_history` | <exact> |

Each command uses its own canonical fresh basetemp, `PYTHONPATH` to the Module 8
worktree, `-p no:cacheprovider`, and `-m "not integration and not gemini_live"`.

## Full offline suite attempt

Command:

```powershell
<exact command>
```

Observed summary:
<exact>

Failure set:
<exact node IDs and sanitized traceback signature>

Baseline failure set:
<exact>

Difference:
<exact; must be empty or a documented Chroma-baseline subset with the same
call site and signature; any other difference means Module 8 regressed>

## Module 5 Chroma isolation

- Baseline failing node ID(s): <the six observations recorded in
  `module-7-git-history-analyzer.md`, copied verbatim: four full-suite nodes and
  two aggregate/diagnostic nodes>
- Baseline signature: `VectorStoreReadError: Vector store data could not be
  read.` at `backend/embedding_vector_store/stores/chroma.py:1255`, wrapping
  `chromadb.errors.InternalError: Error executing plan: Internal error: Error
  creating hnsw segment reader: Nothing found on disk`
- Current failing node ID(s): <exact; may be empty or any documented subset>
- Current signature: <exact; must be the same call site and signature for every
  current failure>
- Difference: <empty when the current set is a documented subset with the same
  call site/signature; any other difference blocks T23>
- Environment: ChromaDB `1.5.9`, Foyer `0.17.4`, Python `3.12.14` (record actual)
- Disposition: pre-existing upstream ChromaDB/Foyer manifest-read failure. It is
  not waived, xfailed, skipped, retried, monkeypatched, or attributed to Module
  8. Module 8 does not import or depend on Module 5. No Module 5 file changed.

## Static, packaging, and scope gates

| Gate | Command | Result |
|---|---|---|
| Compile | `& "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe" -m compileall -q backend/runtime_evidence` | <exact> |
| Dependencies | `& "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe" -m pip check` | <exact> |
| Whitespace | `git diff --check` | <exact> |
| Imports | 16-file AST audit | <exact> |
| File count | 16 production `.py` files | <exact> |
| Public exports | exactly 28 names | <exact> |
| Upstream untouched | `git diff --name-only 9803be4..HEAD` | <exact> |
| Spec untouched | `git diff --name-only 9803be4..HEAD -- docs/superpowers/specs` | <empty> |
| Status | `git status --short` | <clean or exact> |

## Independent audit

Pending Task 24.

Reviewer session ID: <pending>

Specification compliance: <pending>

Implementation/test quality: <pending>

Critical/Important findings and dispositions: <pending>

Residual Minor/Nit risks: <pending>

## Conclusion

DRAFT — do not claim completion until Task 24 records both required PASS
verdicts and the final rerun.
```

- [ ] **Step 4: Run the Module 8 suite and collect evidence.**

```powershell
$env:PYTHON = "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe"
$env:PYTHONPATH = (Resolve-Path .).Path
function New-RTBasetemp([string]$prefix) {
  $path = Join-Path $env:TEMP ($prefix + "-" + [guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Path $path -Force | Out-Null
  return $path
}
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-collect"
& $env:PYTHON -m pytest tests -q -k runtime_evidence --collect-only -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-focused"
& $env:PYTHON -m pytest tests -q -k runtime_evidence -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
```

Expected: the 11 Module 8 test files are collected; record the exact collected
test count and the exact passed/skipped/failed counts from the focused run. All
Module 8 tests pass and no live/network test is selected.

Then run the four evidence groups separately so a later failure is attributable.
Each command gets its own fresh basetemp:

```powershell
$env:PYTHON = "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe"
$env:PYTHONPATH = (Resolve-Path .).Path
function New-RTBasetemp([string]$prefix) {
  $path = Join-Path $env:TEMP ($prefix + "-" + [guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Path $path -Force | Out-Null
  return $path
}
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-determinism"
& $env:PYTHON -m pytest tests/test_runtime_evidence_determinism.py -q -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-security"
& $env:PYTHON -m pytest tests/test_runtime_evidence_security.py -q -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-integration"
& $env:PYTHON -m pytest tests/test_runtime_evidence_integration.py -q -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-scaling"
& $env:PYTHON -m pytest tests/test_runtime_evidence_scaling.py -q -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
```

- [ ] **Step 5: Run every Module 1–7 regression selector.** Assign a new GUID
  basetemp directory immediately before each selector; never reuse a basetemp
  across selectors. Run each selector exactly as listed in the verification
  table. Expected: Modules 1–4, 6, and 7 pass with the same documented
  skips/warnings as the Module 7 baseline. The Module 5 selector may report only
  the pre-existing ChromaDB/Foyer manifest-read failure documented in
  `docs/superpowers/verification/module-7-git-history-analyzer.md`: the same
  `VectorStoreReadError` call site at
  `backend/embedding_vector_store/stores/chroma.py:1255`, wrapping the
  `Nothing found on disk` condition, in unchanged Module 5 tests. Because that
  failure is nondeterministic, the observed failing nodes may be any documented
  subset of the six recorded observations, including the four full-suite node
  IDs and the two additional aggregate/diagnostic observations; do not require
  a fixed node ID. Record a clean Module 5 result too if no failure appears;
  never invent one. Any other failure, any different signature/call site, or
  any Module 8 involvement is a regression; fix the owning Module 8 code/test
  before continuing.

- [ ] **Step 6: Attempt the complete offline suite.**

```powershell
$env:PYTHON = "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe"
$env:PYTHONPATH = (Resolve-Path .).Path
$env:RT_BASETEMP = Join-Path $env:TEMP ("trace-rag-module8-full-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $env:RT_BASETEMP -Force | Out-Null
& $env:PYTHON -m pytest tests -q -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
```

Record the full summary and every failing node ID. Compare each failure to the
six Chroma observations recorded in
`docs/superpowers/verification/module-7-git-history-analyzer.md`. The current
full-suite failure set must be a possibly empty subset of those documented node
IDs, and each failure must show the same `VectorStoreReadError` at
`backend/embedding_vector_store/stores/chroma.py:1255` with the same
`Nothing found on disk` manifest-read signature and publish/inspection call
path. Because the upstream failure is nondeterministic, which subset appears
may vary between attempts; do not require all six, one fixed node, or an exact
count. Any node outside the documented set, any different call site/signature,
and any Module 8 involvement blocks T23. Do not change Module 5,
xfail/skip/retry the failing test, monkeypatch Chroma, or remove tests. If the
pre-existing failure is absent, record an empty failure set and do not invent
one.

- [ ] **Step 7: Run static, packaging, and scope gates.**

```powershell
$env:PYTHON = "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe"
& $env:PYTHON -m compileall -q backend/runtime_evidence
& $env:PYTHON -m pip check
git diff --check
git diff --name-only 9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa..HEAD
git diff --name-only 9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa..HEAD -- docs/superpowers/specs
git status --short
```

Expected: compile exits 0; `pip check` reports no broken requirements; diff
check is clean; committed changed paths equal the expected 28-path list (plan,
16 production files, 11 test files), because README and the verification record
are not committed until Step 8; the spec diff is empty; no Modules 1–7
production/test path changed; no dependency file changed; status shows exactly
the modified README and the new verification record. Record exact output. The
final 30-path diff and clean status are verified in Step 8 after the commit.

- [ ] **Step 8: Fill the verification record and commit.**

Fill every placeholder that T23 can observe (the plan commit is the first commit
after `9803be4` in `git log --reverse`; record its SHA and confirm its parent);
leave the T24 audit fields and the `DRAFT` conclusion pending. Do not write a
PASS for a command that was not run.
The T23 and T24 commit-SHA cells are self-referential: keep `this commit` and
`final evidence commit` respectively rather than inventing a SHA. Then:

```powershell
$base = "9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa"
git add README.md docs/superpowers/verification/module-8-runtime-evidence.md
git diff --cached --check
git diff --cached --name-only
git diff --cached --name-only $base
```

Confirm exactly two staged paths. Record the exact 30-path output of
`git diff --cached --name-only $base` in the verification record, then stage
that edit again. Confirm `git diff --cached --name-only` still lists exactly
two paths and `git diff --cached --name-only $base` still lists exactly 30
paths. Commit, then verify:

```powershell
git commit -m "docs: document module 8 runtime evidence and verification"
git status --short
git diff --name-only 9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa..HEAD
git rev-parse HEAD^
```

Expected: clean status; the post-commit 30-path diff matches the recorded
staged output exactly; `HEAD^` is the T22 commit. If the diff differs, stop and
fix the evidence; do not silently amend the commit.

- [ ] **Step 9: Independent review checklist.** [ ] README additive only/no
  deletions. [ ] All required headings and all 21 config defaults (20 limits
  plus `retain_event_text`) documented. [ ] Public API
  and graph example match real exports. [ ] No sandbox/redaction/causality
  overclaim. [ ] JSON document/JSONL semantics exact. [ ] Metadata-only fields
  exact. [ ] Errors/statuses exact. [ ] All 11 Module 8 files pass. [ ] Module
  1–7 selectors all recorded. [ ] Full offline suite attempted. [ ] Chroma
  failures are a documented baseline subset with the same call site/signature.
  [ ] Static/packaging/scope gates recorded.
  [ ] No Module 1–7 or spec changes. [ ] Verification doc is DRAFT only. [ ]
  Commit contains exactly README and the verification doc.

---

### Task 24: Independent Final Audit and Closure

**Prerequisites:** T23.

**Files:**
- Modify: `docs/superpowers/verification/module-8-runtime-evidence.md` (final audit record)
- Test: full Module 8 suite, full Module 1–7 regression set, full offline suite attempt
- Fix only if the auditor finds a Critical/Important defect: the owning production file and its test file

**Spec references:** all sections; especially §17, §18, §19, §20, §21.

**Interfaces:**
- Candidate is frozen before audit: clean worktree, exact HEAD, no uncommitted
  changes. The auditor is a fresh-context `general` subagent with no prior
  Module 8 session. It must not edit files.
- Required exact verdict lines:
  `Specification compliance: PASS` and
  `Implementation/test quality: PASS`, each on its own line.
- Both must be PASS and the audit must have no Critical or Important findings.
  Minor/Nit findings may remain only when documented with impact and a concrete
  future task, and must not affect public contract, security, determinism, or
  test validity.
- If either verdict is FAIL, or any Critical/Important finding exists, fix the
  finding under strict TDD, rerun the affected gates, and re-review with the
  same reviewer session. Do not downgrade a finding to pass the audit.

- [ ] **Step 1: Freeze the candidate and record state.**

```powershell
git status --short
git rev-parse HEAD
git log --oneline -3
git diff --check
```

Expected: clean status, HEAD is the T23 commit, no untracked files. If any
untracked file exists, stop and resolve its ownership; do not commit it blindly.

- [ ] **Step 2: Run the final local gate set.** Use a separate fresh basetemp
  outside the worktree and all repositories for each pytest command:

```powershell
$env:PYTHON = "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe"
$env:PYTHONPATH = (Resolve-Path .).Path
function New-RTBasetemp([string]$prefix) {
  $path = Join-Path $env:TEMP ($prefix + "-" + [guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Path $path -Force | Out-Null
  return $path
}
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-final-focused"
& $env:PYTHON -m pytest tests -q -k runtime_evidence -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-final-full"
& $env:PYTHON -m pytest tests -q -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-final-m1"
& $env:PYTHON -m pytest tests/test_repository_loader.py -q -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-final-m2"
& $env:PYTHON -m pytest tests/test_file_scanner.py -q -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-final-m3"
& $env:PYTHON -m pytest tests/test_code_parser.py tests/test_code_parser_dependencies.py -q -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-final-m4"
& $env:PYTHON -m pytest tests/test_code_chunker.py -q -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-final-m5"
& $env:PYTHON -m pytest tests -q -k "embedding or semantic or vector_store or chroma" -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-final-m6"
& $env:PYTHON -m pytest tests -q -k code_graph -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
$env:RT_BASETEMP = New-RTBasetemp "trace-rag-module8-final-m7"
& $env:PYTHON -m pytest tests -q -k git_history -p no:cacheprovider --basetemp $env:RT_BASETEMP -m "not integration and not gemini_live" -rs
& $env:PYTHON -m compileall -q backend/runtime_evidence
& $env:PYTHON -m pip check
git diff --check
```

Record exact counts, failure nodes, and the Chroma baseline comparison. The
full-suite failure set must be empty or a documented subset of the six
pre-existing Chroma observations, with the same call site, signature, and
unchanged Module 5 test paths. The Module 1–7 selectors run exactly as in T23
Step 5, each with its own fresh basetemp; the Module 5 selector may show only
the documented baseline subset.

- [ ] **Step 3: Dispatch the independent auditor.** Use `subagent` with
  `agent: "general"`, a fresh session (no `sessionID`), and the following
  prompt, replacing placeholders with absolute paths and observed evidence:

```text
You are the independent Module 8 implementation auditor for TraceRAG. Do not
modify any file, run no implementation command, and do not trust the
implementer's claims.

Read:
- Normative design: <absolute spec path>
- Approved plan: <absolute plan path>
- Implementation diff: <git diff 9803be4..HEAD>
- Public package: <absolute backend/runtime_evidence path>
- All 11 Module 8 tests: <absolute tests paths>
- README Module 8 section: <absolute README path>
- Verification evidence: <absolute verification doc path>

Audit against every design section, including the 21 requested closure items:
1 bytes-in boundary; 2 exact formats/schema; 3 deterministic ordering/fingerprints;
4 artifact/event/frame identity; 5 optional Module 6 association and namespace;
6 validation/limits/statuses/issues; 7 no execution/instrumentation/I/O/Git/
network/persistence/Chroma; 8 metadata-only projection; 9 redacted repr/log/
error behavior; 10 no retained bytes; 11 public exports/API; 12 README accuracy;
13 Module 1-7 isolation; 14 strict TDD evidence; 15 all 11 test files;
16 determinism/permutation/repeated-run tests; 17 security/privacy tests;
18 scaling operation-count tests; 19 real Module 2->3->6 integration;
20 full offline suite and Chroma isolation; 21 no merge/push/Module 9-10 scope.

You may run read-only commands (`git diff`, `git status`, `Get-Content`,
`pip check`) and collection-only inspection with the fixed interpreter
`C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe`,
`$env:PYTHONDONTWRITEBYTECODE = "1"`, and
`& "C:\Users\avane\Desktop\Studies\Projects\Trace_Rag\.venv\Scripts\python.exe" -m pytest --collect-only -p no:cacheprovider`.
Do not run `compileall`,
execute tests, write files or bytecode, use the network, mutate Git, or edit any
file.

Return exactly these fields:
Specification compliance: PASS or FAIL
Implementation/test quality: PASS or FAIL
Critical findings: none or numbered findings with file:line and contract impact
Important findings: none or numbered findings with file:line and contract impact
Minor findings: none or numbered findings with file:line and risk
Evidence reviewed: commands/files and observed results
Final: MODULE 8 IMPLEMENTATION AUDIT APPROVED or MODULE 8 IMPLEMENTATION AUDIT REJECTED

FAIL if any normative requirement is missing, any public behavior is invented,
any test is vacuous/tautological/weakened, any Critical/Important issue exists,
the full-suite failure set contains any failure outside the documented six-node
Chroma baseline subset or any different call site/signature, or evidence is
unreproducible. Do not edit files to make the audit pass.
```

Record the returned reviewer session ID verbatim.

- [ ] **Step 4: Resolve findings.** For each Critical/Important finding:
  add a failing regression test first; run it to observe RED; fix only the
  owning code; run GREEN; rerun the task's focused suite, the full Module 8
  suite, and any Module 1–7 selector affected by the fix. Commit code and test
  together with `fix: resolve module 8 independent audit findings`. For a
  documentation-only finding, correct the documentation and evidence; do not
  change production.
  Minor findings may be deferred only when they do not affect the public
  contract, security, determinism, or test validity; record impact and a future
  task. Never suppress, xfail, skip, retry, or weaken a test, and never change
  the spec to match the implementation.

- [ ] **Step 5: Re-review after any fix.** Continue the same reviewer session;
  provide the new diff, test output, and finding dispositions. Re-run until both
  exact verdict lines are PASS and there are no Critical/Important findings.
  A different reviewer or a fresh session invalidates the continuity of the
  finding disposition; if one is used, record why and re-audit the full scope.

- [ ] **Step 6: Update the verification document.** Replace `DRAFT` with the
  final result and fill every pending audit field:

```markdown
## Independent audit

Reviewer session ID: <exact>

Specification compliance: PASS

Implementation/test quality: PASS

Critical/Important findings and dispositions:
<list every finding with severity, evidence, fix commit, regression test, and
rerun result, or "None">

Residual Minor/Nit risks:
<list each with impact and future task, or "None">

Audit scope: <diff, files, commands, test counts, full-suite failure set>

Evidence commit parent: <T23 or last audit-fix commit SHA>

Evidence commit: final evidence commit (SHA reported after commit)

## Conclusion

Module 8 is implemented and independently audited. The full-suite failure set is
either empty or a documented subset of the pre-existing Module 5 ChromaDB/Foyer
manifest-read exception with the same call site and signature; it is isolated
and not waived. No merge, push, retrieval, or LLM work occurred.
```

Add the final local gate summary, the actual T23 and audit-fix commit SHAs,
exact test counts, the Chroma baseline comparison, and the evidence commit
parent. Replace T23's
`this commit` cell with its actual SHA now that it exists. The T24 evidence
commit cannot embed its own SHA: keep that cell as `final evidence commit` and
report the resolved SHA in the final response. Do not claim PASS unless both
reviewer verdicts and the final rerun are recorded.

- [ ] **Step 7: Commit the final evidence and verify closure.**

```powershell
git add docs/superpowers/verification/module-8-runtime-evidence.md
git diff --cached --check
git diff --cached --name-only
git commit -m "docs: record module 8 independent verification"
git status --short
git rev-parse HEAD
git rev-parse HEAD^
```

Expected: exactly one staged path; clean worktree; direct parent is the T23 or
last audit-fix commit. Report the `HEAD` SHA from this command in the final
response; do not try to write it back into the same commit.

- [ ] **Step 8: Final closure checks.** Confirm:

  - All 11 Module 8 test files pass.
  - All Module 1–7 selectors pass with the same pre-existing skips/warnings,
    except the Module 5 selector may show a documented Chroma-baseline subset.
  - The full offline suite was attempted; its failure set is empty or a
    documented subset of the six pre-existing Chroma observations, each with the
    same call site/signature and no Module 5 change.
  - 16 production files exist; public package exports exactly 28 names; AST
    audit allows only stdlib plus public `backend.code_graph`.
  - `compileall`, `pip check`, `git diff --check`, scope audit, spec diff, and
    dependency diff are clean.
  - README is additive and accurate.
  - Both exact reviewer verdicts are PASS with no Critical/Important findings.
  - Verification document records reviewer session ID, findings, fixes, counts,
    the T23 and audit-fix SHAs, and the evidence commit parent; the T24 evidence
    commit SHA is reported in the final response.
  - Primary `main` is still `6f25511af8fa1615342a6be3083b09ac3e73d4be`; all five
    worktrees and all pre-existing untracked artifact paths/files are preserved.
  - No merge, push, fetch, rebase, reset, clean, Module 9, or Module 10 action
    occurred anywhere.
  - Stop after reporting. Do not merge or push.

After the evidence commit, run:

```powershell
git diff --name-only 9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa..HEAD
git diff --name-only 9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa..HEAD -- docs/superpowers/specs
git diff --name-only 9803be4fcd0ddd792c5ff0fdfe2238596e3bd6aa..HEAD -- requirements.txt pytest.ini
```

Confirm the first output is exactly the 30 approved paths plus only files
explicitly changed by documented audit fixes; the spec diff and dependency diff
are empty. Record the output in the final report.

- [ ] **Step 9: Independent review checklist.** [ ] Candidate frozen before
  audit. [ ] Fresh `general` reviewer with recorded session ID. [ ] Exact two
  PASS verdicts/no Critical/Important. [ ] Every finding disposition has a fix
  commit/regression test or documented Minor risk. [ ] Final rerun after fixes.
  [ ] Verification document has no placeholders and no false PASS. [ ] Exactly
  one final evidence path committed. [ ] Clean worktree/direct parent. [ ] All
  isolation and preservation checks. [ ] No merge/push/Module 9–10. [ ] Report
  and stop.

---
