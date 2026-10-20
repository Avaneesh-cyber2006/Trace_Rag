"""Public models and enums for Module 7."""

import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence, Tuple

from .exceptions import GitParseError
from .identities import compute_change_id
from .invariants import (
    EMPTY_TREE_SHA1,
    OBJECT_ID_RE,
    TIMEZONE_RE,
    require_object_id,
    snapshot_mapping,
)

__all__ = [
    "GitChangeType",
    "GitIncompleteReason",
    "GitErrorCode",
    "GitCommit",
    "GitFileChange",
    "GitHistoryScope",
    "GitHistoryInventory",
]


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


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


@dataclass(frozen=True, slots=True)
class GitCommit:
    commit_id: str
    parent_ids: Tuple[str, ...]
    author_name: str
    author_email: str
    author_timestamp: int
    author_timezone: str
    committer_name: str
    committer_email: str
    committer_timestamp: int
    committer_timezone: str
    message: "str | None"
    is_merge: bool
    is_root: bool

    def __post_init__(self):
        require_object_id(self.commit_id)
        if not isinstance(self.parent_ids, tuple):
            raise GitParseError("parent_ids must be tuple")
        for p in self.parent_ids:
            require_object_id(p)
        if len(set(self.parent_ids)) != len(self.parent_ids):
            raise GitParseError("duplicate parent ids")
        if not _is_int(self.author_timestamp) or not _is_int(self.committer_timestamp):
            raise GitParseError("timestamps must be int")
        if TIMEZONE_RE.fullmatch(self.author_timezone) is None:
            raise GitParseError("invalid author timezone")
        if TIMEZONE_RE.fullmatch(self.committer_timezone) is None:
            raise GitParseError("invalid committer timezone")
        if not isinstance(self.author_name, str) or not isinstance(self.author_email, str):
            raise GitParseError("invalid author fields")
        if not isinstance(self.committer_name, str) or not isinstance(self.committer_email, str):
            raise GitParseError("invalid committer fields")
        if self.message is not None and not isinstance(self.message, str):
            raise GitParseError("message must be str or None")
        if self.is_merge != (len(self.parent_ids) > 1):
            raise GitParseError("is_merge mismatch")
        if self.is_root != (len(self.parent_ids) == 0):
            raise GitParseError("is_root mismatch")


@dataclass(frozen=True, slots=True)
class GitFileChange:
    change_id: str
    commit_id: str
    change_type: GitChangeType
    old_path: "str | None"
    new_path: "str | None"
    similarity: "int | None" = None

    def __post_init__(self):
        if not isinstance(self.change_id, str) or _HEX64.fullmatch(self.change_id) is None:
            raise GitParseError("invalid change_id")
        require_object_id(self.commit_id)
        if not isinstance(self.change_type, GitChangeType):
            # allow str
            try:
                object.__setattr__(self, "change_type", GitChangeType(self.change_type))
            except Exception:
                raise GitParseError("invalid change_type")
        if not (self.old_path is None or isinstance(self.old_path, str)):
            raise GitParseError("old_path type")
        if not (self.new_path is None or isinstance(self.new_path, str)):
            raise GitParseError("new_path type")
        if self.old_path is None and self.new_path is None:
            raise GitParseError("at least one path required")
        if self.similarity is not None:
            if not isinstance(self.similarity, int) or isinstance(self.similarity, bool):
                raise GitParseError("similarity type")
            if self.similarity < 0 or self.similarity > 100:
                raise GitParseError("similarity range")
            if self.change_type not in (GitChangeType.RENAMED, GitChangeType.COPIED):
                raise GitParseError("similarity only for rename/copy")
        else:
            if self.change_type in (GitChangeType.RENAMED, GitChangeType.COPIED):
                pass
        # path shape validation
        for p in (self.old_path, self.new_path):
            if p is not None:
                if not isinstance(p, str) or p == "":
                    raise GitParseError("empty path")
                if "\\" in p or "\x00" in p:
                    raise GitParseError("invalid path")
                if p.startswith("/"):
                    raise GitParseError("absolute path")
                parts = p.split("/")
                if any(part in ("", ".", "..") for part in parts):
                    raise GitParseError("invalid path component")
        # change-type path presence rules
        ct = self.change_type
        if ct == GitChangeType.ADDED:
            if self.old_path is not None or self.new_path is None:
                raise GitParseError("added path rule")
        elif ct == GitChangeType.DELETED:
            if self.new_path is not None or self.old_path is None:
                raise GitParseError("deleted path rule")
        elif ct in (GitChangeType.RENAMED, GitChangeType.COPIED):
            if self.old_path is None or self.new_path is None:
                raise GitParseError("rename/copy path rule")
        else:
            # modified, type_changed, unmerged, unknown: only new_path
            if self.old_path is not None or self.new_path is None:
                raise GitParseError("modified path rule")

        # recompute change_id canonically so callers cannot forge it
        ct_val = ct.value if isinstance(ct, GitChangeType) else str(ct)
        expected = compute_change_id(self.commit_id, ct_val, self.old_path, self.new_path)
        object.__setattr__(self, "change_id", expected)


