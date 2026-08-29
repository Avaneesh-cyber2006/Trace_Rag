# Module 4 — Code Chunker Design

## 1. Scope

Module 4 converts a Module 3 `CodeParseInventory` plus the corresponding Module 2 `FileInventory` into an immutable, deterministic `CodeChunkInventory`. It safely re-reads exact source bytes, proves that those bytes match the revision Module 3 parsed, selects non-overlapping structural ranges, splits oversized ranges, and retains retrieval-ready UTF-8 text with exact provenance.

Module 4 understands syntax-derived boundaries and lexical context. It does not parse again, execute repository content, resolve names, build graphs, embed text, index chunks, search, retrieve, call an LLM, or implement any Module 5+ behavior.

This document is design only. It adds no production package, tests, dependencies, or README changes.

## 2. Existing Module 2/3 Integration

The design is based on the contracts at `main` commit `8d5b7ea`.

Module 2 supplies a frozen `FileInventory` with a resolved `repository_path` and sorted `ScannedFile` values. Each `ScannedFile` has a POSIX `relative_path`, filename, normalized extension, language hint, `FileCategory`, and scan-time `size_bytes`. Module 2 does not retain content or a digest and describes its inventory as a snapshot.

Module 3 accepts only `FileInventory`, considers `SOURCE` and `TEST` entries, and returns a frozen `CodeParseInventory`. A `ParsedFile` contains `relative_path`, `ParsedLanguage`, `ParseStatus`, sorted `SymbolInfo`, `ImportInfo`, `CallSite`, and `ParseIssue` tuples. A `SymbolInfo` provides `SymbolKind`, syntactic qualified and parent names, parameters, declared types, modifiers, type relationships, and an exact `SourceLocation`.

`SourceLocation` addresses original file bytes as a half-open `[start_byte, end_byte)` range. Lines are one-based and columns are zero-based UTF-8 byte columns. Module 3 parses a BOM-free view when necessary but translates ranges back to the original byte buffer, including the three-byte BOM offset on line one.

Module 3's safe reader validates paths, containment, non-following traversal, link/reparse status, regular-file type, scan-time size, exact read length, and stable same-handle/path identity. It strictly accepts UTF-8 or one leading UTF-8 BOM and retains `original_bytes`, `parse_bytes`, and `bom_prefix_bytes` only while processing one file. It does not currently retain a content fingerprint.

## 3. Input Contract

The public API receives both inventories, in pipeline order:

```python
chunks = CodeChunker().chunk_inventory(
    file_inventory,
    parse_inventory,
)
```

The convenience API is:

```python
chunks = chunk_code_inventory(file_inventory, parse_inventory)
```

`CodeParseInventory` is the primary structural input. `FileInventory` is additionally required because it owns the scan-time `ScannedFile` evidence used by the hardened reader, including `size_bytes`, category, extension, and language hint. Module 4 does not rescan or reconstruct missing metadata from the filesystem.

Inventory validation is fatal and occurs before source reads. Module 4 requires:

- both values to be instances of their public frozen models with internally consistent counters and tuple collections;
- exact equality of `repository_path` after Module 2/3 normalization;
- unique `relative_path` values in both relevant file collections;
- every `ParsedFile` to join to exactly one `ScannedFile` by the case-preserving POSIX relative path;
- the matched scanner entry to be `SOURCE` or `TEST` and compatible with the parsed language/extension rules already used by Module 3;
- Module 3 file and skip counters to form their documented partition.

A local `dict[str, ScannedFile]` is built once for O(1) joins. It is not exposed in output. Combining inventories from different roots or inconsistent snapshots raises `InvalidChunkInventory`; it is never treated as a recoverable file issue.

## 4. Snapshot Consistency

Filesystem identity and size checks prove that Module 4 read one stable, safe file during its own operation. They do not prove that its bytes equal the earlier bytes parsed by Module 3. In particular, the following same-size replacement defeats size-only validation:

```python
def a():             def b():
    return 1             return 2
```

Module 4 therefore requires cryptographic content evidence from the parse snapshot. For each processable `ParsedFile`, it compares the SHA-256 of the safely re-read original bytes against the digest Module 3 computed from the exact original bytes it parsed. A mismatch produces a per-file `SOURCE_CHANGED` issue and no chunks for that file.

