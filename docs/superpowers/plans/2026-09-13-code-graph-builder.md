# Code Graph Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Module 6 Code Graph Builder that converts validated Module 2 `FileInventory` plus Module 3 `CodeParseInventory` into a deterministic, immutable `CodeGraphInventory` containing conservative internal graph relationships and explicit unresolved-reference evidence.

**Architecture:** Validate inventories, create file/symbol nodes and reusable indexes, and construct direct structural edges. Resolve all imports before calls and type relationships through separate Python, Java, and ECMAScript resolvers, then materialize unresolved evidence. Validate the graph, canonicalize tuples, and derive immutable final indexes before returning any inventory.

**Tech Stack:** Python 3.12+, stdlib where possible, existing TraceRAG Module 2/3 contracts, pytest. No NetworkX or new graph database dependency.

**Spec:** `docs/superpowers/specs/2026-09-13-code-graph-builder-design.md`

## Global Constraints

- The approved spec at `59f38441e416d162ab3957ffb288ccdc36b39363` is binding. Stop for a true contradiction or material upstream deficiency; do not redesign it.
- This plan-writing change creates only this document. Execution requires user review first; no feature branch/worktree, production code, or tests are created now.
- During execution use a separately authorized isolated checkout through the execution workflow. Do not implement on the documentation-only main branch.
- Module 6 depends only on Modules 2 and 3. Do not import Module 4/5 production code or modify Modules 1–5 production behavior.
- Public input is `FileInventory` plus `CodeParseInventory`, with no path parameter or source-reading callback.
- Require equal nonempty string namespaces; upstream `None` is rejected. Absolute repository paths do not determine IDs.
- FILE and SYMBOL nodes only; use upstream `SymbolKind`. Callable endpoints are FUNCTION, METHOD, CONSTRUCTOR only.
- Preserve exact identity arrays from the spec: canonical JSON with `ensure_ascii=False`, `separators=(",", ":")`, UTF-8, lowercase SHA-256 hex.
- Preserve upstream spelling and original-file locations, including BOM-adjusted bytes. JSX uses `ParsedLanguage.JAVASCRIPT`.
- Exactly one proven internal target yields an edge. Multiple eligible targets yield zero edges and one AMBIGUOUS reference, with sorted candidate IDs.
- A stronger ambiguous/unsupported binding blocks weaker matches. No fuzzy, first-candidate, global unqualified-name, traversal, shortest-path, or lexical-sort winner.
- PARTIAL trusted evidence survives; relevant missing evidence is INCOMPLETE_PARSE. FAILED retains its FILE with no fabricated references.
- Immutable canonical tuples remain authoritative; final mappings cannot retain externally mutable backing maps.
- No source rereads, project execution, dynamic imports, eval/exec, network, Gemini, Chroma, package-manager invocation, or unsafe deserialization in graph building.
- No persistence, graph database, NetworkX, compiler packages, new runtime dependency, or Modules 7–10 functionality.
- Every execution commit is meaningful and unsquashed. Never push as part of this plan without separate authorization.
- All normal verification is offline and excludes both `integration` and `gemini_live`; no Gemini calls.

## Current codebase map and execution conventions

Inspected `backend/file_scanner/`, `backend/code_parser/`, `backend/code_chunker/`,
`backend/embedding_vector_store/`, their relevant tests, `requirements.txt`,
`pytest.ini`, and `README.md`. The approved spec commit remains
`59f38441e416d162ab3957ffb288ccdc36b39363`. The implementation-plan commit and
intended Module 6 execution base is `a8d9d3411e4249750dfd7b719a8a141a68825197`.
Execution must branch/worktree from that plan commit, not from origin/main or
the earlier spec-only commit. Task 18 must record the actual execution base
from Git rather than blindly assuming this hash. These distinctions do not
change spec authority. origin/main remains
`52e74a79d92ee947f6d246a18b959931c877cd0f`.

- Scanner public models/exports live in `backend/file_scanner/models.py` and
  `__init__.py`. `FileScanner.scan(path, *, repository_namespace=None)` is the
  real upstream integration entry point; scanning reads files before graph build.
- Parser public models/exports live in `backend/code_parser/models.py` and
  `__init__.py`. `CodeParser.parse_inventory(file_inventory)` is the integration
  entry point. `parser.py` requests SOURCE/TEST entries, records unsupported
  selectors in `skipped`, and emits empty extraction collections for FAILED.
- `registry.py` maps python/.py, java/.java, javascript/.js and .jsx,
  typescript/.ts, typescript/.tsx to PYTHON/JAVA/JAVASCRIPT/TYPESCRIPT/TSX.
  Do not initialize `ParserRegistry` inside Module 6.
- Python extraction stores direct imports with the imported module repeated
  in an `ImportBinding`; no import-form flag distinguishes all from-imports.
  Java imports carry module strings, empty bindings and optional `static`.
  Java packages are encoded in symbol qualified names. ECMAScript default and
  namespace imports use `default` and `*` binding markers; modifiers can mark
  declared exports, but there is no export map for arbitrary re-exports.
- `extractors/base.py:source_location` translates to original-file byte ranges,
  adjusts first-line columns for BOM, and uses one-based lines/byte columns.
  Truncation yields an EXTRACTION_ERROR issue that can have no location.
- Module 4 has useful frozen-model and hashing patterns in `models.py` and
  `identity.py`. Its `validation.py` must NOT be reused: it requires sorted
  inputs, equal repository paths and successful-parse digests beyond this spec.
  Module 6 accepts permutations and validates optional digest syntax only.
- Module 5 models validate immutable nested values; scaling tests use counters
  and security tests scope broad API patches. Copy the testing technique, not
  dependencies on its provider/store implementation.
- `requirements.txt` already pins Tree-sitter 0.25.2 and five language grammar
  distributions, google-genai 2.22.0 and chromadb 1.5.9. Do not change it.
- `pytest.ini` defaults to `-m "not integration and not gemini_live"`.
  A local pipeline test must NOT use the `integration` marker (that marker
  means network cloning here). Module 3's grammar gate is
  `tests/test_code_parser_dependencies.py`.
- Existing `.venv/Scripts/python.exe` reports Python 3.12.14. In the execution
  checkout activate its prepared Python 3.12+ environment, then use `python`
  for the exact commands below. Do not install dependencies as a resolver step.

Actual upstream fields used throughout:

```text
FileInventory(repository_path, total_files_seen, included_files, ignored_files,
              files, ignored, skipped_directories, repository_namespace=None)
ScannedFile(relative_path, filename, extension, language, category, size_bytes)
CodeParseInventory(repository_path, total_files_requested, success_files,
                   partial_files, failed_files, skipped_files, files, skipped,
                   repository_namespace=None)
ParsedFile(relative_path, language, status, symbols, imports, calls, issues,
           source_sha256=None)
SymbolInfo(name, kind, qualified_name, parent_qualified_name, location,
           parameters, return_type, modifiers, base_types, implemented_types)
ParameterInfo(name, type_name, default_value_text)
ImportInfo(module, bindings, is_wildcard, modifiers, location)
ImportBinding(imported_name, alias)
CallSite(caller_qualified_name, callee_text, kind, location)
SourceLocation(start_byte, end_byte, start_line, start_column, end_line, end_column)
ParseIssue(kind, message, location)
```

No call argument arity/types, receiver binding, import-form flag, package field,
per-base target location, or normalized alias field may be invented upstream.

## File ownership map

All production paths below are new; existing upstream files stay read-only.

- `backend/code_graph/__init__.py`: public exports (Tasks 1, 12, 17).
- `backend/code_graph/models.py`: four public enums and four immutable models (1).
- `backend/code_graph/exceptions.py`: root plus four specified exceptions (1).
- `backend/code_graph/identities.py`: exact canonical identities (2).
- `backend/code_graph/validation.py`: input validation and validated pairs (3).
- `backend/code_graph/indexes.py`: nodes, reusable private indexes, final index derivation (4, 12).
- `backend/code_graph/structure.py`: direct parent selection and structural edges (4).
- `backend/code_graph/references.py`: reference extraction, ownership, resolution materialization (5, 12).
- `backend/code_graph/invariants.py`: graph/index checks before publication (12).
- `backend/code_graph/builder.py`: public multi-pass orchestration (12).
- `backend/code_graph/resolvers/__init__.py`: closed language dispatch (12).
- `backend/code_graph/resolvers/base.py`: shared outcome/binding contracts and reason selection (5).
- `backend/code_graph/resolvers/python.py`: Python imports then calls/types (6, 7).
- `backend/code_graph/resolvers/java.py`: Java imports/types then calls (8, 9).
- `backend/code_graph/resolvers/ecmascript.py`: ECMAScript imports then calls/types (10, 11).

The three additional private modules (`structure`, `references`, `invariants`)
keep `builder.py` orchestration-only without changing approved boundaries.

Test ownership:

- `tests/conftest.py`: new opt-in `cg` in-memory fixture only (1); no autouse hooks.
- `tests/test_code_graph_models.py` (1, 12, 17).
- `tests/test_code_graph_identities.py` (2).
- `tests/test_code_graph_validation.py` (3, 14).
- `tests/test_code_graph_builder.py` (4, 5, 12).
- `tests/test_code_graph_python.py` (6, 7).
- `tests/test_code_graph_java.py` (8, 9).
- `tests/test_code_graph_ecmascript.py` (10, 11).
- `tests/test_code_graph_partial_parses.py` (13).
- `tests/test_code_graph_determinism.py` (14).
- `tests/test_code_graph_security.py` (15).
- `tests/test_code_graph_scaling.py` (16).
- `tests/test_code_graph_integration.py` (17).

Documentation modifications during execution: append Module 6 usage to
`README.md` in Task 17; create `docs/superpowers/verification/module-6-code-graph-builder.md`
with actual evidence in Task 18. Do not rewrite this spec or add another plan.

## Task order and RED/GREEN discipline

Execute 1 -> 2 -> 3 -> 4 -> 5; language work 6 -> 7, 8 -> 9, and 10 -> 11
consumes 1–5. Use the numeric order to avoid shared-file concurrent edits.
Task 12 assembles all resolvers; 13–16 harden the public pipeline; 17 verifies
real upstream integration and documentation; 18 is the final acceptance gate.

Each parameterized case listed in a task is a separate small test-first cycle:
write that case, run RED, implement its branch, and run GREEN before the next
case. Snippets show the concrete starting test and implementation nucleus;
the explicit case lists are mandatory, not optional coverage suggestions.
Do not commit temporary failing stubs, mutation probes, or skipped new tests.
When a regression test is already GREEN, prove sensitivity with a temporary
local fault in the relevant branch, observe RED, restore it, then rerun GREEN.
Do not claim a missing test file or unrelated environment error as useful RED.

Each task's diff and staging commands name its files; inspect staged filenames
before committing. Stop if unrelated changes appear. Commands are run separately
and each exit code checked; a passing final command cannot hide an earlier error.

