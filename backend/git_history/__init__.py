"""Git History Analyzer (Module 7)."""

from .analyzer import GitHistoryAnalyzer, analyze_git_history
from .exceptions import (
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
from .identities import compute_change_id
from .invariants import (
    EMPTY_TREE_SHA1,
    EMPTY_TREE_SHA256,
    snapshot_mapping,
)
from .models import (
    GitChangeType,
    GitCommit,
    GitErrorCode,
    GitFileChange,
    GitHistoryInventory,
    GitHistoryScope,
    GitIncompleteReason,
)
from .parser import RawCommitObject, RawFileChange, parse_batch_stream, parse_name_status, normalize_timezone

__all__ = [
    "GitHistoryAnalyzer",
    "analyze_git_history",
    "GitCommandError",
    "GitCommandTimeoutError",
    "GitConfigurationError",
    "GitHistoryError",
    "GitInventoryMismatchError",
    "GitOutputTooLargeError",
    "GitParseError",
    "GitRepositoryError",
    "GitRevisionError",
    "GitUnavailableError",
    "compute_change_id",
    "GitChangeType",
    "GitCommit",
    "GitErrorCode",
    "GitFileChange",
    "GitHistoryInventory",
    "GitHistoryScope",
    "GitIncompleteReason",
    "RawCommitObject",
    "RawFileChange",
]
