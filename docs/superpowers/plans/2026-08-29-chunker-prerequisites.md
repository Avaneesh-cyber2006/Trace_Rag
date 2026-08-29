# TraceRAG Code Chunker Prerequisites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the minimum backward-compatible Module 2/3 contracts required for repository-namespaced, snapshot-safe Module 4 chunking without implementing the chunker.

**Architecture:** Module 2 accepts an optional opaque repository namespace and retains it on `FileInventory`; Module 3 validates and copies it to `CodeParseInventory`. Module 3 hashes exact verified `SourceBuffer.original_bytes` once and appends the digest to every post-read `ParsedFile` outcome, while the unchanged hardened reader becomes an explicitly supported internal cross-module boundary.

**Tech Stack:** Python 3.11+, pytest, standard-library `hashlib.sha256`, and the existing pinned Tree-sitter packages; no new dependency.

**Spec:** `docs/superpowers/specs/2026-08-29-code-chunker-design.md`

## Global Constraints

- Start from approved documentation commits `e3b7b87` and `c3137d4`; verify the checkout before execution.
- Change only `backend/file_scanner/**`, `backend/code_parser/**`, focused tests, and their existing README sections.
- Do not modify `backend/repository_loader/**` or `tests/test_repository_loader.py`; Module 1 behavior remains unchanged.
- Do not create `backend/code_chunker/`, `tests/test_code_chunker.py`, chunk IDs/models, fragmentation, overlap/fallback logic, or Module 5+ behavior.
- Do not change dependencies, Tree-sitter pins, extraction, statuses, locations, UTF-8/BOM rules, or filesystem security logic.
- Treat `repository_namespace` as opaque `str | None`. Modules 2/3 must not import Module 1, parse URLs, normalize, strip, case-fold, reconstruct, or log it.
- The future caller derives `"tracerag-repository-v1:github:" + repository.repo_url.casefold()`; do not add a helper or orchestration module here.
- Hash exact `SourceBuffer.original_bytes`, including BOM and original LF/CRLF, only after a verified complete read.
- Every production task uses RED, minimal GREEN, focused regression, and a focused commit. Do not push.

---

## File and Interface Map

```text
backend/file_scanner/models.py   append FileInventory.repository_namespace
backend/file_scanner/scanner.py  accept/store the optional opaque namespace
backend/code_parser/models.py    append parse namespace and source digest fields
backend/code_parser/parser.py    validate/copy namespace; compute/propagate digest
backend/code_parser/reader.py    declare supported internal exports; no logic rewrite
tests/test_file_scanner.py       Module 2 compatibility and propagation
tests/test_code_parser.py        Module 3 contracts, outcomes, bytes, reader boundary
README.md                        additive Module 2/3 contract documentation
```

Required interfaces:

```python
@dataclass(frozen=True, slots=True)
class FileInventory:
    repository_path: str
    total_files_seen: int
    included_files: int
    ignored_files: int
    files: tuple[ScannedFile, ...]
    ignored: tuple[IgnoredFile, ...]
    skipped_directories: tuple[SkippedDirectory, ...]
    repository_namespace: str | None = None

class FileScanner:
    def scan(
        self,
        repository_path: Path | str,
        *,
        repository_namespace: str | None = None,
    ) -> FileInventory: ...

def scan_repository(
    repository_path: Path | str,
    max_file_size_bytes: int = 1_000_000,
    binary_sample_size: int = 8192,
    *,
    repository_namespace: str | None = None,
) -> FileInventory: ...

@dataclass(frozen=True, slots=True)
class ParsedFile:
    relative_path: str
    language: ParsedLanguage
    status: ParseStatus
    symbols: tuple[SymbolInfo, ...]
    imports: tuple[ImportInfo, ...]
    calls: tuple[CallSite, ...]
    issues: tuple[ParseIssue, ...]
    source_sha256: str | None = None

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
    repository_namespace: str | None = None
```

`SafeSourceReader`, `SourceBuffer`, and `SourceReadError` stay in `backend.code_parser.reader`. They are supported internal interfaces, not new `backend.code_parser` top-level exports.

---

### Task 1: Extend Module 2 Namespace Input and Inventory

