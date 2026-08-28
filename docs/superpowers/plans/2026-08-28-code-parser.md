# TraceRAG Module 3: Code Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Module 3 so a Module 2 `FileInventory` is safely converted into deterministic, immutable syntax metadata for Python, Java, JavaScript/JSX, TypeScript, and TSX without executing repository code.

**Architecture:** `CodeParser` filters only inventory-provided SOURCE/TEST entries, a fail-closed reader verifies and reads each candidate, a lazy per-instance registry supplies one pinned Tree-sitter parser per grammar, and three language adapters normalize syntax through shared iterative traversal helpers. The orchestrator retains only frozen metadata, represents every candidate with one mutually exclusive outcome, and performs no scanning or cross-file resolution.

**Tech Stack:** Python 3.11+, pytest, `tree-sitter==0.25.2`, `tree-sitter-python==0.25.0`, `tree-sitter-java==0.23.5`, `tree-sitter-javascript==0.25.0`, `tree-sitter-typescript==0.23.2`, standard-library filesystem APIs.

**Spec:** `docs/superpowers/specs/2026-08-28-code-parser-design.md`

## Global Constraints

- Read the approved spec before starting and treat its model fields, enum values, location semantics, policies, and non-goals as binding.
- Modify only `requirements.txt`, `backend/code_parser/**`, `tests/test_code_parser.py`, `tests/test_code_parser_dependencies.py`, and the Module 3 addition to `README.md`.
- Do not modify `backend/repository_loader/**`, `backend/file_scanner/**`, `tests/test_repository_loader.py`, or `tests/test_file_scanner.py`.
- Keep exactly the five direct dependency pins in this header. Never change a version to make the smoke test pass without stopping for design approval.
- V1 supports exactly Python, Java, JavaScript/JSX, TypeScript, and TSX.
- `SourceLocation` refers to original bytes: `[start_byte, end_byte)`, 1-based lines, 0-based UTF-8 byte columns, exclusive end point.
- Outcomes are mutually exclusive: `SUCCESS`, `PARTIAL`, `FAILED`, or skipped unsupported language.
- Never enumerate repository directories, retain full source/tree objects, execute repository code, download grammars at runtime, access a network during parsing, or perform cross-file resolution.
- Process one file at a time and use deterministic sorting and deduplication.
- Fail closed when non-following filesystem primitives or file identity checks are insufficient.
- Every implementation task follows RED, minimal GREEN, focused regression/full-module verification, and a focused commit.
- Use the project virtual environment on this host: `.venv/Scripts/python.exe`. On POSIX use the equivalent `.venv/bin/python`; commands below use `python` for portability after activation.

---

## File and Interface Map

```text
requirements.txt
    Exact tested Tree-sitter engine and grammar pins.

backend/code_parser/__init__.py
    Public exports and parse_code_inventory convenience function.
backend/code_parser/models.py
    Public enums and frozen/slotted data contracts.
backend/code_parser/exceptions.py
    Fatal module exception hierarchy only.
backend/code_parser/registry.py
    Closed grammar map, deterministic selection, lazy per-instance cache.
backend/code_parser/reader.py
    Root validation, path/link/reparse/identity/size checks, strict byte reads.
backend/code_parser/parser.py
    FileInventory validation, candidate orchestration, outcomes, counters, logging.
backend/code_parser/extractors/base.py
    Iterative traversal, scope events, location/text helpers, issues, normalization.
backend/code_parser/extractors/python.py
    Python syntax normalization.
backend/code_parser/extractors/java.py
    Java syntax normalization.
backend/code_parser/extractors/ecmascript.py
    Shared JavaScript/JSX/TypeScript/TSX normalization with explicit modes.
backend/code_parser/extractors/__init__.py
    Internal adapter lookup by closed extractor key.

tests/test_code_parser_dependencies.py
    Exact-version import/ABI/parser/minimal-root smoke gate.
tests/test_code_parser.py
    Public contracts, safety, registry, extractors, orchestration, and integration.
README.md
    Module 3 usage, guarantees, security boundaries, and limitations.
```

Do not create `queries/` speculatively. A language task may add a narrowly named `.scm` file only after a failing test demonstrates that explicit traversal cannot express the stable grammar role cleanly; the same task must test the query and include it in its focused commit.

The internal interfaces used throughout the tasks are:

```python
# registry.py
@dataclass(frozen=True, slots=True)
class ParserSpec:
    inventory_language: str
    language: ParsedLanguage
    extension: str
    extractor_key: str
    language_factory: Callable[[], Language]

@dataclass(frozen=True, slots=True)
class ParserHandle:
    spec: ParserSpec
    parser: Parser

class ParserRegistry:
    def select(self, file: ScannedFile) -> ParserSpec | None: ...
    def get_parser(self, spec: ParserSpec) -> ParserHandle: ...

# reader.py
@dataclass(frozen=True, slots=True)
class SourceBuffer:
    original_bytes: bytes
    parse_bytes: bytes
    bom_prefix_bytes: int

class SourceReadError(Exception):
    kind: ParseIssueKind
    message: str

class SafeSourceReader:
    def __init__(self, repository_path: Path | str) -> None: ...
    def read(self, file: ScannedFile) -> SourceBuffer: ...

# extractors/base.py
@dataclass(frozen=True, slots=True)
class ExtractionResult:
    symbols: tuple[SymbolInfo, ...]
    imports: tuple[ImportInfo, ...]
    calls: tuple[CallSite, ...]
    issues: tuple[ParseIssue, ...]

class BaseExtractor(Protocol):
    def extract(self, tree: Tree, source: SourceBuffer) -> ExtractionResult: ...

def get_extractor(extractor_key: str) -> BaseExtractor: ...

# parser.py
class CodeParser:
    def __init__(self) -> None: ...
    def parse_inventory(self, file_inventory: FileInventory) -> CodeParseInventory: ...

def parse_code_inventory(file_inventory: FileInventory) -> CodeParseInventory: ...
```

---

### Task 1: Exact Dependency and Grammar Compatibility Gate

**Files:**
- Modify: `requirements.txt`
- Create: `tests/test_code_parser_dependencies.py`

**Interfaces:**
- Consumes: the exact dependency versions approved in the design.
- Produces: installed importable engine/grammar packages and a permanent ABI/API smoke test used by every later task.

- [ ] **Step 1: Record the clean starting dependency state**

Run:

```bash
python -m pip show tree-sitter tree-sitter-python tree-sitter-java tree-sitter-javascript tree-sitter-typescript
```

Expected: record which packages are absent/present. Do not infer compatibility from installation alone.

- [ ] **Step 2: Write the failing exact-version and grammar smoke tests**

Create `tests/test_code_parser_dependencies.py` with an exact distribution-version assertion and one intended-API parse case per grammar:

