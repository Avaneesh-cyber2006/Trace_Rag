"""Tests for Module 7 identities."""

from hashlib import sha256

import pytest

from backend.git_history.exceptions import GitParseError, GitRevisionError
from backend.git_history.identities import compute_change_id
from backend.git_history.invariants import require_object_id, require_timezone
from backend.git_history.models import GitChangeType


def test_change_id_is_canonical_sha256_over_sorted_json():
    digest = compute_change_id("a" * 40, GitChangeType.MODIFIED, None, "src/app.py")
    assert digest == sha256(
        b'git-history:change:v1:{"change_type":"modified","commit_id":"' + b"a" * 40
        + b'","new_path":"src/app.py","old_path":null}'
    ).hexdigest()
    assert len(digest) == 64 and digest == digest.lower()


def test_change_id_distinguishes_null_and_path():
    assert compute_change_id("b" * 40, GitChangeType.ADDED, None, "x.py") != \
           compute_change_id("b" * 40, GitChangeType.ADDED, "x.py", None)


def test_change_id_is_stable_across_calls():
    args = ("c" * 40, GitChangeType.RENAMED, "old.py", "new.py")
    assert compute_change_id(*args) == compute_change_id(*args)


def test_require_object_id_rejects_bad():
    with pytest.raises(GitRevisionError):
        require_object_id("A" * 40)
    assert require_object_id("a" * 40) == "a" * 40


def test_require_timezone_rejects_bad():
    with pytest.raises(GitParseError):
        require_timezone("+0000")
    assert require_timezone("+00:00") == "+00:00"