@dataclass(frozen=True, slots=True)
class GitHistoryScope:
    revision: str
    path_filter: Tuple[str, ...]
    max_commits: "int | None"
    include_merges: bool
    detect_renames: bool
    detect_copies: bool
    include_commit_messages: bool

    def __post_init__(self):
        if not isinstance(self.revision, str):
            raise GitParseError("revision type")
        if not isinstance(self.path_filter, tuple):
            raise GitParseError("path_filter must be tuple")
        if self.max_commits is not None:
            if not _is_int(self.max_commits) or self.max_commits <= 0:
                raise GitParseError("max_commits")
        for p in self.path_filter:
            if not isinstance(p, str) or p == "" or "\\" in p or "\x00" in p:
                raise GitParseError("path_filter entry")
            if p.startswith("/"):
                raise GitParseError("path_filter entry")
            parts = p.split("/")
            if any(part in ("", ".", "..") for part in parts):
                raise GitParseError("path_filter entry")
        if not isinstance(self.include_merges, bool):
            raise GitParseError("include_merges")
        if not isinstance(self.detect_renames, bool):
            raise GitParseError("detect_renames")
        if not isinstance(self.detect_copies, bool):
            raise GitParseError("detect_copies")
        if not isinstance(self.include_commit_messages, bool):
            raise GitParseError("include_commit_messages")


def _as_mapping_proxy(v):
    if v is None:
        return MappingProxyType({})
    if isinstance(v, MappingProxyType):
        return v
    if isinstance(v, Mapping):
        return MappingProxyType(dict(v))
    raise GitParseError("expected mapping")


@dataclass(frozen=True, slots=True)
class GitHistoryInventory:
    repository_namespace: "str | None"
    scope: GitHistoryScope
    commits: Tuple[GitCommit, ...]
    changes: Tuple[GitFileChange, ...]
    commit_ids: Tuple[str, ...]
    commits_by_id: Mapping[str, GitCommit]
    changes_by_commit: Mapping[str, Tuple[GitFileChange, ...]]
    changes_by_path: Mapping[str, Tuple[GitFileChange, ...]]
    commits_touching_path: Mapping[str, Tuple[str, ...]]
    co_changed_paths: Mapping[str, Tuple[str, ...]]
    co_change_incomplete_paths: Tuple[str, ...]
    history_is_complete: bool
    incomplete_reasons: Tuple[Any, ...]
    commit_count: int
    change_count: int
    truncated_by_limit: bool
    shallow_boundary_commit_ids: Tuple[str, ...]
    commits_with_unavailable_baseline: Tuple[str, ...]
    detached_head: bool
    git_version: "str | None"

    def __post_init__(self):
        if not isinstance(self.commits, tuple):
            raise GitParseError("commits must be tuple")
        if not isinstance(self.changes, tuple):
            raise GitParseError("changes must be tuple")
        if not isinstance(self.commit_ids, tuple):
            raise GitParseError("commit_ids must be tuple")
        if self.commit_ids != tuple(c.commit_id for c in self.commits):
            raise GitParseError("commit_ids mismatch")
        if self.commit_count != len(self.commits):
            raise GitParseError("commit_count mismatch")
        if self.change_count != len(self.changes):
            raise GitParseError("change_count mismatch")
        if not isinstance(self.history_is_complete, bool):
            raise GitParseError("history_is_complete")
        # validate all changes reference commits
        commit_id_set = {c.commit_id for c in self.commits}
        for ch in self.changes:
            if ch.commit_id not in commit_id_set:
                raise GitParseError("change references unknown commit")
        # sort check
        sorted_commits = tuple(sorted(self.commits, key=lambda c: (-c.committer_timestamp, c.commit_id)))
        if self.commits != sorted_commits:
            raise GitParseError("commits not sorted")
        # tuple fields
        for f in (
            self.co_change_incomplete_paths,
            self.shallow_boundary_commit_ids,
            self.commits_with_unavailable_baseline,
            self.incomplete_reasons,
        ):
            if not isinstance(f, tuple):
                raise GitParseError("tuple field")
        # validate mapping value types
        for mname in ("changes_by_commit", "changes_by_path", "commits_touching_path", "co_changed_paths"):
            mval = getattr(self, mname)
            if isinstance(mval, Mapping):
                for v in mval.values():
                    if not isinstance(v, tuple):
                        raise GitParseError(f"{mname} values must be tuples")
        # snapshot mappings
        object.__setattr__(self, "commits_by_id", _as_mapping_proxy(self.commits_by_id))
        object.__setattr__(self, "changes_by_commit", _as_mapping_proxy(self.changes_by_commit))
        object.__setattr__(self, "changes_by_path", _as_mapping_proxy(self.changes_by_path))
        object.__setattr__(self, "commits_touching_path", _as_mapping_proxy(self.commits_touching_path))
        object.__setattr__(self, "co_changed_paths", _as_mapping_proxy(self.co_changed_paths))
        # validate mappings are proxy
        for m in (
            self.commits_by_id,
            self.changes_by_commit,
            self.changes_by_path,
            self.commits_touching_path,
            self.co_changed_paths,
        ):
            if not isinstance(m, MappingProxyType):
                raise GitParseError("mapping must be proxy")
        if self.repository_namespace is not None and not isinstance(self.repository_namespace, str):
            raise GitParseError("namespace")
        if not isinstance(self.scope, GitHistoryScope):
            raise GitParseError("scope")
        if self.git_version is not None and not isinstance(self.git_version, str):
            raise GitParseError("git_version")
        if not isinstance(self.truncated_by_limit, bool):
            raise GitParseError("truncated")
        if not isinstance(self.detached_head, bool):
            raise GitParseError("detached")
        # history_is_complete consistency
        if self.history_is_complete != (len(self.incomplete_reasons) == 0):
            raise GitParseError("history_is_complete mismatch")
