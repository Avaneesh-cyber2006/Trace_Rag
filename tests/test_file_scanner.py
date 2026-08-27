from __future__ import annotations

from dataclasses import FrozenInstanceError
import io
import os
from pathlib import Path, PurePosixPath

import pytest
import backend.file_scanner.scanner as scanner_module

from backend.file_scanner import (
    FileCategory,
    FileScanner,
    IgnoreReason,
    IgnoredFile,
    InvalidRepositoryPath,
    RepositoryScanError,
    ScannedFile,
    ScannerConfigurationError,
    SkippedDirectory,
    SkippedDirectoryReason,
)
from backend.file_scanner.filters import (
    DEFAULT_IGNORED_DIRECTORIES,
    classify_filename,
    is_binary_sample,
    is_supported_filename,
)
from backend.file_scanner.classifier import classify_file, detect_language


def write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


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
    with pytest.raises(ScannerConfigurationError):
        FileScanner(**{field: value})  # type: ignore[arg-type]


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


def test_empty_repository_returns_empty_inventory(tmp_path: Path) -> None:
    inventory = FileScanner().scan(tmp_path)
    assert inventory.repository_path == str(tmp_path.resolve())
    assert inventory.total_files_seen == 0
    assert inventory.included_files == 0
    assert inventory.ignored_files == 0
    assert inventory.files == ()
    assert inventory.ignored == ()
    assert inventory.skipped_directories == ()


@pytest.mark.parametrize(
    "filename",
    [".env", ".env.production", "APP.ENV", "app.env.local", "private.pem", "ID_RSA", "credentials.json", "service-account.json"],
)
def test_sensitive_filenames_are_rejected(filename: str) -> None:
    assert classify_filename(filename) is IgnoreReason.SENSITIVE_FILE


@pytest.mark.parametrize("filename", [".env.example", ".ENV.SAMPLE", "app.env.template"])
def test_safe_environment_templates_are_supported(filename: str) -> None:
    assert classify_filename(filename) is None
    assert is_supported_filename(filename)


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


@pytest.mark.parametrize(
    "sample",
    [
        b"", b"answer = 42\n", "नमस्ते TraceRAG\n".encode("utf-8"),
        b"\xef\xbb\xbfhello\n", "hello\n".encode("utf-16"), b"caf\xe9\n",
    ],
)
def test_binary_heuristic_accepts_expected_text(sample: bytes) -> None:
    assert not is_binary_sample(sample)


@pytest.mark.parametrize("sample", [b"text\x00payload", b"\xff\x00\x10\x01", b"\xff\xfe\x00"])
def test_binary_heuristic_rejects_binary_samples(sample: bytes) -> None:
    assert is_binary_sample(sample)


def test_exactly_thirty_percent_controls_is_text() -> None:
    assert not is_binary_sample(b"\x01\x02\x03abcdefg")


def test_more_than_thirty_percent_controls_is_binary() -> None:
    assert is_binary_sample(b"\x01\x02\x03\x04abcdef")


@pytest.mark.parametrize(
    ("filename", "extension", "expected"),
    [
        ("app.py", ".py", "python"), ("App.TSX", ".tsx", "typescript"),
        ("main.cpp", ".cpp", "cpp"), ("types.h", ".h", "c/cpp"),
        ("service.java", ".java", "java"), ("app.js", ".js", "javascript"),
        ("main.cs", ".cs", "csharp"), ("main.go", ".go", "go"),
        ("main.rs", ".rs", "rust"), ("Main.kt", ".kt", "kotlin"),
        ("Dockerfile", "", "dockerfile"), ("Makefile", "", "make"),
        ("schema.prisma", ".prisma", "prisma"), ("README.md", ".md", None),
    ],
)
def test_detect_language(filename: str, extension: str, expected: str | None) -> None:
    assert detect_language(filename, extension) == expected


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