### Task 1: Immutable graph contracts and test fixtures

**Files:**
- Create: `backend/code_graph/__init__.py`, `backend/code_graph/models.py`, `backend/code_graph/exceptions.py`.
- Create: `tests/conftest.py`.
- Test: `tests/test_code_graph_models.py`.

**Interfaces:**
- Consumes: upstream models/enums listed above.
- Produces: GraphNodeKind(FILE, SYMBOL), GraphEdgeKind(DEFINES, CONTAINS, IMPORTS, CALLS, INHERITS, IMPLEMENTS), ReferenceKind(IMPORT, CALL, INHERITS, IMPLEMENTS), ResolutionFailureReason(EXTERNAL, NOT_FOUND, AMBIGUOUS, UNSUPPORTED, INCOMPLETE_PARSE).
- Produces: the four models with exact fields below; CodeGraphError and CodeGraphValidationError/CodeGraphIdentityError/CodeGraphInvariantError/CodeGraphResolutionError direct subclasses.
- Produces: opt-in pytest fixture `cg` with `loc`, `symbol`, `parsed`, `pair` callables defined below. It reads no files.

- [ ] Write enum, field-name/order, frozen/slotted and exception-subclass tests, starting with:

```python
from dataclasses import FrozenInstanceError, fields
import pytest
from backend.code_graph.models import GraphNode, GraphNodeKind
from backend.code_parser.models import ParsedLanguage, ParseStatus

def test_file_node_is_frozen_and_has_no_symbol_fields():
    node = GraphNode("a" * 64, GraphNodeKind.FILE, "repo", "a.py",
                     ParsedLanguage.PYTHON, ParseStatus.SUCCESS,
                     None, None, None, None)
    assert not hasattr(node, "__dict__")
    assert (node.symbol_kind, node.name, node.qualified_name, node.location) == (None,) * 4
    with pytest.raises(FrozenInstanceError):
        node.relative_path = "b.py"
    assert [f.name for f in fields(GraphNode)] == [
        "node_id", "node_kind", "repository_namespace", "relative_path",
        "language", "parse_status", "symbol_kind", "name", "qualified_name", "location"]
```

- [ ] Run `python -m pytest tests/test_code_graph_models.py -q`; expect missing graph contracts on RED, then specific assertion failures as contracts are filled.
- [ ] Define string enums with lowercase member values and frozen/slotted records. Implement the record bodies exactly:

```python
@dataclass(frozen=True, slots=True)
class GraphNode:
    node_id: str
    node_kind: GraphNodeKind
    repository_namespace: str
    relative_path: str
    language: ParsedLanguage
    parse_status: ParseStatus
    symbol_kind: SymbolKind | None
    name: str | None
    qualified_name: str | None
    location: SourceLocation | None

@dataclass(frozen=True, slots=True)
class GraphEdge:
    edge_id: str
    edge_kind: GraphEdgeKind
    source_node_id: str
    target_node_id: str
    location: SourceLocation | None

@dataclass(frozen=True, slots=True)
class UnresolvedReference:
    reference_id: str
    reference_kind: ReferenceKind
    reason: ResolutionFailureReason
    repository_namespace: str
    relative_path: str
    language: ParsedLanguage
    source_node_id: str | None
    location: SourceLocation
    raw_target: str
    candidate_node_ids: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class CodeGraphInventory:
    repository_namespace: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    unresolved_references: tuple[UnresolvedReference, ...]
    outgoing_edges: Mapping[str, tuple[GraphEdge, ...]]
    incoming_edges: Mapping[str, tuple[GraphEdge, ...]]
    nodes_by_file: Mapping[str, tuple[GraphNode, ...]]
    nodes_by_qualified_name: Mapping[str, tuple[GraphNode, ...]]
```

Import `dataclass`, `Mapping`, `MappingProxyType`, and upstream types explicitly.
For inventory post-init require exact tuple canonical collections, then replace
each mapping with `MappingProxyType({k: tuple(v) for k, v in mapping.items()})`
using `object.__setattr__`. Require tuple candidate IDs in unresolved records.
Reject mutable/wrong-shaped nested public values with CodeGraphInvariantError;
do not silently retain lists. Global graph consistency is Task 12's boundary.
Add tests mutating original mapping dictionaries and original value lists after
construction; graph values must remain unchanged. Test assignment to each model.

- [ ] Add the fixture only when its first model/location test uses it. Import `SimpleNamespace`, `PurePosixPath`, pytest, and all upstream model classes. Use:

```python
@pytest.fixture
def cg():
    def loc(start=0, end=100):
        return SourceLocation(start, end, 1, start, 1, end)

    def symbol(name, *, kind=SymbolKind.FUNCTION, qualified_name=None,
               parent=None, start=0, end=100, parameters=(), modifiers=(),
               base_types=(), implemented_types=()):
        return SymbolInfo(name, kind, qualified_name or name, parent, loc(start, end),
                          parameters, None, modifiers, base_types, implemented_types)

    def parsed(path="a.py", *, language=ParsedLanguage.PYTHON,
               status=ParseStatus.SUCCESS, symbols=(), imports=(), calls=(), issues=()):
        return ParsedFile(path, language, status, symbols, imports, calls, issues, "0" * 64)

    def pair(*files, namespace="repo"):
        scanned = []
        for file in files:
            path = PurePosixPath(file.relative_path)
            locations = [item.location for item in (*file.symbols, *file.imports, *file.calls)]
            size = max([4096, *(item.end_byte for item in locations)])
            language = "typescript" if file.language is ParsedLanguage.TSX else file.language.value
            scanned.append(ScannedFile(file.relative_path, path.name, path.suffix,
                                       language, FileCategory.SOURCE, size))
        fi = FileInventory("/inert/repo", len(files), len(files), 0, tuple(scanned), (), (), namespace)
        pi = CodeParseInventory("/inert/repo", len(files),
            sum(f.status is ParseStatus.SUCCESS for f in files),
            sum(f.status is ParseStatus.PARTIAL for f in files),
            sum(f.status is ParseStatus.FAILED for f in files), 0, tuple(files), (), namespace)
        return fi, pi

    return SimpleNamespace(loc=loc, symbol=symbol, parsed=parsed, pair=pair)
```

- [ ] Run `python -m pytest tests/test_code_graph_models.py -q` for GREEN.
- [ ] Run neighboring regressions: `python -m pytest tests/test_code_parser.py tests/test_embedding_models.py -q`.
- [ ] Run `git diff --check -- backend/code_graph tests/conftest.py tests/test_code_graph_models.py`.
- [ ] Stage only these files; commit `feat: define code graph contracts`. Do not create empty resolver/test files in this task.

### Task 2: Exact canonical identities

**Files:**
- Create: `backend/code_graph/identities.py`.
- Test: `tests/test_code_graph_identities.py`.

**Interfaces:**
- Consumes: GraphEdgeKind, ReferenceKind, SymbolInfo, SourceLocation.
- Produces: `file_node_id(namespace: str, path: str) -> str`;
  `symbol_node_id(namespace: str, path: str, symbol: SymbolInfo) -> str`;
  `edge_id(namespace: str, kind: GraphEdgeKind, source: str, target: str, location: SourceLocation | None) -> str`;
  `reference_id(namespace: str, path: str, kind: ReferenceKind, source: str | None, location: SourceLocation, raw_target: str) -> str`.

- [ ] Write literal-payload oracle tests. Do not calculate expected values through a production identity helper:

```python
from hashlib import sha256
from backend.code_graph.identities import file_node_id

def test_unicode_file_identity_uses_exact_utf8_payload():
    payload = '["tracerag-code-graph-node-v1","repo","file","café.py"]'
    assert file_node_id("repo", "café.py") == sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] Run `python -m pytest tests/test_code_graph_identities.py -q`; RED is missing helper or incorrect digest.
- [ ] Implement one private JSON hashing function and four payload adapters:

```python
def _digest(payload: list[object]) -> str:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CodeGraphIdentityError("Invalid graph identity material.") from exc
    return sha256(encoded).hexdigest()

def file_node_id(namespace: str, path: str) -> str:
    return _digest(["tracerag-code-graph-node-v1", namespace, "file", path])
```

Use these exact other arrays, with `.value` for enums:

```text
["tracerag-code-graph-node-v1", namespace, "symbol", path,
 symbol.kind.value, symbol.qualified_name, symbol.location.start_byte, symbol.location.end_byte]
["tracerag-code-graph-edge-v1", namespace, kind.value, source, target,
 null OR [location.start_byte, location.end_byte]]
["tracerag-code-graph-unresolved-v1", namespace, path, kind.value, source,
 [location.start_byte, location.end_byte], raw_target]
```

Use Python `None` to encode JSON null, not empty string. No reason/candidates,
line/column, source hash, parse status, or absolute path in payloads.
- [ ] Add independent literal expected payloads for symbol, located edge,
  structural edge, and null-source unresolved ID. Test namespace/range/kind
  changes; nested/repeated definitions; Unicode; preserved path/name case;
  same range with changed line/column retains ID (collision detection is Task 14).
- [ ] Run `python -m pytest tests/test_code_graph_identities.py -q` for GREEN.
- [ ] Run `python -m pytest tests/test_code_graph_models.py tests/test_code_chunker.py -q`.
- [ ] Run `git diff --check -- backend/code_graph/identities.py tests/test_code_graph_identities.py`.
- [ ] Stage those two files; commit `feat: generate deterministic graph identities`.

### Task 3: Validate inventory associations and metadata

**Files:**
- Create: `backend/code_graph/validation.py`.
- Test: `tests/test_code_graph_validation.py`.

**Interfaces:**
- Consumes: all upstream inventory/record types; CodeGraphValidationError.
- Produces: frozen/slotted `ValidatedInputs(repository_namespace: str, pairs: tuple[tuple[ScannedFile, ParsedFile], ...])`;
  `validate_inputs(file_inventory: object, parse_inventory: object) -> ValidatedInputs`.
- Produces: private field/path/location validators local to this module. Parent identity/range associations are completed in Task 4 before any result is published.

- [ ] Write mutation-based tests starting from valid `cg.pair` records:

```python
from dataclasses import replace
import pytest
from backend.code_graph.validation import validate_inputs
from backend.code_graph.exceptions import CodeGraphValidationError

@pytest.mark.parametrize("namespace", [None, "", 1, True])
def test_namespace_is_required(cg, namespace):
    fi, pi = cg.pair(cg.parsed())
    with pytest.raises(CodeGraphValidationError):
        validate_inputs(replace(fi, repository_namespace=namespace), pi)
```

- [ ] Run `python -m pytest tests/test_code_graph_validation.py -q`; confirm RED for rejected values, not fixture errors.
- [ ] Implement exact-type gates before accessing nested fields:

```python
def _nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0