```python
from importlib.metadata import version

import pytest
from tree_sitter import Language, Parser
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript


EXPECTED_VERSIONS = {
    "tree-sitter": "0.25.2",
    "tree-sitter-python": "0.25.0",
    "tree-sitter-java": "0.23.5",
    "tree-sitter-javascript": "0.25.0",
    "tree-sitter-typescript": "0.23.2",
}


def test_tree_sitter_dependency_versions_are_exact() -> None:
    assert {name: version(name) for name in EXPECTED_VERSIONS} == EXPECTED_VERSIONS


@pytest.mark.parametrize(
    ("language_capsule", "source", "root_type"),
    [
        (tree_sitter_python.language, b"def f():\n    pass\n", "module"),
        (tree_sitter_java.language, b"class A {}\n", "program"),
        (tree_sitter_javascript.language, b"function f() {}\n", "program"),
        (tree_sitter_typescript.language_typescript, b"interface A {}\n", "program"),
        (tree_sitter_typescript.language_tsx, b"const x = <div />;\n", "program"),
    ],
)
def test_pinned_grammar_initializes_and_parses_minimal_fixture(
    language_capsule: object, source: bytes, root_type: str
) -> None:
    language = Language(language_capsule())  # intended 0.25.2 API
    parser = Parser(language)
    tree = parser.parse(source)
    assert tree.root_node.type == root_type
    assert not tree.root_node.has_error
```

- [ ] **Step 3: Run the smoke test to verify RED**

Run:

```bash
python -m pytest tests/test_code_parser_dependencies.py -q
```

Expected: collection/import failure or exact-version failure because the approved dependencies are not yet installed/pinned. A different failure in already-installed exact versions is evidence of incompatibility, not permission to alter pins.

- [ ] **Step 4: Add only the approved exact pins**

Append to `requirements.txt` exactly:

```text
tree-sitter==0.25.2
tree-sitter-python==0.25.0
tree-sitter-java==0.23.5
tree-sitter-javascript==0.25.0
tree-sitter-typescript==0.23.2
```

- [ ] **Step 5: Install the locked dependency set in the implementation worktree environment**

Run:

```bash
python -m pip install -r requirements.txt
```

Expected: all exact distributions install. Network use is allowed only for this explicit dependency installation step, never for Module 3 runtime parsing.

- [ ] **Step 6: Run the smoke test to verify GREEN or stop**

Run:

```bash
python -m pytest tests/test_code_parser_dependencies.py -q
```

Expected: `6 passed` (one version test plus five grammar cases). If any import, `Language(...)`, `Parser(...)`, parse, root-type, or ABI assertion fails, stop all implementation, preserve the failing evidence, and report the dependency incompatibility. Do not change versions, API calls, or fixtures silently.

- [ ] **Step 7: Inspect the dependency diff for exactness**

Run:

```bash
git diff -- requirements.txt
python -m pip check
```

Expected: only the five approved pins were added; dependency consistency check exits zero.

- [ ] **Step 8: Commit the proven dependency gate**

```bash
git add requirements.txt tests/test_code_parser_dependencies.py
git commit -m "build: pin code parser grammars"
```

---

### Task 2: Public Models, Enums, and Fatal Exceptions

**Files:**
- Create: `backend/code_parser/__init__.py` with only a package docstring; Task 12 adds public exports.
- Create: `backend/code_parser/models.py`
- Create: `backend/code_parser/exceptions.py`
- Create: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: Module 2 naming and immutable dataclass conventions.
- Produces: every public enum/dataclass from approved design Sections 7–9 and the fatal exception hierarchy used by all later tasks.

- [ ] **Step 1: Write failing enum and immutability tests**

Add tests that assert every exact value:

```python
def test_code_parser_enum_values_are_stable() -> None:
    assert [item.value for item in ParsedLanguage] == [
        "python", "java", "javascript", "typescript", "tsx"
    ]
    assert [item.value for item in SymbolKind] == [
        "class", "interface", "function", "method", "constructor", "enum", "constant"
    ]
    assert [item.value for item in CallKind] == ["call", "constructor"]
    assert [item.value for item in ParseStatus] == ["success", "partial", "failed"]
    assert [item.value for item in ParseSkipReason] == ["unsupported_language"]
    assert [item.value for item in ParseIssueKind] == [
        "syntax_error", "missing_node", "read_error", "file_changed",
        "path_invalid", "link_unsafe", "decoding_error", "parser_unavailable",
        "extraction_error",
    ]


def test_source_location_is_frozen_and_uses_declared_field_order() -> None:
    location = SourceLocation(0, 3, 1, 0, 1, 3)
    assert tuple(location.__dataclass_fields__) == (
        "start_byte", "end_byte", "start_line", "start_column", "end_line", "end_column"
    )
    with pytest.raises(FrozenInstanceError):
        location.end_byte = 4  # type: ignore[misc]
```

Construct each remaining model with tuples and assert mutation raises `FrozenInstanceError`, field order matches the spec, and no field named `source`, `source_text`, `tree`, or `syntax_tree` exists.

- [ ] **Step 2: Write failing exception hierarchy tests**

```python
@pytest.mark.parametrize(
    "error_type",
    [InvalidParseInventory, ParserConfigurationError, RepositoryParseError],
)
def test_fatal_errors_share_code_parser_base(error_type: type[Exception]) -> None:
    assert issubclass(error_type, CodeParserError)
```

- [ ] **Step 3: Run contract tests to verify RED**

Run:

```bash
python -m pytest tests/test_code_parser.py -q -k "enum or frozen or field_order or fatal_errors"
```

Expected: collection fails because `backend.code_parser.models` and `exceptions` do not exist.

- [ ] **Step 4: Implement the exact public values**

Create the package directory and an `__init__.py` containing only `"""TraceRAG Code Parser."""`; public imports remain deliberately unavailable until Task 12. Create `models.py` with the six string enums and ten frozen/slotted dataclasses exactly as specified. Create `exceptions.py`:

```python
class CodeParserError(Exception):
    """Base class for fatal Code Parser failures."""


class InvalidParseInventory(CodeParserError):
    """Raised when FileInventory-level invariants are invalid."""


class ParserConfigurationError(CodeParserError):
    """Raised when the closed parser registry is invalid."""


class RepositoryParseError(CodeParserError):
    """Raised when a safe repository-root boundary cannot be established."""
```

Do not add validation constructors, default collections, source retention fields, global IDs, or resolved relationship fields.

- [ ] **Step 5: Run contract tests to verify GREEN**

Run:

```bash
python -m pytest tests/test_code_parser.py -q -k "enum or frozen or field_order or fatal_errors"
```

Expected: all selected tests pass and all collection fields are immutable tuples.

- [ ] **Step 6: Run dependency and Module 3 tests together**

```bash
python -m pytest tests/test_code_parser_dependencies.py tests/test_code_parser.py -q
```

Expected: all current tests pass.

- [ ] **Step 7: Commit the contracts**

```bash
git add backend/code_parser/__init__.py backend/code_parser/models.py backend/code_parser/exceptions.py tests/test_code_parser.py
git commit -m "feat: define code parser contracts"
```

---

### Task 3: Closed Parser Registry and Isolated Lazy Caching

**Files:**
- Create: `backend/code_parser/registry.py`
- Modify: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: `ParsedLanguage`, Module 2 `ScannedFile`, and the proven grammar construction API.
- Produces: `ParserSpec`, `ParserHandle`, and `ParserRegistry.select/get_parser`; one cache per registry instance and isolated failures.

