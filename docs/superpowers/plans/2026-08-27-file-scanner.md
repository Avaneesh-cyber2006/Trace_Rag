# TraceRAG Module 2: File Scanner & Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a safe, deterministic File Scanner & Filter that turns an existing local repository directory into a classified `FileInventory` for later TraceRAG modules.

**Architecture:** Keep immutable public contracts in `models.py`, module errors in `exceptions.py`, pure name/content rules in `filters.py`, pure language/category rules in `classifier.py`, and all filesystem effects in `scanner.py`. `FileScanner` performs iterative non-following traversal, applies cheap filters before metadata and bounded prefix reads, and returns independently sorted included, ignored, and skipped-directory tuples.

**Tech Stack:** Python 3.11+, standard library (`dataclasses`, `enum`, `logging`, `os`, `pathlib`, `stat`, `unicodedata`), pytest 8+

**Spec:** `docs/superpowers/specs/2026-08-27-file-scanner-design.md`

## Global Constraints

- Preserve all files and public behavior under `backend/repository_loader/`; Module 1 is read-only for this implementation.
- Support Python 3.11+ on Windows 10/11 and Linux.
- Add no runtime dependency; the Python standard library is sufficient.
- Accept an existing `Path | str`; do not require `.git`, network access, GitHub, or a `RepositoryInfo` instance.
- Never execute, import, evaluate, deserialize, install, build, test, or invoke repository-controlled content.
- Never follow symlinks, junctions, or detectable reparse points.
- Never read more than `binary_sample_size` bytes from an eligible file and never open an oversized file.
- Preserve original path spelling but expose child paths in repository-relative POSIX form.
- Sort results by `(relative_path.casefold(), relative_path)`.
- Preserve `total_files_seen == included_files + ignored_files` for every successful scan.
- Use explicit RED then GREEN verification for every behavior; do not write production behavior before its failing test.
- Run tests with the available Python 3.11+ interpreter. If `python` is unavailable, locate the project interpreter before implementation rather than skipping tests.
- Keep commits focused; do not merge into `main` during implementation.
- Do not implement AST parsing, chunking, embeddings, RAG, Git history, runtime analysis, Module 3, APIs, frontend, authentication, private-repository support, or comprehensive secret scanning.

---

## File Responsibility Map

```text
backend/file_scanner/__init__.py
    Public imports and __all__; no behavior.

backend/file_scanner/models.py
    FileCategory, IgnoreReason, SkippedDirectoryReason, ScannedFile,
    IgnoredFile, SkippedDirectory, and FileInventory.

backend/file_scanner/exceptions.py
    FileScannerError, InvalidRepositoryPath, RepositoryScanError,
    and ScannerConfigurationError.

backend/file_scanner/filters.py
    Immutable allow/ignore constants, filename-policy decisions,
    reparse-point detection helper, and deterministic binary heuristic.

backend/file_scanner/classifier.py
    Pure detect_language() and classify_file() rules.

backend/file_scanner/scanner.py
    Configuration validation, root validation, iterative os.scandir traversal,
    size/sample orchestration, recoverable error reporting, counters, sorting,
    logging, FileScanner, and scan_repository().

tests/test_file_scanner.py
    Offline unit and filesystem behavior tests using tmp_path.

README.md
    Module 2 purpose, usage, contract, configuration, security, and limits;
    existing Module 1 text remains intact.
```

---

### Task 1: Public Models, Errors, Configuration, and Root Validation

**Files:**
- Create: `backend/file_scanner/__init__.py`
- Create: `backend/file_scanner/models.py`
- Create: `backend/file_scanner/exceptions.py`
- Create: `backend/file_scanner/scanner.py`
- Create: `tests/test_file_scanner.py`

**Interfaces:**
- Consumes: `Path | str` and constructor integers.
- Produces: the exact enums/dataclasses in design Section 5; `FileScanner.__init__(max_file_size_bytes: int = 1_000_000, binary_sample_size: int = 8192)`; internal `_validate_repository_path(repository_path: Path | str) -> Path`; `ScannerConfigurationError`; `InvalidRepositoryPath`; `RepositoryScanError`.

- [ ] **Step 1: Write failing model and configuration tests**

Add literal enum-value, frozen-model, default-configuration, and invalid-configuration cases:

```python
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from backend.file_scanner import (
    FileCategory,
    FileScanner,
    IgnoreReason,
    ScannedFile,
    ScannerConfigurationError,
    SkippedDirectoryReason,
)


def test_public_enum_values_are_stable() -> None:
    assert FileCategory.SOURCE.value == "source"
    assert IgnoreReason.SENSITIVE_FILE.value == "sensitive_file"
    assert SkippedDirectoryReason.IGNORED_DIRECTORY.value == "ignored_directory"


def test_scanned_file_is_immutable() -> None:
    result = ScannedFile("app.py", "app.py", ".py", "python", FileCategory.SOURCE, 3)
    with pytest.raises(FrozenInstanceError):
        result.size_bytes = 4  # type: ignore[misc]


def test_scanner_defaults_are_exact() -> None:
    scanner = FileScanner()
    assert scanner.max_file_size_bytes == 1_000_000
    assert scanner.binary_sample_size == 8192


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "8192"])
@pytest.mark.parametrize("field", ["max_file_size_bytes", "binary_sample_size"])
def test_scanner_rejects_invalid_configuration(field: str, value: object) -> None:
    arguments = {field: value}
    with pytest.raises(ScannerConfigurationError):
        FileScanner(**arguments)  # type: ignore[arg-type]
```