Module 4 does not accept “best effort” stale offsets, mtime evidence, inode identity across stages, size alone, or validation of only the referenced slices. Only a complete-file digest binds all locations and uncovered ranges to the parse snapshot.

## 5. Source Fingerprint Decision

Module 3 needs a compatibility extension:

```python
@dataclass(frozen=True, slots=True)
class ParsedFile:
    ...
    source_sha256: str | None = None
```

The appended field defaults to `None` so existing explicit model construction remains source-compatible. It is lowercase 64-character hexadecimal SHA-256 of `SourceBuffer.original_bytes`, including an original UTF-8 BOM and original newline bytes. The parser computes it immediately after Module 3's verified complete read and before parsing. It is present whenever the safe read succeeded, including a later parser-unavailable or extraction-failure outcome, and is `None` when no verified source buffer was obtained.

Module 4 processes only `SUCCESS` and `PARTIAL` files, for which the digest must be present and valid. A missing or malformed digest is an inventory contract violation, not a guessed snapshot. Module 4 implementation is blocked until this separately reviewed Module 3 compatibility patch is approved, implemented, tested, documented, merged, and pushed. The patch is not part of Module 4 implementation and is not made by this design task.

The digest is content evidence, not a secret scanner or signature. It does not make repository content trusted.

## 6. Safe Source-Reading Strategy

V1 deliberately reuses Module 3's reviewed `SafeSourceReader` and `SourceBuffer` implementation rather than duplicating security-sensitive filesystem code or weakening checks. The existing reader becomes a documented, supported cross-module internal primitive. Module 4 imports the reader from `backend.code_parser.reader`, calls `read(ScannedFile)`, maps `SourceReadError` into its own issue vocabulary, hashes `original_bytes`, and then slices only the verified buffer.

This creates a narrow dependency on Module 3's reader boundary, but it has lower V1 risk than moving hundreds of lines of POSIX/Windows handle logic or maintaining two copies. No reader logic is refactored merely for package aesthetics. A later shared `backend/source_reader/` extraction is allowed only as its own security-reviewed refactor with parity tests on POSIX and Windows. It is not a Module 4 prerequisite.

The supported-internal status of this reader, its strict UTF-8/BOM behavior, and error mapping must be documented and contract-tested in the prerequisite patch. Module 4 never opens source with `Path.read_text`, follows links, or falls back to a less safe reader.

## 7. Public API

The public package will export:

```python
from backend.code_chunker import (
    ChunkFileStatus,
    ChunkIssue,
    ChunkIssueKind,
    ChunkKind,
    ChunkedFile,
    ChunkerConfig,
    CodeChunk,
    CodeChunkInventory,
    CodeChunker,
    CodeChunkerError,
    InvalidChunkInventory,
    ChunkerConfigurationError,
    RepositoryChunkError,
    chunk_code_inventory,
)
```

`CodeChunker(config: ChunkerConfig | None = None)` validates configuration eagerly. `chunk_inventory(file_inventory, parse_inventory)` is single-threaded and processes already ordered parsed files one at a time. The convenience function creates an isolated default chunker.

## 8. Data Models

All public models are `@dataclass(frozen=True, slots=True)` and all public collections are tuples.

```python
class ChunkKind(str, Enum):
    SYMBOL = "symbol"
    CONTEXT = "context"
    FRAGMENT = "fragment"


class ChunkFileStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ChunkerConfig:
    target_chunk_bytes: int = 4_096
    max_chunk_bytes: int = 8_192


@dataclass(frozen=True, slots=True)
class CodeChunk:
    chunk_id: str
    kind: ChunkKind
    location: SourceLocation
    content: str
    content_hash: str
    symbol_kind: SymbolKind | None
    symbol_name: str | None
    qualified_name: str | None
    parent_qualified_name: str | None
    parameters: tuple[ParameterInfo, ...]
    return_type: str | None
    modifiers: tuple[str, ...]
    base_types: tuple[str, ...]
    implemented_types: tuple[str, ...]
    fragment_index: int
    fragment_count: int


@dataclass(frozen=True, slots=True)
class ChunkIssue:
    kind: ChunkIssueKind
    message: str
    location: SourceLocation | None


@dataclass(frozen=True, slots=True)
class ChunkedFile:
    relative_path: str
    language: ParsedLanguage
    parse_status: ParseStatus
    status: ChunkFileStatus
    source_sha256: str | None
    imports: tuple[ImportInfo, ...]
    chunks: tuple[CodeChunk, ...]
    issues: tuple[ChunkIssue, ...]


@dataclass(frozen=True, slots=True)
class CodeChunkInventory:
    repository_path: str
    total_files_requested: int
    success_files: int
    partial_files: int
    failed_files: int
    total_chunks: int
    files: tuple[ChunkedFile, ...]
```