- [ ] **Step 1: Write failing selection matrix tests**

Parameterize real `ScannedFile` values over the exact mapping and contradictions:

```python
@pytest.mark.parametrize(
    ("language", "extension", "expected"),
    [
        ("python", ".py", ParsedLanguage.PYTHON),
        ("java", ".java", ParsedLanguage.JAVA),
        ("javascript", ".js", ParsedLanguage.JAVASCRIPT),
        ("javascript", ".jsx", ParsedLanguage.JAVASCRIPT),
        ("typescript", ".ts", ParsedLanguage.TYPESCRIPT),
        ("typescript", ".tsx", ParsedLanguage.TSX),
        ("typescript", ".py", None),
        ("python", ".tsx", None),
        ("go", ".go", None),
        (None, ".py", None),
    ],
)
def test_registry_selects_only_exact_language_extension_pairs(language, extension, expected):
    file = scanned_file("src/file" + extension, language=language, extension=extension)
    spec = ParserRegistry().select(file)
    assert (None if spec is None else spec.language) is expected
```

- [ ] **Step 2: Write failing lazy-cache and isolation tests**

Inject a test-only `specs` tuple into `ParserRegistry(specs=...)` with counting and failing `language_factory` callables. Assert construction invokes neither; two `get_parser` calls for one spec invoke its factory once and return the identical parser; a failing spec does not poison a working spec; separate registries return different parser instances.

- [ ] **Step 3: Write failing static-configuration validation tests**

Assert duplicate `(language-hint, extension)` selectors, duplicate normalized languages, an unknown extractor key, or empty extension raises `ParserConfigurationError` during registry construction with sanitized fixed messages.

- [ ] **Step 4: Run registry tests to verify RED**

Run:

```bash
python -m pytest tests/test_code_parser.py -q -k "registry"
```

Expected: import/attribute failures because `ParserRegistry` is absent.

- [ ] **Step 5: Implement the closed registry minimally**

Create exact factories using the smoke-tested API:

```python
def _python_language() -> Language:
    return Language(tree_sitter_python.language())

def _java_language() -> Language:
    return Language(tree_sitter_java.language())

def _javascript_language() -> Language:
    return Language(tree_sitter_javascript.language())

def _typescript_language() -> Language:
    return Language(tree_sitter_typescript.language_typescript())

def _tsx_language() -> Language:
    return Language(tree_sitter_typescript.language_tsx())
```

Use closed extractor keys `python`, `java`, `javascript`, `typescript`, `tsx`. Catch grammar import/language/parser initialization exceptions in `get_parser` and raise an internal `ParserUnavailable` with fixed text; parser orchestration will translate it in Task 11. Do not dynamically import a module from repository metadata.

- [ ] **Step 6: Run registry tests to verify GREEN**

```bash
python -m pytest tests/test_code_parser.py -q -k "registry"
```

Expected: selection, laziness, caching, instance isolation, affected-language failure isolation, and configuration validation pass.

- [ ] **Step 7: Re-run permanent grammar smoke tests**

```bash
python -m pytest tests/test_code_parser_dependencies.py -q
```

Expected: `6 passed`; registry work did not alter pins or the intended API.

- [ ] **Step 8: Commit the registry**

```bash
git add backend/code_parser/registry.py tests/test_code_parser.py
git commit -m "feat: add lazy code parser registry"
```

---

### Task 4: Safe Source Reader and Filesystem Defenses

**Files:**
- Create: `backend/code_parser/reader.py`
- Modify: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: `FileInventory.repository_path`, candidate `ScannedFile`, `ParseIssueKind`.
- Produces: `SourceBuffer`, `SourceReadError`, `SafeSourceReader.read`; no directory enumeration and no decoded replacement text.

- [ ] **Step 1: Write failing repository-root and path-validation tests**

Test missing/file roots raise `RepositoryParseError`. Parameterize `relative_path` with `../../outside.py`, `/absolute.py`, `C:/outside.py`, `src\\file.py`, `src/../file.py`, `./file.py`, `src//file.py`, empty text, and NUL; each `read` raises `SourceReadError` with `PATH_INVALID` and never calls the open boundary. A valid nested POSIX path reconstructs under the resolved root.

- [ ] **Step 2: Write failing size/read/identity tests**

Use small files and narrow monkeypatches to assert:

- missing/unreadable file -> `READ_ERROR`;
- current metadata size differs from `ScannedFile.size_bytes` -> `FILE_CHANGED` before content acceptance;
- short read, overflow byte, or post-read identity mismatch -> `FILE_CHANGED`;
- non-regular final entry -> `LINK_UNSAFE` or `READ_ERROR` as fixed by the adapter contract;
- exactly matching regular bytes return `SourceBuffer(original_bytes=data, parse_bytes=data, bom_prefix_bytes=0)`.

Record calls to `os.scandir` and assert it is never invoked.

- [ ] **Step 3: Write failing encoding and BOM tests**

```python
@pytest.mark.parametrize("data", [b"caf\xe9", "x".encode("utf-16"), "x".encode("utf-32")])
def test_reader_rejects_non_utf8_without_replacement(tmp_path, data):
    file = write_inventory_file(tmp_path, "bad.py", data, "python")
    with pytest.raises(SourceReadError) as raised:
        SafeSourceReader(tmp_path).read(file)
    assert raised.value.kind is ParseIssueKind.DECODING_ERROR


def test_reader_preserves_original_utf8_bom_offsets(tmp_path):
    data = codecs.BOM_UTF8 + b"def f():\n    pass\n"
    file = write_inventory_file(tmp_path, "bom.py", data, "python")
    source = SafeSourceReader(tmp_path).read(file)
    assert source.original_bytes == data
    assert source.parse_bytes == data[len(codecs.BOM_UTF8):]
    assert source.bom_prefix_bytes == 3
```

- [ ] **Step 4: Write failing symlink and Windows-reparse adapter tests**

Where link creation is available, replace a scanned regular file with a link to an outside file and assert `LINK_UNSAFE` without reading target content. Skip only the OS operation that cannot be created. Independently test `is_reparse_metadata` with a fake `st_file_attributes` carrying `stat.FILE_ATTRIBUTE_REPARSE_POINT`. Patch the platform non-following opener as unavailable and assert fail-closed `LINK_UNSAFE`, never a fallback following open.

- [ ] **Step 5: Run reader tests to verify RED**

```bash
python -m pytest tests/test_code_parser.py -q -k "reader or repository_root or path_invalid or file_changed or decoding or bom or symlink or reparse or no_rescan"
```

Expected: collection or behavior failures because the reader does not exist.

- [ ] **Step 6: Implement strict path and byte validation**

Implement `SourceBuffer` and `SourceReadError(kind, message)`. Validate raw POSIX components before `Path` conversion; resolve only the trusted root, never a child link. Compare non-following pre-open metadata, opened-handle metadata, expected size, exact read length plus one-byte overflow probe, and post-read identity. Decode the complete accepted bytes as strict UTF-8 only to validate; retain bytes, not decoded text.

Split the narrow platform boundary into private helpers:

