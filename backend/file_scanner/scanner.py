"""Filesystem orchestration for the File Scanner & Filter."""

import os
import logging
from pathlib import Path

from .classifier import classify_file, detect_language
from .exceptions import InvalidRepositoryPath, RepositoryScanError, ScannerConfigurationError
from .filters import DEFAULT_IGNORED_DIRECTORIES, classify_filename, is_binary_sample, is_link_or_reparse
from .models import (
    FileInventory,
    IgnoredFile,
    IgnoreReason,
    ScannedFile,
    SkippedDirectory,
    SkippedDirectoryReason,
)

logger = logging.getLogger(__name__)


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

    def scan(
        self,
        repository_path: Path | str,
        *,
        repository_namespace: str | None = None,
    ) -> FileInventory:
        logger.info("Starting repository scan")
        root = self._validate_repository_path(repository_path)
        logger.info("Repository root validated")
        files: list[ScannedFile] = []
        ignored: list[IgnoredFile] = []
        skipped: list[SkippedDirectory] = []
        pending = [root]

        while pending:
            directory = pending.pop()
            try:
                entries_context = os.scandir(directory)
            except (OSError, RuntimeError) as exc:
                if directory == root:
                    raise RepositoryScanError("Unable to enumerate the repository root.") from exc
                skipped.append(SkippedDirectory(directory.relative_to(root).as_posix(), SkippedDirectoryReason.UNREADABLE))
                logger.warning("Repository scan skipped an unreadable descendant directory")
                continue
            with entries_context as entries:
                for entry in entries:
                    path = Path(entry.path)
                    relative_path = path.relative_to(root).as_posix()
                    try:
                        if is_link_or_reparse(entry):
                            if entry.is_dir(follow_symlinks=False):
                                skipped.append(SkippedDirectory(relative_path, SkippedDirectoryReason.SYMLINK))
                            else:
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
                            if size > self.max_file_size_bytes:
                                ignored.append(IgnoredFile(relative_path, IgnoreReason.TOO_LARGE))
                                continue
                            try:
                                with path.open("rb") as stream:
                                    sample = stream.read(self.binary_sample_size)
                            except (OSError, RuntimeError):
                                ignored.append(IgnoredFile(relative_path, IgnoreReason.UNREADABLE))
                                continue
                            if is_binary_sample(sample):
                                ignored.append(IgnoredFile(relative_path, IgnoreReason.BINARY))
                                continue
                            extension = path.suffix.casefold()
                            files.append(ScannedFile(
                                relative_path=relative_path,
                                filename=entry.name,
                                extension=extension,
                                language=detect_language(entry.name, extension),
                                category=classify_file(relative_path, entry.name, extension),
                                size_bytes=size,
                            ))
                    except (OSError, RuntimeError):
                        ignored.append(IgnoredFile(relative_path, IgnoreReason.UNREADABLE))

        sort_key = lambda item: (item.relative_path.casefold(), item.relative_path)
        files.sort(key=sort_key)
        ignored.sort(key=sort_key)
        skipped.sort(key=sort_key)
        for item in ignored:
            logger.debug("File ignored: %s (%s)", item.relative_path, item.reason.value)
        inventory = FileInventory(
            repository_path=str(root),
            total_files_seen=len(files) + len(ignored),
            included_files=len(files),
            ignored_files=len(ignored),
            files=tuple(files),
            ignored=tuple(ignored),
            skipped_directories=tuple(skipped),
            repository_namespace=repository_namespace,
        )
        logger.info(
            "Repository scan complete: %d included, %d ignored",
            inventory.included_files,
            inventory.ignored_files,
        )
        return inventory


def scan_repository(
    repository_path: Path | str,
    max_file_size_bytes: int = 1_000_000,
    binary_sample_size: int = 8192,
    *,
    repository_namespace: str | None = None,
) -> FileInventory:
    return FileScanner(max_file_size_bytes, binary_sample_size).scan(
        repository_path,
        repository_namespace=repository_namespace,
    )