**Files:**
- Modify: `backend/file_scanner/models.py`
- Modify: `backend/file_scanner/scanner.py`
- Test: `tests/test_file_scanner.py`

**Interfaces:**
- Consumes: caller-provided `repository_namespace: str | None`.
- Produces: the Module 2 interfaces in the file map above.

- [ ] **Step 1: Write failing contract and propagation tests**

```python
def test_file_inventory_repository_namespace_defaults_to_none(tmp_path: Path) -> None:
    result = FileScanner().scan(tmp_path)
    assert tuple(result.__dataclass_fields__)[-1] == "repository_namespace"
    assert result.repository_namespace is None

@pytest.mark.parametrize("namespace", ("opaque:A", "opaque:a"))
def test_scanner_retains_repository_namespace_exactly(
    tmp_path: Path, namespace: str
) -> None:
    write_bytes(tmp_path / "app.py", b"answer = 42\n")
    result = FileScanner().scan(tmp_path, repository_namespace=namespace)
    assert result.repository_namespace == namespace

def test_scan_repository_keeps_old_calls_and_propagates_new_keyword(
    tmp_path: Path,
) -> None:
    assert scan_repository(tmp_path).repository_namespace is None
    assert scan_repository(
        tmp_path, repository_namespace="opaque:repo"
    ).repository_namespace == "opaque:repo"
```

Keep an existing explicit `FileInventory(...)` construction unchanged and assert its appended field defaults to `None`.

- [ ] **Step 2: Run RED**

Run `python -m pytest tests/test_file_scanner.py -q`.

Expected: missing field/unexpected keyword failures; unrelated scanner tests remain green.

- [ ] **Step 3: Implement the minimal additive surface**

Append the defaulted model field. Add the keyword-only argument to `scan` and `scan_repository`; set `repository_namespace=repository_namespace` only in final `FileInventory` construction and pass it unchanged through the wrapper. Do not validate content or add a Module 1 import.

- [ ] **Step 4: Run GREEN**

Run `python -m pytest tests/test_file_scanner.py -q`; expect all Module 2 tests to pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/file_scanner/models.py backend/file_scanner/scanner.py tests/test_file_scanner.py
git commit -m "feat: propagate repository namespace in scanner"
```

---

### Task 2: Propagate Namespace Through Module 3

**Files:**
- Modify: `backend/code_parser/models.py`
- Modify: `backend/code_parser/parser.py`
- Test: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: Task 1's `FileInventory.repository_namespace`.
- Produces: defaulted `CodeParseInventory.repository_namespace`, copied unchanged.

- [ ] **Step 1: Write failing model/orchestration tests**

```python
def test_code_parse_inventory_namespace_defaults_to_none() -> None:
    result = CodeParseInventory("/repo", 0, 0, 0, 0, 0, (), ())
    assert tuple(result.__dataclass_fields__)[-1] == "repository_namespace"
    assert result.repository_namespace is None

@pytest.mark.parametrize("namespace", ("opaque:A", "opaque:a", ""))
def test_parser_copies_repository_namespace_exactly(
    tmp_path: Path, namespace: str
) -> None:
    (tmp_path / "app.py").write_bytes(b"value = 1\n")
    scanned = FileScanner().scan(tmp_path, repository_namespace=namespace)
    parsed = CodeParser().parse_inventory(scanned)
    assert parsed.repository_namespace == namespace
```

Keep an old explicit construction without the appended argument. Add a malformed inventory test showing a non-`str`/non-`None` namespace raises the existing sanitized `InvalidParseInventory`.

- [ ] **Step 2: Run RED**

Run `python -m pytest tests/test_code_parser.py -q`; expect missing field/copy failures.

- [ ] **Step 3: Append, generically validate, and copy**

Append the field from the interface map. In `_validate_inventory`, require only `isinstance(namespace, (str, type(None)))`; do not reject empty strings because Module 4 owns nonempty validation. Add `repository_namespace=inventory.repository_namespace` to final parse-inventory construction.

- [ ] **Step 4: Run GREEN across Modules 2/3**

Run `python -m pytest tests/test_file_scanner.py tests/test_code_parser.py -q`.

- [ ] **Step 5: Commit**

```powershell
git add backend/code_parser/models.py backend/code_parser/parser.py tests/test_code_parser.py
git commit -m "feat: propagate repository namespace in parser"
```

---

### Task 3: Append the Parsed Source Fingerprint Contract

**Files:**
- Modify: `backend/code_parser/models.py`
- Test: `tests/test_code_parser.py`

**Interfaces:**
- Produces: `ParsedFile.source_sha256: str | None = None` without retaining source.

- [ ] **Step 1: Write failing model tests**

Update exact `ParsedFile` field order to append `source_sha256`, then add:

```python
def test_parsed_file_source_sha256_defaults_to_none() -> None:
    result = ParsedFile(
        "app.py", ParsedLanguage.PYTHON, ParseStatus.SUCCESS, (), (), (), ()
    )
    assert result.source_sha256 is None
    assert not hasattr(result, "source_bytes")
    assert not hasattr(result, "source_text")
