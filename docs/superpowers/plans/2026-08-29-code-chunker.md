# TraceRAG Code Chunker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Module 4 as a deterministic, snapshot-safe structural chunker that converts matching Module 2 and Module 3 inventories into exact, non-overlapping, repository-namespaced source chunks.

**Architecture:** Validate both immutable inventories completely before I/O, join parsed files to scanner evidence once, and process one file at a time through Module 3's supported `SafeSourceReader`. Convert validated symbols into a stack-built containment forest, select non-overlapping symbol/context candidates, fragment exact byte ranges, and construct frozen output models with canonical IDs, hashes, locations, ordering, and counters.

**Tech Stack:** Python 3.11+, pytest, standard-library `dataclasses`, `enum`, `hashlib`, `json`, `bisect`, and `logging`; existing Module 2/3 contracts and pinned Tree-sitter packages; no new dependency.

**Spec:** `docs/superpowers/specs/2026-08-29-code-chunker-design.md`

## Global Constraints

- Begin from canonical `main` commit `1d7c6446919d1b05f61355960c33817eca1f42f8`; create an isolated implementation worktree at execution time.
- Follow strict TDD for every production task: RED → minimal GREEN → focused regression verification → focused commit.
- Public inputs are `FileInventory` followed by `CodeParseInventory`; require equal normalized `repository_path` values and equal, nonempty `repository_namespace` strings.
- Re-read only through `backend.code_parser.reader.SafeSourceReader`; do not duplicate, weaken, relocate, or bypass its filesystem security implementation.
- Hash and slice `SourceBuffer.original_bytes`. Never execute, import, parse again, normalize, concatenate, or otherwise transform repository source.
- Every emitted `CodeChunk.content` must satisfy `content.encode("utf-8") == original_bytes[start_byte:end_byte]`.
- Output ranges within a file never overlap. Callable and constant symbols are primary; a type is whole only without selected descendants; parent and successful-file gaps become exact `CONTEXT` candidates.
- `PARTIAL` files get no arbitrary file fallback. Parent residuals are allowed only when the trusted parent does not intersect a syntax/missing-node issue. `FAILED` parsed files are not read and emit no chunks.
- Defaults are exactly `target_chunk_bytes=4_096` and `max_chunk_bytes=8_192`; both are positive non-`bool` integers and target cannot exceed max.
- `chunk_id` is canonical repository-namespaced structural identity; `content_hash` is exact-slice SHA-256. Never globally deduplicate equal content hashes.
- Process files singly, use iterative interval algorithms, and target `O(files + Σ(bytes) + Σ(symbols log symbols) + chunks)` time without depth recursion or all-pairs containment.
- Do not modify Module 1, dependency pins, Module 2/3 behavior, or their security policies. No embeddings, retrieval, vector database, RAG, graph resolution, Git history, runtime traces, builds, repository test execution, frontend, API, networking, or Module 5+ work.

---

## File and Interface Map

```text
backend/code_chunker/__init__.py       public exports and convenience API
backend/code_chunker/exceptions.py     fatal exception hierarchy
backend/code_chunker/models.py         frozen enums/config/output contracts
backend/code_chunker/validation.py     fatal inventory validation and O(1) joins
backend/code_chunker/locations.py      location checks and byte/line mapping
backend/code_chunker/intervals.py      iterative containment and candidate selection
backend/code_chunker/identity.py       canonical chunk IDs and exact content hashes
backend/code_chunker/fragmenter.py     deterministic newline/UTF-8-safe splitting
backend/code_chunker/chunker.py        safe reads, per-file orchestration, outcomes/order
tests/test_code_chunker.py             unit, integration, security, determinism, scaling
tests/test_code_parser_dependencies.py dependency/import-boundary regression guard
README.md                              Module 4 usage, contracts, security, non-goals
```

Internal interfaces locked by this plan:

```python
@dataclass(frozen=True, slots=True)
class ValidatedInputs:
    repository_path: str
    repository_namespace: str
    pairs: tuple[tuple[ScannedFile, ParsedFile], ...]

@dataclass(frozen=True, slots=True)
class SymbolInterval:
    symbol: SymbolInfo
    children: tuple[int, ...]

@dataclass(frozen=True, slots=True)
class ChunkCandidate:
    kind: ChunkKind
    start_byte: int
    end_byte: int
    owner: SymbolInfo | None

def validate_inputs(file_inventory: object, parse_inventory: object) -> ValidatedInputs: ...
def build_line_starts(source: bytes) -> tuple[int, ...]: ...
def location_from_offsets(source: bytes, line_starts: tuple[int, ...], start: int, end: int) -> SourceLocation: ...
def build_symbol_intervals(symbols: tuple[SymbolInfo, ...], source_size: int) -> tuple[SymbolInterval, ...]: ...
def select_candidates(parsed_file: ParsedFile, source: bytes, intervals: tuple[SymbolInterval, ...]) -> tuple[ChunkCandidate, ...]: ...
def fragment_candidate(candidate: ChunkCandidate, source: bytes, config: ChunkerConfig) -> tuple[tuple[int, int], ...]: ...
def make_chunk_id(repository_namespace: str, relative_path: str, kind: ChunkKind, owner: SymbolInfo | None, start: int, end: int, fragment_index: int) -> str: ...
def make_content_hash(content_bytes: bytes) -> str: ...
```

