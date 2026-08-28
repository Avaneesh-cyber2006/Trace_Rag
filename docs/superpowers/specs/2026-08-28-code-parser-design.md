# TraceRAG Module 3: Code Parser Design

## 1. Scope

Module 3 consumes Module 2's `FileInventory` and turns eligible source and test files into deterministic, language-independent structural metadata. Version 1 parses Python, Java, JavaScript/JSX, TypeScript, and TSX with Tree-sitter. It extracts symbols, parameters, declared types, modifiers, imports, direct inheritance/implementation syntax, calls, nesting, and exact source locations.

The parser treats repository contents as untrusted data. It reads and parses files but never executes, imports, compiles, installs, evaluates, or sends repository content over a network. It does not scan directories and does not recreate Module 2 filtering policy.

Input and output are:

```text
FileInventory
    -> select SOURCE / TEST entries
    -> safely read one file at a time
    -> select an installed grammar
    -> parse and extract syntax
    -> normalize and sort results
    -> CodeParseInventory
```

## 2. Existing-Module Integration

The design is based on the contracts currently on `main` at commit `5100200`.

- `RepositoryInfo.local_path` is a resolved filesystem path represented as `str`.
- `FileInventory.repository_path` is the resolved root represented as `str`.
- `FileInventory.files` is an immutable tuple of frozen/slotted `ScannedFile` values.
- `ScannedFile.relative_path` is repository-relative POSIX text, even on Windows.
- `ScannedFile.category` is a `FileCategory`; only `SOURCE` and `TEST` are candidates.
- `ScannedFile.language` is a lowercase hint or `None`.
- `.ts` and `.tsx` both have the Module 2 language label `typescript`; Module 3 must use the trusted `extension` field to distinguish them.
- Module 2 accepts more source languages than Module 3 V1. A candidate in another language is a normal skipped outcome.
- Module 2's inventory is a snapshot and explicitly requires later consumers to repeat containment and link checks.

Module 3 imports Module 2's public models. It does not alter Modules 1 or 2, accept a GitHub URL, clone a repository, or call `FileScanner.scan` internally.

## 3. Considered Approaches

### Recommended: a small registry plus shared traversal and language adapters

Use one `ParserSpec` registry entry per grammar and three focused adapters: Python, Java, and ECMAScript (shared by JavaScript, TypeScript, and TSX). Each adapter walks the Tree-sitter tree once with shared location, text-slicing, scope, sorting, and deduplication helpers. Small `.scm` queries may identify stable top-level constructs, but semantic normalization remains explicit Python code.

This avoids a single language-conditional parser, prevents copied TypeScript/JavaScript logic, and is easier to debug than making all extraction behavior implicit in large query files.

### Alternative: query-only extraction

Tree-sitter queries can capture declarations and calls concisely. They do not by themselves handle scope ownership, qualified names, import variants, parameter normalization, partial trees, or language-independent output well. Complex capture post-processing would merely move adapter logic into less transparent query conventions. This approach is rejected for V1.

### Alternative: a generic AST visitor with declarative node maps

A declarative map minimizes files initially, but language constructs with the same apparent role have materially different shapes. Import bindings, Java constructors, Python default parameters, and TypeScript interface signatures quickly force language-specific callbacks into the generic layer. This approach is rejected because it hides rather than removes complexity.

## 4. Runtime Dependencies and Compatibility

The implementation plan must add these exact direct pins after design approval:

```text
tree-sitter==0.25.2
tree-sitter-python==0.25.0
tree-sitter-java==0.23.5
tree-sitter-javascript==0.25.0
tree-sitter-typescript==0.23.2
```

`tree-sitter` provides the Python engine. The four official grammar wheels provide Python, Java, JavaScript/JSX, and both TypeScript and TSX language capsules. These versions are pinned as a tested compatibility set rather than independently ranged. The implementation plan must include an initialization smoke test for every grammar and a dependency-update procedure that upgrades the set together, runs all language fixtures, and confirms Tree-sitter language ABI compatibility.

A maintained language-pack dependency was considered but rejected for V1 because its current default distribution fetches parsers on demand. Runtime downloads conflict with the normal-operation offline guarantee. Vendoring grammar sources or compiling them during application startup is also rejected. Package installation may use the package index in the normal development/deployment process; parsing itself uses only installed wheels and requires no network.

