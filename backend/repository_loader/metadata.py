"""Read-only Git and working-tree metadata extraction."""

from __future__ import annotations

import os
from pathlib import Path

from git import InvalidGitRepositoryError, NoSuchPathError, Repo
from git.exc import GitCommandError

from .exceptions import InvalidRepositoryURL, MetadataExtractionError
from .models import GitHubRepositoryURL, RepositoryInfo
from .validator import validate_github_repository_url


def get_normalized_origin_url(repo_path: Path) -> GitHubRepositoryURL:
    """Return the validated, normalized origin URL for an existing clone."""
    try:
        repository = Repo(repo_path)
        origin_url = repository.remote("origin").url
        return validate_github_repository_url(origin_url)
    except (InvalidGitRepositoryError, NoSuchPathError, GitCommandError, ValueError) as exc:
        raise MetadataExtractionError("Unable to read the repository origin.") from exc
    except InvalidRepositoryURL as exc:
        raise MetadataExtractionError("The repository origin is not a supported GitHub URL.") from exc


def extract_repository_info(
    repo_path: Path,
    repository_url: GitHubRepositoryURL,
    reused_existing_clone: bool,
) -> RepositoryInfo:
    """Extract basic Git and filesystem metadata without executing repository code."""
    resolved_path = repo_path.resolve()
    try:
        repository = Repo(resolved_path)
        if repository.bare:
            raise MetadataExtractionError("The repository has no working tree.")
        branch = None if repository.head.is_detached else repository.active_branch.name
        current_commit = repository.head.commit.hexsha
    except MetadataExtractionError:
        raise
    except (InvalidGitRepositoryError, NoSuchPathError, GitCommandError, ValueError) as exc:
        raise MetadataExtractionError("Unable to read repository Git metadata.") from exc

    total_files, repository_size_bytes = _measure_working_tree(resolved_path)
    return RepositoryInfo(
        success=True,
        owner=repository_url.owner,
        repository_name=repository_url.repository_name,
        repo_url=repository_url.normalized_url,
        local_path=str(resolved_path),
        branch=branch,
        current_commit=current_commit,
        total_files=total_files,
        repository_size_bytes=repository_size_bytes,
        reused_existing_clone=reused_existing_clone,
    )


def _measure_working_tree(root: Path) -> tuple[int, int]:
    total_files = 0
    total_bytes = 0
    pending = [root]

    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.name == ".git" and entry.is_dir(follow_symlinks=False):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total_files += 1
                            total_bytes += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue

    return total_files, total_bytes