```python
def _open_verified_posix(root: Path, parts: tuple[str, ...]) -> BinaryIO: ...
def _open_verified_windows(root: Path, parts: tuple[str, ...]) -> BinaryIO: ...
def is_reparse_metadata(metadata: os.stat_result) -> bool: ...
```

POSIX traverses relative to an opened root directory descriptor with `O_DIRECTORY | O_NOFOLLOW` where supported and opens the final component with `O_NOFOLLOW`. Windows uses a handle opened with non-following reparse semantics and verifies file identity from that same handle. If required flags/identity are unavailable, raise `LINK_UNSAFE`; do not fall back to `Path.open()` following behavior.

- [ ] **Step 7: Run reader tests to verify GREEN**

```bash
python -m pytest tests/test_code_parser.py -q -k "reader or repository_root or path_invalid or file_changed or decoding or bom or symlink or reparse or no_rescan"
```

Expected: all supported-platform cases pass, unavailable real-link creation is explicitly skipped, and mocked fail-closed cases pass everywhere.

- [ ] **Step 8: Run focused Module 2 link tests unchanged**

```bash
python -m pytest tests/test_file_scanner.py -q -k "symlink or reparse or escape"
```

Expected: existing Module 2 safety tests still pass without any Module 2 diff.

- [ ] **Step 9: Commit the safe reader**

```bash
git add backend/code_parser/reader.py tests/test_code_parser.py
git commit -m "feat: read parser sources safely"
```

---

### Task 5: Shared Iterative Extraction and Normalization Infrastructure

**Files:**
- Create: `backend/code_parser/extractors/__init__.py`
- Create: `backend/code_parser/extractors/base.py`
- Modify: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: `Tree`, `Node`, `SourceBuffer`, public metadata models.
- Produces: `ExtractionResult`, iterative traversal/scope primitives, exact location conversion, bounded text, modifier normalization, deduplication/sorting, and adapter lookup.

- [ ] **Step 1: Write failing location tests with ASCII, Unicode, and BOM**

Parse fixtures through the proven Python grammar and assert `source_location(node, source)` yields exact original slices, half-open bytes, 1-based lines, and 0-based byte columns. Include a multibyte character before a node on the same line and a BOM fixture; assert columns count UTF-8 bytes and BOM adds three bytes only to line-one offsets/columns.

- [ ] **Step 2: Write failing bounded-text tests**

Assert `bounded_node_text` strips surrounding ASCII whitespace, preserves internal spelling, returns strict UTF-8, leaves values at or below 1,000 bytes unchanged, and truncates an over-limit multibyte value at a valid boundary with a final `…` while reporting `was_truncated=True`.

- [ ] **Step 3: Write failing iterative traversal and scope tests**

Build a deeply nested parsed fixture and assert `iter_events(root)` yields deterministic source-order `ENTER`/`EXIT` events without Python recursion. Exercise `ScopeStack` push/pop and nearest callable ownership across nested named and anonymous nodes.

- [ ] **Step 4: Write failing normalization tests**

Construct duplicate unsorted `SymbolInfo`, `ImportInfo`, `CallSite`, and `ParseIssue` values. Assert `normalize_extraction` uses the exact identity/sort keys from design Section 23, keeps first duplicate, places location-less issues last, and normalizes modifiers through a fixed precedence table.

- [ ] **Step 5: Run shared-infrastructure tests to verify RED**

```bash
python -m pytest tests/test_code_parser.py -q -k "source_location or bounded_text or iter_events or scope_stack or normalize_extraction or modifier_order"
```

Expected: imports fail because the base extractor infrastructure is absent.

- [ ] **Step 6: Implement shared infrastructure**

Implement an explicit stack traversal:

```python
def iter_events(root: Node) -> Iterator[TraversalEvent]:
    stack = [(root, False)]
    while stack:
        node, exiting = stack.pop()
        if exiting:
            yield TraversalEvent(EXIT, node)
            continue
        yield TraversalEvent(ENTER, node)
        stack.append((node, True))
        for child in reversed(node.children):
            stack.append((child, False))
```

Add `source_location`, `bounded_node_text`, `ScopeStack`, modifier precedence, immutable `ExtractionResult`, and exact normalization keys. `get_extractor` initially recognizes closed keys but may raise a fixed internal error until each adapter task registers its implementation; it never imports by arbitrary input string.

- [ ] **Step 7: Run shared tests to verify GREEN**

```bash
python -m pytest tests/test_code_parser.py -q -k "source_location or bounded_text or iter_events or scope_stack or normalize_extraction or modifier_order"
```

Expected: all selected tests pass, including Unicode/BOM exact slicing and deep iterative traversal.

- [ ] **Step 8: Commit the shared extraction layer**

```bash
git add backend/code_parser/extractors tests/test_code_parser.py
git commit -m "feat: add parser extraction primitives"
```

---

### Task 6: Python Extractor

**Files:**
- Create: `backend/code_parser/extractors/python.py`
- Modify: `backend/code_parser/extractors/__init__.py`
- Modify: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: Python `Tree`, `SourceBuffer`, shared traversal/location/normalization helpers.
- Produces: `PythonExtractor.extract(...) -> ExtractionResult` and extractor key `python`.

- [ ] **Step 1: Write the failing primary Python fixture test**

Use the approved fixture containing `import os`, aliased `from` import, `UserController(BaseController)`, `__init__`, typed/default parameters, declared return, `self.service.authenticate(email)`, and top-level helper. Assert exact symbol kinds/names/qualified parents, retained `self`, base types, imports/bindings, call owner/callee, and byte locations slicing the original declarations.

- [ ] **Step 2: Write failing advanced Python behavior tests**

Add separate small fixtures and literal assertions for:

- decorated async top-level function (`async` modifier) and decorator call owner `None`;
- `outer.inner` nested qualification and inner-call ownership;
- class method versus constructor classification;
- module/class uppercase constants only, excluding local/attribute/lowercase assignments;
- wildcard and relative imports;
- class-looking Python calls remain `CallKind.CALL`;
- over-1,000-byte default/type/callee capture truncation adds one `EXTRACTION_ERROR` issue without losing the symbol;
- comments/docstrings produce no metadata field or standalone item.

- [ ] **Step 3: Run Python tests to verify RED**

```bash
python -m pytest tests/test_code_parser.py -q -k "python_extractor"
```

Expected: extractor lookup fails for `python` or returns no structures.

- [ ] **Step 4: Implement Python syntax normalization**

Use named grammar node types and field access, not source regexes. On traversal enter/exit, maintain lexical type/callable scopes. Decorated definitions unwrap to their function/class declaration while the symbol location covers the declaration node selected by the spec tests. Emit `CONSTRUCTOR` only for class-contained `__init__`; retain receiver parameters; capture annotations/defaults/returns without evaluation; emit one import per module; apply uppercase module/class constant policy; emit all `call` nodes with nearest named callable owner.

- [ ] **Step 5: Run Python tests to verify GREEN**

```bash
python -m pytest tests/test_code_parser.py -q -k "python_extractor"
```

Expected: all Python structural, nesting, location, bounding, and exclusion assertions pass.

