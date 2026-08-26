"""Public interface for TraceRAG's Repository Loader."""

from .exceptions import (
    CloneFailed,
    GitNotInstalled,
    InvalidRepositoryURL,
    MetadataExtractionError,
    RepositoryLoaderError,
    RepositoryNotFound,
    RepositoryNotPublic,
    WorkspaceError,
)
from .models import GitHubRepositoryURL, RepositoryInfo
from .loader import RepositoryLoader, load_repository
from .validator import validate_github_repository_url

__all__ = [
    "CloneFailed",
    "GitHubRepositoryURL",
    "GitNotInstalled",
    "InvalidRepositoryURL",
    "MetadataExtractionError",
    "RepositoryInfo",
    "RepositoryLoader",
    "RepositoryLoaderError",
    "RepositoryNotFound",
    "RepositoryNotPublic",
    "WorkspaceError",
    "load_repository",
    "validate_github_repository_url",
]