No dependency is added during this design phase.

## 5. Supported Languages and Registry

The public normalized language enum is:

```python
class ParsedLanguage(str, Enum):
    PYTHON = "python"
    JAVA = "java"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    TSX = "tsx"
```

Registry selection is deterministic and validates both Module 2 metadata fields:

```text
language=python,     extension=.py          -> Python
language=java,       extension=.java        -> Java
language=javascript, extension=.js or .jsx -> JavaScript grammar
language=typescript, extension=.ts          -> TypeScript grammar
language=typescript, extension=.tsx         -> TSX grammar
```

Any other `(language, extension)` pair, including a contradictory supported-looking pair, is `SKIPPED_UNSUPPORTED_LANGUAGE`. Module 3 trusts Module 2's classification but does not guess a grammar from filename alone. `.jsx` is part of the JavaScript language outcome; TSX has its own outcome because grammar selection and downstream syntax behavior differ.

The registry lazily initializes a grammar on first use. A `CodeParser` instance caches one `Language` and one `Parser` per registry entry. There is no mutable process-global parser cache. A `CodeParser` instance is not promised to be thread-safe; callers that parse concurrently use separate instances. Failure to load one grammar is isolated to that registry entry and produces `PARSER_UNAVAILABLE` failures only for affected candidate files. Construction fails only if the static registry configuration itself is invalid.

## 6. Public API

The package mirrors Modules 1 and 2 with a class and convenience function:

```python
from backend.code_parser import CodeParser, CodeParseInventory, parse_code_inventory

parser = CodeParser()
result = parser.parse_inventory(file_inventory)

# Equivalent convenience API
result = parse_code_inventory(file_inventory)
```

V1 has no public language-selection option. Its supported set is a versioned module contract. It also has no duplicate size configuration: Module 3 defensively compares the current file size with `ScannedFile.size_bytes` instead of inventing a second policy limit.

The implementation package is expected to be:

```text
backend/code_parser/
    __init__.py
    parser.py          # inventory orchestration
    reader.py          # containment, link, identity, and byte reads
    registry.py        # ParserSpec and lazy per-instance parser cache
    models.py
    exceptions.py
    extractors/
        __init__.py
        base.py        # traversal context and shared helpers
        python.py
        java.py
        ecmascript.py  # JavaScript, TypeScript, TSX modes
    queries/           # only small stable queries proven useful
```

The exact number of query files may shrink during implementation. The public boundary is the package exports, not this internal layout.

## 7. Immutable Data Models

All public values are frozen/slotted dataclasses. Collection fields are tuples. Enums subclass `str, Enum`, matching Module 2.

```python
@dataclass(frozen=True, slots=True)
class SourceLocation:
    start_byte: int
    end_byte: int
    start_line: int
    start_column: int
    end_line: int
    end_column: int


@dataclass(frozen=True, slots=True)
class ParameterInfo:
    name: str
    type_name: str | None
    default_value_text: str | None


@dataclass(frozen=True, slots=True)
class ImportBinding:
    imported_name: str
    alias: str | None


@dataclass(frozen=True, slots=True)
class ImportInfo:
    module: str
    bindings: tuple[ImportBinding, ...]
    is_wildcard: bool
    modifiers: tuple[str, ...]
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    name: str
    kind: SymbolKind
    qualified_name: str
    parent_qualified_name: str | None
    location: SourceLocation
    parameters: tuple[ParameterInfo, ...]
    return_type: str | None
    modifiers: tuple[str, ...]
    base_types: tuple[str, ...]
    implemented_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CallSite:
    caller_qualified_name: str | None
    callee_text: str
    kind: CallKind
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class ParseIssue:
    kind: ParseIssueKind
    message: str
    location: SourceLocation | None


@dataclass(frozen=True, slots=True)
class ParsedFile:
    relative_path: str
    language: ParsedLanguage
    status: ParseStatus
    symbols: tuple[SymbolInfo, ...]
    imports: tuple[ImportInfo, ...]
    calls: tuple[CallSite, ...]
    issues: tuple[ParseIssue, ...]


@dataclass(frozen=True, slots=True)
class SkippedParseFile:
    relative_path: str
    reason: ParseSkipReason


@dataclass(frozen=True, slots=True)
class CodeParseInventory:
    repository_path: str
    total_files_requested: int
    success_files: int
    partial_files: int
    failed_files: int
    skipped_files: int
    files: tuple[ParsedFile, ...]
    skipped: tuple[SkippedParseFile, ...]
```