- [ ] **Step 6: Run shared and smoke regression tests**

```bash
python -m pytest tests/test_code_parser_dependencies.py tests/test_code_parser.py -q -k "python or source_location or normalize_extraction or dependency"
```

Expected: all selected cases pass.

- [ ] **Step 7: Commit the Python adapter**

```bash
git add backend/code_parser/extractors/python.py backend/code_parser/extractors/__init__.py tests/test_code_parser.py
git commit -m "feat: extract Python code structure"
```

---

### Task 7: Java Extractor

**Files:**
- Create: `backend/code_parser/extractors/java.py`
- Modify: `backend/code_parser/extractors/__init__.py`
- Modify: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: Java `Tree`, `SourceBuffer`, shared helpers.
- Produces: `JavaExtractor.extract(...) -> ExtractionResult` and extractor key `java`.

- [ ] **Step 1: Write the failing primary Java fixture test**

Use the approved `com.example.auth.AuthService` fixture. Assert package-qualified class/method/constructor names, `implements AuthProvider`, import `java.util.Optional`, constructor/method parameters and declared returns, visibility/final modifiers, `repository.findByEmail` call ownership, exact source ranges, and no ordinary `repository` field symbol.

- [ ] **Step 2: Write failing Java matrix tests**

Use focused fixtures for interface methods without bodies, enum declaration without enum-member symbols, class `extends` plus multiple `implements`, interface `extends`, overloaded methods sharing qualified names but distinct locations, `new User()` as `CallKind.CONSTRUCTOR`, static wildcard imports, final constants versus non-final/local variables, nested types, and initializer-block calls with caller `None`.

- [ ] **Step 3: Run Java tests to verify RED**

```bash
python -m pytest tests/test_code_parser.py -q -k "java_extractor"
```

Expected: extractor lookup fails for `java` or expected Java structures are absent.

- [ ] **Step 4: Implement Java syntax normalization**

Extract package text once, prefix top-level/nested type qualified names, distinguish constructors from methods, represent interface/abstract signatures as `METHOD`, retain declared types and modifiers, split extends/implements, include only simple `final` fields as constants, normalize static/wildcard imports, and distinguish method invocation from object creation calls. Do not resolve any qualified name.

- [ ] **Step 5: Run Java tests to verify GREEN**

```bash
python -m pytest tests/test_code_parser.py -q -k "java_extractor"
```

Expected: all primary and matrix assertions pass deterministically.

- [ ] **Step 6: Run all extractor regressions so far**

```bash
python -m pytest tests/test_code_parser.py -q -k "extractor or source_location or normalize_extraction"
```

Expected: Python and Java cases pass together.

- [ ] **Step 7: Commit the Java adapter**

```bash
git add backend/code_parser/extractors/java.py backend/code_parser/extractors/__init__.py tests/test_code_parser.py
git commit -m "feat: extract Java code structure"
```

---

### Task 8: JavaScript and JSX Extractor Mode

**Files:**
- Create: `backend/code_parser/extractors/ecmascript.py`
- Modify: `backend/code_parser/extractors/__init__.py`
- Modify: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: JavaScript grammar tree, shared helpers.
- Produces: `EcmaScriptExtractor(mode=JAVASCRIPT).extract(...)` under extractor key `javascript`; establishes shared adapter structure for Task 9.

- [ ] **Step 1: Write the failing primary JavaScript fixture test**

Use the approved `AuthService` fixture. Assert default ES import binding, exported class/function modifiers, constructor/method/function kinds, parent qualification, parameters, `api.login` and `api.logout` call ownership, exact locations, and no inferred return types.

- [ ] **Step 2: Write failing JavaScript/JSX policy tests**

Cover named arrow/function expressions bound by direct declaration/assignment; anonymous callbacks excluded as symbols but their calls owned by the nearest named callable; module-level simple `const` constants excluding destructuring/local variables; `new Client()` constructor calls; class `extends`; side-effect/named/namespace ES imports; direct/simple-destructured CommonJS imports; literal `require` both import and call; non-literal `require` only call; dynamic `import()` only call; `.jsx` parsing; JSX elements creating no symbols while calls in expression containers remain.

- [ ] **Step 3: Run JavaScript tests to verify RED**

```bash
python -m pytest tests/test_code_parser.py -q -k "javascript_extractor or jsx_extractor"
```

Expected: `ecmascript.py`/JavaScript adapter is missing or produces no structures.

- [ ] **Step 4: Implement shared ECMAScript adapter with JavaScript mode**

Define a closed internal mode enum and mode-specific node sets. Implement JavaScript declarations, stable-name function/arrow bindings, imports/CommonJS policy, classes/methods/constructors, calls/new, lexical ownership, constants, and JSX neutrality. Preserve the adapter seams for TypeScript-specific type/interface handling without enabling them in JavaScript mode.

- [ ] **Step 5: Run JavaScript/JSX tests to verify GREEN**

```bash
python -m pytest tests/test_code_parser.py -q -k "javascript_extractor or jsx_extractor"
```

Expected: all JavaScript and JSX cases pass, including dual `require` evidence and anonymous-symbol exclusion.

- [ ] **Step 6: Run all three language suites**