`SymbolInterval.children` contains indices into the returned normalized interval tuple. Implementers may add private fields needed for parent indices, but public result structures must never retain Tree-sitter nodes, `SourceBuffer`, a full-file byte/string copy, or a flattened inventory chunk tuple.

---

### Task 1: Public Models, Errors, and Configuration

**Files:**
- Create: `backend/code_chunker/models.py`
- Create: `backend/code_chunker/exceptions.py`
- Test: `tests/test_code_chunker.py`

**Interfaces:**
- Consumes: Module 3 `SourceLocation`, `SymbolKind`, `ParameterInfo`, `ImportInfo`, `ParsedLanguage`, and `ParseStatus`.
- Produces: every enum/model and fatal exception named in design Sections 7, 8, and 25.

- [ ] **Step 1: Write RED contract tests**

Add exact enum-value assertions for `ChunkKind`, `ChunkFileStatus`, and all eight `ChunkIssueKind` values; exact dataclass field-order assertions from the spec; frozen/slotted mutation failures; tuple-only collection examples; and exception inheritance assertions:

```python
assert ChunkIssueKind.SOURCE_CHANGED.value == "source_changed"
assert tuple(ChunkerConfig.__dataclass_fields__) == ("target_chunk_bytes", "max_chunk_bytes")
assert issubclass(InvalidChunkInventory, CodeChunkerError)
with pytest.raises(FrozenInstanceError):
    ChunkerConfig().max_chunk_bytes = 1
```

Parametrize invalid configs over `True`, `False`, `0`, `-1`, `1.5`, `"4096"`, and `(target, max)=(8193,8192)`. Assert eager `ChunkerConfigurationError` and defaults `4096/8192`.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_code_chunker.py -q`

Expected: collection fails because `backend.code_chunker` does not exist.

- [ ] **Step 3: Implement minimal contracts**

Create the exact frozen/slotted models in the design, the exception tree `CodeChunkerError -> {ChunkerConfigurationError, InvalidChunkInventory, RepositoryChunkError}`, and `ChunkIssueKind` values `source_changed`, `read_error`, `path_invalid`, `link_unsafe`, `decoding_error`, `location_invalid`, `parse_unavailable`, `fragmentation_error`. Validate `ChunkerConfig` in `__post_init__` using `type(value) is int`, positivity, and target ≤ max.

- [ ] **Step 4: Run GREEN and regression**

Run: `python -m pytest tests/test_code_chunker.py -q`

Run: `python -m pytest tests/test_file_scanner.py tests/test_code_parser_dependencies.py tests/test_code_parser.py -q`

- [ ] **Step 5: Commit**

```powershell
git add backend/code_chunker/models.py backend/code_chunker/exceptions.py tests/test_code_chunker.py
git commit -m "feat: define code chunker contracts"
```

---

### Task 2: Fatal Inventory Validation and O(1) Joins

**Files:**
- Create: `backend/code_chunker/validation.py`
- Test: `tests/test_code_chunker.py`

**Interfaces:**
- Consumes: exact Module 2/3 public frozen models.
- Produces: `validate_inputs(...) -> ValidatedInputs` with pairs in validated Module 3 order.

- [ ] **Step 1: Write RED validation tests**

Construct frozen inventories with `dataclasses.replace` and assert `InvalidChunkInventory` before a monkeypatched reader can run for: wrong input types; list instead of every required tuple; inconsistent scanner counters; inconsistent parser success/partial/failed/skipped partition; unsorted parser files; duplicate scanner or parser paths; root mismatch; missing/empty/non-string/different namespaces; missing join; matched `CONFIG`; incompatible language/extension; malformed or missing 64-character lowercase `source_sha256` on `SUCCESS`/`PARTIAL`. Include `.jsx` compatibility with `ParsedLanguage.JAVASCRIPT` and the closed `.py/.java/.js/.jsx/.ts/.tsx` mapping. Confirm a `FAILED` parsed file may have `source_sha256=None`.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_code_chunker.py -q -k "inventory or namespace or join"`

Expected: import/function failures; reader spy remains uncalled.

- [ ] **Step 3: Implement complete pre-I/O validation**