`ChunkKind` describes how the chunk was formed; it does not duplicate `SymbolKind`. `SYMBOL` is one complete selected symbol range, `CONTEXT` is a selected contiguous uncovered range, and `FRAGMENT` is one bounded piece of a larger selected range. Symbol metadata fields remain populated for a context or fragment owned by a symbol. File-level context uses `None` for optional identity/type fields and empty tuples for collection fields.

Language, path, imports, parse status, and source digest live once on `ChunkedFile` rather than being copied into every chunk. The inventory does not store a second flattened chunk tuple. Consumers iterate `inventory.files` and each file's `chunks`; a later indexing layer may flatten transiently.

`content` is strict UTF-8 `str` decoded from the exact verified byte slice. Exact byte provenance remains in `location`, and `content.encode("utf-8")` must reproduce that slice byte-for-byte. A BOM is valid only when the selected range includes it; no newline or Unicode normalization occurs.

## 9. Chunk Identity

`chunk_id` is a lowercase 64-character SHA-256 over canonical identity material, not content. Canonical material is UTF-8 encoding of a JSON array serialized with `ensure_ascii=False` and `separators=(",", ":")`:

```text
[
  "tracerag-code-chunk-v1",
  relative_path,
  kind.value,
  symbol_kind.value-or-empty,
  qualified_name-or-empty,
  parent_qualified_name-or-empty,
  start_byte,
  end_byte,
  fragment_index
]
```

JSON array types and escaping make boundaries unambiguous. No random UUID, Python `hash()`, timestamps, inode, global output index, file ordering, import metadata, source digest, or content is included.

The ID represents a structural source location. If bytes change but path, selected role, symbol identity, byte range, and fragment index remain unchanged, `chunk_id` remains stable while `content_hash` changes. Moving or resizing a symbol changes its ID because its authoritative citation range changed. Inserting an unrelated file or reordering input does not change IDs. Overloads at different byte ranges remain distinct.

## 10. Content Hashes

`content_hash` is lowercase SHA-256 of the exact UTF-8 bytes in `[start_byte, end_byte)`. It is separate from `chunk_id` and supports cache invalidation, incremental indexing, debugging, and duplicate evidence detection.

Equal source slices in different files have equal content hashes but different chunk IDs. Module 4 never drops a chunk merely because another chunk has the same content hash; provenance and symbol identity still matter.

## 11. Chunk-Selection Strategy

V1 uses a non-overlapping structural partition, recommended over “every symbol” and “only leaves.”

Three approaches were considered. Emitting every symbol gives strong type-level retrieval but duplicates every nested method/function body and makes memory/index growth proportional to nesting. Emitting only leaf symbols avoids overlap but drops outer-function executable code, class fields, declarations, imports, and module initialization. Synthetic structural shells retain some context but concatenate non-contiguous bytes and weaken citations. The selected partition approach keeps leaf retrieval precision, preserves parent/file residual source as exact context, and emits each retained byte at most once. It is slightly more involved than leaf-only selection, but the interval-stack algorithm keeps it deterministic and near-linear.

