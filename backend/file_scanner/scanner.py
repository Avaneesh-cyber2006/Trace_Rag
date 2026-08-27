"""Filesystem orchestration for the File Scanner & Filter."""

import os
from pathlib import Path

from .classifier import classify_file, detect_language
from .exceptions import InvalidRepositoryPath, ScannerConfigurationError
from .filters import DEFAULT_IGNORED_DIRECTORIES, classify_filename
from .models import (
    FileInventory,
    IgnoredFile,
    IgnoreReason,
    ScannedFile,
    SkippedDirectory,
    SkippedDirectoryReason,
)


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

    @staticmethod
    def _validate_repository_path(repository_path: Path | str) -> Path:
        try:
            root = Path(repository_path).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise InvalidRepositoryPath("Unable to resolve the repository path.") from exc
        if not root.is_dir():
            raise InvalidRepositoryPath("The repository path is not a directory.")
        return root

    def scan(self, repository_path: Path | str) -> FileInventory:
        root = self._validate_repository_path(repository_path)
        files: list[ScannedFile] = []
        ignored: list[IgnoredFile] = []
        skipped: list[SkippedDirectory] = []
        pending = [root]

        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    relative_path = path.relative_to(root).as_posix()
                    if entry.is_symlink():
                        ignored.append(IgnoredFile(relative_path, IgnoreReason.SYMLINK))
                    elif entry.is_dir(follow_symlinks=False):
                        if entry.name.casefold() in DEFAULT_IGNORED_DIRECTORIES:
                            skipped.append(SkippedDirectory(relative_path, SkippedDirectoryReason.IGNORED_DIRECTORY))
                        else:
                            pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        reason = classify_filename(entry.name)
                        if reason is not None:
                            ignored.append(IgnoredFile(relative_path, reason))
                            continue
                        size = entry.stat(follow_symlinks=False).st_size
                        extension = path.suffix.casefold()
                        files.append(ScannedFile(
                            relative_path=relative_path,
                            filename=entry.name,
                            extension=extension,
                            language=detect_language(entry.name, extension),
                            category=classify_file(relative_path, entry.name, extension),
                            size_bytes=size,
                        ))

        sort_key = lambda item: (item.relative_path.casefold(), item.relative_path)
        files.sort(key=sort_key)
        ignored.sort(key=sort_key)
        skipped.sort(key=sort_key)
        return FileInventory(
            repository_path=str(root),
            total_files_seen=len(files) + len(ignored),
            included_files=len(files),
            ignored_files=len(ignored),
            files=tuple(files),
            ignored=tuple(ignored),
            skipped_directories=tuple(skipped),
        )