Validate exact model instances, tuple shapes and member types, all documented counters, scanner and parser uniqueness, parser sort key `(relative_path.casefold(), relative_path)`, resolved-root string equality, namespaces, processable digests, categories, and the closed language selector. Build one scanner-path dictionary and produce one tuple of pairs; do not enumerate the filesystem or instantiate the reader here.

- [ ] **Step 4: Run GREEN and focused regression**

Run: `python -m pytest tests/test_code_chunker.py -q -k "inventory or namespace or join"`

Run: `python -m pytest tests/test_file_scanner.py tests/test_code_parser.py -q`

- [ ] **Step 5: Commit**

```powershell
git add backend/code_chunker/validation.py tests/test_code_chunker.py
git commit -m "feat: validate chunker inventories"
```

---

### Task 3: Safe Reader Integration and Snapshot Enforcement

**Files:**
- Create: `backend/code_chunker/chunker.py`
- Test: `tests/test_code_chunker.py`

**Interfaces:**
- Consumes: `ValidatedInputs`, `SafeSourceReader.read(ScannedFile) -> SourceBuffer`, `ParsedFile.source_sha256`.
- Produces: initial per-file `ChunkedFile` failure outcomes and the orchestration seam for later candidate construction.

- [ ] **Step 1: Write RED reader/digest tests**

Parse revision A, rewrite it with equal-length revision B, and assert `SOURCE_CHANGED`, fixed sanitized message, `FAILED`, and zero chunks. Add BOM-inclusive digest, LF/CRLF digest difference, disappearance, size change, path escape, symlink/reparse swap, invalid UTF-8, generic read failure, and unsupported-platform mappings. Spy that `FAILED` parses never call `read`, while `SUCCESS` and `PARTIAL` call it exactly once with the matched `ScannedFile`.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_code_chunker.py -q -k "source_changed or safe_reader or read_error or parse_unavailable"`

Expected: orchestration is absent or does not emit the required fixed issues.

- [ ] **Step 3: Implement minimal safe-read orchestration**

Instantiate `SafeSourceReader` only after fatal validation; map `RepositoryParseError` to `RepositoryChunkError`. Map reader kinds: `FILE_CHANGED -> SOURCE_CHANGED`, `READ_ERROR -> READ_ERROR`, `PATH_INVALID -> PATH_INVALID`, `LINK_UNSAFE -> LINK_UNSAFE`, `DECODING_ERROR -> DECODING_ERROR`. After a successful read, compare `sha256(buffer.original_bytes).hexdigest()` with the parsed digest before using locations. Use fixed messages and never include raw exceptions, source, hashes, or absolute paths.

- [ ] **Step 4: Run GREEN and reader boundary regression**

Run: `python -m pytest tests/test_code_chunker.py -q -k "source_changed or safe_reader or read_error or parse_unavailable"`

Run: `python -m pytest tests/test_code_parser.py -q -k "reader or sha256 or bom"`

- [ ] **Step 5: Commit**

```powershell
git add backend/code_chunker/chunker.py tests/test_code_chunker.py
git commit -m "feat: verify chunk source snapshots"
```

---

### Task 4: Location Validation and Coordinate Reconstruction

**Files:**
- Create: `backend/code_chunker/locations.py`
- Test: `tests/test_code_chunker.py`

**Interfaces:**
- Produces: iterative location validation, `build_line_starts`, and `location_from_offsets`.

- [ ] **Step 1: Write RED location tests**

Reject negative, reversed, empty symbol, beyond-EOF, invalid one-based line, invalid byte-column, and coordinates inconsistent with offsets. Validate symbols, imports, calls, and located parse issues before selection. Assert reconstructed coordinates for ASCII, `नमस्ते`, BOM columns, LF, CRLF without splitting the pair, empty trailing lines, and offset `len(source)`. Assert original unfragmented symbol locations remain object-equal to Module 3 locations.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_code_chunker.py -q -k "location or coordinate or unicode"`

Expected: missing helpers and no `LOCATION_INVALID` fail-closed result.

- [ ] **Step 3: Implement linear indexing and binary-search lookup**