1. Validate every symbol and issue location against the verified original buffer.
2. Sort symbol intervals by `(start_byte, -end_byte, kind.value, qualified_name)` and build containment relationships with an interval stack plus Module 3 parent evidence.
3. Select callable symbols (`FUNCTION`, `METHOD`, `CONSTRUCTOR`) and conservative `CONSTANT` symbols as primary retrieval units.
4. Select a `CLASS`, `INTERFACE`, or `ENUM` as a complete `SYMBOL` only when it contains no selected descendant symbol.
5. When a selected/meaningful parent symbol contains selected descendants, do not emit its overlapping whole range. Emit its non-whitespace residual ranges as owned `CONTEXT` candidates. These are the exact gaps inside the parent after the union of direct selected descendant ranges is removed.
6. For a `SUCCESS` file, emit non-whitespace file-level `CONTEXT` candidates for source outside the union of all top-level structural candidates. This retains imports, package/module declarations, comments, initialization code, and other executable statements.
7. Bound every candidate through the fragmentation algorithm, then sort the final chunks by the deterministic key in Section 22.

This policy retains essentially all non-whitespace source from successful files once, keeps high-value callable/constant boundaries, avoids embedding entire classes again around their methods, and preserves parent-only code without manufacturing a structural shell.

## 12. Symbol Overlap Policy

No two chunks in one file may overlap in byte range. Natural containment in Module 3 is resolved before fragmentation.

For a class with methods, each method is a symbol chunk and the class's remaining contiguous spans become class-owned context chunks. The complete class range is not emitted. A class header, fields, decorators/annotations, and braces may therefore appear in one or more exact context spans, but never as synthetic concatenated text.

For a nested function, the inner callable is a symbol chunk. The outer callable is represented by its exact residual spans around the nested declaration, owned by the outer function. If the outer callable has no nested selected symbol, it is one normal symbol candidate. This avoids both dropping the outer executable code and duplicating the inner body.

Malformed overlapping intervals that are neither equal nor proper containment are not guessed. The affected file receives `LOCATION_INVALID` and no chunks, because a crossing interval indicates inconsistent parse metadata. Exact duplicate symbol intervals are deterministically resolved by the normalized symbol order; incompatible identities on the same interval are an inventory violation for that file rather than duplicated chunks.

## 13. Type, Interface, and Enum Behavior

A type with no selected descendant is a complete `SYMBOL` chunk. This covers marker/field-only classes, interfaces with no emitted methods, and simple enums.

A type with callable or constant descendants is not emitted whole. Its descendants become chunks and its non-whitespace residual spans become type-owned `CONTEXT`. Thus Java and TypeScript interface method signatures remain individual method chunks, while the interface declaration and extends clause remain exact context. An enum with methods follows the same partition; a simple enum with no selected descendants remains one enum symbol chunk.

Nested types participate in the same containment tree. A nested type is a selected descendant of its lexical parent. No “small class” exception exists because a byte threshold would make overlap policy content-dependent and reintroduce duplication.

## 14. Constants Policy

Every Module 3 `CONSTANT` is selected as its own symbol candidate unless it lies inside a selected descendant with an identical or narrower authoritative range. Module 4 does not infer additional constants, fields, enum members, or local variables.

Constants remain separate even when small because their qualified identity is valuable for retrieval and future graph nodes. Adjacent tiny constants are not merged in V1; preserving symbol identity is preferred over speculative minimum-size packing. Their containing type/module residual context excludes their bytes, so content is not duplicated.

## 15. File-Level and Fallback Chunks

For `SUCCESS` files, Module 4 computes the complement of all top-level selected structural ranges over `[0, file_size)`. Each non-whitespace contiguous range becomes file-level `CONTEXT`, then is fragmented if necessary. This retains:

- imports and package/module declarations;
- module docstrings and comments;
- top-level initialization and registration calls;
- executable statements not represented as symbols;
- declarations Module 3 intentionally does not model;
- BOM bytes when they belong to a retained file prefix.

Only spans containing ASCII or Unicode non-whitespace are emitted. Leading/trailing whitespace is not silently trimmed from retained candidates because doing so would complicate evidence and change formatting; a span that is entirely whitespace is dropped. Gaps caused only by adjacent symbol boundaries remain absent.

This is fallback coverage, not reclassification. Module 4 does not inspect syntax to label imports or statements, and it does not add generated-code heuristics.

## 16. Partial-File Behavior

