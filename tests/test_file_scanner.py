from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

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