No source text, Tree-sitter tree, repository-global symbol ID, resolved dependency, or resolved callee is stored.

## 8. Source Location Semantics

`SourceLocation` always refers to the original file bytes:

- `start_byte` is inclusive.
- `end_byte` is exclusive.
- lines are 1-based.
- columns are 0-based UTF-8 byte columns, matching Tree-sitter point semantics.
- the end line/column identify the exclusive end point.

Columns are deliberately byte columns, not Unicode code-point or display columns. This preserves exact Tree-sitter semantics and makes the byte range authoritative for Module 4. Documentation and tests must call this out so a future UI can convert byte columns when needed.

For a UTF-8 BOM, parsing may use a BOM-free view only if a location adapter adds the three-byte prefix back to byte offsets and to line-one byte columns. All returned ranges must slice the original byte buffer correctly. Locations are validated before model creation: `0 <= start_byte <= end_byte <= file_size`, positive lines, and non-negative columns.

## 9. Enums and Stable Outcome Vocabulary

V1 symbol kinds are intentionally limited:

```text
CLASS
INTERFACE
FUNCTION
METHOD
CONSTRUCTOR
ENUM
CONSTANT
```

`CallKind` is `CALL` or `CONSTRUCTOR`. `ParseStatus` is `SUCCESS`, `PARTIAL`, or `FAILED`. `ParseSkipReason` contains `UNSUPPORTED_LANGUAGE` only in V1 because non-code categories are ignored before request accounting.

`ParseIssueKind` is:

```text
SYNTAX_ERROR
MISSING_NODE
READ_ERROR
FILE_CHANGED
PATH_INVALID
LINK_UNSAFE
DECODING_ERROR
PARSER_UNAVAILABLE
EXTRACTION_ERROR
```

These string values, all enum values, frozen behavior, field order, and counter invariants are public-contract tests.

## 10. Symbol Names, Nesting, and Identity Evidence

Module 3 produces syntactic qualified names only. It does not claim repository-wide uniqueness.

- A top-level Python/JavaScript/TypeScript symbol uses its declared name.
- Nested types/functions use dot-separated lexical parents: `outer.inner` and `Outer.Inner.method`.
- Class/interface/enum members use the lexical type: `AuthService.login`.
- Java prefixes top-level types with the declared package when present: `com.example.auth.AuthService`; members extend it.
- Python does not derive an importable module name from the file path.
- JavaScript/TypeScript do not infer package names from directories or manifests.

`parent_qualified_name` is preferred over a plain parent name. Together with `relative_path`, `kind`, `qualified_name`, and the exact byte range, it gives Module 6 sufficient evidence to generate a stable repository-local ID later without Module 3 defining that ID prematurely.

Anonymous functions and callbacks are not symbols. A function expression or arrow function becomes a `FUNCTION` only when it is directly bound to a stable identifier by a module-level or lexical `const`/`let`/`var` declaration or a simple assignment. Object-literal property callbacks are deferred. Nested named functions are included.

Overloaded Java/TypeScript declarations may share a qualified name; location and parameter evidence distinguish them. Module 3 does not invent signature-based global IDs.

## 11. Parameters, Declared Types, Defaults, and Modifiers

Parameters preserve source order. Python `self` and `cls` remain in the tuple because Module 3 reports syntax rather than rewriting language conventions. Java receiver parameters, rest parameters, optional markers, destructuring, and TypeScript parameter properties are represented deterministically:

- `name` is the declared identifier when one exists.
- For destructuring or another non-identifier pattern, `name` is the bounded exact pattern text.
- `type_name` is bounded exact declared type/annotation text or `None`; no type inference occurs.
- `default_value_text` is bounded exact expression text or `None`; it is never evaluated.

