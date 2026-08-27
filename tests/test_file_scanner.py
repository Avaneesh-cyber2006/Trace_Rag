from __future__ import annotations

from dataclasses import FrozenInstanceError
import io
import logging
import os
import stat
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
    scan_repository,
)
from backend.file_scanner.filters import (
    DEFAULT_IGNORED_DIRECTORIES,
    classify_filename,
    is_binary_sample,
    is_link_or_reparse,
    is_supported_filename,
)
from backend.file_scanner.classifier import classify_file, detect_language
from backend.repository_loader import RepositoryInfo


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


def make_symlink(link: Path, target: Path, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")


def test_file_and_broken_symlinks_are_ignored(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_bytes(b"pass\n")
    make_symlink(tmp_path / "linked.py", target)
    make_symlink(tmp_path / "broken.py", tmp_path / "missing.py")
    inventory = FileScanner().scan(tmp_path)
    assert IgnoredFile("broken.py", IgnoreReason.SYMLINK) in inventory.ignored
    assert IgnoredFile("linked.py", IgnoreReason.SYMLINK) in inventory.ignored
    assert [item.relative_path for item in inventory.files] == ["target.py"]
    assert inventory.total_files_seen == 3


def test_directory_symlink_escape_is_never_traversed(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    outside = tmp_path / "outside"
    write_bytes(outside / "secret.py", b"secret = True\n")
    make_symlink(repository / "outside_link", outside, target_is_directory=True)
    inventory = FileScanner().scan(repository)
    assert inventory.files == ()
    assert inventory.ignored == (IgnoredFile("outside_link", IgnoreReason.SYMLINK),)


def test_reparse_attribute_is_detected_without_symlink_flag() -> None:
    class FakeStat:
        st_file_attributes = stat.FILE_ATTRIBUTE_REPARSE_POINT

    class FakeEntry:
        def is_symlink(self) -> bool:
            return False

        def stat(self, *, follow_symlinks: bool = True) -> FakeStat:
            assert not follow_symlinks
            return FakeStat()

    assert is_link_or_reparse(FakeEntry())  # type: ignore[arg-type]


def test_scan_repository_convenience_function_uses_public_configuration(tmp_path: Path) -> None:
    write_bytes(tmp_path / "too-big.py", b"12345")
    inventory = scan_repository(tmp_path, max_file_size_bytes=4, binary_sample_size=2)
    assert inventory.ignored == (IgnoredFile("too-big.py", IgnoreReason.TOO_LARGE),)


def test_scanner_accepts_repository_info_local_path(tmp_path: Path) -> None:
    write_bytes(tmp_path / "app.py", b"answer = 42\n")
    info = RepositoryInfo(
        success=True, owner="acme", repository_name="example",
        repo_url="https://github.com/acme/example", local_path=str(tmp_path),
        branch="main", current_commit="a" * 40, total_files=1,
        repository_size_bytes=12, reused_existing_clone=False,
    )
    inventory = FileScanner().scan(info.local_path)
    assert inventory.files[0].relative_path == "app.py"


def test_scan_logs_lifecycle_without_file_contents(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write_bytes(tmp_path / "app.py", b"TOP_SECRET_SENTINEL\n")
    write_bytes(tmp_path / "logo.png", b"BINARY_SECRET_SENTINEL")
    with caplog.at_level(logging.DEBUG, logger="backend.file_scanner.scanner"):
        FileScanner().scan(tmp_path)
    messages = [record.getMessage() for record in caplog.records]
    assert any("Starting repository scan" in message for message in messages)
    assert any("Repository scan complete" in message for message in messages)
    assert any("unsupported_type" in message for message in messages)
    assert all("TOP_SECRET_SENTINEL" not in message for message in messages)
    assert all("BINARY_SECRET_SENTINEL" not in message for message in messages)


@pytest.mark.parametrize(
    ("filename", "language"),
    [
        ("x.py", "python"), ("x.java", "java"), ("x.js", "javascript"),
        ("x.jsx", "javascript"), ("x.ts", "typescript"), ("x.tsx", "typescript"),
        ("x.c", "c"), ("x.cc", "cpp"), ("x.h", "c/cpp"), ("x.hpp", "cpp"),
        ("x.cs", "csharp"), ("x.go", "go"), ("x.rs", "rust"), ("x.rb", "ruby"),
        ("x.php", "php"), ("x.kt", "kotlin"), ("x.swift", "swift"),
        ("x.scala", "scala"), ("x.sh", "shell"), ("x.ps1", "powershell"),
    ],
)
def test_source_extension_matrix(tmp_path: Path, filename: str, language: str) -> None:
    write_bytes(tmp_path / filename, b"source\n")
    result = FileScanner().scan(tmp_path).files[0]
    assert result.language == language
    assert result.category is FileCategory.SOURCE


@pytest.mark.parametrize(
    "filename",
    ["x.class", "x.jar", "x.exe", "x.dll", "x.so", "x.png", "x.jpg", "x.mp3", "x.mp4", "x.zip", "x.gz", "x.ttf", "x.woff2", "x.db", "x.sqlite", "x.svg"],
)
def test_unsupported_type_matrix(tmp_path: Path, filename: str) -> None:
    write_bytes(tmp_path / filename, b"text-looking payload")
    assert FileScanner().scan(tmp_path).ignored == (
        IgnoredFile(filename, IgnoreReason.UNSUPPORTED_TYPE),
    )


@pytest.mark.parametrize(
    ("filename", "category"),
    [
        ("package.json", FileCategory.BUILD), ("requirements.txt", FileCategory.BUILD),
        ("pyproject.toml", FileCategory.BUILD), ("pom.xml", FileCategory.BUILD),
        ("build.gradle", FileCategory.BUILD), ("Cargo.toml", FileCategory.BUILD),
        ("go.mod", FileCategory.BUILD), ("application.properties", FileCategory.CONFIG),
        ("design.rst", FileCategory.DOCUMENTATION), ("schema.sql", FileCategory.DATABASE),
    ],
)
def test_manifest_and_category_matrix(tmp_path: Path, filename: str, category: FileCategory) -> None:
    write_bytes(tmp_path / filename, b"text\n")
    assert FileScanner().scan(tmp_path).files[0].category is category


@pytest.mark.parametrize(
    ("filename", "reason"),
    [
        (".env.production", IgnoreReason.SENSITIVE_FILE),
        ("package-lock.json", IgnoreReason.LOCKFILE),
        ("huge.min.js", IgnoreReason.MINIFIED),
        ("huge.png", IgnoreReason.UNSUPPORTED_TYPE),
    ],
)
def test_ignore_reason_precedence_without_content_reads(
    tmp_path: Path, filename: str, reason: IgnoreReason, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / filename
    target.write_bytes(b"x" * 20)
    original_open = Path.open

    def guarded_open(self: Path, *args: object, **kwargs: object):
        if self == target:
            raise AssertionError("name-filtered file must not be opened")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    assert FileScanner(max_file_size_bytes=10).scan(tmp_path).ignored == (
        IgnoredFile(filename, reason),
    )


def test_inventory_collections_sort_independently(tmp_path: Path) -> None:
    write_bytes(tmp_path / "z.py", b"pass\n")
    write_bytes(tmp_path / "A.py", b"pass\n")
    write_bytes(tmp_path / "z.png", b"x")
    write_bytes(tmp_path / "A.png", b"x")
    write_bytes(tmp_path / "z" / "node_modules" / "x.py", b"pass\n")
    write_bytes(tmp_path / "A" / "build" / "x.py", b"pass\n")
    inventory = FileScanner().scan(tmp_path)
    assert [item.relative_path for item in inventory.files] == ["A.py", "z.py"]
    assert [item.relative_path for item in inventory.ignored] == ["A.png", "z.png"]
    assert [item.relative_path for item in inventory.skipped_directories] == ["A/build", "z/node_modules"]