```

- [ ] **Step 2: Run RED**

Run the exact model field-order test and new default test; expect missing-field failures.

- [ ] **Step 3: Append the field exactly**

Append `source_sha256: str | None = None` after `issues`. Do not change another model or add source content.

- [ ] **Step 4: Run GREEN**

Run `python -m pytest tests/test_code_parser.py -q`.

- [ ] **Step 5: Commit**

```powershell
git add backend/code_parser/models.py tests/test_code_parser.py
git commit -m "feat: add parsed source fingerprint contract"
```

---

### Task 4: Fingerprint Successful and Partial Parses

**Files:**
- Modify: `backend/code_parser/parser.py`
- Test: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: `SourceBuffer.original_bytes` and Task 3's field.
- Produces: lowercase SHA-256 hex for `SUCCESS` and `PARTIAL`.

- [ ] **Step 1: Write failing five-language tests**

```python
@pytest.mark.parametrize(
    ("filename", "data"),
    (
        ("app.py", b"def run():\n    return 1\n"),
        ("App.java", b"class App { int run() { return 1; } }\n"),
        ("app.js", b"function run() { return 1; }\n"),
        ("app.ts", b"function run(): number { return 1; }\n"),
        ("app.tsx", b"function App() { return <div />; }\n"),
    ),
)
def test_successful_parse_hashes_exact_original_bytes(
    tmp_path: Path, filename: str, data: bytes
) -> None:
    (tmp_path / filename).write_bytes(data)
    parsed = CodeParser().parse_inventory(FileScanner().scan(tmp_path)).files[0]
    assert parsed.source_sha256 == hashlib.sha256(data).hexdigest()
```

Use an existing malformed fixture known to produce `PARTIAL`; assert its digest hashes the complete original bytes.

- [ ] **Step 2: Run RED**

Run the new test nodes; expect every digest to remain `None`.

- [ ] **Step 3: Compute once at the verified-buffer boundary**

Import `sha256` from `hashlib`. Immediately after `source = reader.read(file)` assign:

```python
source_sha256 = sha256(source.original_bytes).hexdigest()
```

Pass it into the successful/partial `ParsedFile`. Never hash `parse_bytes` or decoded text.

- [ ] **Step 4: Run GREEN**

Run `python -m pytest tests/test_code_parser.py -q`.

- [ ] **Step 5: Commit**

```powershell
git add backend/code_parser/parser.py tests/test_code_parser.py
git commit -m "feat: fingerprint verified parsed sources"
```

---

### Task 5: Preserve Digests Across Post-Read Failures

**Files:**
- Modify: `backend/code_parser/parser.py`
- Test: `tests/test_code_parser.py`

**Interfaces:**
- Produces: digest on parser-unavailable/extraction failure; `None` on read failure.

- [ ] **Step 1: Write failing outcome tests**

Extend existing parser-unavailable and throwing-extractor tests with:

```python
assert failed.source_sha256 == hashlib.sha256(data).hexdigest()
```

Extend the existing `FailingReader` test with:

```python
assert result.files[0].source_sha256 is None
```

- [ ] **Step 2: Run RED**

Run those exact tests; expect post-read failure digests to be lost.

- [ ] **Step 3: Add the smallest optional failure path**

Add keyword-only `source_sha256: str | None = None` to `_failed_file` and pass it into `ParsedFile`. Supply the local digest only from parser-unavailable and extraction-exception branches; leave reader-failure calls unchanged.

- [ ] **Step 4: Run GREEN**

Run `python -m pytest tests/test_code_parser.py -q`; confirm existing statuses/issues are unchanged.

- [ ] **Step 5: Commit**

```powershell
git add backend/code_parser/parser.py tests/test_code_parser.py
git commit -m "fix: retain fingerprints after parser failures"
```

---

### Task 6: Lock Exact-Byte Digest Semantics

**Files:**
- Test: `tests/test_code_parser.py`

**Interfaces:**
- Produces: regression evidence for BOM, newline form, mutations, determinism, and no retained bytes.

- [ ] **Step 1: Add exact-byte regression tests**

Create a helper that writes bytes, scans, parses, and returns one `ParsedFile`. Add these concrete assertions:

```python
def test_source_digest_includes_utf8_bom(tmp_path: Path) -> None:
    data = codecs.BOM_UTF8 + b"def run():\n    pass\n"
    parsed = parse_single_bytes(tmp_path, "app.py", data)
    assert parsed.source_sha256 == hashlib.sha256(data).hexdigest()

