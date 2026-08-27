"""Public interface for TraceRAG's File Scanner & Filter."""

from .exceptions import (
    FileScannerError,
    InvalidRepositoryPath,
    RepositoryScanError,
    ScannerConfigurationError,
)
from .models import (
    FileCategory,
    FileInventory,
    IgnoredFile,
    IgnoreReason,
    ScannedFile,
    SkippedDirectory,
    SkippedDirectoryReason,
)
from .scanner import FileScanner, scan_repository

__all__ = [
    "FileCategory", "FileInventory", "FileScanner", "FileScannerError",
    "IgnoredFile", "IgnoreReason", "InvalidRepositoryPath", "RepositoryScanError",
    "ScannedFile", "ScannerConfigurationError", "SkippedDirectory",
    "SkippedDirectoryReason", "scan_repository",
]