`FAILED` parsed files are represented as failed `ChunkedFile` outcomes with no source read and no chunks. Their sanitized parse failure remains owned by Module 3; Module 4 reports a fixed `PARSE_UNAVAILABLE` chunk issue.

For a `PARTIAL` file, Module 4 verifies the complete-file digest and may chunk only ranges backed by trustworthy Module 3 symbols. Arbitrary file-level fallback is disabled because uncovered bytes may include malformed syntax. Whole trusted symbols without selected descendants are allowed. When one trustworthy parent symbol contains trustworthy descendants, exact residual context inside that parent is allowed only if the parent range does not intersect any Module 3 syntax/missing-node issue; otherwise only its non-overlapping selected descendants are retained.

Imports are preserved once as file metadata, but no import source chunk is inferred on a partial file unless its exact bytes fall within an independently selected trustworthy range. Calls do not create chunks. A partial file yields `ChunkFileStatus.PARTIAL` even if all trusted symbols chunk successfully. Invalid or stale metadata can reduce it to `FAILED` for Module 4 without aborting the inventory.

## 17. Exact Contiguous-Source Invariant

Every `CodeChunk` is exactly one contiguous `[start_byte, end_byte)` slice from one digest-verified source file. The invariant is mandatory in V1:

```python
chunk.content.encode("utf-8") == original_bytes[start_byte:end_byte]
```

Module 4 never prepends file names, import summaries, signatures, parent names, comments, or fragment labels to primary content. It never concatenates non-contiguous class shells. All context is structured metadata. Module 5 may render a separate embedding input from content plus metadata without changing the evidentiary chunk.

## 18. Chunk-Size Configuration

V1 exposes exactly two provider-independent byte limits:

```text
target_chunk_bytes = 4,096
max_chunk_bytes = 8,192
```

Both must be integers but not `bool`, both must be greater than zero, and `target_chunk_bytes <= max_chunk_bytes`. Invalid values raise `ChunkerConfigurationError` before inventory processing.

The 4 KiB target gives the splitter room to choose a nearby line boundary; the 8 KiB hard maximum bounds normal output while accommodating ordinary complete methods. These values are intentionally source-byte limits, not claims about a model's token count. They sit well below Module 2's 1,000,000-byte file maximum and are configurable for later benchmarking without introducing provider coupling. Module 5 must still enforce its chosen model's tokenizer-aware input limit.

V1 has no minimum-size option, overlap window, tokenizer, context-injection flag, or per-language size configuration.

## 19. Oversized-Symbol Fragmentation

Every selected symbol or context candidate follows the same deterministic splitter:

1. If its byte length is at most `max_chunk_bytes`, emit it unchanged.
2. Otherwise scan newline terminators within the candidate once, preserving LF and CRLF bytes exactly.
3. Starting at the current fragment start, prefer the last complete line boundary at or before `target_chunk_bytes` when one exists.
4. If no such boundary exists, use the last complete line boundary at or before `max_chunk_bytes`.
5. If the first complete line itself exceeds `max_chunk_bytes`, cut at the greatest UTF-8 code-point boundary not exceeding `max_chunk_bytes`.
6. Continue until the exact candidate range is exhausted; do not trim, overlap, or repeat bytes.
7. Set zero-based `fragment_index` and the common positive `fragment_count` after all boundaries are known.

All pieces of an oversized candidate use `ChunkKind.FRAGMENT`, retain the candidate's owner metadata, and have independent locations, IDs, and content hashes. A later fragment does not repeat a signature synthetically; its qualified name, parameters, return type, parent context, and file imports remain available as metadata.

The hierarchy “child symbols before line splitting” is realized during candidate selection: nested symbols are first separated from parent residual ranges, and only then is each remaining contiguous candidate line-split.

## 20. UTF-8, Unicode, BOM, and Newline Semantics

All offsets and sizes are bytes. Module 4 uses the exact `original_bytes` returned by the shared reader and strict UTF-8 decoding. It never normalizes Unicode, line endings, tabs, or trailing whitespace.

LF and CRLF terminators remain attached to the fragment that ends at that boundary. A CRLF pair is never split. A UTF-8 BOM remains only in a file-level context chunk whose range begins at byte zero; symbol coordinates already point after it. Hashes include every byte in the chosen range.