@pytest.mark.parametrize(
    "data", (b"value = 1\n", b"value = 1\r\n")
)
def test_source_digest_preserves_original_newline_bytes(
    tmp_path: Path, data: bytes
) -> None:
    parsed = parse_single_bytes(tmp_path, "app.py", data)
    assert parsed.source_sha256 == hashlib.sha256(data).hexdigest()
```

Also assert: LF and CRLF digests differ; a one-byte edit changes the digest; equal-length `value = 1` → `value = 2` changes it; repeated parsing is identical; `dataclasses.fields(ParsedFile)` contains no bytes/text field and all public field values contain no source `bytes`.

- [ ] **Step 2: Run parser and dependency regressions**

```powershell
python -m pytest tests/test_code_parser_dependencies.py tests/test_code_parser.py -q
```

Expected: all tests pass; any failure is fixed only in digest propagation, never reader normalization/location logic.

- [ ] **Step 3: Commit**

```powershell
git add tests/test_code_parser.py
git commit -m "test: lock parsed source fingerprint semantics"
```

---

### Task 7: Formalize the Safe Reader Internal Contract

**Files:**
- Modify: `backend/code_parser/reader.py`
- Test: `tests/test_code_parser.py`

**Interfaces:**
- Produces: supported direct internal imports from `backend.code_parser.reader` only.

- [ ] **Step 1: Write the failing boundary test**

```python
def test_reader_declares_supported_cross_module_internal_exports() -> None:
    assert reader_module.__all__ == [
        "SafeSourceReader", "SourceBuffer", "SourceReadError"
    ]
    assert not hasattr(code_parser_package, "SafeSourceReader")
```

- [ ] **Step 2: Run RED**

Run the exact test; expect missing reader `__all__`.

- [ ] **Step 3: Declare the boundary without changing logic**

Add:

```python
__all__ = ["SafeSourceReader", "SourceBuffer", "SourceReadError"]
```

Update only module/class docstrings to identify these as supported internal cross-module interfaces. Do not change `backend/code_parser/__init__.py`, helper exports, or any POSIX/Windows/open/read body.

- [ ] **Step 4: Run every reader/security regression**

Run `python -m pytest tests/test_code_parser.py -q`; all path, link/reparse, identity, size, exact-read, UTF-8, BOM, and race tests must pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/code_parser/reader.py tests/test_code_parser.py
git commit -m "docs: support code parser reader boundary"
```

---

### Task 8: Add Cross-Module Compatibility Integration

**Files:**
- Test: `tests/test_code_parser.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces: end-to-end namespace plus digest evidence.

- [ ] **Step 1: Add the integration test**

```python
def test_scanner_parser_pipeline_preserves_chunker_prerequisites(
    tmp_path: Path,
) -> None:
    namespace = "tracerag-repository-v1:github:https://github.com/acme/example"
    data = b"def run():\r\n    return 1\r\n"
    (tmp_path / "app.py").write_bytes(data)
    scanned = FileScanner().scan(tmp_path, repository_namespace=namespace)
    parsed = CodeParser().parse_inventory(scanned)
    assert scanned.repository_namespace == namespace
    assert parsed.repository_namespace == namespace
    assert parsed.files[0].source_sha256 == hashlib.sha256(data).hexdigest()