All captured text fields are stripped only of surrounding ASCII whitespace, preserve internal spelling, and are limited to 1,000 UTF-8 bytes. Exceeding text is truncated at a valid UTF-8 boundary with a final Unicode ellipsis. The bound applies to parameter patterns, types, defaults, callee text, base types, implemented types, and import names. Names that exceed the bound still produce deterministic truncated metadata plus an `EXTRACTION_ERROR` issue; source locations remain authoritative.

Return types are captured only when explicitly declared. Java constructor return type is `None`; untyped JavaScript is `None`. Interface/abstract method signatures become `METHOD` symbols with their parameters and declared return type even though they have no body.

Useful syntactic modifiers are normalized to lowercase, deduplicated, and sorted by a fixed module-defined precedence rather than alphabetically. The supported vocabulary includes visibility, `static`, `async`, `abstract`, `final`, `readonly`, `export`, `default`, `generator`, and language-specific modifiers encountered on supported declarations. Unknown modifiers are ignored rather than expanding the public contract accidentally.

## 12. Inheritance and Implementation Syntax

Relevant type symbols carry bounded exact syntactic names:

- Python class bases are `base_types`; `implemented_types` is empty because Python syntax does not distinguish implementation.
- Java `extends` populates `base_types`; `implements` populates `implemented_types`. Java interface `extends` also uses `base_types`.
- TypeScript/TSX class and interface `extends` populate `base_types`; class `implements` populates `implemented_types`.
- JavaScript class `extends` populates `base_types`.

Generic/type-argument spelling is retained as source text. No base or interface is resolved to a file or symbol.

## 13. Constants Policy

V1 includes only conservative constant declarations:

- Python module/class assignments whose simple identifier is conventionally uppercase.
- Java class/interface/enum fields declared `final` with a simple declarator; `static` is recorded when present.
- JavaScript/TypeScript module-level simple identifiers declared with `const`.
- TypeScript class fields declared `static readonly` or `readonly static`.

Local variables, destructuring declarations, ordinary fields, enum members, and assignments to attributes/properties are not symbols. A module-level `const helper = () => ...` is a `FUNCTION`, not both a function and a constant.

## 14. Comments, Docstrings, and Signatures

Comments, docstrings, and signature text are deferred from V1. Capturing them would increase output size and introduce language-specific attachment rules without being required for exact chunk boundaries. Module 4 can extract source bytes from `SymbolInfo.location`; a later approved contract can add bounded documentation metadata if retrieval experiments demonstrate value.

## 15. Import Representation and Policies

`ImportInfo` records syntax, not resolution. One source declaration may normalize into one or more records when its modules differ.

- Python `import os, sys as system` becomes one record per module. A binding records the imported module name and alias. `from services.auth import AuthService as Service` uses module `services.auth` and one binding. Relative dots remain in `module`; wildcard sets `is_wildcard=True`.
- Java records the imported qualified name as `module`; wildcard imports set `is_wildcard=True`; static imports include `static` in modifiers. Bindings are empty because Java syntax imports the recorded qualified target directly.
- ECMAScript default, named, and namespace bindings use `ImportBinding`. Side-effect imports have no bindings. The module specifier has quotes removed but no path normalization.
- Direct CommonJS forms `require("fs")`, `const fs = require("fs")`, and simple object destructuring from `require("module")` are imports. The same `require(...)` syntax remains a call site as well, because it is both dependency evidence and an actual call. Non-literal/dynamic `require` is only a call.
- Dynamic `import(...)` is a call, not a static import declaration.

Alias-per-binding is why `ImportBinding` is a separate value rather than one alias on `ImportInfo`.

## 16. Call-Site Extraction and Caller Assignment

Calls include ordinary call expressions and explicit object construction (`new` in Java/ECMAScript and class-looking calls in Python remain ordinary `CALL`, because Python syntax alone cannot prove construction). Java `new` and ECMAScript `new` use `CONSTRUCTOR`.

`callee_text` is the bounded exact callee expression, excluding arguments and excluding the Java/ECMAScript `new` keyword. Examples include `service.authenticate`, `repository.findByEmail`, `AuthService`, and `onLogin`. Computed/member expressions are retained as syntax; no target is resolved.