Build line starts once by scanning LF bytes (CRLF is one terminator because only LF opens the next line). Resolve boundaries with `bisect_right`; columns are byte distances. Validate all authoritative locations against source length and reconstructed endpoints. Any inconsistency fails only that file with `LOCATION_INVALID` and no chunks.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/test_code_chunker.py -q -k "location or coordinate or unicode"`

- [ ] **Step 5: Commit**

```powershell
git add backend/code_chunker/locations.py backend/code_chunker/chunker.py tests/test_code_chunker.py
git commit -m "feat: validate chunk source locations"
```

---

### Task 5: Iterative Symbol Containment and Primary Selection

**Files:**
- Create: `backend/code_chunker/intervals.py`
- Test: `tests/test_code_chunker.py`

**Interfaces:**
- Produces: normalized interval forest and primary `ChunkCandidate` values.

- [ ] **Step 1: Write RED interval tests**

Cover functions, methods, constructors, nested functions, constants, leaf classes/interfaces/enums, nested types, overloads, and parent evidence. Assert callable/constant primaries, whole leaf types, deterministic sort `(start_byte, -end_byte, kind.value, qualified_name)`, and no output overlap. Crossing ranges and incompatible identical ranges must produce file-level `LOCATION_INVALID` and no chunks. Equal-range duplicates with identical normalized identity collapse deterministically to one candidate.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_code_chunker.py -q -k "interval or primary or crossing or duplicate_range"`

Expected: missing containment/selection behavior.

- [ ] **Step 3: Implement the stack algorithm**

Sort once, walk with an explicit stack, pop completed ancestors, reject crossings, and reconcile lexical containment with `parent_qualified_name`. Never scan all symbol pairs and never recurse by nesting depth. Treat `FUNCTION`, `METHOD`, `CONSTRUCTOR`, and `CONSTANT` as primary and `CLASS`, `INTERFACE`, `ENUM` as whole only without selected descendants.

- [ ] **Step 4: Run GREEN and grammar-fixture evidence**

Run: `python -m pytest tests/test_code_chunker.py -q -k "interval or primary or crossing or duplicate_range"`

Run: `python -m pytest tests/test_code_parser.py -q -k "python_extractor or java_extractor or javascript_extractor or typescript_extractor or tsx_extractor"`

- [ ] **Step 5: Commit**

```powershell
git add backend/code_chunker/intervals.py backend/code_chunker/chunker.py tests/test_code_chunker.py
git commit -m "feat: select structural chunk intervals"
```

---

### Task 6: Parent Residuals and SUCCESS File Fallback

**Files:**
- Modify: `backend/code_chunker/intervals.py`
- Test: `tests/test_code_chunker.py`

**Interfaces:**
- Extends: `select_candidates` with owned and file-level context complements.

- [ ] **Step 1: Write RED partition tests**

For class/method, outer/inner callable, nested type, interface method, enum method, and constants, concatenate candidates in byte order and assert exact slice ownership with no overlap. For `SUCCESS`, cover imports, comments, BOM preamble, package/module declarations, initialization, top-level statements, uncovered declarations, empty files, and whitespace-only gaps. Assert no missing non-whitespace coverage outside selected structure and do not trim retained gap boundaries.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_code_chunker.py -q -k "residual or fallback or non_whitespace_coverage"`

Expected: parent/file gaps are absent.

- [ ] **Step 3: Implement exact complements**

Subtract the union of direct selected child ranges from meaningful parents, emitting non-whitespace gaps as owner-backed `CONTEXT`. For successful files, subtract top-level structural candidates from `[0, len(source))` and emit non-whitespace file context with `owner=None`. Determine whitespace by strict UTF-8 decoding and `str.isspace()` without trimming or syntax classification.

- [ ] **Step 4: Run GREEN and invariant check**

Run: `python -m pytest tests/test_code_chunker.py -q -k "residual or fallback or non_whitespace_coverage or no_overlap"`

- [ ] **Step 5: Commit**

```powershell
git add backend/code_chunker/intervals.py tests/test_code_chunker.py
git commit -m "feat: preserve structural residual context"
```

---

### Task 7: PARTIAL Restrictions and FAILED Behavior

**Files:**
- Modify: `backend/code_chunker/intervals.py`
- Modify: `backend/code_chunker/chunker.py`
- Test: `tests/test_code_chunker.py`

**Interfaces:**
- Produces: spec-accurate `ChunkFileStatus.PARTIAL` and `FAILED` outcomes.

- [ ] **Step 1: Write RED status-policy tests**

Create partial fixtures with trustworthy symbols beside malformed syntax. Assert no arbitrary file-level fallback, unsafe-area exclusion, trusted leaf chunks retained, and parent residuals emitted only when the parent range intersects neither `SYNTAX_ERROR` nor `MISSING_NODE`. Assert imports remain file metadata but do not create inferred partial chunks. Assert failed parses yield exactly one fixed `PARSE_UNAVAILABLE`, no read, no chunks, and failed status.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_code_chunker.py -q -k "partial or failed_parse or unsafe_area"`

Expected: current fallback leaks uncovered partial bytes or status handling is incomplete.

- [ ] **Step 3: Implement fail-closed partial selection**