```

Add a narrow source guard using each package directory's `rglob("*.py")` and assert no file contains an import from `backend.repository_loader`; exclude caches by operating only on returned `.py` paths.

- [ ] **Step 2: Run Module 1–3 regressions**

```powershell
python -m pytest tests/test_repository_loader.py tests/test_file_scanner.py tests/test_code_parser_dependencies.py tests/test_code_parser.py -q
```

- [ ] **Step 3: Inspect scope**

Run `git diff c3137d4 -- backend requirements.txt`; expect no Module 1/dependency changes and no reader security-body rewrite.

- [ ] **Step 4: Commit**

```powershell
git add tests/test_code_parser.py
git commit -m "test: cover chunker prerequisite propagation"
```

---

### Task 9: Document the Compatibility Contracts

**Files:**
- Modify: `README.md`

**Interfaces:**
- Produces: user-facing Module 2/3 guidance without claiming Module 4 exists.

- [ ] **Step 1: Update the existing Module 2 example**

Document this intended caller-side construction:

```python
repository = RepositoryLoader().load(url)
namespace = "tracerag-repository-v1:github:" + repository.repo_url.casefold()
inventory = FileScanner().scan(
    repository.local_path,
    repository_namespace=namespace,
)
```

State that Module 2 stores it unchanged and old calls default to `None`; it does not import Module 1 or normalize URLs.

- [ ] **Step 2: Update existing Module 3 text**

Document unchanged namespace copying, exact-original-byte digest semantics, `None` before verified read, and supported direct reader imports from `backend.code_parser.reader` rather than the top-level API.

- [ ] **Step 3: Verify and commit**

Run `git diff --check` and inspect `git diff -- README.md`; ensure no Module 4 implementation claim.

```powershell
git add README.md
git commit -m "docs: document chunker prerequisite contracts"
```

---

### Task 10: Full Verification and Independent Review

**Files:**
- Review only: all files changed since `c3137d4`

**Interfaces:**
- Produces: evidence for approval; no merge or push.

- [ ] **Step 1: Run required focused verification**

```powershell
python -m pytest tests/test_file_scanner.py -q
python -m pytest tests/test_code_parser_dependencies.py tests/test_code_parser.py -q
```

- [ ] **Step 2: Run complete verification**

```powershell
python -m pytest -q
python -m compileall -q backend tests
python -m pip check
git diff --check
```

Every command must exit zero; `pip check` must report no broken requirements.

- [ ] **Step 3: Verify scope mechanically**

```powershell
git diff --name-status c3137d4..HEAD
git diff c3137d4..HEAD -- requirements.txt backend/repository_loader tests/test_repository_loader.py
git status --short --branch
```

Expect no dependencies/Module 1 changes, no chunker package/test, and a clean unpushed branch.

- [ ] **Step 4: Request independent review**

Use `superpowers:requesting-code-review` on the complete compatibility diff. Require checks of digest timing, post-read failure propagation, opaque namespace retention, appended defaults/signatures, and absence of reader security changes.

- [ ] **Step 5: Process verified findings only**

Use `superpowers:receiving-code-review`. Reproduce behavior findings with focused failing tests, apply the smallest fix, rerun focused/full verification, and commit each accepted correction separately.

- [ ] **Step 6: Report and stop**

Report commits, exact files, test counts/output, review result, clean status, and no push. Do not merge, begin chunker code, or write the Module 4 implementation plan.

---

## Expected Commit Sequence

```text
feat: propagate repository namespace in scanner
feat: propagate repository namespace in parser
feat: add parsed source fingerprint contract
feat: fingerprint verified parsed sources
fix: retain fingerprints after parser failures
test: lock parsed source fingerprint semantics
docs: support code parser reader boundary
test: cover chunker prerequisite propagation
docs: document chunker prerequisite contracts
```

Review corrections, if any, get separate narrowly named commits. Do not squash during execution.

## Approval and Execution Gate

This plan creates no production compatibility code. After committing it, stop for review. Execution requires explicit approval and an isolated worktree created with `superpowers:using-git-worktrees`. Module 4 implementation planning remains blocked until this compatibility milestone is implemented, independently reviewed, fully verified, and approved.