```bash
python -m pytest tests/test_code_parser.py -q -k "python_extractor or java_extractor or javascript_extractor or jsx_extractor"
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit JavaScript/JSX extraction**

```bash
git add backend/code_parser/extractors/ecmascript.py backend/code_parser/extractors/__init__.py tests/test_code_parser.py
git commit -m "feat: extract JavaScript code structure"
```

---

### Task 9: TypeScript and TSX Extractor Modes

**Files:**
- Modify: `backend/code_parser/extractors/ecmascript.py`
- Modify: `backend/code_parser/extractors/__init__.py`
- Modify: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: TypeScript and TSX trees plus the JavaScript adapter behavior.
- Produces: extractor keys `typescript` and `tsx`, with interface/type syntax enabled but no type-alias symbols.

- [ ] **Step 1: Write the failing TypeScript primary fixture test**

Use the approved `AuthProvider`/`AuthService` fixture. Assert interface and class symbols, interface method without body as `METHOD`, typed parameter and return, `implements AuthProvider`, import binding, `api.login` call owner, and exact byte ranges.

- [ ] **Step 2: Write failing TypeScript policy tests**

Cover interface `extends`, class `extends` plus multiple `implements`, overload signatures sharing qualified names, optional/rest/default/destructured parameters with bounded exact pattern/type/default text, named typed arrows, module `const`, `static readonly` class constants, and exclusion of type aliases, anonymous callbacks, local variables, and ordinary class fields.

- [ ] **Step 3: Write the failing TSX fixture test**

Use the approved `LoginButton` fixture and assert registry-selected TSX mode parses JSX, emits one `FUNCTION`, stores parameter name `{ onLogin }`, type `Props`, return `JSX.Element`, emits `onLogin` call with caller `LoginButton`, and emits no symbols for `type Props`, JSX tags, or inline anonymous arrow.

- [ ] **Step 4: Run TypeScript/TSX tests to verify RED**

```bash
python -m pytest tests/test_code_parser.py -q -k "typescript_extractor or tsx_extractor"
```

Expected: TypeScript/TSX keys are absent or type/interface assertions fail.

- [ ] **Step 5: Implement TypeScript and TSX modes**

Reuse JavaScript traversal/import/call logic. Add mode-gated interface declarations/method signatures, declared parameter/return types, extends/implements syntax, TypeScript modifiers, parameter patterns, and `static readonly` constants. Register separate `typescript` and `tsx` extractor instances. Do not treat type aliases or JSX nodes as symbols.

- [ ] **Step 6: Run TypeScript/TSX tests to verify GREEN**

```bash
python -m pytest tests/test_code_parser.py -q -k "typescript_extractor or tsx_extractor"
```

Expected: all TypeScript and TSX assertions pass, including TSX grammar behavior and exact locations.

- [ ] **Step 7: Run the complete language matrix and smoke gate**

```bash
python -m pytest tests/test_code_parser_dependencies.py tests/test_code_parser.py -q -k "extractor or grammar or source_location"
```

Expected: all five grammar modes and four language families pass together.

- [ ] **Step 8: Commit TypeScript/TSX extraction**

```bash
git add backend/code_parser/extractors/ecmascript.py backend/code_parser/extractors/__init__.py tests/test_code_parser.py
git commit -m "feat: extract TypeScript and TSX structure"
```

---

### Task 10: Syntax Issues and Partial-Parse Classification

**Files:**
- Modify: `backend/code_parser/extractors/base.py`
- Modify: `backend/code_parser/extractors/python.py`
- Modify: `backend/code_parser/extractors/java.py`
- Modify: `backend/code_parser/extractors/ecmascript.py`
- Modify: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: Tree-sitter `ERROR`, `is_missing`, completed extraction results.
- Produces: `collect_syntax_issues(tree, source)`, fixed sanitized issues, and `classify_parse_status(result) -> ParseStatus`.

- [ ] **Step 1: Write failing malformed-source tests for every grammar mode**

Use malformed Python, Java, JavaScript, TypeScript, and TSX fixtures containing one complete declaration plus a broken construct. Assert useful complete metadata is retained, status helper returns `PARTIAL`, issues contain `SYNTAX_ERROR` and/or `MISSING_NODE` with original-byte locations, and no raw source fragment appears in messages.

- [ ] **Step 2: Write failing failed-versus-success status tests**

Assert a clean empty/symbol-free supported tree is `SUCCESS`; a clean structural tree is `SUCCESS`; malformed input with no trustworthy extracted item is `FAILED`; a bounded-text `EXTRACTION_ERROR` plus retained symbol is `PARTIAL`; and a fatal extractor result has empty structural collections with `FAILED`.

- [ ] **Step 3: Write failing error-span and extraction exclusion tests**

Assert duplicate/overlapping identical error captures collapse by issue key, missing nodes produce `MISSING_NODE`, location-less issues sort last, and declarations/calls wholly inside an invalid span are excluded unless the captured node is complete with required name/location fields.

- [ ] **Step 4: Run issue/status tests to verify RED**

```bash
python -m pytest tests/test_code_parser.py -q -k "syntax_issue or partial_parse or parse_status or malformed"
```

Expected: issue collection/status classification is absent or malformed fixtures are incorrectly treated as clean.

- [ ] **Step 5: Implement issue collection and classification once**

Traverse the tree iteratively, emit fixed messages (`"Syntax error in source file."`, `"Required syntax is missing."`), convert locations through the original-byte adapter, merge adapter issues, normalize/deduplicate, then apply:

```python
def classify_parse_status(result: ExtractionResult) -> ParseStatus:
    has_structure = bool(result.symbols or result.imports or result.calls)
    if not result.issues:
        return ParseStatus.SUCCESS
    return ParseStatus.PARTIAL if has_structure else ParseStatus.FAILED
```

Adapters consult invalid spans before emitting nodes. Do not expose Tree-sitter exception strings or source text.

- [ ] **Step 6: Run issue/status tests to verify GREEN**

```bash
python -m pytest tests/test_code_parser.py -q -k "syntax_issue or partial_parse or parse_status or malformed"
```

Expected: malformed cases are recoverable and status rules are exact across all grammars.

- [ ] **Step 7: Run complete extractor regression suite**

```bash
python -m pytest tests/test_code_parser.py -q -k "extractor or syntax_issue or partial_parse or source_location or normalize_extraction"
```

Expected: all extraction and partial-parse tests pass.

- [ ] **Step 8: Commit partial parsing behavior**

```bash
git add backend/code_parser/extractors tests/test_code_parser.py
git commit -m "feat: report partial code parses"
```

---

### Task 11: Inventory Orchestration, Determinism, Outcomes, and Counters

**Files:**
- Create: `backend/code_parser/parser.py`
- Modify: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: valid `FileInventory`, `ParserRegistry`, `SafeSourceReader`, extractor lookup/results.
- Produces: internal-complete `CodeParser.parse_inventory(...) -> CodeParseInventory`; public exporting waits for Task 12.

- [ ] **Step 1: Write failing inventory validation tests**

Assert non-`FileInventory`, `included_files != len(files)`, `ignored_files != len(ignored)`, or `total_files_seen != included_files + ignored_files` raises `InvalidParseInventory`. Assert missing/inaccessible root raises `RepositoryParseError`. Individual malformed `ScannedFile` data remains a per-file outcome.

- [ ] **Step 2: Write failing category/unsupported/outcome tests**

Construct an inventory mixing SOURCE, TEST, CONFIG, BUILD, DOCUMENTATION, supported, unsupported, and contradictory language/extension entries. Assert only SOURCE/TEST affect `total_files_requested`; non-code categories appear nowhere; unsupported/contradictory candidates are `SkippedParseFile(..., UNSUPPORTED_LANGUAGE)`; supported files become exactly one `ParsedFile` outcome.

- [ ] **Step 3: Write failing per-file recovery tests**

Inject/monkeypatch reader, registry, parser, and extractor boundaries to assert mappings:

- missing/unreadable -> `FAILED/READ_ERROR`;
- changed -> `FAILED/FILE_CHANGED`;
- invalid path -> `FAILED/PATH_INVALID`;
- unsafe link -> `FAILED/LINK_UNSAFE`;
- invalid encoding -> `FAILED/DECODING_ERROR`;
- affected grammar failure -> `FAILED/PARSER_UNAVAILABLE` while another language succeeds;
- unexpected adapter exception -> `FAILED/EXTRACTION_ERROR` while siblings continue.

All failure structural tuples are empty and messages are fixed/sanitized.

- [ ] **Step 4: Write failing deterministic ordering/dedup/counter tests**

Provide inventory files in reverse/mixed-case order and mocked capture outputs in varying duplicate order. Assert file/skipped order, within-file normalization keys, first-value duplicate retention, exact mutually exclusive counts, and:

```python
assert result.total_files_requested == (
    result.success_files + result.partial_files + result.failed_files + result.skipped_files
)
assert len(result.files) == result.success_files + result.partial_files + result.failed_files
assert len(result.skipped) == result.skipped_files
```

- [ ] **Step 5: Write failing one-file-at-a-time/no-retention test**

Use recording fakes and weak references where supported to assert each file completes extraction before the next read begins, every supported file parses exactly once, the inventory contains no `bytes`, `str` source payload, `Tree`, or `Node` fields/references, and `os.scandir` remains unused.

- [ ] **Step 6: Run orchestration tests to verify RED**

```bash
python -m pytest tests/test_code_parser.py -q -k "inventory_validation or category_selection or unsupported or per_file_failure or counters or deterministic or no_retention or one_file"
```

Expected: `CodeParser`/orchestration is absent.

- [ ] **Step 7: Implement inventory orchestration**

Validate root-level invariants before processing. Sort SOURCE/TEST candidates by `(relative_path.casefold(), relative_path)`. For each, select registry, skip unsupported, read bytes, parse once, extract once, merge syntax issues, classify status, normalize, append immutable result, and release local tree/source references before continuing. Translate only known per-file boundaries; allow `InvalidParseInventory`, `ParserConfigurationError`, and `RepositoryParseError` to remain fatal.

- [ ] **Step 8: Run orchestration tests to verify GREEN**

```bash
python -m pytest tests/test_code_parser.py -q -k "inventory_validation or category_selection or unsupported or per_file_failure or counters or deterministic or no_retention or one_file"
```

Expected: all outcome, counter, ordering, isolation, and retention assertions pass.

- [ ] **Step 9: Run the complete Module 3 suite**

```bash
python -m pytest tests/test_code_parser_dependencies.py tests/test_code_parser.py -q
```

Expected: all Module 3 tests pass.

- [ ] **Step 10: Commit orchestration**

```bash
git add backend/code_parser/parser.py tests/test_code_parser.py
git commit -m "feat: orchestrate code inventory parsing"
```

---

### Task 12: Public API, Module 2 Integration, and Safe Logging

**Files:**
- Modify: `backend/code_parser/__init__.py`
- Modify: `backend/code_parser/parser.py`
- Modify: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: completed Module 3 internals and unchanged Module 2 public contracts.
- Produces: explicit package `__all__`, `CodeParser.parse_inventory`, `parse_code_inventory`, lifecycle logging, and proven Module 2 handoff without rescanning.

- [ ] **Step 1: Write failing public export and convenience tests**

Import every public model, enum, fatal exception, `CodeParser`, and `parse_code_inventory` from `backend.code_parser`; assert the explicit `__all__` set. Patch `CodeParser.parse_inventory` and assert:

```python
def parse_code_inventory(file_inventory: FileInventory) -> CodeParseInventory:
    return CodeParser().parse_inventory(file_inventory)