def _valid_path(value: object) -> bool:
    if type(value) is not str or not value or "\\" in value or "\x00" in value:
        return False
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))
```

Require exact dataclasses and enum classes, exact tuples, exact bool wildcard,
strings/optional strings, integer counters/sizes excluding bool. Check all
ScannedFile fields and ignored/skipped reason types. Inventory path fields are
nonempty strings; differing absolute repository paths with equal namespace
remain valid. Compare coverage using sets, never input order.
- [ ] Add separate RED/GREEN cases for: namespace mismatch; every counter;
  SOURCE and TEST coverage; unsupported-language skipped entry; orphan parsed
  path; duplicate included/ignored/skipped paths; selector mismatches including
  JSX and TSX positive cases; absolute, drive-relative, UNC, backslash, NUL,
  repeated separator, dot and dot-dot paths; no normalization of hostile paths.
- [ ] Validate each SourceLocation's six exact integers, `0 <= start_byte <=
  end_byte <= scanned.size_bytes`, positive ordered lines and ordered same-line
  columns. Test BOM-adjusted values accepted, reversed and out-of-bounds bytes,
  bool offsets, negative columns, zero lines, and nullable issue location.
- [ ] Validate SymbolInfo names, parent optionality, kind, parameters and each
  ParameterInfo field, return_type, modifiers, base_types, implemented_types;
  ImportInfo/ImportBinding fields; CallSite/CallKind and optional owner;
  ParseIssue/ParseIssueKind. Test malformed nested records, tuples containing
  dicts, and mutable collections. Present digests must match `[0-9a-f]{64}`;
  `None` is accepted without source access or digest recomputation.
- [ ] Require FAILED symbols/imports/calls empty. Test empty and nonempty
  SUCCESS, trusted PARTIAL records and invalid FAILED extractions. Preserve
  evidence duplicates for collision-aware handling, but reject duplicate files.
  Return pairs sorted by exact relative path in a frozen ValidatedInputs.
- [ ] Run `python -m pytest tests/test_code_graph_validation.py -q` for GREEN.
- [ ] Run `python -m pytest tests/test_code_graph_models.py tests/test_file_scanner.py tests/test_code_parser.py -q`.
- [ ] Run `git diff --check -- backend/code_graph/validation.py tests/test_code_graph_validation.py`.
- [ ] Stage those files; commit `feat: validate code graph inventories`.

### Task 4: Node indexes and direct structural graph

**Files:**
- Create: `backend/code_graph/indexes.py`, `backend/code_graph/structure.py`.
- Test: `tests/test_code_graph_builder.py`.

**Interfaces:**
- Consumes: ValidatedInputs and the four identity functions.
- Produces: `build_graph_indexes(inputs: ValidatedInputs) -> GraphIndexes` and `structural_edges(index: GraphIndexes) -> tuple[GraphEdge, ...]`.
- Produces: private `_resolve_parent_ids(nodes_by_id: Mapping[str, GraphNode], symbols_by_id: Mapping[str, SymbolInfo]) -> dict[str, str | None]` in structure.py, called by build_graph_indexes after node creation. structure.py uses TYPE_CHECKING for its GraphIndexes annotation to avoid an import cycle.
- GraphIndexes is a private frozen record containing `repository_namespace: str`, `nodes: tuple[GraphNode, ...]`, `nodes_by_id: Mapping[str, GraphNode]`, `files_by_path: Mapping[str, ParsedFile]`, `file_ids: Mapping[str, str]`, `symbols_by_id: Mapping[str, SymbolInfo]`, `by_file_name: Mapping[tuple[str, str], tuple[str, ...]]`, `by_qualified_name: Mapping[str, tuple[str, ...]]`, `parent_ids: Mapping[str, str | None]`, `children: Mapping[str, tuple[str, ...]]`, `python_modules: Mapping[str, tuple[str, ...]]`, `java_packages: Mapping[str, tuple[str, ...]]`.
- `parent_ids` covers SYMBOL IDs; None means top-level. `children` contains direct SYMBOL children keyed by FILE or SYMBOL ID. Module/package buckets hold FILE IDs. All mappings are read-only and tuple-valued where plural.

- [ ] Add this structural behavior test and a duplicate-qualified-name/range test:

```python
from backend.code_graph.indexes import build_graph_indexes
from backend.code_graph.structure import structural_edges
from backend.code_graph.validation import validate_inputs
from backend.code_parser.models import SymbolKind

def test_only_direct_structural_edges(cg):
    parent = cg.symbol("A", kind=SymbolKind.CLASS, end=200)
    method = cg.symbol("run", kind=SymbolKind.METHOD, qualified_name="A.run",
                       parent="A", start=20, end=80)
    index = build_graph_indexes(validate_inputs(*cg.pair(cg.parsed(symbols=(method, parent)))))
    edges = structural_edges(index)
    assert sorted(e.edge_kind.value for e in edges) == ["contains", "defines"]
    assert all(e.location is None for e in edges)
    assert len(index.nodes) == 3
```

- [ ] Run `python -m pytest tests/test_code_graph_builder.py -q`; expect missing structural builder on RED.
- [ ] Build FILE nodes first, then project each SymbolInfo with its exact fields.
  Before projection deduplicate by symbol identity and FULL SymbolInfo equality:

```python
node_key = symbol_node_id(inputs.repository_namespace, parsed.relative_path, symbol)
previous = symbols_by_id.get(node_key)
if previous is not None and previous != symbol:
    raise CodeGraphIdentityError("Conflicting symbol evidence.")
symbols_by_id[node_key] = symbol
```

Store file/status/language evidence unchanged. Build exact-name buckets once.
Index Python module keys by removing `.py`, mapping separators to dots, and
removing a final `.__init__` component. Do not invent suffix source roots;
root `__init__.py` has no importable nonempty module name. Preserve module/package
collisions. Derive Java package buckets from top-level qualified type names
minus their exact final name component; do not use directory names as packages.
- [ ] Match parent qualified name within its file and containing byte range.
  Pre-sort range events once and use per-name active candidates to find unique
  parents; event order must include a parent's start before its child's start
  and remove ended disjoint ranges. Reject self-parent, missing parent, multiple
  containing matches and cycles with CodeGraphValidationError. Do not choose
  the shortest matching range when multiple candidates satisfy the parent key.
  Range sorting belongs to O(n log n) canonical/index preparation; structural
  edge emission is one pass over parent_ids.
- [ ] Implement structural emission as one edge per symbol:

```python
for symbol_id, parent_id in index.parent_ids.items():
    node = index.nodes_by_id[symbol_id]
    source = index.file_ids[node.relative_path] if parent_id is None else parent_id
    kind = GraphEdgeKind.DEFINES if parent_id is None else GraphEdgeKind.CONTAINS
    edges.append(GraphEdge(edge_id(index.repository_namespace, kind, source, symbol_id, None),
                           kind, source, symbol_id, None))
```

- [ ] Cover zero files, empty parsed file, FAILED FILE, skipped/scanner-only
  exclusion, every SymbolKind, nested function/method/constant, repeated names
  with disjoint ranges, and conflicting modifiers/parents under the same ID.
  Verify SYMBOL names are not module-prefixed or normalized.
- [ ] Run `python -m pytest tests/test_code_graph_builder.py -q` for GREEN.
- [ ] Run `python -m pytest tests/test_code_graph_validation.py tests/test_code_graph_identities.py -q`.
- [ ] Run `git diff --check -- backend/code_graph/indexes.py backend/code_graph/structure.py tests/test_code_graph_builder.py`.
- [ ] Stage those files; commit `feat: build indexed structural code graph`.

### Task 5: Resolution outcomes, reference ownership and reason precedence

**Files:**
- Create: `backend/code_graph/resolvers/base.py`, `backend/code_graph/resolvers/__init__.py`, `backend/code_graph/references.py`.
- Modify/Test: `tests/test_code_graph_builder.py`.

**Interfaces:**
- Consumes: GraphIndexes, ParsedFile, ImportInfo, CallSite, SymbolInfo.
- Produces: immutable contracts below; `choose_resolution(candidates: tuple[str, ...], *, unsupported: bool=False, incomplete: bool=False, external: bool=False) -> Resolution`.
- Produces: `import_reference(index: GraphIndexes, file: ParsedFile, item: ImportInfo) -> Reference`; `call_reference(index: GraphIndexes, file: ParsedFile, item: CallSite) -> Reference`; `type_references(index: GraphIndexes, file: ParsedFile, symbol_id: str) -> tuple[Reference, ...]`.
- Produces: `materialize(index: GraphIndexes, reference: Reference, resolution: Resolution) -> GraphEdge | UnresolvedReference`.
- Language modules will expose `resolve_import(index: GraphIndexes, file: ParsedFile, item: ImportInfo) -> ImportResult` and `resolve_reference(index: GraphIndexes, environment: ImportEnvironment, reference: Reference) -> Resolution` with the same signatures in all three modules.

- [ ] Add an explicit ambiguity and precedence test:

```python
from backend.code_graph.resolvers.base import choose_resolution
from backend.code_graph.models import ResolutionFailureReason

def test_ambiguity_has_no_target_and_sorted_candidates():
    result = choose_resolution(("b" * 64, "a" * 64, "b" * 64))
    assert result.target_node_id is None
    assert result.reason is ResolutionFailureReason.AMBIGUOUS
    assert result.candidate_node_ids == ("a" * 64, "b" * 64)
```

- [ ] Run `python -m pytest tests/test_code_graph_builder.py -q`; RED must identify missing outcome behavior.
- [ ] Define private frozen/slotted records with exact signatures:

Place Reference in references.py. Place Resolution, Binding, ImportResult and
ImportEnvironment in resolvers/base.py. references.py imports the base outcome
types; base.py does not import references.py or any language resolver. Language
resolvers import both modules, and only resolvers/__init__.py owns dispatch.

```python
@dataclass(frozen=True, slots=True)
class Resolution:
    target_node_id: str | None
    reason: ResolutionFailureReason | None
    candidate_node_ids: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class Reference:
    reference_kind: ReferenceKind
    relative_path: str
    language: ParsedLanguage
    source_node_id: str | None
    location: SourceLocation
    raw_target: str
    call_kind: CallKind | None = None
    blocked_reason: ResolutionFailureReason | None = None

@dataclass(frozen=True, slots=True)
class Binding:
    local_name: str
    scope_node_id: str
    mode: Literal["symbol", "module", "blocked"]
    resolution: Resolution

@dataclass(frozen=True, slots=True)
class ImportResult:
    resolution: Resolution
    bindings: tuple[Binding, ...]

@dataclass(frozen=True, slots=True)
class ImportEnvironment:
    by_file: Mapping[str, tuple[tuple[ImportInfo, ImportResult], ...]]
    bindings_by_scope_name: Mapping[tuple[str, str], tuple[Binding, ...]] = field(init=False)
    wildcards_by_file: Mapping[str, tuple[ImportInfo, ...]] = field(init=False)