Traversal maintains a lexical symbol stack. A call is assigned to the innermost containing extracted callable symbol. Calls in field initializers, decorators, base expressions, top-level statements, or anonymous callbacks outside a named callable have `caller_qualified_name=None`. A call inside an anonymous callback nested within a named method remains attributed to that nearest named method. Calls inside a nested named function are attributed to the nested function.

Declarations that syntactically resemble calls but are not call-expression nodes are not emitted. JSX elements are not calls. Calls inside JSX expression containers are emitted normally.

## 17. Source Reading and Defensive Filesystem Checks

Only candidate entries supplied in `FileInventory.files` are considered. There is no directory enumeration. For each candidate, `reader.py`:

1. Validates `relative_path` as a non-empty relative `PurePosixPath`; rejects absolute paths, drives, empty components, `.` and `..` components, NULs, and backslashes.
2. Strictly resolves and validates `repository_path` as an accessible directory. Root failure is fatal.
3. Converts POSIX components with `Path.joinpath`, checks lexical containment, and performs non-following metadata checks on every traversed component where the platform permits.
4. Rejects symbolic links and Windows reparse points before reading.
5. Opens the final file in binary mode with non-following flags when available, then compares handle metadata with pre-open metadata to detect replacement.
6. Requires a regular file and requires its current `st_size` to equal `ScannedFile.size_bytes`. A mismatch is `FILE_CHANGED`; Module 3 does not parse an unverified revision.
7. Reads exactly the expected number of bytes plus a one-byte overflow probe, rejecting short/long reads as `FILE_CHANGED`.
8. Rechecks handle/path identity after the read where stable device/inode or Windows file identity is available.

On POSIX, the implementation should open path components relative to a root directory descriptor using `O_NOFOLLOW`/`O_DIRECTORY` where supported, then read from the verified final descriptor. On Windows, it should centralize reparse detection and handle identity in the reader boundary, use non-following Windows handle flags where Python's standard flags are insufficient, and unit-test the platform adapter independently. If a platform cannot provide the required non-following primitive, the affected file is `LINK_UNSAFE`; security is not weakened silently.

No portable filesystem API can eliminate every malicious mutation between all checks. The design narrows the race by reading from the same verified handle and treating identity/size instability as failure. It never intentionally resolves or follows a repository-controlled link.

## 18. Encoding and BOM Policy

Files are read as bytes because Tree-sitter offsets are byte offsets. Module 3 accepts only strict UTF-8 and UTF-8 with one leading BOM. It validates the complete byte sequence before parsing. It does not use replacement decoding and does not accept UTF-16/UTF-32 even if Module 2's prefix heuristic classified such a file as text. An invalid file receives `FAILED` plus `DECODING_ERROR`.

The original bytes remain the location authority during that file's processing. Text slices decode with strict UTF-8 after range validation. No source buffer is retained in `ParsedFile`.

## 19. Extraction Architecture and Per-Language Behavior

`parser.py` processes sorted candidates one at a time. `registry.py` supplies a parser and adapter mode. The parser produces one tree. A language adapter performs a deterministic depth-first, source-order traversal while a shared context maintains lexical scopes and converts nodes to normalized models.

Queries are used only where they simplify stable grammar-node selection. Query captures are not returned directly: adapters validate node roles and normalize them. This hybrid approach tolerates grammar differences without copying traversal, location, bounding, sorting, issue, or deduplication code.

### Python

Extract classes, sync/async functions, decorated declarations, constructors named `__init__`, methods by lexical class containment, nested functions, declared parameters/types/defaults/returns, class bases, supported constants, imports, and calls. Decorators contribute the `async` modifier only through the actual function syntax; decorator calls are top-level or enclosing-scope calls, not calls owned by the decorated body.

### Java

Extract package context, classes, interfaces, enums, constructors, concrete and abstract/interface methods, parameters, declared returns, modifiers, extends/implements clauses, supported constants, imports, method invocations, and object creation. Initializer blocks are not symbols; their calls have no callable owner unless nested in an extracted callable.

### JavaScript, TypeScript, and TSX

