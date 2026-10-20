"""Tests for Module 7 exception hierarchy."""

from backend.git_history.exceptions import (
    GitCommandError,
    GitCommandTimeoutError,
    GitConfigurationError,
    GitHistoryError,
    GitInventoryMismatchError,
    GitOutputTooLargeError,
    GitParseError,
    GitRepositoryError,
    GitRevisionError,
    GitUnavailableError,
)


def test_every_error_descends_from_git_history_error():
    for cls in (GitUnavailableError, GitRepositoryError, GitRevisionError,
                GitCommandError, GitCommandTimeoutError,
                GitOutputTooLargeError, GitParseError,
                GitInventoryMismatchError, GitConfigurationError):
        assert issubclass(cls, GitHistoryError), cls.__name__


def test_command_failures_descend_from_git_command_error():
    assert issubclass(GitCommandTimeoutError, GitCommandError)
    assert issubclass(GitOutputTooLargeError, GitCommandError)


def test_error_message_is_preserved():
    assert str(GitParseError("bad header at byte 12")) == "bad header at byte 12"