Index located syntax/missing-node issue intervals and suppress residuals for an intersecting parent. Preserve independently trustworthy non-overlapping descendants. Disable `[0,file_size)` fallback for every partial file. Preserve the original imports tuple once and set `PARTIAL` even when all allowed candidates succeed.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/test_code_chunker.py -q -k "partial or failed_parse or unsafe_area"`

- [ ] **Step 5: Commit**

```powershell
git add backend/code_chunker/intervals.py backend/code_chunker/chunker.py tests/test_code_chunker.py
git commit -m "feat: enforce partial chunk safety"
```

---

### Task 8: Content Hash and Canonical Repository-Namespace Identity

**Files:**
- Create: `backend/code_chunker/identity.py`
- Test: `tests/test_code_chunker.py`

**Interfaces:**
- Produces: exact SHA-256 `content_hash` and canonical JSON-array `chunk_id`.

- [ ] **Step 1: Write RED identity tests**

Compute expected IDs independently with `json.dumps(material, ensure_ascii=False, separators=(",", ":"))`. Assert same repository namespace plus different clone paths gives identical IDs; different namespaces plus identical source/provenance gives different IDs; equal content in different files gives equal content hashes and distinct IDs with both chunks retained; stable range/identity plus changed bytes preserves ID and changes content hash in a constructed valid snapshot; moving/resizing changes ID; inserting an earlier unrelated file does not. Include Unicode names and paths.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_code_chunker.py -q -k "chunk_id or content_hash or clone_path or namespace_identity"`

Expected: identity functions absent.

- [ ] **Step 3: Implement exact canonical material**

Hash the UTF-8 JSON array `['tracerag-code-chunk-v1', namespace, relative_path, kind.value, symbol_kind-or-empty, qualified_name-or-empty, parent_qualified_name-or-empty, start, end, fragment_index]`. Hash content bytes directly. Return lowercase hex and never use path roots, source digest, global index, timestamps, UUIDs, Python `hash()`, or content in IDs.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/test_code_chunker.py -q -k "chunk_id or content_hash or clone_path or namespace_identity"`

- [ ] **Step 5: Commit**

```powershell
git add backend/code_chunker/identity.py tests/test_code_chunker.py
git commit -m "feat: identify code chunks canonically"
```

---

### Task 9: Newline-Aware and UTF-8-Safe Fragmentation

**Files:**
- Create: `backend/code_chunker/fragmenter.py`
- Test: `tests/test_code_chunker.py`

**Interfaces:**
- Produces: exhaustive, ordered, non-overlapping `(start_byte, end_byte)` pieces for one candidate.

- [ ] **Step 1: Write RED splitter tests**

Cover candidates at 4096 and 8192 bytes, one byte beyond max, multiple lines/fragments, LF, CRLF, a large single line containing repeated `नमस्ते`, BOM-bearing file context, and EOF without newline. Assert preference for the last complete line at/before target, fallback at/before max, no CRLF/code-point split, positive progress, `piece_size <= max`, and `b''.join(source[a:b] for a,b in pieces) == source[candidate.start:candidate.end]`.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_code_chunker.py -q -k "fragment or max_byte or oversized_line or crlf"`

Expected: splitter absent.

- [ ] **Step 3: Implement one-pass boundaries**

Scan newline ends once per candidate. Choose target boundary, then max boundary, then back up from the hard cut until strict UTF-8 decode succeeds. Return the candidate unchanged when ≤ max. Catch only impossible invariant failures at orchestration and convert them to fixed `FRAGMENTATION_ERROR`; do not silently drop bytes.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/test_code_chunker.py -q -k "fragment or max_byte or oversized_line or crlf"`

- [ ] **Step 5: Commit**

```powershell
git add backend/code_chunker/fragmenter.py backend/code_chunker/chunker.py tests/test_code_chunker.py
git commit -m "feat: fragment oversized code ranges"
```

---

### Task 10: Chunk Construction, Fragment Metadata, and Exact Bytes

**Files:**
- Modify: `backend/code_chunker/chunker.py`
- Test: `tests/test_code_chunker.py`

**Interfaces:**
- Consumes: candidates, fragment ranges, location and identity helpers.
- Produces: immutable `CodeChunk` tuples per processed file.

- [ ] **Step 1: Write RED construction tests**

Assert unfragmented candidate kind remains `SYMBOL`/`CONTEXT` with `fragment_index=0`, `fragment_count=1`; every oversized piece is `FRAGMENT` with zero-based indices and common positive count. Verify owner name/kind/qualified/parent/parameters/return/modifiers/base/implemented metadata on symbol, owned context, and fragments; file context uses `None`/empty tuples. For BOM, LF, CRLF, and `नमस्ते`, assert exact byte slicing, independent content hashes, max-byte guarantee, and reconstructed line/column parity.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_code_chunker.py -q -k "fragment_metadata or exact_byte or owner_metadata"`

Expected: no completed chunk construction.

- [ ] **Step 3: Implement minimal construction**

