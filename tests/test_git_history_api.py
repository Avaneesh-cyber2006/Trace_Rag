"""Tests for Module 7 public API."""

import pytest

from backend.git_history import GitHistoryAnalyzer, analyze_git_history, GitConfigurationError
import backend.git_history as public


def test_public_exports_are_importable():
    for name in ("GitHistoryAnalyzer", "analyze_git_history", "GitChangeType",
                 "GitIncompleteReason", "GitErrorCode", "GitCommit", "GitFileChange",
                 "GitHistoryScope", "GitHistoryInventory", "GitHistoryError",
                 "GitUnavailableError", "GitRepositoryError", "GitRevisionError",
                 "GitCommandError", "GitCommandTimeoutError", "GitOutputTooLargeError",
                 "GitParseError", "GitInventoryMismatchError", "GitConfigurationError"):
        assert name in public.__all__, name
        assert hasattr(public, name)


def test_convenience_function_matches_the_class(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    direct = GitHistoryAnalyzer().analyze(repo.path)
    convenience = analyze_git_history(repo.path)
    assert direct.commit_ids == convenience.commit_ids


def test_convenience_function_rejects_invalid_configuration(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    with pytest.raises(GitConfigurationError):
        analyze_git_history(repo.path, command_timeout_seconds=0)