- [ ] **Step 2: Run the focused tests to verify RED**

Run: `python -m pytest tests/test_file_scanner.py -q`

Expected: test collection fails because `backend.file_scanner` does not exist.

- [ ] **Step 3: Add the minimal public contracts and constructor**

Create the exact frozen, slotted models from the approved spec and this exception hierarchy:

```python
class FileScannerError(Exception):
    """Base class for File Scanner & Filter failures."""


class InvalidRepositoryPath(FileScannerError):
    """Raised when the supplied scan root is missing or unusable."""


class RepositoryScanError(FileScannerError):
    """Raised when repository traversal cannot safely proceed."""


class ScannerConfigurationError(FileScannerError):
    """Raised when scanner configuration is invalid."""
```

Implement only constructor validation in `scanner.py`; leave `scan()` raising `NotImplementedError` until its RED task:

```python
class FileScanner:
    def __init__(self, max_file_size_bytes: int = 1_000_000, binary_sample_size: int = 8192) -> None:
        for name, value in (
            ("max_file_size_bytes", max_file_size_bytes),
            ("binary_sample_size", binary_sample_size),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ScannerConfigurationError(f"{name} must be a positive integer.")
        self.max_file_size_bytes = max_file_size_bytes
        self.binary_sample_size = binary_sample_size
```

- [ ] **Step 4: Run model/configuration tests to verify GREEN**

Run: `python -m pytest tests/test_file_scanner.py -q`

Expected: all currently collected tests pass.

- [ ] **Step 5: Write failing root-validation tests**

Add valid-directory, nonexistent, file-as-root, resolution-loop/error, and non-directory cases. Patch the narrow `Path.resolve` boundary only for the otherwise unsafe resolution failure:

```python
def test_validate_repository_path_returns_strictly_resolved_directory(tmp_path: Path) -> None:
    scanner = FileScanner()
    assert scanner._validate_repository_path(tmp_path) == tmp_path.resolve(strict=True)


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_scan_rejects_invalid_repository_path(tmp_path: Path, kind: str) -> None:
    candidate = tmp_path / "candidate"
    if kind == "file":
        candidate.write_text("not a directory", encoding="utf-8")
    with pytest.raises(InvalidRepositoryPath):
        FileScanner().scan(candidate)


def test_resolution_failure_is_translated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_resolve(self: Path, strict: bool = False) -> Path:
        raise RuntimeError("simulated resolution loop")

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    with pytest.raises(InvalidRepositoryPath, match="resolve"):
        FileScanner().scan(tmp_path)
```

- [ ] **Step 6: Run root-validation tests to verify RED**

Run: `python -m pytest tests/test_file_scanner.py -q -k "repository_path or resolution_failure"`

Expected: FAIL because `_validate_repository_path`/`scan` root validation is missing.

- [ ] **Step 7: Implement strict root validation and an empty-root result**

Translate `(OSError, RuntimeError)` from `Path.resolve(strict=True)`, reject non-directories, and make `scan(empty_directory)` return an empty `FileInventory` with the resolved root and zero counters. Do not add traversal of child entries yet.

- [ ] **Step 8: Run Task 1 tests to verify GREEN**

Run: `python -m pytest tests/test_file_scanner.py -q`

Expected: all Task 1 tests pass.

- [ ] **Step 9: Commit Task 1**

```bash
git add backend/file_scanner tests/test_file_scanner.py
git commit -m "feat: define file scanner contracts"
```

---

### Task 2: Deterministic Filename Policy and Ignore Precedence

**Files:**
- Create: `backend/file_scanner/filters.py`
- Modify: `tests/test_file_scanner.py`

**Interfaces:**
- Consumes: a filename string.
- Produces: `classify_filename(filename: str) -> IgnoreReason | None`; `is_supported_filename(filename: str) -> bool`; immutable `frozenset` defaults for supported extensions, special filenames, lockfiles, ignored directory names, and sensitive suffixes.

- [ ] **Step 1: Write failing sensitive-file and safe-template tests**

Parameterize exact case-insensitive behavior and prove the helper makes no content access:

