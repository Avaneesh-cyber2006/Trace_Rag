"""Tests for Module 7 parser."""

import pytest

from backend.git_history.exceptions import GitParseError
from backend.git_history.models import GitChangeType
from backend.git_history.parser import (
    RawCommitObject,
    normalize_timezone,
    parse_batch_stream,
    parse_name_status,
)


def _batch(payload: bytes) -> bytes:
    body = b"tree " + b"a" * 40 + b"\n" + payload
    return b"a" * 40 + b" commit " + str(len(body)).encode() + b"\n" + body + b"\n"


def test_parses_tree_and_parents_in_original_order():
    raw = _batch(b"parent " + b"b" * 40 + b"\nparent " + b"c" * 40 + b"\n"
                 b"author A U Thor <a@example.com> 1577865600 -0800\n"
                 b"committer A U Thor <a@example.com> 1577865600 -0800\n\nmsg\n")
    parsed = parse_batch_stream(raw, ["a" * 40])
    commit = parsed["a" * 40]
    assert commit.tree_id == "a" * 40
    assert commit.parent_ids == ("b" * 40, "c" * 40)


def test_normalizes_timezone_to_colon_form():
    assert normalize_timezone("-0800") == "-08:00"
    assert normalize_timezone("+0530") == "+05:30"
    assert normalize_timezone("+00:00") == "+00:00"


def test_missing_metadata_headers_yield_sentinels():
    raw = _batch(b"\nno metadata here\n")
    commit = parse_batch_stream(raw, ["a" * 40])["a" * 40]
    assert commit.author_name == "" and commit.author_timestamp == 0
    assert commit.author_timezone == "+00:00"
    assert commit.author_header_present is False
    assert commit.committer_header_present is False


def test_malformed_header_raises_parse_error():
    raw = _batch(b"author broken header\n\nmsg\n")
    with pytest.raises(GitParseError):
        parse_batch_stream(raw, ["a" * 40])


def test_invalid_utf8_message_raises_parse_error():
    raw = _batch(b"author A <a@e.com> 1 +0000\ncommitter A <a@e.com> 1 +0000\n\n\xff\xfe\n")
    with pytest.raises(GitParseError):
        parse_batch_stream(raw, ["a" * 40])


def test_missing_requested_object_is_reported():
    raw = _batch(b"\nmsg\n")
    with pytest.raises(GitParseError):
        parse_batch_stream(raw, ["a" * 40, "d" * 40])


def test_encoding_header_is_captured():
    raw = _batch(b"encoding ISO-8859-1\nauthor A <a@e.com> 1 +0000\ncommitter A <a@e.com> 1 +0000\n\ncaf\xe9\n")
    commit = parse_batch_stream(raw, ["a" * 40])["a" * 40]
    assert commit.encoding == "ISO-8859-1"


def test_unicode_message_is_decoded():
    raw = _batch(b"author A <a@e.com> 1 +0000\ncommitter A <a@e.com> 1 +0000\n\ncaf\xc3\xa9 \xe2\x9c\x93\n")
    commit = parse_batch_stream(raw, ["a" * 40])["a" * 40]
    assert b"caf\xc3\xa9" in commit.message_bytes


def test_root_commit_has_no_parents():
    raw = _batch(b"author A <a@e.com> 1 +0000\ncommitter A <a@e.com> 1 +0000\n\nmsg\n")
    commit = parse_batch_stream(raw, ["a" * 40])["a" * 40]
    assert commit.parent_ids == ()


# diff-tree parsing
def test_parses_single_letter_statuses():
    records = parse_name_status(b"A\0added.py\0M\0mod.py\0D\0gone.py\0T\0type.py\0U\0merge.py\0")
    assert [(r.change_type, r.old_path if r.change_type is GitChangeType.DELETED else r.new_path) for r in records] == [
        (GitChangeType.ADDED, "added.py"), (GitChangeType.MODIFIED, "mod.py"),
        (GitChangeType.DELETED, "gone.py"), (GitChangeType.TYPE_CHANGED, "type.py"),
        (GitChangeType.UNMERGED, "merge.py")]


def test_parses_rename_with_similarity():
    records = parse_name_status(b"R087\0old.py\0new.py\0")
    assert records[0].change_type is GitChangeType.RENAMED
    assert records[0].similarity == 87
    assert (records[0].old_path, records[0].new_path) == ("old.py", "new.py")


def test_parses_copy_with_similarity():
    records = parse_name_status(b"C100\0src.py\0dst.py\0")
    assert records[0].change_type is GitChangeType.COPIED
    assert records[0].old_path == "src.py" and records[0].new_path == "dst.py"


def test_ignores_trailing_empty_record():
    assert len(parse_name_status(b"A\0a.py\0")) == 1


def test_rejects_unexpected_field_count():
    with pytest.raises(GitParseError):
        parse_name_status(b"R087\0old.py\0")


def test_rejects_invalid_utf8_path():
    with pytest.raises(GitParseError):
        parse_name_status(b"A\0\xff\xfe.py\0")


def test_paths_with_spaces_are_preserved():
    records = parse_name_status(b"A\0a file with spaces.py\0")
    assert records[0].new_path == "a file with spaces.py"


def test_invalid_utf8_path_error_carries_the_offending_bytes():
    with pytest.raises(GitParseError) as excinfo:
        parse_name_status(b"A\0\xff\xfe.py\0")
    message = str(excinfo.value).lower()
    assert "fffe" in message or "ff fe" in message or "0xff" in message


def test_unicode_filename_is_preserved():
    records = parse_name_status("A\0café.py\0".encode("utf-8"))
    assert records[0].new_path == "café.py"