The long-line fallback finds code-point boundaries by backing up from the proposed byte cut until the prefix decodes strictly. It must always make positive progress and can exceed neither the candidate end nor `max_chunk_bytes`. Because the complete source was already validated, a valid boundary always exists within at most four bytes of the proposed cut.

## 21. Fragment Location Calculation

Byte offsets are authoritative. Fragment start/end lines and columns are derived without reparsing from a single linear scan of the verified original bytes.

Module 4 builds a per-file ordered line-start byte array once, treating LF as a line terminator and CRLF as one terminator. For a boundary offset, binary search finds the greatest line start not greater than the offset. The line is its one-based index; the column is the UTF-8 byte distance from that line start. Offset `len(source)` is valid and maps to the exclusive end position, matching Module 3 semantics. A BOM contributes three bytes to line-one columns exactly as Module 3 does.

Original unfragmented symbol chunks retain Module 3's `SourceLocation` verbatim after validation. Context and fragment locations are calculated from byte boundaries. Tests must assert byte/line/column parity for ASCII, multibyte Unicode, BOM, LF, CRLF, empty trailing lines, and end-of-file boundaries.

## 22. Deterministic Ordering

`ChunkedFile` values use Module 3's file order, validated against the explicit key `(relative_path.casefold(), relative_path)`. Within each file, chunks sort by:

```text
(
    location.start_byte,
    location.end_byte,
    kind.value,
    "" if symbol_kind is None else symbol_kind.value,
    "" if qualified_name is None else qualified_name,
    fragment_index,
    chunk_id,
)
```

Issues sort with location-less issues last, then by start byte, end byte, kind value, and fixed message. Candidate construction, set/dict iteration, filesystem enumeration, locale, object identity, and global chunk order never determine public ordering or IDs.

## 23. Complexity

For file `f`, let `b_f` be source bytes and `s_f` the number of symbols. Validation, hashing, line indexing, uncovered-range construction, and splitting are linear in bytes plus emitted boundaries. Symbol sorting is `O(s_f log s_f)`. The interval containment pass is `O(s_f)` after sorting, using a stack rather than all-pairs comparison. Lookup joins are O(1) after one O(files) dictionary build.

The inventory target is:

```text
O(files + Σ(source bytes) + Σ(symbols log symbols) + chunks)
```

No depth-sensitive recursion or O(symbols²) containment scan is allowed.

## 24. Memory Behavior

Module 4 processes one file at a time. During processing it retains one verified byte buffer, one line-start array, the file's symbol/interval metadata, and its accumulating immutable chunks. It releases the source buffer and temporary interval structures before reading the next file.

The final inventory necessarily retains chunk text. That is Module 4's retrieval-ready output and avoids forcing Module 5 to repeat I/O and snapshot verification. Low-overlap partitioning prevents class/method and outer/inner duplication. Imports are stored once per file. Temporary decoding should occur per final slice rather than creating a second full-file string.

Expected retained content is at most approximately the non-whitespace source bytes of successful files plus Python object/string overhead; partial files retain less because arbitrary fallback is disabled.

## 25. Error Taxonomy

Fatal exceptions are:

```text
CodeChunkerError
├── ChunkerConfigurationError
├── InvalidChunkInventory
└── RepositoryChunkError
```

- `ChunkerConfigurationError`: invalid target/maximum limits.
- `InvalidChunkInventory`: wrong model types, inconsistent counters/tuples, duplicate paths, mismatched roots, impossible joins, language/category mismatch, or missing required snapshot digest.
- `RepositoryChunkError`: Module 4 cannot establish the safe repository-root boundary, so inventory processing cannot begin.

Per-file `ChunkIssueKind` is:

```text
SOURCE_CHANGED
READ_ERROR
PATH_INVALID
LINK_UNSAFE
DECODING_ERROR
LOCATION_INVALID
PARSE_UNAVAILABLE
FRAGMENTATION_ERROR
```

Read errors map from the shared reader. Digest mismatch maps to `SOURCE_CHANGED`. Invalid/crossing/out-of-buffer locations map to `LOCATION_INVALID`. Unexpected splitting or decoding invariant failures map to `FRAGMENTATION_ERROR`. Messages are fixed sanitized templates, contain no source, and do not expose raw repository-controlled exception text.