```

- [ ] **Step 2: Write failing real Module 2 integration test**

Create a temporary tree containing one file for each supported grammar, one unsupported `.go` source, and config/docs. Run the real unchanged `FileScanner().scan(tmp_path)`, then `CodeParser().parse_inventory(inventory)`. Assert five supported successes, one unsupported skip, non-code exclusion, TSX selected by `.tsx`, and no call to `FileScanner.scan` from inside Module 3 by patching it only after inventory construction.

- [ ] **Step 3: Write failing logging tests**

With `caplog`, parse files containing sentinel source/default/callee/invalid bytes. Assert INFO start/aggregate completion, DEBUG relative path/status/counts, WARNING sanitized relative path/issue kind for recoverable failure, ERROR only for a separately triggered fatal root failure, and absence of all sentinel source text, raw bytes, exception text, defaults, annotations, and callee expressions.

- [ ] **Step 4: Run API/integration/logging tests to verify RED**

```bash
python -m pytest tests/test_code_parser.py -q -k "public_export or convenience or module_2_integration or logging"
```

Expected: package exports/convenience function/logging assertions fail.

- [ ] **Step 5: Implement public exports, wrapper, and logging**

Mirror Modules 1/2 with explicit imports and `__all__`. Add `logger = logging.getLogger(__name__)` to `parser.py`; log only lifecycle aggregates and sanitized metadata at the levels fixed by design Section 27. Do not change Module 2 or accept a repository URL/path in the public parsing API.

- [ ] **Step 6: Run API/integration/logging tests to verify GREEN**

```bash
python -m pytest tests/test_code_parser.py -q -k "public_export or convenience or module_2_integration or logging"
```

Expected: public usage and real scanner handoff pass without rescanning or content leakage.

- [ ] **Step 7: Run Modules 1–3 together**

```bash
python -m pytest tests/test_repository_loader.py tests/test_file_scanner.py tests/test_code_parser_dependencies.py tests/test_code_parser.py -q
```

Expected: all offline tests pass; only existing platform/integration skips or deselections remain.

- [ ] **Step 8: Commit the public API**

```bash
git add backend/code_parser/__init__.py backend/code_parser/parser.py tests/test_code_parser.py
git commit -m "feat: expose code parser public API"
```

---

### Task 13: README and Dependency-Update Documentation

**Files:**
- Modify: `README.md`
- Modify: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: completed public API and exact behavior.
- Produces: Module 3 user documentation plus regression assertions that examples/claims stay valid.

- [ ] **Step 1: Write failing documentation-contract test**

Read `README.md` as UTF-8 and assert it contains `Module 3 — Code Parser`, `CodeParser`, `parse_inventory`, `FileInventory`, `CodeParseInventory`, all five language names including TSX, `[start_byte, end_byte)`, partial/unsupported behavior, exact Tree-sitter pins or a direct reference to `requirements.txt`, offline/no-execution language, and explicit no cross-file resolution/chunking/RAG statements.

- [ ] **Step 2: Run documentation test to verify RED**

```bash
python -m pytest tests/test_code_parser.py -q -k "readme_documents_module_3"
```

Expected: FAIL because README has no Module 3 section.

- [ ] **Step 3: Append the Module 3 README section**

Preserve Modules 1 and 2 verbatim. Include executable usage:

```python
from backend.code_parser import CodeParser
from backend.file_scanner import FileScanner