def test_scan_returns_classified_files_in_deterministic_posix_order(tmp_path: Path) -> None:
    write_bytes(tmp_path / "src" / "service.py", b"service = True\n")
    write_bytes(tmp_path / "README.md", "Résumé\n".encode("utf-8"))
    write_bytes(tmp_path / "src" / "app.py", b"answer = 42\n")

    inventory = FileScanner().scan(tmp_path)

    assert [file.relative_path for file in inventory.files] == [
        "README.md", "src/app.py", "src/service.py"
    ]
    assert all("\\" not in file.relative_path for file in inventory.files)
    assert [file.language for file in inventory.files] == [None, "python", "python"]
    assert inventory.repository_path == str(tmp_path.resolve())
    assert inventory.included_files == 3
    assert inventory.ignored_files == 0
    assert inventory.total_files_seen == 3


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


def test_ignored_files_and_counters_are_consistent(tmp_path: Path) -> None:
    write_bytes(tmp_path / "app.py", b"pass\n")
    write_bytes(tmp_path / ".env", b"SECRET=value\n")
    write_bytes(tmp_path / "package-lock.json", b"{}")
    write_bytes(tmp_path / "app.min.js", b"x")
    write_bytes(tmp_path / "logo.png", b"not opened")
    write_bytes(tmp_path / "node_modules" / "hidden.py", b"pass\n")

    inventory = FileScanner().scan(tmp_path)

    assert inventory.ignored == (
        IgnoredFile(".env", IgnoreReason.SENSITIVE_FILE),
        IgnoredFile("app.min.js", IgnoreReason.MINIFIED),
        IgnoredFile("logo.png", IgnoreReason.UNSUPPORTED_TYPE),
        IgnoredFile("package-lock.json", IgnoreReason.LOCKFILE),
    )
    assert inventory.included_files == len(inventory.files) == 1
    assert inventory.ignored_files == len(inventory.ignored) == 4
    assert inventory.total_files_seen == inventory.included_files + inventory.ignored_files == 5


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


def test_scanner_reads_only_configured_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "sample.py"
    target.write_bytes(b"abcdefgh")
    read_sizes: list[int] = []

    class RecordingBytesIO(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return super().read(size)

    original_open = Path.open

    def recording_open(self: Path, *args: object, **kwargs: object):
        if self == target:
            return RecordingBytesIO(b"abcdefgh")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", recording_open)
    assert FileScanner(binary_sample_size=4).scan(tmp_path).included_files == 1
    assert read_sizes == [4]


def test_binary_unicode_and_empty_files_are_classified(tmp_path: Path) -> None:
    write_bytes(tmp_path / "payload.txt", b"text\x00payload")
    write_bytes(tmp_path / "unicode.md", "नमस्ते\n".encode("utf-8"))
    write_bytes(tmp_path / "bom.txt", "hello\n".encode("utf-16"))
    write_bytes(tmp_path / "empty.py", b"")
    inventory = FileScanner().scan(tmp_path)
    assert IgnoredFile("payload.txt", IgnoreReason.BINARY) in inventory.ignored
    assert [item.relative_path for item in inventory.files] == ["bom.txt", "empty.py", "unicode.md"]


def test_file_open_failure_is_recoverable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "unreadable.py"
    target.write_bytes(b"pass\n")
    original_open = Path.open

    def failing_open(self: Path, *args: object, **kwargs: object):
        if self == target:
            raise PermissionError("denied")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    inventory = FileScanner().scan(tmp_path)
    assert inventory.ignored == (IgnoredFile("unreadable.py", IgnoreReason.UNREADABLE),)
    assert inventory.total_files_seen == 1


def test_root_enumeration_failure_is_translated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_scandir(path: object):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "scandir", failing_scandir)
    with pytest.raises(RepositoryScanError):
        FileScanner().scan(tmp_path)


def test_descendant_enumeration_failure_is_recoverable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    write_bytes(tmp_path / "sibling.py", b"pass\n")
    original_scandir = scanner_module.os.scandir

    def selective_scandir(path: object):
        if Path(path) == blocked:
            raise PermissionError("denied")
        return original_scandir(path)

    monkeypatch.setattr(scanner_module.os, "scandir", selective_scandir)
    inventory = FileScanner().scan(tmp_path)
    assert inventory.included_files == 1
    assert inventory.skipped_directories == (
        SkippedDirectory("blocked", SkippedDirectoryReason.UNREADABLE),
    )