```

Import `field` from dataclasses. In ImportEnvironment.__post_init__, copy by_file,
build bindings_by_scope_name once from all ImportResult.bindings, and group
wildcard ImportInfo records once by source path. Freeze all three maps. Resolve
local bindings with `(scope_node_id, local_name)` lookups, never a fresh scan
of by_file on each call. Preserve multiple bindings in each bucket so conflicts
cannot disappear. Wildcard lookup uses indexed package/type/member candidates
and per-build memoization for repeated `(scope, member)` requests.

`scope_node_id` is a unique containing SYMBOL or the FILE for a top-level
binding. Determine import containment with the indexed ranges; if the scope
cannot be proven, emit a blocked binding instead of making it globally visible.
The binding remains associated with its original ImportInfo in by_file.
For unknown binding scope, attach the blocked record to the FILE solely as a
conservative barrier for that name, never as positive visibility evidence.
All outcome tuples/maps are copied and frozen. A Resolution must contain exactly
one of target/reason; only AMBIGUOUS can have target candidates. Violations raise
CodeGraphResolutionError, not ordinary unresolved evidence.
- [ ] Implement deterministic selection:

```python
def choose_resolution(candidates, *, unsupported=False, incomplete=False, external=False):
    if unsupported:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    if incomplete:
        return Resolution(None, ResolutionFailureReason.INCOMPLETE_PARSE)
    ids = tuple(sorted(set(candidates)))
    if len(ids) > 1:
        return Resolution(None, ResolutionFailureReason.AMBIGUOUS, ids)
    if len(ids) == 1:
        return Resolution(ids[0], None)
    reason = ResolutionFailureReason.EXTERNAL if external else ResolutionFailureReason.NOT_FOUND
    return Resolution(None, reason)
```

Callers set `unsupported=False, incomplete=True` when known parse loss causes
missing evidence. Truly dynamic syntax remains unsupported despite unrelated
partial files. Stronger-tier outcomes are terminal, including failures.
- [ ] Implement reference ownership from exact qualified name AND file/range.
  Null call owner is UNSUPPORTED unless relevant parse loss explains it.
  Multiple matching callable owners yield AMBIGUOUS with null source and no
  target candidates; no owner match is UNSUPPORTED or INCOMPLETE_PARSE.
  Imports source the FILE; types source the declaring symbol and reuse its
  location with exact type string. Test top-level calls, ownership barriers,
  duplicate owner names with disjoint and overlapping ranges, and non-callable
  owner names. Never attach a null owner by nearest-range inference.
- [ ] In materialize, honor blocked_reason before language target outcomes;
  build IDs with Task 2 helpers. Map reference kinds to edge kinds explicitly.
  Test target versus unresolved XOR, exact raw strings, null source IDs,
  inheritance declaration locations, no candidate edges, and invalid resolver
  outcomes raising CodeGraphResolutionError. No source reads.
- [ ] Run `python -m pytest tests/test_code_graph_builder.py -q` for GREEN.
- [ ] Run `python -m pytest tests/test_code_graph_models.py tests/test_code_graph_validation.py -q`.
- [ ] Run `git diff --check -- backend/code_graph/resolvers backend/code_graph/references.py tests/test_code_graph_builder.py`.
- [ ] Stage only task files; commit `feat: define conservative graph resolution outcomes`.

### Task 6: Python module imports and explicit bindings

**Files:**
- Create: `backend/code_graph/resolvers/python.py`.
- Test: `tests/test_code_graph_python.py`.

**Interfaces:**
- Consumes: GraphIndexes.python_modules, ImportInfo/ImportBinding, shared outcomes.
- Produces: `resolve_import(index: GraphIndexes, file: ParsedFile, item: ImportInfo) -> ImportResult` in the Python resolver.

- [ ] Write a concrete root-relative import test:

```python
from backend.code_graph.indexes import build_graph_indexes
from backend.code_graph.validation import validate_inputs
from backend.code_graph.resolvers.python import resolve_import
from backend.code_parser.models import ImportInfo, ImportBinding

def test_absolute_module_import(cg):
    item = ImportInfo("pkg.util", (ImportBinding("pkg.util", None),), False, (), cg.loc(1, 9))
    source = cg.parsed("main.py", imports=(item,))
    target = cg.parsed("pkg/util.py")
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))
    result = resolve_import(index, source, item)
    assert result.resolution.target_node_id == index.file_ids["pkg/util.py"]
```

- [ ] Run `python -m pytest tests/test_code_graph_python.py -q` for RED.
- [ ] Resolve absolute module keys by exact indexed lookup. For leading dots
  operate on slash-separated directory strings only:

```python
level = len(item.module) - len(item.module.lstrip("."))
parts = file.relative_path.split("/")[:-1]
if level and level - 1 > len(parts):
    return ImportResult(choose_resolution(()), ())
anchor = parts[:len(parts) - (level - 1)] if level else []
tail = item.module[level:].split(".") if item.module[level:] else []
key = ".".join(anchor + tail) if level else item.module
candidate_ids = index.python_modules.get(key, ())
```

Check package context before treating a root-level relative import as valid.
No path filesystem normalization, `sys.path`, arbitrary suffix roots, importlib,
package execution, configuration, or source reread.
- [ ] Test module `pkg.py` versus `pkg/__init__.py` ambiguity; `.util` and
  `..util` from packages; escape above root -> NOT_FOUND; missing unanchored
  absolute package -> EXTERNAL; matched failed FILE still imports successfully.
- [ ] Build bindings only when import-form interpretations agree. For example,
  `ImportInfo("x", (ImportBinding("x", "y"),), False, (), loc)` can represent
  `import x as y` or `from x import x as y`: preserve its proven FILE import,
  but block `y` as UNSUPPORTED if module and symbol interpretations differ.
  `from pkg.util import run as execute` has distinct module/imported_name and
  can establish execute's unique symbol. Preserve wildcard scope evidence
  without exposing all repository names. Repeated conflicting binding names
  must remain ambiguous/blocked, never last-write wins.
- [ ] Use complete module candidates with `choose_resolution`; for dependent
  symbols inspect only proven module scopes and eligible declarations. A
  missing member in relevant PARTIAL/FAILED extraction is INCOMPLETE_PARSE.
  ImportInfo.module remains the unresolved import raw target; binding metadata
  does not create another public import reference or symbol-import edge.
- [ ] Run `python -m pytest tests/test_code_graph_python.py -q` for GREEN.
- [ ] Run `python -m pytest tests/test_code_graph_builder.py tests/test_code_parser.py -q`.
- [ ] Run `git diff --check -- backend/code_graph/resolvers/python.py tests/test_code_graph_python.py`.
- [ ] Stage both files; commit `feat: resolve python graph imports`.

### Task 7: Python calls and declared base types

**Files:**
- Modify: `backend/code_graph/resolvers/python.py`.
- Modify/Test: `tests/test_code_graph_python.py`.

**Interfaces:**
- Consumes: Reference, GraphIndexes, ImportEnvironment from Task 5, Python bindings from Task 6.
- Produces: `resolve_reference(index: GraphIndexes, environment: ImportEnvironment, reference: Reference) -> Resolution` in the Python resolver.

- [ ] Add same-file callable test with explicit ownership:

```python
from backend.code_graph.references import call_reference
from backend.code_graph.resolvers.base import ImportEnvironment
from backend.code_graph.resolvers.python import resolve_reference
from backend.code_parser.models import CallSite, CallKind

