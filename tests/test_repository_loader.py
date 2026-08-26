from __future__ import annotations

import subprocess
from pathlib import Path
import pytest
import backend.repository_loader.metadata as metadata_module
from gitdb.exc import ODBError

from backend.repository_loader import (
    CloneFailed,
    GitNotInstalled,
    InvalidRepositoryURL,
    RepositoryLoader,
    RepositoryInfo,
    RepositoryNotFound,
    RepositoryNotPublic,
    WorkspaceError,
)
from backend.repository_loader.metadata import (
    extract_repository_info,
    get_normalized_origin_url,
)
from backend.repository_loader.validator import validate_github_repository_url
from scripts.test_repository_loader import main as repository_loader_main


def run_git(repository_path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository_path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def create_committed_repository(path: Path) -> tuple[str, int]:
    path.mkdir(parents=True)
    run_git(path, "init", "-b", "main")
    run_git(path, "config", "user.name", "TraceRAG Tests")
    run_git(path, "config", "user.email", "tests@example.invalid")
    (path / "README.md").write_bytes(b"hello\n")
    (path / "empty.txt").write_bytes(b"")
    source = path / "src"
    source.mkdir()
    (source / "app.py").write_bytes(b"answer = 42\n")
    run_git(path, "add", ".")
    run_git(path, "commit", "-m", "initial")
    run_git(path, "remote", "add", "origin", "https://github.com/acme/example.git")
    return run_git(path, "rev-parse", "HEAD"), 6 + 0 + 12


@pytest.mark.parametrize(
    ("repository_url", "owner", "repository_name", "normalized_url"),
    [
        ("https://github.com/pallets/flask", "pallets", "flask", "https://github.com/pallets/flask"),
        ("https://github.com/pallets/flask.git", "pallets", "flask", "https://github.com/pallets/flask"),
        ("http://github.com/pallets/flask", "pallets", "flask", "https://github.com/pallets/flask"),
    ],
)
def test_validator_normalizes_repository_root_urls(
    repository_url: str, owner: str, repository_name: str, normalized_url: str
) -> None:
    result = validate_github_repository_url(repository_url)

    assert result.owner == owner
    assert result.repository_name == repository_name
    assert result.normalized_url == normalized_url


@pytest.mark.parametrize(
    "repository_url",
    [
        "github",
        "github.com",
        "https://github.com",
        "https://github.com/",
        "https://github.com/pallets",
        "https://google.com/pallets/flask",
        "ftp://github.com/pallets/flask",
        "https://github.com/pallets/flask/issues",
        "https://github.com/pallets/flask/tree/main",
        "https://user@github.com/pallets/flask",
        "https://github.com:443/pallets/flask",
        "https://github.com/pallets/flask?tab=readme",
        "https://github.com/pallets/flask#readme",
        "https://github.com/pallets%2Fflask/project",
        "https://github.com/pallets/%2e%2e",
        "https://github.com/../flask",
        "https://github.com/pallets/..",
        "https://github.com/pallets/flask/",
        "https://github.com/pallets//flask",
        "https://github.com/pallets/flask.git.git",
        "https://github.com/owner name/repo",
        "https://github.com/owner/repo name",
        "https://github.com/-owner/repo",
    ],
)
def test_validator_rejects_non_repository_root_urls(repository_url: str) -> None:
    with pytest.raises(InvalidRepositoryURL):
        validate_github_repository_url(repository_url)


def test_metadata_reports_git_and_working_tree_values(tmp_path: Path) -> None:
    repository_path = tmp_path / "repository"
    commit, expected_size = create_committed_repository(repository_path)
    (repository_path / ".git" / "ignored-payload").write_bytes(b"x" * 4096)
    repository_url = validate_github_repository_url("https://github.com/acme/example")

    info = extract_repository_info(repository_path, repository_url, True)

    assert info.success is True
    assert info.owner == "acme"
    assert info.repository_name == "example"
    assert info.repo_url == "https://github.com/acme/example"
    assert info.local_path == str(repository_path.resolve())
    assert info.branch == "main"
    assert info.current_commit == commit
    assert info.total_files == 3
    assert info.repository_size_bytes == expected_size
    assert info.reused_existing_clone is True


def test_metadata_allows_detached_head(tmp_path: Path) -> None:
    repository_path = tmp_path / "repository"
    commit, _ = create_committed_repository(repository_path)
    run_git(repository_path, "checkout", "--detach", commit)
    repository_url = validate_github_repository_url("https://github.com/acme/example")

    info = extract_repository_info(repository_path, repository_url, False)

    assert info.branch is None
    assert info.current_commit == commit


def test_origin_url_is_normalized(tmp_path: Path) -> None:
    repository_path = tmp_path / "repository"
    create_committed_repository(repository_path)

    origin = get_normalized_origin_url(repository_path)

    assert origin.owner == "acme"
    assert origin.repository_name == "example"
    assert origin.normalized_url == "https://github.com/acme/example"


def test_loader_clones_into_deterministic_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    commit, _ = create_committed_repository(source)
    workspace = tmp_path / "workspace"
    real_run = subprocess.run

    def clone_local(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "clone" in command:
            command = command.copy()
            command[-2] = source.as_uri()
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", clone_local)

    info = RepositoryLoader(workspace).load("https://github.com/acme/example.git")

    assert info.local_path == str((workspace / "acme_example").resolve())
    assert info.current_commit == commit
    assert info.total_files == 3
    assert info.reused_existing_clone is False


def test_loader_reuses_existing_matching_clone(tmp_path: Path) -> None:
    destination = tmp_path / "workspace" / "acme_example"
    create_committed_repository(destination)

    info = RepositoryLoader(tmp_path / "workspace").load("https://github.com/acme/example")

    assert info.reused_existing_clone is True
    assert not (tmp_path / "workspace" / "acme_example_2").exists()


@pytest.mark.parametrize("existing_kind", ["plain_directory", "different_origin"])
def test_loader_preserves_invalid_existing_destination(
    tmp_path: Path, existing_kind: str
) -> None:
    destination = tmp_path / "workspace" / "acme_example"
    destination.mkdir(parents=True)
    sentinel = destination / "keep.txt"
    sentinel.write_text("user data", encoding="utf-8")
    if existing_kind == "different_origin":
        run_git(destination, "init", "-b", "main")
        run_git(destination, "remote", "add", "origin", "https://github.com/acme/other")

    with pytest.raises(WorkspaceError):
        RepositoryLoader(tmp_path / "workspace").load("https://github.com/acme/example")

    assert sentinel.read_text(encoding="utf-8") == "user data"


def test_loader_reports_missing_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_git(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing_git)

    with pytest.raises(GitNotInstalled):
        RepositoryLoader(tmp_path / "workspace").load("https://github.com/acme/example")


def test_loader_translates_git_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def inaccessible_git(*args: object, **kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(subprocess, "run", inaccessible_git)

    with pytest.raises(GitNotInstalled):
        RepositoryLoader(tmp_path / "workspace").load("https://github.com/acme/example")


def test_clone_uses_private_empty_hooks_and_git_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    create_committed_repository(source)
    workspace = tmp_path / "workspace"
    predictable_hooks = workspace / ".tracerag-disabled-hooks"
    predictable_hooks.mkdir(parents=True)
    (predictable_hooks / "post-checkout").write_text("malicious", encoding="utf-8")
    real_run = subprocess.run

    def inspect_clone(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "clone" not in command:
            return real_run(command, **kwargs)
        hooks_path = Path(command[command.index("-c") + 1].split("=", 1)[1])
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        global_config = Path(environment["GIT_CONFIG_GLOBAL"])
        assert hooks_path != predictable_hooks
        assert hooks_path.is_dir()
        assert list(hooks_path.iterdir()) == []
        assert global_config.is_file()
        assert global_config.read_bytes() == b""
        assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
        local_command = command.copy()
        local_command[-2] = source.as_uri()
        return real_run(local_command, **kwargs)

    monkeypatch.setattr(subprocess, "run", inspect_clone)

    info = RepositoryLoader(workspace).load("https://github.com/acme/example")

    assert info.success is True


def test_clone_translates_os_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def inaccessible_clone(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "git version test", "")
        raise PermissionError("denied")

    monkeypatch.setattr(subprocess, "run", inaccessible_clone)

    with pytest.raises(CloneFailed):
        RepositoryLoader(tmp_path / "workspace").load("https://github.com/acme/example")


def test_metadata_translates_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def inaccessible_repository(path: Path) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(metadata_module, "Repo", inaccessible_repository)
    repository_url = validate_github_repository_url("https://github.com/acme/example")

    with pytest.raises(metadata_module.MetadataExtractionError):
        extract_repository_info(tmp_path / "repository", repository_url, False)


def test_metadata_translates_corrupt_object_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def corrupt_repository(path: Path) -> None:
        raise ODBError("corrupt object database")

    monkeypatch.setattr(metadata_module, "Repo", corrupt_repository)
    repository_url = validate_github_repository_url("https://github.com/acme/example")

    with pytest.raises(metadata_module.MetadataExtractionError):
        extract_repository_info(tmp_path / "repository", repository_url, False)


def test_workspace_translates_symlink_loop_resolution_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def symlink_loop(path: Path, strict: bool = False) -> Path:
        raise RuntimeError("symlink loop")

    monkeypatch.setattr(Path, "resolve", symlink_loop)

    with pytest.raises(WorkspaceError):
        RepositoryLoader._safe_destination(tmp_path, "acme_example")


def test_workspace_preparation_translates_symlink_loop_resolution_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def symlink_loop(path: Path, strict: bool = False) -> Path:
        raise RuntimeError("symlink loop")

    monkeypatch.setattr(Path, "resolve", symlink_loop)

    with pytest.raises(WorkspaceError):
        RepositoryLoader(tmp_path / "workspace")._prepare_workspace()


def test_metadata_translates_symlink_loop_resolution_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def symlink_loop(path: Path, strict: bool = False) -> Path:
        raise RuntimeError("symlink loop")

    monkeypatch.setattr(Path, "resolve", symlink_loop)
    repository_url = validate_github_repository_url("https://github.com/acme/example")

    with pytest.raises(metadata_module.MetadataExtractionError):
        extract_repository_info(tmp_path / "repository", repository_url, False)


def test_loader_removes_only_new_partial_clone_after_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "workspace" / "acme_example"

    def timeout_clone(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "git version test", "")
        destination.mkdir()
        (destination / "partial").write_text("incomplete", encoding="utf-8")
        raise subprocess.TimeoutExpired(command, 0.01)

    monkeypatch.setattr(subprocess, "run", timeout_clone)

    with pytest.raises(CloneFailed, match="timed out"):
        RepositoryLoader(tmp_path / "workspace", clone_timeout_seconds=0.01).load(
            "https://github.com/acme/example"
        )

    assert not destination.exists()


@pytest.mark.parametrize(
    ("stderr", "expected_exception"),
    [
        ("fatal: repository 'https://github.com/acme/example/' not found", RepositoryNotFound),
        ("fatal: Authentication failed for 'https://github.com/acme/example/'", RepositoryNotPublic),
        ("fatal: unable to access repository", CloneFailed),
    ],
)
def test_loader_translates_clone_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stderr: str,
    expected_exception: type[Exception],
) -> None:
    def failed_clone(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "git version test", "")
        raise subprocess.CalledProcessError(128, command, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", failed_clone)

    with pytest.raises(expected_exception):
        RepositoryLoader(tmp_path / "workspace").load("https://github.com/acme/example")


def test_manual_script_prints_human_readable_success(capsys: pytest.CaptureFixture[str]) -> None:
    expected = RepositoryInfo(
        success=True,
        owner="acme",
        repository_name="example",
        repo_url="https://github.com/acme/example",
        local_path="workspace/acme_example",
        branch="main",
        current_commit="a" * 40,
        total_files=12,
        repository_size_bytes=4_200_000,
        reused_existing_clone=False,
    )

    class SuccessfulLoader:
        def load(self, repository_url: str) -> RepositoryInfo:
            assert repository_url == "https://github.com/acme/example"
            return expected

    exit_code = repository_loader_main(
        ["https://github.com/acme/example"], loader=SuccessfulLoader()
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Status:              SUCCESS" in output
    assert "Repository:          acme/example" in output
    assert "Commit:              aaaaaaaaaaaa" in output
    assert "Files:               12" in output
    assert "Size:                4.2 MB" in output
    assert "Existing Clone:      No" in output


def test_manual_script_returns_nonzero_for_loader_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingLoader:
        def load(self, repository_url: str) -> RepositoryInfo:
            raise RepositoryNotFound("The public repository was not found.")

    exit_code = repository_loader_main(
        ["https://github.com/acme/missing"], loader=FailingLoader()
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "Status: ERROR" in output
    assert "Reason: The public repository was not found." in output
    assert "Traceback" not in output


@pytest.mark.integration
def test_public_repository_integration(tmp_path: Path) -> None:
    info = RepositoryLoader(tmp_path / "workspace").load(
        "https://github.com/octocat/Hello-World.git"
    )

    assert info.repo_url == "https://github.com/octocat/Hello-World"
    assert info.current_commit is not None
    assert len(info.current_commit) == 40
    assert info.total_files > 0
    assert Path(info.local_path) == (tmp_path / "workspace" / "octocat_Hello-World").resolve()
    assert run_git(Path(info.local_path), "rev-parse", "--is-shallow-repository") == "true"