Share one adapter with grammar-mode feature flags. Extract classes, constructors, methods, function declarations, stable-name function/arrow assignments, TypeScript interfaces and interface methods, parameters, declared TypeScript return types, modifiers, extends/implements clauses, supported constants, ES imports, supported CommonJS imports, calls, and `new` expressions. Type aliases are not V1 symbols. JSX syntax produces no symbol by itself; expression-container calls are retained.

## 20. Parse Errors, Status, and Recovery

Tree-sitter normally returns a tree even for malformed source. The adapter traverses useful subtrees but never extracts a declaration or call from inside an `ERROR` or missing-node span unless the extracted node itself is complete and its required name/location fields are valid.

- `SUCCESS`: read, parse, and extraction complete with no syntax, missing-node, or extraction issues.
- `PARTIAL`: parsing/extraction completes and at least one valid structural item is retained, but the tree contains an error/missing node or a recoverable extraction issue.
- `FAILED`: no trustworthy structural result is available because reading, validation, decoding, parser loading, parsing, or extraction failed; or syntax/extraction issues leave no valid item. Collections are empty except `issues`.

Every Tree-sitter `ERROR` node produces a `SYNTAX_ERROR`; every missing node produces `MISSING_NODE`. Issue messages are fixed sanitized templates, never raw source or exception text. Duplicate/overlapping error spans are deduplicated. Malformed files never abort the inventory.

An unexpected adapter exception is caught at the per-file boundary, logged without content, and becomes a `FAILED` file with `EXTRACTION_ERROR`. Programming/configuration errors detected before file processing are fatal rather than disguised.

## 21. Fatal Exceptions Versus Per-File Outcomes

The exception hierarchy is small:

```text
CodeParserError
├── InvalidParseInventory
├── ParserConfigurationError
└── RepositoryParseError
```

Fatal errors are limited to an object that is not a valid `FileInventory`, inconsistent inventory-level counters/collections, an invalid/inaccessible repository root, invalid static registry configuration, or inability to establish a safe root reading boundary. These indicate that inventory processing cannot safely begin.

Malformed individual `ScannedFile` paths, changed/disappeared files, unsafe links, decode failures, affected-language parser failures, syntax errors, and extractor failures are represented in per-file output. A candidate that disappears is `FAILED/READ_ERROR`; an escape path is `FAILED/PATH_INVALID`; a swapped link/reparse point is `FAILED/LINK_UNSAFE`.

## 22. Candidate Selection, Skips, and Counters

Module 3 first filters `FileInventory.files` to `SOURCE` and `TEST`. Other categories are outside the parser request and do not appear in `files`, `skipped`, or counters. This avoids calling known non-code inventory entries "skipped parsing" and preserves the strict Module 3 boundary.

`total_files_requested` means all SOURCE/TEST entries presented to Module 3, including unsupported languages. Every requested file has exactly one mutually exclusive outcome:

```text
total_files_requested ==
    success_files + partial_files + failed_files + skipped_files
```

`files` contains all `SUCCESS`, `PARTIAL`, and `FAILED` `ParsedFile` values, so:

```text
len(files) == success_files + partial_files + failed_files
len(skipped) == skipped_files
```

An unsupported SOURCE/TEST language is `SkippedParseFile(..., UNSUPPORTED_LANGUAGE)`, not a failure. An empty supported file with a clean tree is `SUCCESS` with empty structural collections.

## 23. Deterministic Ordering and Duplicate Suppression

Candidate files and both output collections sort by `(relative_path.casefold(), relative_path)`. Within a parsed file:

- symbols: `(start_byte, end_byte, kind.value, qualified_name)`;
- imports: `(start_byte, end_byte, module, bindings)`;
- calls: `(start_byte, end_byte, kind.value, callee_text)`;
- issues: location-less last, then `(start_byte, end_byte, kind.value, message)`.

Deduplication occurs before sorting and preserves the first normalized value in source traversal. Identity keys are:

- symbol: `(kind, name, start_byte, end_byte)`;
- import: `(module, bindings, is_wildcard, start_byte, end_byte)`;
- call: `(kind, callee_text, start_byte, end_byte, caller_qualified_name)`;
- issue: `(kind, start_byte-or--1, end_byte-or--1)`.

