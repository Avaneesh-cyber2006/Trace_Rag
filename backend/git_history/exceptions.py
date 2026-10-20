"""Typed exception hierarchy for Module 7."""


class GitHistoryError(Exception):
    """Base class for all Module 7 errors."""


class GitUnavailableError(GitHistoryError):
    pass


class GitRepositoryError(GitHistoryError):
    pass


class GitRevisionError(GitHistoryError):
    pass


class GitCommandError(GitHistoryError):
    pass


class GitCommandTimeoutError(GitCommandError):
    pass


class GitOutputTooLargeError(GitCommandError):
    pass


class GitParseError(GitHistoryError):
    pass


class GitInventoryMismatchError(GitHistoryError):
    pass


class GitConfigurationError(GitHistoryError):
    pass
