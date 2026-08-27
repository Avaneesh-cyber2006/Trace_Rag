"""Immutable values produced by the File Scanner & Filter."""

from dataclasses import dataclass
from enum import Enum


class FileCategory(str, Enum):
    SOURCE = "source"
    TEST = "test"
    CONFIG = "config"
    DATABASE = "database"
    DOCUMENTATION = "documentation"
    BUILD = "build"
    OTHER_TEXT = "other_text"


class IgnoreReason(str, Enum):
    SENSITIVE_FILE = "sensitive_file"
    LOCKFILE = "lockfile"
    UNSUPPORTED_TYPE = "unsupported_type"
    MINIFIED = "minified"
    TOO_LARGE = "too_large"
    BINARY = "binary"
    UNREADABLE = "unreadable"
    SYMLINK = "symlink"


class SkippedDirectoryReason(str, Enum):
    IGNORED_DIRECTORY = "ignored_directory"
    UNREADABLE = "unreadable"
    SYMLINK = "symlink"


@dataclass(frozen=True, slots=True)
class ScannedFile:
    relative_path: str
    filename: str
    extension: str
    language: str | None
    category: FileCategory
    size_bytes: int


@dataclass(frozen=True, slots=True)
class IgnoredFile:
    relative_path: str
    reason: IgnoreReason


@dataclass(frozen=True, slots=True)
class SkippedDirectory:
    relative_path: str
    reason: SkippedDirectoryReason


@dataclass(frozen=True, slots=True)
class FileInventory:
    repository_path: str
    total_files_seen: int
    included_files: int
    ignored_files: int
    files: tuple[ScannedFile, ...]
    ignored: tuple[IgnoredFile, ...]
    skipped_directories: tuple[SkippedDirectory, ...]