inventory = FileScanner().scan(repository.local_path)
parsed = CodeParser().parse_inventory(inventory)
```

Document input/output, supported grammar modes, pinned installed-wheel strategy, exact source-location semantics, extracted metadata, unsupported skips, partial parsing, one-file memory behavior, no source/tree retention, no execution/network/runtime downloads/rescanning/cross-file resolution, and Module 4+ exclusions.

Add a dependency-update note: change all five pins as one reviewed set, create a clean environment, run `tests/test_code_parser_dependencies.py`, then all language fixtures; stop/revert on any ABI/root/error regression.

- [ ] **Step 4: Run documentation test to verify GREEN**

```bash
python -m pytest tests/test_code_parser.py -q -k "readme_documents_module_3"
```

Expected: PASS.

- [ ] **Step 5: Run the complete offline suite**

```bash
python -m pytest -q
```

Expected: all offline tests pass with only explicitly expected skips/deselections.

- [ ] **Step 6: Commit documentation**

```bash
git add README.md tests/test_code_parser.py
git commit -m "docs: document code parser module"
```

---

### Task 14: Final Security, Scope, and Full-Suite Verification Gate

**Files:**
- Modify only a Module 3 file/test if a freshly reproduced verification failure requires a minimal RED/GREEN correction.
- Do not modify: `backend/repository_loader/**`, `backend/file_scanner/**`, `tests/test_repository_loader.py`, `tests/test_file_scanner.py`, or the approved design.

**Interfaces:**
- Consumes: completed Module 3 implementation and documentation.
- Produces: fresh evidence, exact commit inventory, and a clean implementation worktree ready for the finishing-development-branch workflow.

- [ ] **Step 1: Derive the comparison base and confirm scope**

```bash
traceRagBase=$(git merge-base HEAD main)
git diff --name-only "$traceRagBase...HEAD"
git diff --exit-code "$traceRagBase...HEAD" -- backend/repository_loader backend/file_scanner tests/test_repository_loader.py tests/test_file_scanner.py
```

PowerShell equivalent:

```powershell
$traceRagBase = git merge-base HEAD main
git diff --name-only "$traceRagBase...HEAD"
git diff --exit-code "$traceRagBase...HEAD" -- backend/repository_loader backend/file_scanner tests/test_repository_loader.py tests/test_file_scanner.py
```

Expected: changes are limited to the plan-authorized Module 3, dependency, tests, and README paths; the Module 1/2 preservation diff exits zero.

- [ ] **Step 2: Re-run the hard grammar compatibility gate**

```bash
python -m pytest tests/test_code_parser_dependencies.py -q
```

Expected: `6 passed` with exact installed versions and all five intended grammar API parses clean. Any failure blocks completion and must not trigger silent version changes.

- [ ] **Step 3: Run focused Module 3 tests**

```bash
python -m pytest tests/test_code_parser.py -q
```

Expected: every Module 3 contract, safety, language, partial parse, ordering, integration, and logging test passes; platform-unavailable real symlink cases may be explicitly skipped while adapter tests still pass.

- [ ] **Step 4: Run focused security/invariant tests**

```bash
python -m pytest tests/test_code_parser.py -q -k "path_invalid or symlink or reparse or file_changed or decoding or no_rescan or no_retention or deterministic or counters or parser_unavailable or logging"
```

Expected: all selected supported-platform cases pass; no escape/link following, replacement acceptance, destructive decoding, content retention, nondeterminism, or failure cascade occurs.

- [ ] **Step 5: Run the full pytest suite fresh**

```bash
python -m pytest -q
```

Expected: all offline Modules 1–3 tests pass, with only explicitly expected skips and the configured network integration deselection.

- [ ] **Step 6: Run static syntax and whitespace verification**

```bash
git diff --check "$traceRagBase...HEAD"
python -m compileall -q backend tests
```

Expected: both commands exit zero with no whitespace or Python compilation errors.

- [ ] **Step 7: Inspect the dependency diff and environment consistency**

```bash
git diff "$traceRagBase...HEAD" -- requirements.txt
python -m pip check
python -m pip show tree-sitter tree-sitter-python tree-sitter-java tree-sitter-javascript tree-sitter-typescript
```

Expected: the diff contains exactly the five approved pins; installed versions match them; `pip check` exits zero.

- [ ] **Step 8: Search for forbidden execution, network, scanning, and dynamic loading behavior**

Run:

```bash
rg -n "subprocess|shell=True|os\.system|eval\(|exec\(|compile\(|__import__|import_module|pickle|marshal|yaml\.load|requests|httpx|urllib|socket|GitPython|from git|import git|npm|maven|gradle|pip install|gemini|openai|embedding|vector|os\.walk|os\.scandir|rglob|glob\(" backend/code_parser tests/test_code_parser.py
```

Expected: no production match provides forbidden behavior. Review expected test assertions and the deliberate `os.scandir` no-rescan sentinel in context. The only `pip install` instruction is documentation/planning, not production runtime code.

- [ ] **Step 9: Inspect retention and cross-file-resolution boundaries manually**

```bash
rg -n "source_text|syntax_tree|Tree\b|Node\b|resolve_import|resolve_call|resolve_symbol|call_graph|inheritance_graph" backend/code_parser
```

Expected: `Tree`/`Node` appear only in transient extractor annotations/locals; no public model stores them or full source; no resolution/graph implementation exists.

- [ ] **Step 10: Review exact commits and worktree state**

```bash
git log --oneline "$traceRagBase..HEAD"
git status --short --branch
```

Expected: the focused sequence below appears with no unrelated commits, and the worktree is clean.

- [ ] **Step 11: Correct only freshly demonstrated failures through RED/GREEN**

If Steps 1–10 expose a defect, add one focused regression test, run it to observe the exact failure, make the smallest in-scope Module 3 correction, rerun that test and Steps 1–10, then commit with a narrowly descriptive `fix:` message. If the dependency smoke gate fails, stop and report instead of correcting pins.

- [ ] **Step 12: Stop before integration or Module 4**

Report exact test counts/skips/deselections, grammar smoke result, security search review, dependency diff, Module 1/2 zero-diff evidence, commit list, changed-file list, and worktree status. Do not merge, push, create a PR, or begin Module 4. Invoke `superpowers:finishing-a-development-branch` only after the user chooses to integrate.

---

## Expected Commit Sequence

```text
build: pin code parser grammars
feat: define code parser contracts
feat: add lazy code parser registry
feat: read parser sources safely
feat: add parser extraction primitives
feat: extract Python code structure
feat: extract Java code structure
feat: extract JavaScript code structure
feat: extract TypeScript and TSX structure
feat: report partial code parses
feat: orchestrate code inventory parsing
feat: expose code parser public API
docs: document code parser module
```

A verification-only correction receives one additional narrowly named `fix:` commit after a demonstrated RED/GREEN cycle. Dependency incompatibility receives no workaround commit; execution stops for design review.

## Unresolved Implementation Risks

- **Pinned native compatibility:** Task 1 is a hard gate. Wheel availability or ABI/API incompatibility on an implementation platform blocks all later work and must be reported without changing versions.
- **Windows non-following file semantics:** Task 4 must prove the platform adapter reads from a non-following, identity-verified handle. If the required primitive cannot be established, affected files fail closed with `LINK_UNSAFE`; weakening the guarantee requires a new design approval.
- **Grammar node-shape drift:** Exact pins contain this risk. Language tasks use minimal fixtures plus behavior matrices and avoid source regex parsing. Any grammar upgrade is a coordinated dependency-set change.
- **Partial-tree trust boundary:** Task 10 tests extraction around `ERROR`/missing spans across every grammar so malformed syntax cannot create fabricated complete metadata.
- **Byte-column/BOM correctness:** Task 5 makes original-byte slicing, multibyte columns, and BOM adjustment independent shared contract tests before language adapters rely on them.
- **Output volume from calls:** V1 intentionally records every syntactic call. Bounded text, one-file processing, no tree/source retention, and deterministic normalization constrain memory; semantic pruning belongs to later approved work.

## Approval and Execution Gate

This plan changes no production code by itself. After plan approval, create an isolated feature worktree using `superpowers:using-git-worktrees`, then execute task-by-task with `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`. Stop at the first failed hard gate. Do not merge, push, create a PR, or start Module 4 without explicit user direction.