```python
@pytest.mark.parametrize(
    "filename",
    [".env", ".env.production", "APP.ENV", "app.env.local", "private.pem", "ID_RSA", "credentials.json", "service-account.json"],
)
def test_sensitive_filenames_are_rejected(filename: str) -> None:
    assert classify_filename(filename) is IgnoreReason.SENSITIVE_FILE


@pytest.mark.parametrize(
    "filename",
    [".env.example", ".ENV.SAMPLE", "app.env.template"],
)
def test_safe_environment_templates_are_supported(filename: str) -> None:
    assert classify_filename(filename) is None
    assert is_supported_filename(filename)
```

- [ ] **Step 2: Write failing lockfile, minified, unsupported, and allowlist tests**

Cover all requested families and the exact precedence:

```python
@pytest.mark.parametrize("filename", ["package-lock.json", "YARN.LOCK", "poetry.lock", "Cargo.lock"])
def test_lockfiles_have_specific_reason(filename: str) -> None:
    assert classify_filename(filename) is IgnoreReason.LOCKFILE


@pytest.mark.parametrize("filename", ["app.min.js", "STYLES.MIN.CSS"])
def test_minified_files_win_before_type_checks(filename: str) -> None:
    assert classify_filename(filename) is IgnoreReason.MINIFIED


@pytest.mark.parametrize("filename", ["logo.png", "diagram.svg", "archive.zip", "data.sqlite", "notes.unknown"])
def test_unsupported_types_are_rejected(filename: str) -> None:
    assert classify_filename(filename) is IgnoreReason.UNSUPPORTED_TYPE


@pytest.mark.parametrize(
    "filename",
    ["app.py", "App.TSX", "README.md", "package.json", "pyproject.toml", "Dockerfile", ".gitignore"],
)
def test_supported_source_manifest_doc_and_hidden_files(filename: str) -> None:
    assert classify_filename(filename) is None
```

- [ ] **Step 3: Run filename-policy tests to verify RED**

Run: `python -m pytest tests/test_file_scanner.py -q -k "filename or lockfile or minified or unsupported or environment"`

Expected: FAIL because `filters.py` and its functions do not exist.

- [ ] **Step 4: Implement immutable rules and filename precedence**

Use `filename.casefold()` and final lowercase suffix. Implement the exact order `SENSITIVE_FILE -> LOCKFILE -> MINIFIED -> UNSUPPORTED_TYPE`; symlink, size, unreadable, and binary decisions remain scanner concerns. Safe environment templates override the general environment-sensitive pattern. Include every extension/special filename from design Section 10.

- [ ] **Step 5: Run filename-policy tests to verify GREEN**

Run: `python -m pytest tests/test_file_scanner.py -q -k "filename or lockfile or minified or unsupported or environment"`

Expected: all focused tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add backend/file_scanner/filters.py tests/test_file_scanner.py
git commit -m "feat: add file scanner filename policy"
```

---

### Task 3: Binary Heuristic

**Files:**
- Modify: `backend/file_scanner/filters.py`
- Modify: `tests/test_file_scanner.py`

**Interfaces:**
- Consumes: a bounded `bytes` sample.
- Produces: `is_binary_sample(sample: bytes) -> bool` implementing the exact approved BOM/NUL/UTF-8/control-ratio rules.

- [ ] **Step 1: Write failing text and BOM tests**

```python
@pytest.mark.parametrize(
    "sample",
    [
        b"",
        b"answer = 42\n",
        "नमस्ते TraceRAG\n".encode("utf-8"),
        b"\xef\xbb\xbfhello\n",
        "hello\n".encode("utf-16"),
        b"caf\xe9\n",
    ],
)
def test_binary_heuristic_accepts_expected_text(sample: bytes) -> None:
    assert not is_binary_sample(sample)
```

- [ ] **Step 2: Write failing binary and threshold tests**

```python
@pytest.mark.parametrize("sample", [b"text\x00payload", b"\xff\x00\x10\x01", b"\xff\xfe\x00"])
def test_binary_heuristic_rejects_binary_samples(sample: bytes) -> None:
    assert is_binary_sample(sample)


def test_exactly_thirty_percent_controls_is_text() -> None:
    assert not is_binary_sample(b"\x01\x02\x03abcdefg")


def test_more_than_thirty_percent_controls_is_binary() -> None:
    assert is_binary_sample(b"\x01\x02\x03\x04abcdef")
```

- [ ] **Step 3: Run binary tests to verify RED**

Run: `python -m pytest tests/test_file_scanner.py -q -k binary_heuristic`

Expected: FAIL because `is_binary_sample` does not exist.

- [ ] **Step 4: Implement the minimal deterministic heuristic**

Check BOMs before the general NUL rule, decode BOM-marked samples strictly, attempt strict UTF-8 without a BOM, and use `unicodedata.category` for decoded controls. For invalid UTF-8, count disallowed C0 bytes and DEL; compare strictly with `> 0.30`. Keep allowed whitespace exactly tab, LF, CR, form feed, and backspace. Never perform I/O in this helper.

- [ ] **Step 5: Run binary tests to verify GREEN**

Run: `python -m pytest tests/test_file_scanner.py -q -k binary_heuristic`

Expected: all binary heuristic tests pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add backend/file_scanner/filters.py tests/test_file_scanner.py
git commit -m "feat: detect likely binary samples"
```

