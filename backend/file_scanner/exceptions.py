"""File Scanner & Filter exception hierarchy."""


class FileScannerError(Exception):
    """Base class for File Scanner & Filter failures."""


class InvalidRepositoryPath(FileScannerError):
    """Raised when the supplied scan root is missing or unusable."""


class RepositoryScanError(FileScannerError):
    """Raised when repository traversal cannot safely proceed."""


class ScannerConfigurationError(FileScannerError):
    """Raised when scanner configuration is invalid."""
