"""Validation and normalization for public GitHub repository root URLs."""

import re
from urllib.parse import urlsplit

from .exceptions import InvalidRepositoryURL
from .models import GitHubRepositoryURL

_OWNER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}\Z")


def validate_github_repository_url(repository_url: str) -> GitHubRepositoryURL:
    """Validate a GitHub repository root URL and return normalized components."""
    if not isinstance(repository_url, str) or not repository_url:
        raise InvalidRepositoryURL("A GitHub repository URL is required.")

    try:
        parsed = urlsplit(repository_url)
        port = parsed.port
    except ValueError as exc:
        raise InvalidRepositoryURL("The repository URL is malformed.") from exc

    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.hostname.lower() != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or "%" in parsed.path
    ):
        raise InvalidRepositoryURL("Only GitHub repository root URLs are supported.")

    path_parts = parsed.path.removeprefix("/").split("/")
    if len(path_parts) != 2 or any(not part for part in path_parts):
        raise InvalidRepositoryURL("The URL must identify a GitHub repository root.")

    owner, repository_name = path_parts
    if repository_name.endswith(".git"):
        repository_name = repository_name[:-4]

    if (
        not _OWNER_PATTERN.fullmatch(owner)
        or not _REPOSITORY_PATTERN.fullmatch(repository_name)
        or repository_name in {".", ".."}
        or repository_name.endswith(".git")
    ):
        raise InvalidRepositoryURL("The GitHub owner or repository name is invalid.")

    return GitHubRepositoryURL(
        owner=owner,
        repository_name=repository_name,
        normalized_url=f"https://github.com/{owner}/{repository_name}",
    )