---

### Task 4: Language Detection and Category Precedence

**Files:**
- Create: `backend/file_scanner/classifier.py`
- Modify: `tests/test_file_scanner.py`

**Interfaces:**
- Consumes: repository-relative POSIX path, filename, and normalized extension.
- Produces: `detect_language(filename: str, extension: str) -> str | None`; `classify_file(relative_path: str, filename: str, extension: str) -> FileCategory`.

- [ ] **Step 1: Write failing language mapping tests**

Parameterize every representative mapping, including `.h -> "c/cpp"`, special filenames, uppercase extensions, SQL/Prisma, and `None` for documentation/config:

```python
@pytest.mark.parametrize(
    ("filename", "extension", "expected"),
    [
        ("app.py", ".py", "python"),
        ("App.TSX", ".tsx", "typescript"),
        ("main.cpp", ".cpp", "cpp"),
        ("types.h", ".h", "c/cpp"),
        ("Dockerfile", "", "dockerfile"),
        ("Makefile", "", "make"),
        ("schema.prisma", ".prisma", "prisma"),
        ("README.md", ".md", None),
    ],
)
def test_detect_language(filename: str, extension: str, expected: str | None) -> None:
    assert detect_language(filename, extension) == expected
```

- [ ] **Step 2: Write failing category and precedence tests**

```python
@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/config.json", FileCategory.TEST),
        ("src/AuthServiceTest.java", FileCategory.TEST),
        ("src/auth.spec.js", FileCategory.TEST),
        ("migrations/001_create.sql", FileCategory.DATABASE),
        ("schema.prisma", FileCategory.DATABASE),
        ("package.json", FileCategory.BUILD),
        ("Dockerfile", FileCategory.BUILD),
        (".github/workflows/ci.yml", FileCategory.CONFIG),
        (".env.example", FileCategory.CONFIG),
        ("README.md", FileCategory.DOCUMENTATION),
        ("src/app.py", FileCategory.SOURCE),
        ("notes.txt", FileCategory.OTHER_TEXT),
    ],
)
def test_category_precedence(path: str, expected: FileCategory) -> None:
    filename = PurePosixPath(path).name
    extension = PurePosixPath(path).suffix.casefold()
    assert classify_file(path, filename, extension) is expected
```

- [ ] **Step 3: Run classifier tests to verify RED**

Run: `python -m pytest tests/test_file_scanner.py -q -k "detect_language or category_precedence"`

Expected: FAIL because `classifier.py` does not exist.

- [ ] **Step 4: Implement pure case-insensitive classifier rules**

Use complete case-folded POSIX path components. Apply exactly `TEST -> DATABASE -> BUILD -> CONFIG -> DOCUMENTATION -> SOURCE -> OTHER_TEXT`. Restrict filename test patterns to the approved conventions and keep path-component matching exact so `contest` and `specification` do not become test directories.

- [ ] **Step 5: Run classifier tests to verify GREEN**

Run: `python -m pytest tests/test_file_scanner.py -q -k "detect_language or category_precedence"`

Expected: all classifier tests pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add backend/file_scanner/classifier.py tests/test_file_scanner.py
git commit -m "feat: classify scanned text files"
```

---

### Task 5: Iterative Traversal, Directory Pruning, Ordering, and Counters

**Files:**
- Modify: `backend/file_scanner/scanner.py`
- Modify: `tests/test_file_scanner.py`

**Interfaces:**
- Consumes: validated root, filename policy, language/category helpers.
- Produces: working `FileScanner.scan(repository_path: Path | str) -> FileInventory` for regular readable text files; internal `_sort_key(relative_path: str) -> tuple[str, str]`; directory pruning and counter invariants.

- [ ] **Step 1: Write failing basic-scan and POSIX-path tests**

Create files in deliberately scrambled order and assert complete `ScannedFile` values:

```python
def test_scan_returns_classified_files_in_deterministic_posix_order(tmp_path: Path) -> None:
    write_bytes(tmp_path / "src" / "service.py", b"service = True\n")
    write_bytes(tmp_path / "README.md", "Résumé\n".encode("utf-8"))
    write_bytes(tmp_path / "src" / "app.py", b"answer = 42\n")

    inventory = FileScanner().scan(tmp_path)

    assert [file.relative_path for file in inventory.files] == [
        "README.md", "src/app.py", "src/service.py"
    ]
    assert all("\\" not in file.relative_path for file in inventory.files)
    assert inventory.repository_path == str(tmp_path.resolve())
    assert inventory.included_files == 3
    assert inventory.ignored_files == 0
    assert inventory.total_files_seen == 3