A recoverable issue fails only the affected file. `SUCCESS` means the processable file produced its complete V1 partition without chunk issues. `PARTIAL` means its Module 3 input was partial but trusted ranges were chunked. `FAILED` means no chunks were emitted because the parse was unavailable, source was stale/unsafe/unreadable, locations were inconsistent, or fragmentation failed. Counters satisfy:

```text
total_files_requested == success_files + partial_files + failed_files
total_chunks == sum(len(file.chunks) for file in files)
```

## 26. Logging

Module 4 uses `logging.getLogger(__name__)`.

- INFO: inventory start and aggregate completion only.
- DEBUG: sanitized relative path, parse/chunk status, chunk count, and fragment count.
- WARNING: recoverable per-file issue by sanitized relative path and issue kind.
- ERROR: fatal configuration, inventory, or repository-root failure.

Logs never contain source text, chunk content, hashes, imports, default expressions, callee text, secret values, raw exceptions, or arbitrary repository-controlled messages.

## 27. Module 5 Contract

Module 5 receives exact chunk text, content hash, structural identity, fragment metadata, exact citation location, file path/language, file imports, parse/chunk status, and source digest. It may render an embedding document that prepends bounded metadata or relevant import context, but that derived text must remain distinct from `CodeChunk.content`.

Module 5 chooses an embedding provider and tokenizer-aware cap, embeds/indexes chunks, and can key caches by `content_hash`. It must not assume equal content hashes imply one provenance record, and it must preserve `chunk_id` and locations for citations.

## 28. Module 6 Contract

Module 6 may use file path, language, symbol kind, qualified/parent names, parameters, types, modifiers, imports, source ranges, and chunk IDs as graph evidence. A fragment shares its owning symbol metadata but remains a distinct chunk node/evidence unit. Context chunks with an owner can link to that syntactic owner; file context can link to the file node.

Module 4 does not resolve imports, calls, inheritance, implementations, or duplicate qualified names. Its IDs are chunk identities, not repository-global semantic symbol IDs.

## 29. Security

Repository bytes remain untrusted. Module 4 may only validate immutable inputs, safely read verified regular files, hash bytes with `hashlib.sha256`, validate/sort locations, split exact byte ranges, strictly decode UTF-8 slices, and construct frozen metadata.

It must never execute or import repository code; run a subprocess, shell, build, test, package manager, hook, or container; evaluate syntax; deserialize repository data; load repository plugins; access the network; call Git/GitHub/an LLM; follow a link/reparse point; leave the inventory root; log content; or parse with Tree-sitter again.

Module 2's generated/minified and sensitive-name policies are not reimplemented. A content hash is not permission to disclose content.

## 30. Testing Strategy

Implementation will follow strict pytest RED/GREEN TDD in a separately approved implementation plan. Tests use temporary repositories, no network, and real Module 2 → Module 3 → Module 4 integration where useful. Modules 1–3 behavior must remain green.

Required acceptance areas are:

- stable enum values; frozen/slotted field order; config validation; public exports and convenience delegation;
- inventory/root/path uniqueness and counter validation; O(1) path joins; no directory rescanning;
- deterministic chunk IDs and content hashes, including equal content in different files;
- Python functions, methods, constructors, nested functions, classes, and constants;
- Java methods, constructors, interfaces, enums, nested types, constants, and overloads;
- JavaScript functions/arrows/classes, TypeScript interfaces/methods, and TSX components;
- class/method, outer/inner callable, nested-type, interface-method, and enum-method non-overlap;
- imports, comments, preambles, top-level statements, initialization, uncovered declarations, empty files, and whitespace-only gaps;
- successful, partial, and failed parse files; disabled arbitrary fallback for partial files; trusted ranges near syntax errors;
- exact 4,096/8,192 boundaries; line-aware fragmentation; multiple fragments; a line longer than maximum; zero-based stable fragment indices;
- strict Unicode byte slicing and hashing using `नमस्ते`; UTF-8 code-point-safe long-line cuts; BOM; LF; CRLF; end-of-file coordinates;
- source disappearance, size change, same-size content mutation, symlink/reparse swap, path escape, read failure, and unsupported safe-open platform;
- repeated runs produce identical file/chunk/issue order, IDs, hashes, locations, statuses, and counters;
- insertion of an unrelated earlier file does not alter existing IDs, while moving/resizing a symbol does;
- content change at the same identity/range preserves `chunk_id` and changes `content_hash` when tested with constructed valid snapshots;
- no source execution, second parser, tokenizer, external service, or new dependency.

