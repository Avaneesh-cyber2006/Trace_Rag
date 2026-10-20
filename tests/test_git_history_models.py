"""Tests for Module 7 models and enums."""

import pytest
from dataclasses import FrozenInstanceError
from types import MappingProxyType

from backend.git_history import (
    GitChangeType,
    GitCommit,
    GitErrorCode,
    GitFileChange,
    GitHistoryInventory,
    GitHistoryScope,
    GitIncompleteReason,
)
from backend.git_history.exceptions import GitParseError, GitRevisionError
from backend.git_history.identities import compute_change_id
from backend.git_history.invariants import EMPTY_TREE_SHA1


def _commit(**overrides):
    kwargs = dict(
        commit_id="a" * 40,
        parent_ids=(),
        author_name="A",
        author_email="a@example.com",
        author_timestamp=1,
        author_timezone="+00:00",
        committer_name="C",
        committer_email="c@example.com",
        committer_timestamp=2,
        committer_timezone="+00:00",
        message="msg",
    )
    parents = overrides.get("parent_ids", kwargs["parent_ids"])
    if "is_root" not in overrides:
        kwargs["is_root"] = len(parents) == 0
    if "is_merge" not in overrides:
        kwargs["is_merge"] = len(parents) > 1
    kwargs.update(overrides)
    return GitCommit(**kwargs)


def _scope(**overrides):
    kwargs = dict(
        revision="HEAD",
        path_filter=(),
        max_commits=None,
        include_merges=True,
        detect_renames=True,
        detect_copies=False,
        include_commit_messages=True,
    )
    kwargs.update(overrides)
    return GitHistoryScope(**kwargs)


def _change(**overrides):
    kwargs = dict(
        change_id="0" * 64,
        commit_id="a" * 40,
        change_type=GitChangeType.MODIFIED,
        old_path=None,
        new_path="x.py",
        similarity=None,
    )
    kwargs.update(overrides)
    return GitFileChange(**kwargs)


def _commits(ids):
    out = []
    for cid in ids:
        out.append(_commit(commit_id=cid, committer_timestamp=1))
    return tuple(out)


def _inventory(**overrides):
    commits = overrides.pop("commits", _commits(["a" * 40]))
    changes = overrides.pop("changes", ())
    commit_ids = overrides.pop("commit_ids", tuple(c.commit_id for c in commits))
    kwargs = dict(
        repository_namespace=None,
        scope=_scope(),
        commits=commits,
        changes=changes,
        commit_ids=commit_ids,
        commits_by_id={},
        changes_by_commit={},
        changes_by_path={},
        commits_touching_path={},
        co_changed_paths={},
        co_change_incomplete_paths=(),
        history_is_complete=True,
        incomplete_reasons=(),
        commit_count=len(commits),
        change_count=len(changes),
        truncated_by_limit=False,
        shallow_boundary_commit_ids=(),
        commits_with_unavailable_baseline=(),
        detached_head=False,
        git_version=None,
    )
    kwargs.update(overrides)
    return GitHistoryInventory(**kwargs)


# Enums
def test_change_type_members_and_values():
    assert GitChangeType.ADDED.value == "added"
    assert GitChangeType.MODIFIED.value == "modified"
    assert GitChangeType.DELETED.value == "deleted"
    assert GitChangeType.RENAMED.value == "renamed"
    assert GitChangeType.COPIED.value == "copied"
    assert GitChangeType.TYPE_CHANGED.value == "type_changed"
    assert GitChangeType.UNMERGED.value == "unmerged"
    assert GitChangeType.UNKNOWN.value == "unknown"


def test_incomplete_reason_members_and_values():
    assert GitIncompleteReason.SHALLOW_HISTORY.value == "shallow_history"
    assert GitIncompleteReason.MAX_COMMITS_REACHED.value == "max_commits_reached"
    assert GitIncompleteReason.PARENT_UNAVAILABLE.value == "parent_unavailable"
    assert GitIncompleteReason.MERGE_SEMANTICS_PARTIAL.value == "merge_semantics_partial"
    assert GitIncompleteReason.MERGES_EXCLUDED.value == "merges_excluded"
    assert GitIncompleteReason.MISSING_METADATA.value == "missing_metadata"
    assert GitIncompleteReason.PATH_FILTER_APPLIED.value == "path_filter_applied"
    assert GitIncompleteReason.CO_CHANGE_LIMIT_EXCEEDED.value == "co_change_limit_exceeded"


def test_error_code_members_and_values():
    assert GitErrorCode.NOT_A_REPOSITORY.value == "not_a_repository"
    assert GitErrorCode.GIT_UNAVAILABLE.value == "git_unavailable"
    assert GitErrorCode.INVALID_REVISION.value == "invalid_revision"
    assert GitErrorCode.COMMAND_FAILED.value == "command_failed"
    assert GitErrorCode.COMMAND_TIMEOUT.value == "command_timeout"
    assert GitErrorCode.OUTPUT_TOO_LARGE.value == "output_too_large"
    assert GitErrorCode.PARSE_ERROR.value == "parse_error"
    assert GitErrorCode.INVENTORY_MISMATCH.value == "inventory_mismatch"