No output depends on filesystem enumeration, hash order, query-capture order, locale, or platform path separators.

## 24. Performance and Memory

The parser processes one file at a time: verify/read bytes, validate UTF-8, parse once, traverse once, normalize, discard tree and bytes, then advance. Parser/language objects are reused per `CodeParser` instance. The result retains only immutable metadata.

No complete-file source text is stored and no trees from earlier files remain referenced. Extraction uses iterative traversal with explicit enter/exit scope events so adversarial syntax nesting does not consume Python call stack. Text slicing is bounded. Sorting cost is local to one file plus final file ordering.

V1 is single-threaded. Parallel parsing can be designed later using one parser instance per worker after measuring need and confirming library thread-safety.

## 25. Module 4 Contract

Module 4 can create symbol chunks without reparsing because every symbol provides:

- the source file's POSIX-relative path through `ParsedFile`;
- exact original byte range `[start_byte, end_byte)`;
- language, kind, name, qualified name, and lexical parent;
- parameters, declared return type, modifiers, and type relationships.

Module 4 must repeat safe file-open and change-detection checks because Module 3 output is also a snapshot. Module 3 does not retain source bytes or define chunk IDs.

## 26. Module 6 Contract

Module 6 receives syntax-level evidence: symbols and lexical parents, static import declarations and aliases, base/implemented type text, and calls with lexical caller and exact locations. It may combine these with repository paths and later evidence to create graph nodes/edges and stable IDs.

Module 3 never resolves an import to a file, a type to a class, or a callee to a symbol. Qualified names are syntactic hints, not semantic guarantees.

## 27. Logging

Use `logging.getLogger(__name__)`.

- `INFO`: inventory start and aggregate completion.
- `DEBUG`: parser selection, per-file status, and extraction counts by relative path.
- `WARNING`: recoverable read, safety, parser, or extraction failure by sanitized relative path and issue kind.
- `ERROR`: fatal inventory/root/registry failure only.

Logs never contain source bytes, snippets, default expressions, annotations, callee text, environment values, raw parser exceptions, or arbitrary exception messages originating from repository-controlled paths/content.

## 28. Security Model

Repository code and metadata are untrusted data. Module 3 may only read verified candidate bytes, create trees with trusted installed grammars, inspect nodes, and retain bounded structural text.

It must never import repository modules; execute Python, Node, Java, shell, tests, or builds; evaluate decorators, annotations, defaults, or computed names; deserialize repository objects; source environment/configuration; load repository plugins or grammar binaries; invoke a package manager/compiler; call Git/GitHub; access a network; or call an LLM.

Grammar modules are ordinary locked application dependencies. The analyzed repository cannot choose a grammar module, query file, executable, or native library path. Registry keys are closed constants, not dynamically imported strings.

## 29. Testing Strategy

Implementation uses strict pytest RED/GREEN cycles in `tests/test_code_parser.py`. Tests are offline, use temporary trees, and manually construct real Module 2 `FileInventory`/`ScannedFile` values except for focused integration cases that run `FileScanner` locally.

Required contract and orchestration coverage:

- stable enum values, frozen/slotted models, explicit public exports, and convenience API;
- exact location bases, UTF-8 byte columns, end exclusivity, Unicode, and BOM offset adjustment;
- mutually exclusive counters and non-code category exclusion;
- all five registry outcomes, `.ts` versus `.tsx`, `.js`/`.jsx`, contradictory metadata, and unsupported source/test languages;
- parser caching per instance and affected-language initialization isolation;
- no rescanning, one-file-at-a-time behavior, no retained source/tree references, and deterministic ordering;
- duplicate capture suppression;
- empty supported files.

Language fixtures cover all cases in the Module 3 brief:

- Python imports, class/bases, constructor, methods, sync/async/decorated/nested functions, receiver parameters, types/defaults/returns, constants, calls, qualification, and malformed partial source;
- Java package/imports, class/interface/enum, constructors, abstract/interface and concrete methods, extends/multiple implements, constants, calls/new, modifiers, types, and malformed partial source;
- JavaScript ES/CommonJS imports, class/constructor/method/function, named arrow/function expressions, anonymous callback exclusion, constants, calls/new, JSX parsing, and malformed partial source;
- TypeScript interface/method signatures, class/implements/extends, typed/default/destructured parameters, returns, named arrows, imports, calls, and malformed partial source;
- TSX function component, JSX tolerance, deterministic destructured parameter text, declared return, and calls within JSX expressions without JSX symbol noise.

