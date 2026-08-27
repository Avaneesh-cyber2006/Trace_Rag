from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path, PurePosixPath

import pytest

from backend.file_scanner import (
    FileCategory,
    FileScanner,
    IgnoreReason,
    InvalidRepositoryPath,
    ScannedFile,
    ScannerConfigurationError,
    SkippedDirectoryReason,
)
from backend.file_scanner.filters import classify_filename, is_binary_sample, is_supported_filename
from backend.file_scanner.classifier import classify_file, detect_language


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