def test_enums_are_str_subclasses():
    assert isinstance(GitChangeType.ADDED, str)


# GitCommit
def test_commit_is_frozen_and_slotted():
    commit = _commit()
    with pytest.raises(FrozenInstanceError):
        commit.commit_id = "z" * 40
    assert not hasattr(commit, "__dict__")


def test_commit_validates_object_id():
    with pytest.raises(GitRevisionError):
        _commit(commit_id="A" * 40)
    with pytest.raises(GitRevisionError):
        _commit(commit_id="a" * 39)


def test_commit_preserves_original_parent_order():
    commit = _commit(parent_ids=("b" * 40, "a" * 40))
    assert commit.parent_ids == ("b" * 40, "a" * 40)


def test_commit_rejects_duplicate_parents():
    with pytest.raises(GitParseError):
        _commit(parent_ids=("a" * 40, "a" * 40))


def test_merge_and_root_flags_follow_parent_count():
    assert _commit(parent_ids=()).is_root is True
    assert _commit(parent_ids=()).is_merge is False
    assert _commit(parent_ids=("a" * 40, "b" * 40)).is_merge is True


def test_merge_and_root_flags_are_derived_not_trusted():
    with pytest.raises(GitParseError):
        _commit(parent_ids=(), is_root=False)
    with pytest.raises(GitParseError):
        _commit(parent_ids=("a" * 40, "b" * 40), is_merge=False)
    with pytest.raises(GitParseError):
        _commit(parent_ids=(), is_merge=True)


def test_commit_accepts_negative_epoch_timestamps():
    assert _commit(author_timestamp=-1).author_timestamp == -1


def test_commit_rejects_non_utc_style_timezone():
    with pytest.raises(GitParseError):
        _commit(author_timezone="+0000")


def test_scope_is_frozen_and_tuple_typed():
    scope = _scope()
    with pytest.raises(FrozenInstanceError):
        scope.revision = "other"
    assert isinstance(scope.path_filter, tuple)


# GitFileChange
def test_change_requires_at_least_one_path():
    with pytest.raises(GitParseError):
        _change(old_path=None, new_path=None)


def test_similarity_only_for_rename_and_copy():
    assert _change(change_type=GitChangeType.RENAMED, old_path="a", new_path="b", similarity=87).similarity == 87
    with pytest.raises(GitParseError):
        _change(change_type=GitChangeType.MODIFIED, new_path="a", similarity=10)


def test_added_sets_only_new_path():
    change = _change(change_type=GitChangeType.ADDED, new_path="a.py")
    assert change.old_path is None and change.new_path == "a.py"


def test_deleted_sets_only_old_path():
    change = _change(change_type=GitChangeType.DELETED, old_path="a.py", new_path=None)
    assert change.old_path == "a.py" and change.new_path is None


def test_copy_and_rename_set_both_paths():
    change = _change(change_type=GitChangeType.COPIED, old_path="src.py", new_path="dst.py", similarity=100)
    assert (change.old_path, change.new_path) == ("src.py", "dst.py")


def test_change_id_matches_canonical_digest():
    change = _change(change_type=GitChangeType.MODIFIED, new_path="src/app.py")
    assert change.change_id == compute_change_id(change.commit_id, GitChangeType.MODIFIED, None, "src/app.py")


def test_change_id_cannot_be_forged_by_the_caller():
    forged = _change(change_type=GitChangeType.MODIFIED, new_path="src/app.py",
                     change_id="0" * 64)
    assert forged.change_id != "0" * 64
    assert forged.change_id == compute_change_id(forged.commit_id, GitChangeType.MODIFIED,
                                                 None, "src/app.py")


def test_similarity_range_is_enforced():
    with pytest.raises(GitParseError):
        _change(change_type=GitChangeType.RENAMED, old_path="a", new_path="b", similarity=101)


# Inventory
def test_inventory_publishes_immutable_snapshots():
    inv = _inventory()
    assert isinstance(inv.commits_by_id, MappingProxyType)
    with pytest.raises(TypeError):
        inv.commits_by_id["x"] = None
    with pytest.raises(TypeError):
        inv.changes_by_commit["x"] = ()


def test_inventory_copies_the_backing_store():
    backing = {"a" * 40: _commit()}
    inv = _inventory(commits_by_id=backing)
    backing["b" * 40] = _commit()
    assert "b" * 40 not in inv.commits_by_id


def test_inventory_counts_match_lengths():
    inv = _inventory()
    assert inv.commit_count == len(inv.commits)
    assert inv.change_count == len(inv.changes)
    assert inv.commit_ids == tuple(c.commit_id for c in inv.commits)


def test_inventory_rejects_an_inconsistent_count():
    with pytest.raises(GitParseError):
        _inventory(commit_count=99)
    with pytest.raises(GitParseError):
        _inventory(change_count=99)


def test_history_is_complete_iff_no_reasons():
    assert _inventory(incomplete_reasons=()).history_is_complete is True
    assert _inventory(incomplete_reasons=(GitIncompleteReason.SHALLOW_HISTORY,), history_is_complete=False).history_is_complete is False


def test_inventory_rejects_non_tuple_collections():
    with pytest.raises(GitParseError):
        _inventory(commits=[])