```

- [ ] **Step 2: Write failing directory-pruning tests**

Parameterize every default ignored directory and prove descendants do not become ignored files or counters. Add allowed lookalikes and `.github`:

```python
@pytest.mark.parametrize("directory", sorted(DEFAULT_IGNORED_DIRECTORIES))
def test_ignored_directory_is_pruned_once(tmp_path: Path, directory: str) -> None:
    write_bytes(tmp_path / directory / "nested" / "payload.py", b"outside = True\n")
    inventory = FileScanner().scan(tmp_path)
    assert inventory.total_files_seen == 0
    assert inventory.ignored == ()
    assert inventory.skipped_directories == (
        SkippedDirectory(directory, SkippedDirectoryReason.IGNORED_DIRECTORY),
    )


@pytest.mark.parametrize("directory", ["builder", "distribution", "targeting", ".github"])
def test_directory_lookalikes_are_not_pruned(tmp_path: Path, directory: str) -> None:
    write_bytes(tmp_path / directory / "config.yml", b"enabled: true\n")
    assert FileScanner().scan(tmp_path).included_files == 1
```

- [ ] **Step 3: Write failing ignored-file and counter tests**

Mix included, sensitive, lockfile, minified, unsupported, and pruned content. Assert exact reasons and:

```python
assert inventory.included_files == len(inventory.files)
assert inventory.ignored_files == len(inventory.ignored)
assert inventory.total_files_seen == inventory.included_files + inventory.ignored_files
```

- [ ] **Step 4: Run traversal tests to verify RED**

Run: `python -m pytest tests/test_file_scanner.py -q -k "scan_returns or pruned or lookalikes or counter"`

Expected: FAIL because `scan()` does not enumerate entries.

- [ ] **Step 5: Implement iterative non-following traversal for regular files**

Use a pending-directory stack and `os.scandir`. Derive relative paths from root-relative traversal components and call `.as_posix()`. Match ignored directory names by `entry.name.casefold()`. For ordinary regular files in this task, apply filename policy, obtain non-following size metadata for the `ScannedFile`, apply the classifier, and append one included or ignored result. Do not open file content or implement size/binary rejection yet; those behaviors begin with RED tests in Task 6. Defer sorting until the end.

At this task boundary, link-specific handling may conservatively record links as ignored/skipped; Task 7 will harden and verify all link/reparse behavior. Do not follow any link while reaching GREEN.

- [ ] **Step 6: Run traversal tests to verify GREEN**

Run: `python -m pytest tests/test_file_scanner.py -q -k "scan_returns or pruned or lookalikes or counter"`

Expected: all focused traversal tests pass.

- [ ] **Step 7: Run all Module 2 tests**

Run: `python -m pytest tests/test_file_scanner.py -q`

Expected: all tests written through Task 5 pass.

- [ ] **Step 8: Commit Task 5**

```bash
git add backend/file_scanner/scanner.py tests/test_file_scanner.py
git commit -m "feat: scan and prune repository files"
```

---

### Task 6: Size Filtering, Bounded Reads, and Recoverable Failures

**Files:**
- Modify: `backend/file_scanner/scanner.py`
- Modify: `tests/test_file_scanner.py`

**Interfaces:**
- Consumes: `max_file_size_bytes`, `binary_sample_size`, file metadata, `is_binary_sample`.
- Produces: `TOO_LARGE`, `BINARY`, and `UNREADABLE` outcomes; fatal `RepositoryScanError` for root enumeration failure; recoverable `SkippedDirectory(..., UNREADABLE)` for descendant enumeration failure.

- [ ] **Step 1: Write failing size-boundary and no-open tests**

Use a small configured limit and patch only the target path's open boundary:

```python
def test_oversized_file_is_ignored_without_opening(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "large.py"
    target.write_bytes(b"x" * 11)
    original_open = Path.open

    def guarded_open(self: Path, *args: object, **kwargs: object):
        if self == target:
            raise AssertionError("oversized file must not be opened")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    inventory = FileScanner(max_file_size_bytes=10).scan(tmp_path)
    assert inventory.ignored == (IgnoredFile("large.py", IgnoreReason.TOO_LARGE),)


def test_file_exactly_at_size_limit_is_included(tmp_path: Path) -> None:
    (tmp_path / "limit.py").write_bytes(b"x" * 10)
    assert FileScanner(max_file_size_bytes=10).scan(tmp_path).included_files == 1
```

- [ ] **Step 2: Write failing bounded-read and binary integration tests**

Use a recording binary stream or patch `Path.open` for one eligible path. Assert the scanner calls exactly one `read(4)` when `binary_sample_size=4`. Separately assert `payload.txt` containing a NUL is `BINARY`, while Unicode, BOM, and zero-byte supported files remain included.

- [ ] **Step 3: Write failing per-file and descendant-directory recovery tests**

Patch `Path.stat`, `Path.open`, or the module's narrow scandir wrapper for one discovered descendant. Assert one `IgnoredFile(..., UNREADABLE)` or `SkippedDirectory(..., UNREADABLE)`, continued inclusion of siblings, and intact counters. Patch root enumeration separately and assert `RepositoryScanError` with no raw `OSError` escaping.

- [ ] **Step 4: Run filtering/recovery tests to verify RED**

Run: `python -m pytest tests/test_file_scanner.py -q -k "oversized or size_limit or bounded_read or payload or unreadable or enumeration"`

Expected: at least the no-open, bounded-read, and translated-failure assertions fail.

- [ ] **Step 5: Implement size-first sampling and error translation**

Call non-following stat once for eligible regular files, reject `st_size > limit`, and only then open in `rb` and call `read(binary_sample_size)` once. Catch per-file `(OSError, RuntimeError)` as `UNREADABLE`. Distinguish the initial root `os.scandir` failure (`RepositoryScanError`) from descendant failures (`SkippedDirectoryReason.UNREADABLE`). Do not include raw exception text in public messages or file contents in logs.

- [ ] **Step 6: Run filtering/recovery tests to verify GREEN**

Run: `python -m pytest tests/test_file_scanner.py -q -k "oversized or size_limit or bounded_read or payload or unreadable or enumeration"`

Expected: all focused tests pass.

- [ ] **Step 7: Commit Task 6**

```bash
git add backend/file_scanner/scanner.py tests/test_file_scanner.py
git commit -m "feat: bound file inspection safely"
```

---

### Task 7: Symlink, Escape, Broken-Link, and Windows Reparse-Point Safety

**Files:**
- Modify: `backend/file_scanner/filters.py`
- Modify: `backend/file_scanner/scanner.py`
- Modify: `tests/test_file_scanner.py`

**Interfaces:**
- Consumes: `os.DirEntry`, non-following metadata, optional Windows `st_file_attributes`.
- Produces: internal `is_link_or_reparse(entry: os.DirEntry[str]) -> bool`; `IgnoredFile(..., SYMLINK)` for file-like links/reparse points; `SkippedDirectory(..., SYMLINK)` for directory-like links/reparse points; no target traversal or opening.

- [ ] **Step 1: Write failing file-symlink and broken-link tests**

Create an outside target and an internal target. Skip only on `(OSError, NotImplementedError)` from link creation. Assert both links are ignored, their target content is never duplicated, broken links are ignored, and file-like links increment seen/ignored counters once.

- [ ] **Step 2: Write failing directory escape and cycle tests**

Create `repo/outside_link -> outside_directory` and, where possible, `repo/loop -> repo`. Assert each appears exactly once as a link exclusion, no outside target file is discovered, and scanning terminates. If non-following metadata identifies the entry itself as directory-like, expect `SkippedDirectory(..., SYMLINK)`; otherwise expect the conservative `IgnoredFile(..., SYMLINK)` fallback.

- [ ] **Step 3: Write failing reparse-bit unit test**

Avoid requiring administrative junction creation for the core rule. Pass a fake entry whose non-following stat has `st_file_attributes = stat.FILE_ATTRIBUTE_REPARSE_POINT` and assert `is_link_or_reparse(fake_entry)` is true even when `is_symlink()` is false. Add an optional real Windows junction test guarded by `sys.platform == "win32"` and a creation-permission skip.

- [ ] **Step 4: Run link-safety tests to verify RED**

Run: `python -m pytest tests/test_file_scanner.py -q -k "symlink or broken_link or escape or cycle or reparse"`

Expected: FAIL until all links/reparse points are detected before type checks or descent.

- [ ] **Step 5: Implement link/reparse detection before traversal decisions**

Check `entry.is_symlink()` first. Obtain non-following stat and, when present, mask `st_file_attributes` with `stat.FILE_ATTRIBUTE_REPARSE_POINT`. Never call `resolve()` on a child link, never call `is_dir(follow_symlinks=True)`, and never open a linked target. Determine file-like versus directory-like reporting from non-following metadata/entry information only; if target kind is unknowable—as it normally is for a POSIX symlink—conservatively use `IgnoredFile(..., SYMLINK)` so the entry participates in the file counter invariant without traversal.

- [ ] **Step 6: Run link-safety tests to verify GREEN**

Run: `python -m pytest tests/test_file_scanner.py -q -k "symlink or broken_link or escape or cycle or reparse"`

Expected: all supported link tests pass and unavailable platform operations are explicitly skipped.

- [ ] **Step 7: Re-run ordering and counter tests with links present**

Run: `python -m pytest tests/test_file_scanner.py -q -k "counter or deterministic or posix or symlink"`

Expected: all focused tests pass; directory links remain outside counters and file-like links preserve the invariant.

- [ ] **Step 8: Commit Task 7**

```bash
git add backend/file_scanner/filters.py backend/file_scanner/scanner.py tests/test_file_scanner.py
git commit -m "fix: prevent file scanner link traversal"
```

---

### Task 8: Public API, Logging, and Module 1 Integration

**Files:**
- Modify: `backend/file_scanner/__init__.py`
- Modify: `backend/file_scanner/scanner.py`
- Modify: `tests/test_file_scanner.py`

**Interfaces:**
- Consumes: all completed Module 2 contracts and `RepositoryInfo.local_path` as a plain string.
- Produces: explicit package `__all__`; `scan_repository(repository_path: Path | str, max_file_size_bytes: int = 1_000_000, binary_sample_size: int = 8192) -> FileInventory`; lifecycle logging; proven Module 1-to-Module 2 handoff without coupling.

- [ ] **Step 1: Write failing public-export and convenience-function tests**

Assert every public model, enum, error, `FileScanner`, and `scan_repository` is importable from `backend.file_scanner`. Patch `FileScanner.scan` narrowly and assert the wrapper passes exact configuration and path values.

- [ ] **Step 2: Write failing Module 1 contract integration test**

Construct the existing frozen `RepositoryInfo` directly—do not clone and do not modify Module 1:

```python
def test_scanner_accepts_repository_info_local_path(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("answer = 42\n", encoding="utf-8")
    info = RepositoryInfo(
        success=True,
        owner="acme",
        repository_name="example",
        repo_url="https://github.com/acme/example",
        local_path=str(tmp_path),
        branch="main",
        current_commit="a" * 40,
        total_files=1,
        repository_size_bytes=12,
        reused_existing_clone=False,
    )
    inventory = FileScanner().scan(info.local_path)
    assert inventory.files[0].relative_path == "app.py"
```

Also assert `backend.repository_loader.RepositoryInfo` field values and public imports remain unchanged.

- [ ] **Step 3: Write failing logging tests**

With `caplog`, assert INFO contains start and aggregate completion but not per-file payload bytes or secret values. Assert per-file exclusions are DEBUG and descendant traversal failures are WARNING. Use sentinel secret content and verify it is absent from every log record.

- [ ] **Step 4: Run API/integration/logging tests to verify RED**

Run: `python -m pytest tests/test_file_scanner.py -q -k "public or convenience or repository_info or logging"`

Expected: FAIL for missing wrapper/exports or lifecycle log assertions.

- [ ] **Step 5: Implement exports, wrapper, and aggregate logging**

Mirror Module 1's explicit `__all__`. The wrapper must instantiate `FileScanner(max_file_size_bytes, binary_sample_size)` and call `.scan(repository_path)`. Use `logger = logging.getLogger(__name__)`; INFO only for lifecycle/aggregate events, DEBUG for per-file decisions, and WARNING for partial descendant traversal. Never log contents or sampled bytes.

- [ ] **Step 6: Run API/integration/logging tests to verify GREEN**

Run: `python -m pytest tests/test_file_scanner.py -q -k "public or convenience or repository_info or logging"`

Expected: all focused tests pass.

- [ ] **Step 7: Run Module 1 and Module 2 offline suites together**

Run: `python -m pytest tests/test_repository_loader.py tests/test_file_scanner.py -q`

Expected: all offline tests pass; the existing integration marker remains deselected; no Module 1 test changes are required.

- [ ] **Step 8: Commit Task 8**

```bash
git add backend/file_scanner tests/test_file_scanner.py
git commit -m "feat: expose file scanner public API"
```

---

### Task 9: README Documentation and Complete Behavior Matrix

**Files:**
- Modify: `README.md`
- Modify: `tests/test_file_scanner.py`

**Interfaces:**
- Consumes: completed Module 2 public API.
- Produces: documented usage/security/limitations and any missing parameterized regression cases required by the approved design.

- [ ] **Step 1: Audit the test matrix against design Section 24**

Add explicit parameterized cases not already exercised for: every source extension family, every unsupported binary/media/archive/font/database family, all lockfiles/manifests, all classification patterns, case-insensitive matching, hidden useful files, ignore-reason precedence (`.env.production`, `package-lock.json`, `huge.min.js`, `huge.png`, oversized `data.json`), and independent sorting of `files`, `ignored`, and `skipped_directories`.

- [ ] **Step 2: Run the new behavior-matrix tests to verify RED where coverage exposes gaps**

Run: `python -m pytest tests/test_file_scanner.py -q`

Expected: any newly exposed missing rule fails with a literal expected enum/category/path; if all cases already pass, record that the tests extend coverage without production changes.

- [ ] **Step 3: Make only minimal production corrections required by RED cases**

Change only the responsible constant or pure rule in `filters.py`/`classifier.py`, or the narrow scanner behavior demonstrated by the failure. Do not refactor Module 1 or add Module 3 behavior.

- [ ] **Step 4: Run the complete Module 2 suite to verify GREEN**

Run: `python -m pytest tests/test_file_scanner.py -q`

Expected: all Module 2 tests pass with platform-unavailable link tests explicitly skipped.

- [ ] **Step 5: Extend README without rewriting Module 1**

Add `## Module 2 — File Scanner & Filter` after the existing Module 1 material. Include:

```python
from backend.file_scanner import FileScanner
from backend.repository_loader import RepositoryLoader

repository = RepositoryLoader().load("https://github.com/pallets/flask")
inventory = FileScanner(max_file_size_bytes=1_000_000).scan(repository.local_path)
```

Document categories, representative supported types, common pruned directories, `SENSITIVE_FILE` behavior and safe `.env` templates, 1,000,000-byte default, 8192-byte binary sample, all-link exclusion, deterministic POSIX paths, exact counter invariant, Module 1 counter difference, standard-library-only behavior, snapshot/TOCTOU limitation, and explicit Module 3+ non-goals.

- [ ] **Step 6: Run both offline suites after documentation changes**

Run: `python -m pytest tests/test_repository_loader.py tests/test_file_scanner.py -q`

Expected: all offline tests pass and Module 1 remains unchanged.

- [ ] **Step 7: Commit Task 9**

```bash
git add README.md backend/file_scanner tests/test_file_scanner.py
git commit -m "docs: document file scanner module"
```

---

### Task 10: Final Security and Verification Gate

**Files:**
- Modify only Module 2 files required to correct a freshly reproduced failure.
- Do not modify: `backend/repository_loader/**`, `tests/test_repository_loader.py`, or the approved spec.

**Interfaces:**
- Consumes: completed Module 2 implementation and documentation.
- Produces: fresh verification evidence and a reviewable implementation branch/worktree ready for integration decision.

- [ ] **Step 1: Verify scope and Module 1 preservation**

Run:

```bash
$traceRagBase = git merge-base HEAD main
git diff --name-only "$traceRagBase...HEAD"
git diff --exit-code "$traceRagBase...HEAD" -- backend/repository_loader tests/test_repository_loader.py
```

Expected: changes are limited to `backend/file_scanner/**`, `tests/test_file_scanner.py`, `README.md`, and approved plan artifacts; the Module 1 diff command exits zero.

- [ ] **Step 2: Run static repository checks**

Run:

```bash
git diff --check "$traceRagBase...HEAD"
python -m compileall -q backend tests
```

Expected: both commands exit zero with no syntax or whitespace errors.

- [ ] **Step 3: Run the complete offline suite fresh**

Run: `python -m pytest -q`

Expected: all offline tests pass; only explicitly unsupported platform link tests and the existing network integration test may be skipped/deselected.

- [ ] **Step 4: Run focused security and invariant tests fresh**

Run:

```bash
python -m pytest tests/test_file_scanner.py -q -k "sensitive or oversized or bounded_read or binary or symlink or reparse or escape or resolution or enumeration or counter or deterministic"
```

Expected: all selected supported-platform tests pass; no outside target is traversed, no sensitive/oversized content is opened, reads are bounded, and every inventory satisfies the counter invariant.

- [ ] **Step 5: Inspect for forbidden behavior and dependencies**

Run:

```bash
rg -n "subprocess|shell=True|eval\(|exec\(|pickle|yaml\.load|requests|httpx|GitPython|from git|import git|tree_sitter|embedding|gemini" backend/file_scanner tests/test_file_scanner.py
git diff "$traceRagBase...HEAD" -- requirements.txt
```

Expected: no forbidden runtime behavior or new dependency appears. Test references that assert absence must be reviewed in context rather than treated as production matches.

- [ ] **Step 6: Inspect final status and commit any verification-only correction**

Run: `git status --short`

Expected: clean worktree. If verification found a defect, first add a focused failing regression test, observe RED, make the minimal Module 2 fix, observe GREEN, rerun Steps 1–5, and commit only that correction.

- [ ] **Step 7: Stop before integration**

Report exact test counts, skips/deselections, commit list, changed-file list, and any remaining platform limitation. Do not merge, push, create a PR, or begin Module 3 without explicit user direction and the finishing-development-branch workflow.

---

## Planned Commit Sequence

```text
feat: define file scanner contracts
feat: add file scanner filename policy
feat: detect likely binary samples
feat: classify scanned text files
feat: scan and prune repository files
feat: bound file inspection safely
fix: prevent file scanner link traversal
feat: expose file scanner public API
docs: document file scanner module
```

Verification-only corrections, if needed, receive a separate narrowly named commit after a demonstrated RED/GREEN cycle.

## Approval Gate

This plan creates no production or test implementation. After plan approval, implementation must begin in an isolated feature worktree/branch using the required Superpowers execution workflow and TDD. The verification task derives the comparison base with `git merge-base HEAD main` in `$traceRagBase` before using it.

One Important contract detail needs approval with this plan: a POSIX symlink's non-following metadata identifies the entry as a symlink but cannot safely reveal whether its target is a file or directory. The approved design requested `SkippedDirectory` for directory symlinks, while also prohibiting target following. The plan resolves this safely by using `SkippedDirectory` only when non-following metadata identifies a directory-like reparse entry; otherwise every unknowable link is an `IgnoredFile(..., SYMLINK)`. It is never traversed or opened. This slightly changes reporting/counter semantics for POSIX directory symlinks but preserves the stronger security invariant.