The critical same-size mutation integration test parses revision A, rewrites the file to equal-length revision B, and asserts Module 4 emits `SOURCE_CHANGED` and zero chunks. A size-only implementation must fail this test.

## 31. Explicit Non-Goals

V1 does not implement embeddings, tokenization, vector databases, BM25, hybrid/semantic search, RAG, LLM/provider calls, graph construction/resolution, call/dependency/import/inheritance resolution, Git history, runtime traces, test/build execution, cloning, rescanning, frontend/API/auth, private GitHub support, source rewriting, docstring attachment, semantic relevance of imports, global content deduplication, parallel chunking, streaming inventories, or Module 5+ behavior.

It does not alter Module 2 filtering, add generated-code heuristics, retain ASTs, parse again, synthesize class shells, merge non-contiguous ranges, or promise model-token limits.

## 32. Resolved Trade-offs

- **Fingerprint:** Module 3 must add original-byte `source_sha256`; safe reread plus size is insufficient.
- **Implementation gate:** Module 4 implementation is blocked on that separately approved compatibility patch.
- **Inputs:** accept both inventories in pipeline order; Module 3 supplies structure and Module 2 supplies safe-read evidence.
- **Reader:** deliberately reuse/promote Module 3's hardened reader as a supported internal boundary; do not duplicate or immediately relocate it.
- **Primary content:** every chunk is one exact contiguous verified source slice.
- **Selection:** callable and constant symbols are primary; leaf types are whole; parents with selected descendants become exact residual context.
- **Duplication:** byte ranges form a non-overlapping partition; no complete class around method chunks and no complete outer function around inner chunks.
- **Successful fallback:** non-whitespace uncovered source becomes file context, retaining imports and executable top-level code.
- **Partial fallback:** arbitrary file fallback is disabled; only trustworthy symbol ranges and safe residuals inside an issue-free trusted parent are allowed.
- **Imports:** retain the Module 3 tuple once per `ChunkedFile`, not once per chunk and not prepended to content.
- **Constants:** each conservative Module 3 constant remains an independent symbol chunk.
- **Sizes:** provider-independent 4 KiB target and 8 KiB maximum, configurable with only two fields.
- **Splitting:** child boundaries first, then line-aware exact fragments; UTF-8-safe hard cuts only for overlong lines.
- **Identity:** `chunk_id` hashes canonical structural provenance without content; edits can preserve identity when range/metadata do.
- **Content evidence:** `content_hash` separately hashes exact slice bytes; equal hashes do not cause deduplication.
- **Storage:** retain chunk text in Module 4 output; avoid repeated future I/O and stale verification.
- **Ordering/complexity:** explicit stable keys and stack-based interval containment target `O(bytes + symbols log symbols)` per file.

## 33. Unresolved Issues

### Critical

Approval and delivery of the Module 3 compatibility patch adding `ParsedFile.source_sha256` and documenting the supported cross-module safe-reader contract are prerequisites. Until then, Module 4 cannot prove same-size snapshot consistency and must not be implemented.

### Important

Implementation benchmarking should validate the 4,096-byte target and 8,192-byte maximum against representative TraceRAG repositories before these defaults become a released public contract. The design fixes them for the first implementation and tests; changing them after evidence would require an explicit compatibility decision.

The exact treatment of a rare pair of different Module 3 symbols with identical byte ranges must be confirmed against all five grammar fixtures. The safe default is to fail that file with `LOCATION_INVALID`; an implementation plan may narrow the rule only with concrete parser evidence and tests, without permitting overlapping output.

No other design issue blocks planning after the critical prerequisite and this specification are approved.
