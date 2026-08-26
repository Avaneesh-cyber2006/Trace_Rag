"""Structured values returned by the Repository Loader."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GitHubRepositoryURL:
    """Validated components of a GitHub repository root URL."""

    owner: str
    repository_name: str
    normalized_url: str


@dataclass(frozen=True, slots=True)
class RepositoryInfo:
    """Basic metadata for a cloned or reused repository."""

    success: bool
    owner: str
    repository_name: str
    repo_url: str
    local_path: str
    branch: str | None
    current_commit: str | None
    total_files: int
    repository_size_bytes: int
    reused_existing_clone: bool
