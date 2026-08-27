"""Filesystem orchestration for the File Scanner & Filter."""

from pathlib import Path

from .exceptions import InvalidRepositoryPath, ScannerConfigurationError
from .models import FileInventory


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
        return FileInventory(str(root), 0, 0, 0, (), (), ())