Decode only each final verified slice with strict UTF-8, calculate its location, ID, and hash, and copy owner metadata exactly. Retain a Module 3 location verbatim for an unfragmented symbol. On any decode/splitting invariant error, discard all chunks for that file and return one `FRAGMENTATION_ERROR`.

- [ ] **Step 4: Run GREEN and full chunker tests**

Run: `python -m pytest tests/test_code_chunker.py -q`

- [ ] **Step 5: Commit**

```powershell
git add backend/code_chunker/chunker.py tests/test_code_chunker.py
git commit -m "feat: construct exact code chunks"
```

---

### Task 11: Deterministic Files, Issues, Counters, and Logging

**Files:**
- Modify: `backend/code_chunker/chunker.py`
- Test: `tests/test_code_chunker.py`

**Interfaces:**
- Produces: fully invariant `CodeChunkInventory`.

- [ ] **Step 1: Write RED aggregate tests**

Run identical inventories repeatedly and assert identical files, chunks, issues, IDs, hashes, locations, statuses, and counters. Assert chunk sort key and issue sort key exactly match design Section 22. Assert `total_files_requested == success_files + partial_files + failed_files`, `total_chunks == sum(len(file.chunks) ...)`, no chunk-range duplicates, and no global content-hash dedup. Capture logs and assert only aggregate INFO, sanitized path/status/count DEBUG, issue-kind WARNING, and fatal ERROR; reject source, content, hashes, imports, raw exceptions, and secret fixture values.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_code_chunker.py -q -k "deterministic or counter or ordering or logging"`

Expected: aggregate invariants/order/log policy incomplete.

- [ ] **Step 3: Implement deterministic aggregation**

Sort chunks by `(start,end,kind.value,symbol_kind-or-empty,qualified_name-or-empty,fragment_index,chunk_id)` and issues with location-less last then `(start,end,kind.value,message)`. Keep validated Module 3 file order, derive counters from final files, assert invariants before returning, and log only fixed sanitized fields.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/test_code_chunker.py -q -k "deterministic or counter or ordering or logging"`

- [ ] **Step 5: Commit**

```powershell
git add backend/code_chunker/chunker.py tests/test_code_chunker.py
git commit -m "feat: finalize deterministic chunk inventories"
```

---

### Task 12: Public API and Convenience Delegation

**Files:**
- Create: `backend/code_chunker/__init__.py`
- Modify: `backend/code_chunker/chunker.py`
- Test: `tests/test_code_chunker.py`

**Interfaces:**
- Produces: `CodeChunker(config: ChunkerConfig | None = None)`, `chunk_inventory(file_inventory, parse_inventory)`, `chunk_code_inventory(file_inventory, parse_inventory)`, and exactly the approved top-level exports.

- [ ] **Step 1: Write RED API tests**

Assert exact `__all__`, eager config validation, isolated default chunkers, argument order, and convenience delegation. Monkeypatch `CodeChunker.chunk_inventory` and assert one call with the two inventories. Assert public model graphs contain no Tree-sitter node, `SourceBuffer`, `bytes`, full-file source field, or flattened inventory chunk tuple.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_code_chunker.py -q -k "public_api or exports or convenience or retained"`

Expected: top-level package/API unavailable.

- [ ] **Step 3: Implement the approved surface**

Export only the names listed in design Section 7. Keep reader types as direct internal imports from `backend.code_parser.reader`, not Module 4 exports. Store only validated config on `CodeChunker`; create a fresh default instance in `chunk_code_inventory`.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/test_code_chunker.py -q`

- [ ] **Step 5: Commit**

```powershell
git add backend/code_chunker/__init__.py backend/code_chunker/chunker.py tests/test_code_chunker.py
git commit -m "feat: expose code chunker api"
```

---

### Task 13: Real Module 2 → 3 → 4 Language Integration

**Files:**
- Modify: `tests/test_code_chunker.py`

**Interfaces:**
- Verifies: production pipeline behavior without adding production surface.

- [ ] **Step 1: Add integration acceptance tests**

Use temporary repositories and `FileScanner` → `CodeParser` → `CodeChunker` for Python functions/methods/constructors/nested functions/classes/constants; Java methods/constructors/interfaces/enums/nested types/constants/overloads; JavaScript functions/arrows/classes; TypeScript interfaces/methods; and TSX components. For every file assert exact bytes, no overlap, expected owners, imports stored once, deterministic repeat results, and successful-file non-whitespace fallback coverage.

- [ ] **Step 2: Verify the new tests expose any integration gap**

Run: `python -m pytest tests/test_code_chunker.py -q -k "pipeline_"`

Expected: RED if real extractor shapes contradict a unit assumption; otherwise record that the already-minimal production path is GREEN and do not add speculative code.

- [ ] **Step 3: Make only evidence-driven corrections**

