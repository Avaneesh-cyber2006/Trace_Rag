"""Safe orchestration for cloning or reusing public GitHub repositories."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .exceptions import (
    CloneFailed,
    GitNotInstalled,
    MetadataExtractionError,
    RepositoryLoaderError,
    RepositoryNotFound,
    RepositoryNotPublic,
    WorkspaceError,
)
from .metadata import extract_repository_info, get_normalized_origin_url
from .models import RepositoryInfo
from .validator import validate_github_repository_url

logger = logging.getLogger(__name__)


class RepositoryLoader:
    """Load public GitHub repositories into a controlled local workspace."""

    def __init__(
        self,
        workspace_path: Path | str = "workspace",
        clone_timeout_seconds: float = 120,
    ) -> None:
        if clone_timeout_seconds <= 0:
            raise ValueError("clone_timeout_seconds must be positive")
        self.workspace_path = Path(workspace_path)
        self.clone_timeout_seconds = clone_timeout_seconds

    def load(self, repository_url: str) -> RepositoryInfo:
        """Validate, clone or reuse, and describe a public GitHub repository."""
        logger.info("Validating repository URL")
        validated_url = validate_github_repository_url(repository_url)
        workspace = self._prepare_workspace()
        destination = self._safe_destination(
            workspace, f"{validated_url.owner}_{validated_url.repository_name}"
        )

        self._check_git_available()
        if destination.exists():
            logger.info("Reusing existing repository")
            self._verify_existing_clone(destination, validated_url.normalized_url)
            reused_existing_clone = True
        else:
            logger.info("Cloning repository")
            self._clone(validated_url.normalized_url, destination)
            reused_existing_clone = False

        logger.info("Extracting metadata")
        try:
            info = extract_repository_info(destination, validated_url, reused_existing_clone)
        except MetadataExtractionError:
            if not reused_existing_clone:
                self._remove_new_destination(destination)
            raise
        logger.info("Repository loaded successfully")
        return info

    def _prepare_workspace(self) -> Path:
        logger.info("Preparing workspace")
        try:
            self.workspace_path.mkdir(parents=True, exist_ok=True)
            workspace = self.workspace_path.resolve(strict=True)
        except OSError as exc:
            raise WorkspaceError("Unable to prepare the repository workspace.") from exc
        if not workspace.is_dir():
            raise WorkspaceError("The configured workspace is not a directory.")
        return workspace

    @staticmethod
    def _safe_destination(workspace: Path, directory_name: str) -> Path:
        destination = (workspace / directory_name).resolve(strict=False)
        if destination.parent != workspace:
            raise WorkspaceError("The repository destination would escape the workspace.")
        return destination

    @staticmethod
    def _check_git_available() -> None:
        try:
            subprocess.run(
                ["git", "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise GitNotInstalled("Git is not installed or is not available on PATH.") from exc

    @staticmethod
    def _verify_existing_clone(destination: Path, expected_url: str) -> None:
        if not destination.is_dir():
            raise WorkspaceError("The repository destination exists but is not a directory.")
        try:
            origin = get_normalized_origin_url(destination)
        except MetadataExtractionError as exc:
            raise WorkspaceError("The existing destination is not a valid GitHub clone.") from exc
        if origin.normalized_url.casefold() != expected_url.casefold():
            raise WorkspaceError("The existing destination belongs to another repository.")

    def _clone(self, repository_url: str, destination: Path) -> None:
        environment = os.environ.copy()
        for name in tuple(environment):
            if name.startswith("GIT_CONFIG_"):
                environment.pop(name)
        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment["GIT_CONFIG_NOSYSTEM"] = "1"
        environment["GIT_ATTR_NOSYSTEM"] = "1"
        try:
            with tempfile.TemporaryDirectory(prefix="tracerag-git-") as temporary_directory:
                trusted_directory = Path(temporary_directory)
                hooks_path = trusted_directory / "hooks"
                hooks_path.mkdir()
                global_config = trusted_directory / "global.gitconfig"
                global_config.write_bytes(b"")
                environment["GIT_CONFIG_GLOBAL"] = str(global_config)
                command = [
                    "git",
                    "-c",
                    f"core.hooksPath={hooks_path}",
                    "clone",
                    "--depth",
                    "1",
                    repository_url,
                    str(destination),
                ]
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.clone_timeout_seconds,
                    env=environment,
                )
        except subprocess.TimeoutExpired as exc:
            self._remove_new_destination(destination)
            raise CloneFailed("Repository cloning timed out.") from exc
        except FileNotFoundError as exc:
            self._remove_new_destination(destination)
            raise GitNotInstalled("Git became unavailable while cloning.") from exc
        except OSError as exc:
            self._remove_new_destination(destination)
            raise CloneFailed("The clone process could not access a required resource.") from exc
        except subprocess.CalledProcessError as exc:
            self._remove_new_destination(destination)
            raise self._translate_clone_failure(exc.stderr) from exc

    @staticmethod
    def _translate_clone_failure(stderr: str | None) -> RepositoryLoaderError:
        diagnostic = (stderr or "").casefold()
        if "authentication failed" in diagnostic or "could not read username" in diagnostic:
            return RepositoryNotPublic("The repository is not publicly accessible.")
        if "repository" in diagnostic and "not found" in diagnostic:
            return RepositoryNotFound("The public repository was not found.")
        return CloneFailed("Git could not clone the repository.")

    @staticmethod
    def _remove_new_destination(destination: Path) -> None:
        if destination.exists():
            try:
                shutil.rmtree(destination)
            except OSError:
                logger.warning("Unable to remove a partial clone destination")


def load_repository(
    repository_url: str,
    workspace_path: Path | str = "workspace",
    clone_timeout_seconds: float = 120,
) -> RepositoryInfo:
    """Convenience wrapper around :class:`RepositoryLoader`."""
    return RepositoryLoader(workspace_path, clone_timeout_seconds).load(repository_url)
