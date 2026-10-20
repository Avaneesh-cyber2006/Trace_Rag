"""Public models and enums for Module 7."""

from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Mapping, Sequence


class GitChangeType(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    TYPE_CHANGED = "type_changed"
    UNMERGED = "unmerged"
    UNKNOWN = "unknown"


class GitIncompleteReason(str, Enum):
    SHALLOW_HISTORY = "shallow_history"
    MAX_COMMITS_REACHED = "max_commits_reached"
    PARENT_UNAVAILABLE = "parent_unavailable"
    MERGE_SEMANTICS_PARTIAL = "merge_semantics_partial"
    MERGES_EXCLUDED = "merges_excluded"
    MISSING_METADATA = "missing_metadata"
    PATH_FILTER_APPLIED = "path_filter_applied"
    CO_CHANGE_LIMIT_EXCEEDED = "co_change_limit_exceeded"


class GitErrorCode(str, Enum):
    NOT_A_REPOSITORY = "not_a_repository"
    GIT_UNAVAILABLE = "git_unavailable"
    INVALID_REVISION = "invalid_revision"
    COMMAND_FAILED = "command_failed"
    COMMAND_TIMEOUT = "command_timeout"
    OUTPUT_TOO_LARGE = "output_too_large"
    PARSE_ERROR = "parse_error"
    INVENTORY_MISMATCH = "inventory_mismatch"
"""
