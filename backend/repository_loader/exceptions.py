"""Repository Loader exception hierarchy."""


class RepositoryLoaderError(Exception):
    """Base class for all Repository Loader failures."""


class InvalidRepositoryURL(RepositoryLoaderError):
    """Raised when an input is not a supported GitHub repository root URL."""


class RepositoryNotFound(RepositoryLoaderError):
    """Raised when the requested public repository does not exist."""


class RepositoryNotPublic(RepositoryLoaderError):
    """Raised when a repository cannot be accessed without credentials."""


class CloneFailed(RepositoryLoaderError):
    """Raised when Git cannot clone a repository."""


class GitNotInstalled(RepositoryLoaderError):
    """Raised when the Git executable is unavailable."""


class WorkspaceError(RepositoryLoaderError):
    """Raised when the configured workspace is unsafe or unusable."""


class MetadataExtractionError(RepositoryLoaderError):
    """Raised when basic repository metadata cannot be read."""