If a fixture exposes a mismatch, change only the relevant Module 4 validation/interval rule and keep the approved fail-closed overlap policy. In particular, incompatible identical symbol ranges remain `LOCATION_INVALID` unless all five grammar fixture families provide concrete evidence for a narrower deterministic equivalence rule.

- [ ] **Step 4: Run focused and upstream regression**

Run: `python -m pytest tests/test_code_chunker.py -q`

Run: `python -m pytest tests/test_file_scanner.py tests/test_code_parser_dependencies.py tests/test_code_parser.py -q`

- [ ] **Step 5: Commit**

```powershell
git add backend/code_chunker tests/test_code_chunker.py
git commit -m "test: cover code chunker language integration"
```

---

### Task 14: Security and Dependency Regression Gates

**Files:**
- Modify: `tests/test_code_chunker.py`
- Modify: `tests/test_code_parser_dependencies.py`

**Interfaces:**
- Verifies: static-only, offline, shared-reader, no-new-dependency boundary.

- [ ] **Step 1: Add RED security/dependency assertions**

Monkeypatch repository-file execution/import, subprocess, shell, network, Git, parser construction, `Path.read_text/read_bytes/open`, and directory enumeration to fail if Module 4 calls them. Assert only `SafeSourceReader.read` supplies bytes; symlink/reparse, escape, race/size mutation, unsupported safe-open, and decoding failures fail closed. Extend the dependency test to inspect Module 4 AST imports and allow only standard library plus `backend.file_scanner`, `backend.code_parser`, and sibling `backend.code_chunker` modules; assert requirements files are unchanged from the prerequisite commit.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_code_chunker.py tests/test_code_parser_dependencies.py -q -k "security or dependency or no_"`

Expected: dependency guard fails until Module 4 is included; any forbidden call fails loudly.

- [ ] **Step 3: Remove boundary violations only**

Route all reads through the existing reader, retain static metadata operations only, and adjust imports without adding packages. Do not change Module 1 or security-sensitive reader implementation to satisfy a test.

- [ ] **Step 4: Run GREEN and confirm dependency preservation**

Run: `python -m pytest tests/test_code_chunker.py tests/test_code_parser_dependencies.py -q`

Run: `git diff 1d7c6446919d1b05f61355960c33817eca1f42f8 -- requirements.txt`

Expected: tests pass and dependency diff is empty.

- [ ] **Step 5: Commit**

```powershell
git add tests/test_code_chunker.py tests/test_code_parser_dependencies.py
git commit -m "test: lock code chunker security boundary"
```

---

### Task 15: Performance and Scaling Evidence

**Files:**
- Modify: `tests/test_code_chunker.py`

**Interfaces:**
- Verifies: planned complexity and one-file-at-a-time memory discipline.

- [ ] **Step 1: Add deterministic operation-count tests**

Generate deeply nested and wide synthetic `SymbolInfo` tuples. Instrument interval comparisons/stack operations to prove linear work after sorting and ensure no recursion error at depth above Python's recursion limit. Instrument scanner joins to demonstrate one dictionary build plus O(1) lookup per parsed file. Use a multi-file reader spy holding weak references/counters to assert the prior `SourceBuffer` is not retained when the next file read begins; inspect public outputs for no full-file source/AST retention.

- [ ] **Step 2: Verify scaling test behavior**

Run: `python -m pytest tests/test_code_chunker.py -q -k "scaling or deep or one_file_at_a_time"`

Expected: fail if containment is recursive/quadratic or source buffers are retained.

- [ ] **Step 3: Make minimal complexity corrections**

Replace any all-pairs/depth recursion with sorting, stacks, interval sweeps, and per-file local lifetimes. Do not introduce parallelism, streaming output, caches, or benchmark-only production APIs.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/test_code_chunker.py -q -k "scaling or deep or one_file_at_a_time"`

Run: `python -m pytest tests/test_code_chunker.py -q`

- [ ] **Step 5: Commit**

```powershell
git add backend/code_chunker tests/test_code_chunker.py
git commit -m "test: verify code chunker scaling"
```

---

### Task 16: README and Contract Documentation

**Files:**
- Modify: `README.md`
- Test: `tests/test_code_chunker.py`

**Interfaces:**
- Documents: Module 4 usage, exact invariants, supported boundary, error behavior, and non-goals.

- [ ] **Step 1: Add a RED documentation contract test**