Safety/error coverage includes path escapes and malformed POSIX paths, disappearing files, size/identity changes, short/long reads, symlink swaps, Windows reparse detection through a platform-independent adapter test, unavailable symlink creation skips, invalid UTF-8/UTF-16, inaccessible root versus inaccessible file, sanitized issues/logs, parser-unavailable isolation, extractor failure isolation, Tree-sitter `ERROR`/missing nodes, partial-versus-failed rules, and no raw content retention.

Dependency smoke tests instantiate every pinned grammar, parse a minimal fixture, and assert the expected root node. Normal tests must not clone, use the network, or run repository-controlled code.

## 30. README Changes During Implementation

The implementation phase will append `Module 3 — Code Parser` without rewriting Modules 1 or 2. It will document purpose, supported languages, exact Tree-sitter dependency strategy, `FileInventory` input, `CodeParseInventory` output, public usage, structural fields, location semantics, unsupported-language skips, partial parsing, safe/offline behavior, and the explicit absence of execution, cross-file resolution, graph construction, chunking, and RAG.

## 31. Explicit Non-Goals

V1 does not implement directory scanning, repository loading/cloning, private-repository support, chunking, full-source retention, documentation/comment extraction, type aliases as symbols, every local variable, anonymous callbacks as symbols, semantic type inference, import resolution, inheritance/call/dependency graphs, repository-global IDs, Git history, runtime evidence, builds/tests, embeddings, vector databases, retrieval, LLM calls, APIs, authentication, or a frontend.

SQL and Prisma remain `DATABASE` and are not parsed. C, C++, C#, Go, Rust, Kotlin, Ruby, PHP, Swift, Scala, shell, and PowerShell source entries are cleanly skipped as unsupported in V1.

## 32. Resolved Design Questions and Trade-offs

- **Tree-sitter packages:** official engine plus four official per-language grammar wheels, pinned as the exact set in Section 4.
- **Compatibility:** upgrade pins together and gate them with grammar initialization and full fixture tests.
- **TSX detection:** Module 2's `typescript` plus `.tsx`; no Module 2 change.
- **Locations:** original byte offsets included; bytes and end points are half-open, lines 1-based, columns 0-based UTF-8 bytes.
- **Source storage:** excluded from results.
- **Defaults:** bounded exact syntax is retained and never evaluated.
- **Documentation:** comments/docstrings deferred.
- **Python receivers:** `self`/`cls` retained.
- **Arrow functions:** only directly stable-name-bound expressions become symbols.
- **Anonymous functions:** not symbols.
- **Nested call ownership:** nearest extracted lexical callable; anonymous wrappers do not erase that owner.
- **Constructors:** explicit `CONSTRUCTOR` symbols; Java/ECMAScript `new` is a constructor call.
- **Interface methods:** `METHOD` symbols even without bodies.
- **Syntax errors:** useful metadata plus issues is `PARTIAL`; no trustworthy item is `FAILED`.
- **Unsupported source:** skipped, not failed.
- **Changed files:** containment, non-following link/reparse, regular-file, size, read-length, and identity checks are repeated.
- **Parser initialization:** lazy and isolated per affected language; invalid static registry configuration is fatal.
- **Constants:** conservative module/class declarations only.
- **Architecture:** shared deterministic traversal infrastructure with three language adapters; queries are narrow helpers rather than the whole extraction design.

## 33. Unresolved Issues

There are no Critical unresolved issues.

There is one Important implementation validation item: the pinned dependency set must pass a clean-environment smoke test on every supported CI platform before implementation is considered complete. The design fixes exact versions and behavior, but wheel availability and native ABI loading are deployment properties that must be demonstrated rather than assumed.

There is one documented residual filesystem limitation, not an approval blocker: no cross-platform user-space design can make a mutable repository snapshot perfectly race-free. The implementation must use the strongest non-following, same-handle checks available and fail closed where those primitives are unavailable.