def test_same_file_call(cg):
    call = CallSite("run", "helper", CallKind.CALL, cg.loc(20, 25))
    file = cg.parsed(symbols=(cg.symbol("run"), cg.symbol("helper", start=200, end=250)), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    ref = call_reference(index, file, call)
    result = resolve_reference(index, ImportEnvironment({file.relative_path: ()}), ref)
    assert result.target_node_id == index.by_file_name[("a.py", "helper")][0]
```

- [ ] Run `python -m pytest tests/test_code_graph_python.py -q` for RED.
- [ ] Implement an explicit eligibility gate before indexed lookup:

```python
def _dotted_identifier(text: str) -> bool:
    return bool(text) and all(part.isidentifier() for part in text.split("."))
```

For CALL use indexed lexical scope, containing type only for a supported
receiver, same-file top-level functions, explicit bindings, and anchored
qualified names in spec order. Return a stronger failure rather than falling
through. Exclude class-member bare-name Python lookup. Parameter name conflicts
and unknown receivers return UNSUPPORTED. Dynamic text and `getattr`/`globals`
calls remain UNSUPPORTED; do not evaluate or parse source snippets.
- [ ] Test self.method inside a method with `self` parameter and a unique
  containing class; reject arbitrary `thing.method`, shadowed self and class
  scope barriers. Test uniquely established Class.method and imported alias
  call, module-qualified member call, absent target, duplicate target names,
  blocked import-form fallback, nested lexical function and recursion.
- [ ] For INHERITS use reference source class and exact base string; only CLASS
  targets qualify. Complex expressions, MRO lookup and runtime constructor
  inference remain UNSUPPORTED. Explicit constructor resolution must target an
  extracted CONSTRUCTOR, not the CLASS; test class-only target does not become
  a CALLS edge. Do not invent `__init__` nodes or reinterpret METHOD as CONSTRUCTOR.
- [ ] Test a same-name symbol in another file/language never resolves an
  unqualified call. Assert AMBIGUOUS has no target and sorted IDs, while an
  imported external binding propagates EXTERNAL for a call.
- [ ] Run `python -m pytest tests/test_code_graph_python.py -q` for GREEN.
- [ ] Run `python -m pytest tests/test_code_graph_builder.py tests/test_code_graph_validation.py -q`.
- [ ] Run `git diff --check -- backend/code_graph/resolvers/python.py tests/test_code_graph_python.py`.
- [ ] Stage both files; commit `feat: resolve python graph calls and bases`.

### Task 8: Java imports, packages and type relationships

**Files:**
- Create: `backend/code_graph/resolvers/java.py`.
- Test: `tests/test_code_graph_java.py`.

**Interfaces:**
- Consumes: GraphIndexes.java_packages/by_qualified_name, shared Reference/outcomes.
- Produces: Java `resolve_import(index: GraphIndexes, file: ParsedFile, item: ImportInfo) -> ImportResult` and `resolve_reference(index: GraphIndexes, environment: ImportEnvironment, reference: Reference) -> Resolution` for INHERITS/IMPLEMENTS. CALL dispatch is completed by Task 9; until then unsupported CALL returns UNSUPPORTED, not an exception.

- [ ] Start with an explicit package import independent of directory layout:

```python
from backend.code_graph.resolvers.java import resolve_import
from backend.code_graph.indexes import build_graph_indexes
from backend.code_graph.validation import validate_inputs
from backend.code_parser.models import ParsedLanguage, SymbolKind, ImportInfo

def test_java_import_uses_qualified_metadata(cg):
    item = ImportInfo("p.Service", (), False, (), cg.loc(1, 10))
    source = cg.parsed("Main.java", language=ParsedLanguage.JAVA, imports=(item,))
    target = cg.parsed("odd/Service.java", language=ParsedLanguage.JAVA,
        symbols=(cg.symbol("Service", kind=SymbolKind.CLASS, qualified_name="p.Service"),))
    index = build_graph_indexes(validate_inputs(*cg.pair(source, target)))
    assert resolve_import(index, source, item).resolution.target_node_id == index.file_ids["odd/Service.java"]
```

- [ ] Run `python -m pytest tests/test_code_graph_java.py -q` for RED.
- [ ] Map exact qualified types to their files; do not derive package from path.
  Deduplicate file IDs only after checking eligible qualified symbols:

```python
type_ids = tuple(node_id for node_id in index.by_qualified_name.get(item.module, ())
                 if index.nodes_by_id[node_id].language is ParsedLanguage.JAVA
                 and index.nodes_by_id[node_id].symbol_kind in
                 (SymbolKind.CLASS, SymbolKind.INTERFACE, SymbolKind.ENUM))
file_ids = tuple(index.file_ids[index.nodes_by_id[node_id].relative_path] for node_id in type_ids)
```

Explicit static member imports resolve only from the named type and static
member metadata. Wildcards query only package/type buckets; their FILE edge
requires exactly one file. Preserve ImportInfo in the environment so a specific
member can resolve uniquely even when the wildcard import spans multiple files.
- [ ] Test external package, conflicting qualified internal types, package
  wildcard ambiguity, static wildcard ambiguity, and a unique wildcard target.
  Use base_types for CLASS->CLASS and INTERFACE->INTERFACE INHERITS, and
  implemented_types for CLASS/ENUM->INTERFACE IMPLEMENTS. Test forbidden endpoint
  pairs and no invented per-type location. Reject unsupported generic/annotated
  expressions as UNSUPPORTED instead of stripping text until a name matches.
- [ ] Implement type matching using exact file/scope/import/qualified buckets
  and terminal stronger outcomes. Test an anchored PARTIAL package: trustworthy
  qualified-symbol metadata establishes the package, and a reference potentially
  affected by missing declarations in that package yields INCOMPLETE_PARSE only
  when relevance follows from existing metadata. Test unrelated FAILED-file
  isolation: a FAILED Java file without package-identifying metadata must not
  automatically make an unrelated reference INCOMPLETE_PARSE. Retain unrelated
  PARTIAL-file isolation. Do not infer packages from paths/filenames or fabricate
  FAILED symbols/imports; preserve reason precedence. If the PARTIAL fixture
  cannot establish relevance, report the limitation instead of forcing the result.
- [ ] Run `python -m pytest tests/test_code_graph_java.py -q` for GREEN.
- [ ] Run `python -m pytest tests/test_code_graph_python.py tests/test_code_parser.py -q`.
- [ ] Run `git diff --check -- backend/code_graph/resolvers/java.py tests/test_code_graph_java.py`.
- [ ] Stage both files; commit `feat: resolve java graph imports and types`.

### Task 9: Java calls, overloads and explicit constructors

**Files:**
- Modify: `backend/code_graph/resolvers/java.py`.
- Modify/Test: `tests/test_code_graph_java.py`.

**Interfaces:**
- Consumes: Java type/import resolution from Task 8 and Reference.call_kind.
- Produces: completed Java `resolve_reference` CALL behavior; unchanged signature.

- [ ] Add overload ambiguity test with two differently located methods:

```python
from backend.code_graph.references import call_reference
from backend.code_graph.resolvers.base import ImportEnvironment
from backend.code_graph.resolvers.java import resolve_reference
from backend.code_graph.models import ResolutionFailureReason
from backend.code_parser.models import CallSite, CallKind, ParameterInfo

def test_overloads_cannot_use_missing_argument_evidence(cg):
    owner = cg.symbol("A", kind=SymbolKind.CLASS, qualified_name="p.A", end=500)
    run = cg.symbol("run", kind=SymbolKind.METHOD, qualified_name="p.A.run", parent="p.A", start=10, end=90)
    first = cg.symbol("m", kind=SymbolKind.METHOD, qualified_name="p.A.m", parent="p.A", start=100, end=150)
    second = cg.symbol("m", kind=SymbolKind.METHOD, qualified_name="p.A.m", parent="p.A", start=160, end=220,
                       parameters=(ParameterInfo("x", "int", None),))
    call = CallSite("p.A.run", "this.m", CallKind.CALL, cg.loc(20, 30))
    file = cg.parsed("A.java", language=ParsedLanguage.JAVA, symbols=(owner, run, first, second), calls=(call,))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    outcome = resolve_reference(index, ImportEnvironment({"A.java": ()}), call_reference(index, file, call))
    assert outcome.reason is ResolutionFailureReason.AMBIGUOUS
    assert len(outcome.candidate_node_ids) == 2
    assert outcome.target_node_id is None
```

- [ ] Run `python -m pytest tests/test_code_graph_java.py -q` for RED.
- [ ] Implement current-type and this-qualified lookup through the caller's
  parent type; exact Type.method requires an established type and static
  modifier. Use `choose_resolution` on all eligible declarations:

```python
eligible = tuple(node_id for node_id in candidate_ids
                 if index.nodes_by_id[node_id].symbol_kind in
                 (SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.CONSTRUCTOR))
return choose_resolution(eligible)
```

`candidate_ids` here is the scope/type bucket already selected by Java syntax;
never a repository-wide list. Do not use callee string argument parsing,
SymbolInfo.parameters counts, type inference, or lexical order to pick overloads.
- [ ] Test bare current-type call, this.method, static Type.method,
  instance-method rejection for Type.method, imported static member, parameter
  receiver conflict, external binding and unknown receiver. Constructor calls
  select explicit CONSTRUCTOR children only; missing implicit constructor
  yields no edge, and multiple constructor overloads are AMBIGUOUS.
- [ ] Run `python -m pytest tests/test_code_graph_java.py -q` for GREEN.
- [ ] Run `python -m pytest tests/test_code_graph_builder.py tests/test_code_graph_python.py -q`.
- [ ] Run `git diff --check -- backend/code_graph/resolvers/java.py tests/test_code_graph_java.py`.
- [ ] Stage both files; commit `feat: resolve java calls without overload guessing`.

### Task 10: ECMAScript module candidates and imported bindings

**Files:**
- Create: `backend/code_graph/resolvers/ecmascript.py`.
- Test: `tests/test_code_graph_ecmascript.py`.

**Interfaces:**
- Consumes: exact file path index, GraphIndexes symbol metadata, ImportInfo markers.
- Produces: ECMAScript `resolve_import(index: GraphIndexes, file: ParsedFile, item: ImportInfo) -> ImportResult`.

- [ ] Add extension/index ambiguity test:

```python
from backend.code_graph.resolvers.ecmascript import resolve_import
from backend.code_graph.indexes import build_graph_indexes
from backend.code_graph.validation import validate_inputs
from backend.code_graph.models import ResolutionFailureReason
from backend.code_parser.models import ParsedLanguage, ImportInfo

def test_extensionless_import_has_no_extension_winner(cg):
    item = ImportInfo("./util", (), False, (), cg.loc(1, 10))
    source = cg.parsed("main.ts", language=ParsedLanguage.TYPESCRIPT, imports=(item,))
    first = cg.parsed("util.js", language=ParsedLanguage.JAVASCRIPT)
    second = cg.parsed("util/index.ts", language=ParsedLanguage.TYPESCRIPT)
    index = build_graph_indexes(validate_inputs(*cg.pair(source, first, second)))
    outcome = resolve_import(index, source, item).resolution
    assert outcome.reason is ResolutionFailureReason.AMBIGUOUS
    assert outcome.candidate_node_ids == tuple(sorted((index.file_ids["util.js"], index.file_ids["util/index.ts"])))
```

- [ ] Run `python -m pytest tests/test_code_graph_ecmascript.py -q` for RED.
- [ ] Normalize only relative module syntax with a lexical stack: append normal
  components, ignore `.`, pop for `..`, return NOT_FOUND if popping above root.
  Never normalize upstream inventory paths. Generate bounded candidates:

```python
extensions = (".js", ".jsx", ".ts", ".tsx")
paths = (normalized,) if normalized.endswith(extensions) else tuple(
    path for extension in extensions
    for path in (normalized + extension, normalized + "/index" + extension))
candidate_ids = tuple(index.file_ids[path] for path in paths if path in index.file_ids)
```

The snippet applies only to extensionless or explicitly supported extensions;
an unsupported explicit extension returns UNSUPPORTED. Filter candidates to
ECMAScript languages; explicit `.js` does not rewrite to `.ts`.
- [ ] Test one exact extension, all eight extension/index candidates, .jsx using
  JAVASCRIPT, TSX using TSX, path escape, empty/malformed specifier, missing
  relative file -> NOT_FOUND, bare package and `@scope/pkg` -> EXTERNAL,
  `@/alias` -> UNSUPPORTED, ordinary bare alias-looking name -> EXTERNAL under
  the current contract. Do not read package.json, tsconfig.json or node_modules.
- [ ] Establish named exported symbol bindings only with `export` modifier and
  exact name; default only with `export` plus `default` evidence; namespace
  member only within the uniquely resolved module. Preserve alias spelling.
  Test sole non-default function does not resolve default, unexported same-name
  declaration does not become a named export, conflicting declarations remain
  ambiguous, and re-export/CommonJS export-map-dependent lookup is UNSUPPORTED.
  A proven FILE import remains even when its local binding is blocked.
- [ ] Run `python -m pytest tests/test_code_graph_ecmascript.py -q` for GREEN.
- [ ] Run `python -m pytest tests/test_code_graph_builder.py tests/test_code_parser.py -q`.
- [ ] Run `git diff --check -- backend/code_graph/resolvers/ecmascript.py tests/test_code_graph_ecmascript.py`.
- [ ] Stage both files; commit `feat: resolve ecmascript graph imports`.

### Task 11: ECMAScript calls and TypeScript relationships

**Files:**
- Modify: `backend/code_graph/resolvers/ecmascript.py`.
- Modify/Test: `tests/test_code_graph_ecmascript.py`.

**Interfaces:**
- Consumes: Reference, ImportEnvironment, ECMAScript import outcomes from Task 10.
- Produces: ECMAScript `resolve_reference(index: GraphIndexes, environment: ImportEnvironment, reference: Reference) -> Resolution`.

- [ ] Add the TypeScript implementation relationship test:

```python
from backend.code_graph.references import type_references
from backend.code_graph.resolvers.base import ImportEnvironment
from backend.code_graph.resolvers.ecmascript import resolve_reference
from backend.code_parser.models import SymbolKind

def test_typescript_implements_uses_declaration_location(cg):
    interface = cg.symbol("Port", kind=SymbolKind.INTERFACE, start=0, end=50)
    concrete = cg.symbol("Adapter", kind=SymbolKind.CLASS, start=60, end=160, implemented_types=("Port",))
    file = cg.parsed("a.ts", language=ParsedLanguage.TYPESCRIPT, symbols=(interface, concrete))
    index = build_graph_indexes(validate_inputs(*cg.pair(file)))
    source_id = index.by_file_name[("a.ts", "Adapter")][0]
    ref, = type_references(index, file, source_id)
    assert ref.location == concrete.location
    result = resolve_reference(index, ImportEnvironment({"a.ts": ()}), ref)
    assert result.target_node_id == index.by_file_name[("a.ts", "Port")][0]
```

- [ ] Run `python -m pytest tests/test_code_graph_ecmascript.py -q` for RED.
- [ ] Implement supported identifier/member calls using lexical/current scope,
  same file, explicit binding, qualified imported member precedence. Retain
  modifier-backed export limits from Task 10. Explicit endpoint checks include:

```python
if reference.reference_kind is ReferenceKind.IMPLEMENTS:
    eligible = tuple(node_id for node_id in candidate_ids
                     if index.nodes_by_id[node_id].symbol_kind is SymbolKind.INTERFACE)
    return choose_resolution(eligible)
```

Check source CLASS/ENUM before the snippet. For INHERITS require CLASS->CLASS
or INTERFACE->INTERFACE. For CALL require callable source and callable target;
constructor expressions target explicit CONSTRUCTOR nodes only.
- [ ] Test JS/JSX/TS/TSX local function calls, named alias calls, metadata-proven
  default and namespace-member calls, missing exports, blocked re-export
  fallback, arbitrary receiver/computed member -> UNSUPPORTED, top-level call
  source null, unknown bare call -> NOT_FOUND, and ambiguity with sorted IDs.
  Test JS class extends, TS class extends, interface extends, implements,
  forbidden class->interface INHERITS, generic expressions unsupported, and
  complex dynamic cross-language strings producing no inferred bridges.
- [ ] Run `python -m pytest tests/test_code_graph_ecmascript.py -q` for GREEN.
- [ ] Run `python -m pytest tests/test_code_graph_java.py tests/test_code_graph_python.py -q`.
- [ ] Run `git diff --check -- backend/code_graph/resolvers/ecmascript.py tests/test_code_graph_ecmascript.py`.
- [ ] Stage both files; commit `feat: resolve ecmascript graph calls and types`.

### Task 12: Public multi-pass builder and validated immutable publication

**Files:**
- Create: `backend/code_graph/builder.py`, `backend/code_graph/invariants.py`.
- Modify: `backend/code_graph/__init__.py`, `backend/code_graph/indexes.py`, `backend/code_graph/references.py`, `backend/code_graph/resolvers/__init__.py`.
- Modify/Test: `tests/test_code_graph_builder.py`, `tests/test_code_graph_models.py`.

**Interfaces:**
- Consumes: Tasks 1–11 unchanged contracts.
- Produces: `CodeGraphBuilder.build(self, file_inventory: FileInventory, parse_inventory: CodeParseInventory) -> CodeGraphInventory` and `build_code_graph(file_inventory: FileInventory, parse_inventory: CodeParseInventory) -> CodeGraphInventory`.
- Produces: `finalize_graph(index: GraphIndexes, edges: tuple[GraphEdge, ...], unresolved: tuple[UnresolvedReference, ...]) -> CodeGraphInventory` in indexes.py;
  `validate_graph(graph: CodeGraphInventory, index: GraphIndexes) -> None` in invariants.py.
- Produces: dispatcher functions `resolve_import` and `resolve_reference` in resolvers/__init__.py with the same signatures as the language modules. Dispatch is a literal ParsedLanguage-to-module mapping, not dynamic import.

- [ ] Write public end-to-end and import-order tests:

```python
from backend.code_graph import CodeGraphBuilder, build_code_graph
from backend.code_graph.models import GraphEdgeKind
from backend.code_parser.models import CallSite, CallKind

def test_public_builder_produces_calls(cg):
    call = CallSite("run", "helper", CallKind.CALL, cg.loc(20, 25))
    file = cg.parsed(symbols=(cg.symbol("run"), cg.symbol("helper", start=200, end=250)), calls=(call,))
    pair = cg.pair(file)
    graph = build_code_graph(*pair)
    assert graph == CodeGraphBuilder().build(*pair)
    assert sum(e.edge_kind is GraphEdgeKind.CALLS for e in graph.edges) == 1
```

- [ ] Run `python -m pytest tests/test_code_graph_builder.py tests/test_code_graph_models.py -q` for RED.
- [ ] Implement the orchestration in the approved order. The core wiring is:

```python
inputs = validate_inputs(file_inventory, parse_inventory)
index = build_graph_indexes(inputs)
edges = list(structural_edges(index))
by_file = {file.relative_path: tuple((item, resolve_import(index, file, item))
           for item in file.imports) for file in index.files_by_path.values()}
environment = ImportEnvironment(by_file)
```

Collect import references and their outcomes in a private
`list[tuple[Reference, Resolution]]`, then extract all call/type references and
resolve them through the dispatcher into that same list. Only after pass 3
finishes, pass 4 calls materialize for those outcomes and separates the returned
edges from unresolved records. The resolver modules must already be imported
before calls. No unresolved record is published during import processing.
Deduplicate identical records by ID; differing same-ID content raises
CodeGraphIdentityError. Never catch graph faults as NOT_FOUND. Return only
`finalize_graph(index, tuple(edges), tuple(unresolved))`.
- [ ] Implement canonical sort by node_id/edge_id/reference_id. Build incoming
  and outgoing entries for every node, including isolated FILEs; nodes_by_file
  covers every FILE path; qualified-name index contains only SYMBOLs, preserving
  duplicate names. Sort keys lexically and tuple values by IDs; detach backing
  maps before returning. Derive from canonical tuples, not resolver dictionaries.
- [ ] In validate_graph independently check ID uniqueness and identity
  recomputation using original SymbolInfo in index.symbols_by_id, namespace
  membership, one FILE per symbol path, source/target
  existence and kind, unresolved source file and candidate existence, exactly
  one incoming structural edge per SYMBOL, direct same-file structural edges,
  and structural-cycle freedom using a structural-only adjacency traversal.
  Compare structural edges to index.parent_ids; GraphNode alone does not retain
  parent_qualified_name. Recompute node projection from the indexed ParsedFile
  and SymbolInfo so altered model fields cannot pass through a valid ID.
  Verify every derived mapping against recomputed expected entries. A malformed
  graph raises CodeGraphInvariantError and cannot escape finalization. Call
  validate_graph(graph, index) before finalize_graph returns. Use TYPE_CHECKING
  for the invariants module's GraphIndexes annotation to avoid an import cycle.
- [ ] Add negative tests by replacing one valid graph field: duplicate node or
  edge, dangling source/target/candidate, wrong namespace, invalid endpoint,
  absent/extra structural parent, structural cycle, incorrect index, unresolved
  source in another file, and fabricated class CALLS endpoint. Positive tests
  cover recursion, import cycles, callable cycles, isolated nodes and empty graph.
  Test reused builder instances do not retain prior state; test all import
  outcomes exist before a call in an earlier-sorted file is resolved.
- [ ] Run `python -m pytest tests/test_code_graph_builder.py tests/test_code_graph_models.py -q` for GREEN.
- [ ] Run `python -m pytest tests -q -k code_graph` for neighboring graph regressions.
- [ ] Run `git diff --check -- backend/code_graph tests/test_code_graph_builder.py tests/test_code_graph_models.py`.
- [ ] Stage the eight listed production/test files; commit `feat: assemble and validate immutable code graphs`.

### Task 13: Relevant PARTIAL/FAILED evidence through the public pipeline

**Files:**
- Modify: `backend/code_graph/references.py`, `backend/code_graph/resolvers/base.py`, `backend/code_graph/resolvers/python.py`, `backend/code_graph/resolvers/java.py`, `backend/code_graph/resolvers/ecmascript.py`.
- Test: `tests/test_code_graph_partial_parses.py`.

**Interfaces:**
- Consumes: public build_code_graph and shared outcome contracts.
- Produces: no new public interface; complete relevant-incompleteness behavior using existing issues, ranges, scopes and module anchors.

- [ ] Write a retention test, then fault-probe status handling if it already passes:

```python
from backend.code_graph import build_code_graph
from backend.code_graph.models import GraphNodeKind
from backend.code_parser.models import ParseStatus, ParseIssue, ParseIssueKind

def test_partial_keeps_trusted_symbols(cg):
    issue = ParseIssue(ParseIssueKind.SYNTAX_ERROR, "broken elsewhere", cg.loc(300, 310))
    file = cg.parsed(status=ParseStatus.PARTIAL, symbols=(cg.symbol("safe"),), issues=(issue,))
    graph = build_code_graph(*cg.pair(file))
    assert len(graph.nodes) == 2
    assert all(n.parse_status is ParseStatus.PARTIAL for n in graph.nodes)
    assert sum(n.node_kind is GraphNodeKind.SYMBOL for n in graph.nodes) == 1
```

- [ ] Run `python -m pytest tests/test_code_graph_partial_parses.py -q` and record meaningful RED before a fix, or under the temporary fault probe.
- [ ] Keep explicit positive declarations usable; compute missing-evidence
  relevance from the selected lexical/type/module lookup, not a global PARTIAL
  flag. Locationless extraction issues contaminate the relevant file's uncertain
  lookup; do not invent a narrower affected range. Reference classification
  feeds the existing chooser:

```python
return choose_resolution(candidate_ids, unsupported=unsupported_shape,
                         incomplete=relevant_parse_loss, external=external_anchor)
```

Set unsupported_shape false only when parse loss itself accounts for missing
required evidence. Known complete explicit FILE mapping survives target FAILED.
Distinct trustworthy local scopes remain usable when their binding is complete.
- [ ] Cover all six supported source forms with parameterized parsed fixtures:
  trusted symbols/structural edges/unique relationships survive PARTIAL;
  unknown member in target FAILED/PARTIAL -> INCOMPLETE_PARSE; unrelated failed
  file has no effect; locationless truncation never resolves truncated names;
  true dynamic expression remains UNSUPPORTED; no owner because parse loss is
  INCOMPLETE_PARSE; valid top-level unowned syntax is UNSUPPORTED.
- [ ] Test FAILED file produces only FILE, no unresolved parse-issue records,
  and contradictory extraction rejects before any resolver runs. Test relevant
  incomplete candidate set does not become a definitive NOT_FOUND or a false
  unique edge; complete multiple candidates still produce AMBIGUOUS.
- [ ] Run `python -m pytest tests/test_code_graph_partial_parses.py -q` for GREEN.
- [ ] Run `python -m pytest tests -q -k code_graph`.
- [ ] Run `git diff --check -- backend/code_graph/references.py backend/code_graph/resolvers tests/test_code_graph_partial_parses.py`.
- [ ] Stage only changed listed files; commit `test: verify partial parse graph evidence boundaries` (use `fix:` if production correction is required).

### Task 14: Determinism, deduplication and full-evidence collisions

**Files:**
- Modify: `backend/code_graph/indexes.py`, `backend/code_graph/references.py`.
- Test: `tests/test_code_graph_determinism.py`.
- Modify/Test: `tests/test_code_graph_validation.py`.

**Interfaces:**
- Consumes: public builder, identities, canonical model equality.
- Produces: no new interfaces; deterministic records and hard same-ID conflict behavior.

- [ ] Add permutation equality test using independently constructed input order:

```python
from dataclasses import replace
from backend.code_graph import build_code_graph

def test_permuted_inventory_and_symbols_are_equal(cg):
    a = cg.parsed("a.py", symbols=(cg.symbol("x"), cg.symbol("y", start=200, end=250)))
    b = cg.parsed("b.py")
    first = build_code_graph(*cg.pair(a, b))
    second = build_code_graph(*cg.pair(b, replace(a, symbols=tuple(reversed(a.symbols)))))
    assert first == second
    assert tuple(first.nodes_by_file) == tuple(second.nodes_by_file)
```

- [ ] Run `python -m pytest tests/test_code_graph_determinism.py tests/test_code_graph_validation.py -q`; prove RED for each missing guarantee, using a reversible sort/collision fault if already GREEN.
- [ ] Implement/check a single equality-before-dedup rule in each canonical
  record accumulator, never `setdefault` without comparison:

```python
previous = records.get(record_id)
if previous is not None and previous != record:
    raise CodeGraphIdentityError("Conflicting graph identity.")
records[record_id] = record
```

Keep complete SymbolInfo equality before projection from Task 4. Distinct
reference strings that produce identical resolved edges may collapse as
identical graph evidence; differing content under that same ID must fail.
- [ ] Test repeated identical symbols/imports/calls collapse; repeated file
  records reject; same symbol ID differing modifiers, base_types, parent,
  parameters or full location raises identity error. Same unresolved ID with
  different reason/candidates raises collision during one build; the ID helper
  itself remains reason-independent. Different source sites yield different
  edge IDs. Shuffled imports, calls, type entries and files produce identical
  canonical outputs. Cover Unicode and case-distinct paths and names.
- [ ] Test ambiguity permutations yield exactly one unresolved record and zero
  speculative edges, including wildcard candidates and duplicate qualified
  names. Assert changing repository_path alone has no effect; changing namespace
  changes graph IDs. Do not use nondeterministic randomized fixture seeds.
- [ ] Run `python -m pytest tests/test_code_graph_determinism.py tests/test_code_graph_validation.py -q` for GREEN.
- [ ] Run `python -m pytest tests/test_code_graph_identities.py tests/test_code_graph_partial_parses.py tests/test_code_graph_builder.py -q`.
- [ ] Run `git diff --check -- backend/code_graph/indexes.py backend/code_graph/references.py tests/test_code_graph_determinism.py tests/test_code_graph_validation.py`.
- [ ] Stage only changed listed files; commit `test: lock graph determinism and collision semantics` (use `fix:` for production corrections).

### Task 15: Security and zero source rereads

**Files:**
- Test: `tests/test_code_graph_security.py`.
- Modify only on a demonstrated failure: `backend/code_graph/builder.py`, `backend/code_graph/validation.py`, `backend/code_graph/indexes.py`, `backend/code_graph/references.py`, `backend/code_graph/resolvers/python.py`, `backend/code_graph/resolvers/java.py`, `backend/code_graph/resolvers/ecmascript.py`.

**Interfaces:**
- Consumes: public graph builder and prepared in-memory fixtures.
- Produces: executable no-I/O/no-execution boundary tests; no runtime security wrapper or new dependency.

- [ ] Import trusted dependencies before patching and add this scoped guard:

```python
import builtins
import importlib
import io
import socket
import subprocess
from pathlib import Path
from backend.code_graph import build_code_graph

def test_graph_build_has_no_io(cg, monkeypatch):
    pair = cg.pair(cg.parsed(symbols=(cg.symbol("run"),)))
    def forbidden(*args, **kwargs):
        raise AssertionError("Module 6 crossed the in-memory boundary")
    with monkeypatch.context() as guard:
        for owner, name in [(builtins, "open"), (io, "open"),
            (Path, "open"), (Path, "read_text"), (Path, "read_bytes"),
            (subprocess, "run"), (subprocess, "Popen"),
            (importlib, "import_module"), (socket, "socket"),
            (socket, "create_connection")]:
            guard.setattr(owner, name, forbidden)
        graph = build_code_graph(*pair)
    assert len(graph.nodes) == 2
```

- [ ] Run `python -m pytest tests/test_code_graph_security.py -q` for RED proof.
  If already green, temporarily insert one forbidden source read at build entry,
  confirm the guard fails, and restore it before GREEN. Probe network and
  subprocess guards separately with equally temporary calls; commit no probes.
- [ ] Add parameterized prepared inventories exercising validation, every
  language import/call/type branch, ambiguity and partial parsing under the
  guard. Add `os.open`, `os.scandir`, `Path.stat`, and `Path.resolve` guards to
  catch hidden inspection. Hostile raw strings containing eval-like text,
  shell commands, URLs, `.env`, and path aliases remain inert evidence or reject
  validation; never execute them. Test exact raw_target preservation where valid.
- [ ] Add AST dependency audit before API patches. Walk only new production
  files, resolve relative imports, allow stdlib and `backend.code_graph.*`,
  `backend.file_scanner.models`, `backend.code_parser.models`. Reject imports
  of parser/reader/registry, Module 4/5, graph DBs, and compiler packages. Walk
  AST Call nodes for eval/exec and unsafe deserialization entry points; review
  attribute-based I/O too. Test the audit logic on an in-memory forbidden import
  AST so a vacuous scan cannot pass.
- [ ] Eliminate any demonstrated forbidden operation by consuming validated
  metadata/indexes; do not weaken guard scopes or whitelist production rereads.
  If resolution needs new upstream evidence, stop for design review.
- [ ] Run `python -m pytest tests/test_code_graph_security.py -q` for GREEN.
- [ ] Run `python -m pytest tests/test_code_graph_builder.py tests/test_code_graph_python.py tests/test_code_graph_java.py tests/test_code_graph_ecmascript.py -q`.
- [ ] Run `git diff --check -- backend/code_graph tests/test_code_graph_security.py`.
- [ ] Stage only test and demonstrated fixes; commit `test: enforce in-memory code graph security`.

### Task 16: Indexed scaling and candidate-output accounting

**Files:**
- Test: `tests/test_code_graph_scaling.py`.
- Modify only for measured regressions: `backend/code_graph/indexes.py`, `backend/code_graph/structure.py`, `backend/code_graph/references.py`, `backend/code_graph/resolvers/base.py`, `backend/code_graph/resolvers/python.py`, `backend/code_graph/resolvers/java.py`, `backend/code_graph/resolvers/ecmascript.py`.

**Interfaces:**
- Consumes: GraphIndexes and resolver interfaces; public builder.
- Produces: operation-count evidence, with no timing-dependent production API.

- [ ] Build synthetic inventories with 64, 128, 256 files and symbols; vary R
  independently from S. Wrap read-only mappings in a test CountingMapping whose
  `__getitem__`, `get`, `__iter__`, and yielded-key counts are recorded:

```python
from collections import Counter
from collections.abc import Mapping

class CountingMapping(Mapping):
    def __init__(self, source):
        self.source = source
        self.counts = Counter()
    def __getitem__(self, key):
        self.counts["get"] += 1
        return self.source[key]
    def __iter__(self):
        self.counts["iter"] += 1
        for key in self.source:
            self.counts["yield"] += 1
            yield key
    def __len__(self):
        return len(self.source)
```

- [ ] Run `python -m pytest tests/test_code_graph_scaling.py -q` for meaningful
  RED; temporarily replace one exact lookup with a global scan to prove that
  the count assertion catches O(R*S), then restore before GREEN.
- [ ] Resolve a fixed batch of same-file calls against a replaced GraphIndexes
  whose global maps are counted. Assert no global map iteration per reference,
  and doubling unrelated symbols does not increase per-call lookup count.
  Assert total scoped candidate visits are bounded by input/output size, not
  a fixed wall-clock threshold. Example assertion after the resolution loop:

```python
assert counted_global.counts["iter"] == 0
assert counted_global.counts["get"] <= 20 * reference_count
```

Here `counted_global` is the CountingMapping installed on nodes_by_id or
symbols_by_id, and reference_count is the actual batch length. Use separate
tests for index construction so its one-time linear scan is not forbidden.
- [ ] Count node visits during build_graph_indexes and structural_edges across
  doubled F/S. Test deep nested parents without repeated ancestor-chain walking
  per call; use precomputed scope links or memoized `(scope, name)` lookup.
  Count ImportEnvironment construction once and assert imported-name calls use
  bindings_by_scope_name rather than scanning all import records per reference.
  Test Java broad package wildcards with K matching files and U unrelated files:
  candidate visits grow with K and reference/output count, not U. Test all
  eight ECMAScript candidate paths without scanning file maps. Include repeated
  ambiguity sites and account for the R*K candidate IDs actually emitted.
- [ ] For failures, replace repeated global scans with Task 4's keyed buckets;
  memoize identical scope/name outcomes per build, never across repositories.
  Preserve ambiguity sets and identity order during optimization. Sorting remains
  O(n log n); do not assert linear comparison counts for canonical sorting.
- [ ] Run `python -m pytest tests/test_code_graph_scaling.py -q` for GREEN.
- [ ] Run `python -m pytest tests/test_code_graph_determinism.py tests/test_code_graph_security.py tests/test_code_graph_builder.py -q`.
- [ ] Run `git diff --check -- backend/code_graph tests/test_code_graph_scaling.py`.
- [ ] Stage test and measured fixes only; commit `test: verify indexed code graph scaling`.

### Task 17: Real upstream integration, public exports and usage

**Files:**
- Test: `tests/test_code_graph_integration.py`.
- Modify/Test: `tests/test_code_graph_models.py`.
- Modify: `backend/code_graph/__init__.py`, `README.md` (append Module 6 section only).

**Interfaces:**
- Consumes: FileScanner.scan, CodeParser.parse_inventory, CodeGraphBuilder.build, build_code_graph.
- Produces: verified public `__all__`, local multi-language pipeline tests, documented in-memory API usage.

- [ ] Write a real parser integration test, unmarked for network integration:

```python
from pathlib import Path
from backend.file_scanner import FileScanner
from backend.code_parser import CodeParser
from backend.code_graph import build_code_graph
from backend.code_graph.models import GraphEdgeKind

def test_real_pipeline_blocks_source_after_parse(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("def helper():\n    pass\n\ndef run():\n    helper()\n", encoding="utf-8")
    fi = FileScanner().scan(tmp_path, repository_namespace="integration-repo")
    pi = CodeParser().parse_inventory(fi)
    def forbidden(*args, **kwargs):
        raise AssertionError("Graph builder reread source")
    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", forbidden)
        guard.setattr(Path, "read_text", forbidden)
        guard.setattr(Path, "read_bytes", forbidden)
        graph = build_code_graph(fi, pi)
    assert sum(e.edge_kind is GraphEdgeKind.CALLS for e in graph.edges) == 1
```

- [ ] Run `python -m pytest tests/test_code_graph_integration.py tests/test_code_graph_models.py -q`; capture RED for missing exports/behavior. If already green, use the temporary source-read probe to establish the integration guard's sensitivity.
- [ ] Expand the guard to Task 15's complete forbidden API list AFTER real
  scanner/parser execution and grammar initialization. Add real `.py`, `.java`,
  `.js`, `.jsx`, `.ts`, `.tsx` files under tmp_path, with known import/call/type
  relationships. Assert actual upstream metadata first, then exact expected
  graph relationships and unresolved reasons; do not manually augment parsed
  fields to make resolution pass. Include BOM/Unicode bytes, nested declarations,
  real PARTIAL/FAILED syntax, external imports and candidate ambiguity.
- [ ] Export exactly the four models, four enums, five exceptions and two
  builder entry points. Test `__all__`, direct imports, API parameter names,
  return values and rejected path/callback keywords. Add no extra query API.
- [ ] Append a Module 6 README section with Python 3.12+ requirement scoped to
  this module, architecture branch independence, precision/unresolved semantics,
  immutable in-memory output, and this concrete usage snippet:

```python
from backend.file_scanner import FileScanner
from backend.code_parser import CodeParser
from backend.code_graph import build_code_graph

files = FileScanner().scan("workspace/example/repo", repository_namespace="example/repo")
parsed = CodeParser().parse_inventory(files)
graph = build_code_graph(files, parsed)
print(len(graph.nodes), len(graph.edges), len(graph.unresolved_references))
```

Explain that scanning/parsing reads source but graph construction does not.
Document `python -m pytest tests -q -k code_graph` and the explicit offline
marker expression. Do not alter Modules 1–5 usage or their requirements text.
- [ ] Run `python -m pytest tests/test_code_graph_integration.py tests/test_code_graph_models.py -q` for GREEN.
- [ ] Run `python -m pytest tests/test_code_parser_dependencies.py tests/test_code_parser.py tests/test_file_scanner.py tests/test_code_graph_security.py -q`.
- [ ] Run `git diff --check -- backend/code_graph/__init__.py README.md tests/test_code_graph_models.py tests/test_code_graph_integration.py`.
- [ ] Stage those files; commit `docs: document and verify code graph pipeline usage`.

### Task 18: Comprehensive offline acceptance and evidence commit

**Files:**
- Create: `docs/superpowers/verification/module-6-code-graph-builder.md`.
- Test: all twelve `tests/test_code_graph_*.py` files and existing regression paths in the commands below.
- Modify: no production file in this acceptance task. New or deterministic failures stop acceptance and return to their owning task for a focused RED/GREEN fix and meaningful commit before acceptance resumes. Only the specifically characterized Chroma flake below follows its evidence-and-rerun ruling.

**Interfaces:**
- Consumes: completed public Module 6 and all prior tests.
- Produces: fresh command results, scope/security audit, and a clean execution branch. This verification-only task does not invent a failing test or production stub.

- [ ] Confirm Python 3.12+ and the execution checkout state with `python --version`, `git branch --show-current`, `git status --short --branch`, and `git log --oneline -20`. Record the actual execution base from Git and all implementation commit IDs; verify it against the intended plan-commit base above rather than blindly assuming a hash. No squash.
- [ ] Run each command separately, preserve exit codes/counts, and stop on new or deterministic failures. The only exception is the narrowly defined Chroma ruling below:

```powershell
python -m pytest tests -q -k code_graph -m "not integration and not gemini_live" -rs
python -m pytest tests/test_repository_loader.py -q -m "not integration and not gemini_live" -rs
python -m pytest tests/test_file_scanner.py -q -rs
python -m pytest tests/test_code_parser_dependencies.py tests/test_code_parser.py -q -rs
python -m pytest tests/test_code_chunker.py -q -rs
python -m pytest tests/test_embedding_models.py tests/test_embedding_documents.py tests/test_embedding_validation.py tests/test_embedding_providers.py tests/test_embedding_retry.py tests/test_gemini_embedding_provider.py tests/test_gemini_live_safety.py tests/test_vector_store_contract.py tests/test_chroma_vector_store.py tests/test_chroma_integrity_deletion.py tests/test_semantic_synchronization.py tests/test_semantic_search.py tests/test_embedding_security.py tests/test_embedding_scaling.py tests/test_embedding_integration.py tests/capability/test_module5_dependency_capabilities.py -q -m "not integration and not gemini_live" -rs
python -m pytest -q -m "not integration and not gemini_live" -rs
python -m compileall -q backend tests
python -m pip check
git diff --check
git status --short --branch
```

No `-m "not gemini_live"` alone: that would include network cloning tests.
Do not run optional live tests. Inspect skip/deselection reasons; new graph
tests cannot be skipped. A missing package or grammar is an environment failure,
not evidence that Module 6 passed; repair only through the authorized execution
environment workflow, never by adding a Module 6 runtime dependency.

**Known ChromaDB 1.5.9 persisted HNSW reader flake ruling:**
This applies only when Module 5 regression or full-suite verification matches
the already-characterized intermittent persisted-reader failure in the
previously observed "Nothing found on disk" class. It is not a generic policy
to retry failed tests. Any NEW deterministic Module 1–6 failure remains an
immediate STOP condition; any other new failure stops acceptance for diagnosis.

1. Record the exact failing test/node, original failure and command.
2. Do NOT add production retries.
3. Do NOT weaken, skip, xfail, or suppress the test.
4. Do NOT modify Module 6 to work around Chroma.
5. Rerun the exact affected test/node in a fresh Python process with a fresh
   pytest basetemp, preserving the offline marker selection. Use a new unused
   basetemp directory so pytest cannot reuse or delete prior run evidence.
6. If the isolated node fails deterministically, STOP as a real blocker. Do not
   repeatedly rerun until it passes; an unexplained isolated failure also stops
   acceptance for diagnosis.
7. If the isolated node passes AND the original failure matches the already-known
   Chroma flake, rerun the affected regression/full suite cleanly in a fresh
   Python process with a fresh pytest basetemp.
8. Acceptance still requires a clean authoritative final run. A passing isolated
   node alone does not satisfy acceptance.
9. Record both the flaky run and clean evidence, including node IDs, commands,
   basetemp paths and outcomes, in the Module 6 verification report.

- [ ] In the graph focused result identify actual passing tests for IDs,
  permutations, collisions, graph invariants, immutable backing maps,
  PARTIAL/FAILED relevance, ownership, one-unresolved/zero-edge ambiguity,
  wildcard/scaling counts and real pipeline no-reread integration. Do not repeat
  suites absent a new fix or unresolved concern; cite the fresh run above.
- [ ] Audit changed paths against the execution base (set `$graphBase` to the
  recorded pre-implementation commit, not a guessed branch):

```powershell
git diff --name-status $graphBase HEAD
git diff $graphBase HEAD -- requirements.txt pytest.ini backend/file_scanner backend/code_parser backend/code_chunker backend/embedding_vector_store backend/repository_loader
rg -n 'eval\(|exec\(|read_text|read_bytes|subprocess|import_module|socket|Gemini|Chroma|node_modules|\.env|Maven|Gradle|npm|pip|pickle|yaml\.load' backend/code_graph
git diff $graphBase HEAD --check
```

The upstream/dependency diff must be empty. Review search hits as code, imports,
or documentation; a no-match rg exit 1 is a successful empty search, not a test
failure. Confirm AST security tests cover aliases/relative imports beyond text
search. Confirm no persistence, graph database, compiler/runtime packages,
Module 7 Git history, Module 8 runtime evidence, Module 9 hybrid retrieval, or
Module 10 Gemini reasoning/UI appears in the changed code.
- [ ] Write the evidence document with actual run date, Python version, base
  and implementation commit IDs, exact commands, exit statuses, pass/skip counts,
  scaling observations, and explicit scope/security results. Do not write
  predicted results or claim skipped gates passed. Record that graph integration
  reads source only in Modules 2/3 before the guard and makes zero network calls.
- [ ] Run `git diff --check -- docs/superpowers/verification/module-6-code-graph-builder.md`.
- [ ] Stage only `docs/superpowers/verification/module-6-code-graph-builder.md`, run `git diff --cached --check`, and
  commit `docs: record code graph verification evidence`.
- [ ] Run `git status --short --branch`, `git show --stat --oneline HEAD`, and
  `git diff $graphBase HEAD --check`. Report clean state and verification evidence
  to the user. Stop; do not push, merge, or start Modules 7–10.

## Spec coverage and plan self-review

The complete approved specification was checked section by section against this
plan. Coverage assignments are:

- Purpose/dependency position, public API and ownership: constraints, map, 12, 17, 18.
- Existing contracts, optional namespaces, JSX mapping, source coordinates: map, 1, 3, 17.
- Graph values, exceptions, node/edge semantics and direct containment: 1, 2, 4, 12.
- Unresolved evidence, call ownership, type declaration locations: 5, 7, 9, 11–14.
- Resolution precedence, blocked stronger bindings, raw evidence and candidate semantics: 5–14.
- Python import-form ambiguity and no MRO/runtime inference: 6, 7, 15.
- Java package metadata, overload limitation, explicit constructors: 8, 9, 17.
- ECMAScript extension/index candidates, export-map limits, aliases: 10, 11, 17.
- Cross-language boundary and no fabricated bridges: 7, 11, 15, 18.
- Parse status and input validation, nested records and ranges: 3, 4, 13, 17.
- Multi-pass order and graph invariants: 4, 5, 12, 14, 18.
- Exact identity payloads, full evidence collisions, ordering, case/Unicode: 2, 4, 14.
- Canonical tuples, immutable derived backing maps, isolated nodes: 1, 12, 14.
- Performance, deep scopes, wildcard output-size accounting: 4, 16, 18.
- Security, no hidden rereads, in-memory output: 15, 17, 18.
- Test strategy, every regression gate and strict non-goals: 1–18 and constraints.

Completed self-review of this plan (execution task checkboxes remain unchecked):

- [x] Matched interface names, fields and arguments to their producing tasks and
  upstream models; every consumer has an earlier producer. Clarified private
  record ownership and type-only imports to prevent circular dependencies.
- [x] Checked task Files lists, test command targets, RED/GREEN steps,
  neighboring regressions, scoped diff checks and meaningful commit messages.
- [x] Placeholder scan passed; all 36 Python snippets parsed successfully.
- [x] The verification-only task has no artificial RED step; existing-green
  hardening tasks use reversible sensitivity probes.
- [x] Mapped every spec section above. Corrected indexed binding lookup,
  metadata-backed final validation and pass-4 materialization sequencing.
- [x] Confirmed documentation-only scope: no production code or tests created
  or executed, no feature worktree, no new dependency, and no Modules 7–10 work.

Execution remains pending user review of this written plan.