Assert README contains the two-inventory call, namespace/digest requirements, default/max byte sizes, exact-slice invariant, `chunk_id` versus `content_hash`, partial/failed policy, supported reader import path, and explicit no-execution/no-Module-5 boundary.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_code_chunker.py -q -k "readme"`

Expected: README lacks Module 4 documentation.

- [ ] **Step 3: Write the Module 4 README section**

Add a concise example from `FileScanner` and `CodeParser` into `CodeChunker`, describe exact-byte provenance and statuses, explain repository-namespaced structural identity and non-deduplicating content hashes, list defaults, and state security/non-goals. Do not document embeddings, APIs, graph behavior, or unimplemented orchestration.

- [ ] **Step 4: Run GREEN**

Run: `python -m pytest tests/test_code_chunker.py -q -k "readme"`

- [ ] **Step 5: Commit**

```powershell
git add README.md tests/test_code_chunker.py
git commit -m "docs: document code chunker contracts"
```

---

### Task 17: Full Verification and Independent Review

**Files:**
- Review only: `backend/code_chunker/**`, `tests/test_code_chunker.py`, `tests/test_code_parser_dependencies.py`, `README.md`

**Interfaces:**
- Produces: review evidence only; no new feature scope.

- [ ] **Step 1: Run focused Module 4 verification**

```powershell
python -m pytest tests/test_code_chunker.py -q -rs
python -m pytest tests/test_code_parser_dependencies.py -q
```

Expected: all Module 4 and dependency-boundary tests pass; only explicitly platform-dependent symlink/reparse skips are acceptable.

- [ ] **Step 2: Run upstream and full regression**

```powershell
python -m pytest tests/test_repository_loader.py -q
python -m pytest tests/test_file_scanner.py -q
python -m pytest tests/test_code_parser_dependencies.py tests/test_code_parser.py -q
python -m pytest -q -rs
python -m compileall -q backend tests
python -m pip check
git diff --check
```

Expected: Modules 1–3 remain green, the complete suite passes, compilation is clean, and `pip check` reports no broken requirements.

- [ ] **Step 3: Audit scope and invariants**

```powershell
git diff 1d7c6446919d1b05f61355960c33817eca1f42f8 -- backend/repository_loader tests/test_repository_loader.py requirements.txt
git diff --name-only 1d7c6446919d1b05f61355960c33817eca1f42f8
rg -n "subprocess|requests|urllib|socket|tree_sitter|read_text|read_bytes|eval\(|exec\(" backend/code_chunker
```

Expected: the first diff is empty; changed files are limited to the file map; any static-search match is reviewed and justified as harmless (for example documentation text), with no source execution, second parser, network, unsafe read, new dependency, or Module 5+ behavior.

- [ ] **Step 4: Request independent code review**

Use `superpowers:requesting-code-review` against the full implementation diff. Require explicit findings on design coverage, filesystem-boundary reuse, exact-byte/no-overlap invariants, partial safety, deterministic identities/order, error sanitization, retained-memory contracts, complexity, and scope. Resolve findings with RED/GREEN regression tests and focused fix commits.

- [ ] **Step 5: Record final implementation evidence without merging**

```powershell
git status --short
git log --oneline 1d7c6446919d1b05f61355960c33817eca1f42f8..HEAD
```

Expected: clean implementation branch with reviewable focused commits. Stop for integration approval; do not merge or push unless separately authorized.

---

## Requirement Traceability

- Contracts/config/inventory/namespace: Tasks 1–2.
- Safe reread, digest mismatch, fatal/per-file errors: Tasks 3–4 and 7.
- Location trust, interval containment, primary/type/constant selection: Tasks 4–5.
- Parent residuals, SUCCESS fallback, PARTIAL exclusion, FAILED behavior: Tasks 6–7.
- Content hash, repository-namespaced ID, no hash deduplication: Task 8.
- Newline fragmentation, UTF-8 oversized lines, metadata, byte/coordinate invariants: Tasks 9–10.
- Ordering, deduplication, counters, inventory invariants, logging: Task 11.
- Public API and no retained parser/source internals: Task 12.
- All five language families and clone/namespace/mutation acceptance: Task 13.
- Filesystem security, no source execution, no new dependencies, no Module 1 changes: Task 14.
- Complexity and memory targets: Task 15.
- README and all explicit non-goals: Task 16.
- Full verification and independent review: Task 17.

## Known Implementation Risks (Not Design Changes)

1. **Equal symbol ranges:** Keep the approved safe default: incompatible identities at an identical range fail that file with `LOCATION_INVALID`. Narrow only if concrete tests across all five grammar fixtures establish an equivalent duplicate representation without permitting overlap.
2. **Default-size benchmarking:** Implement and test 4,096/8,192 exactly. Benchmarking may inform a later explicit compatibility decision but does not authorize changing V1 defaults during implementation.
3. **Platform link tests:** POSIX symlink and Windows reparse behavior depends on host capabilities. Tests may skip only when the OS refuses fixture creation; production behavior must always fail closed.

No unresolved prerequisite or design ambiguity blocks implementation planning at canonical commit `1d7c6446919d1b05f61355960c33817eca1f42f8`.
