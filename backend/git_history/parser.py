"""Pure parser for git cat-file --batch output."""

from dataclasses import dataclass
from typing import Sequence

from .exceptions import GitParseError
from .invariants import require_object_id as _require_object_id
from .models import GitChangeType

__all__ = ["RawCommitObject", "RawFileChange", "parse_batch_stream", "parse_name_status", "normalize_timezone"]


@dataclass(frozen=True, slots=True)
class RawCommitObject:
    commit_id: str
    tree_id: str
    parent_ids: tuple[str, ...]
    author_name: str
    author_email: str
    author_timestamp: int
    author_timezone: str
    author_header_present: bool
    committer_name: str
    committer_email: str
    committer_timestamp: int
    committer_timezone: str
    committer_header_present: bool
    message_bytes: bytes
    encoding: "str | None"


@dataclass(frozen=True, slots=True)
class RawFileChange:
    change_type: GitChangeType
    old_path: "str | None"
    new_path: "str | None"
    similarity: "int | None" = None


def normalize_timezone(raw: str) -> str:
    raw = raw.strip()
    if len(raw) == 5 and raw[0] in "+-" and raw[1:3].isdigit() and raw[3:5].isdigit():
        return f"{raw[0]}{raw[1:3]}:{raw[3:5]}"
    if len(raw) == 6 and raw[0] in "+-" and raw[1:3].isdigit() and raw[3] == ":" and raw[4:6].isdigit():
        return raw
    raise GitParseError(f"invalid timezone: {raw!r}")


def parse_batch_stream(data: bytes, expected_ids: Sequence[str]) -> dict[str, RawCommitObject]:
    pos = 0
    n = len(data)
    results: dict[str, RawCommitObject] = {}
    while pos < n:
        if data[pos:pos + 1] == b"\n":
            pos += 1
            continue
        nl = data.find(b"\n", pos)
        if nl == -1:
            raise GitParseError("truncated frame header")
        header = data[pos:nl]
        parts = header.split(b" ")
        if len(parts) != 3:
            raise GitParseError("bad frame header")
        oid = parts[0].decode("ascii")
        typ = parts[1]
        try:
            size = int(parts[2])
        except ValueError:
            raise GitParseError("bad size")
        pos = nl + 1
        body = data[pos:pos + size]
        if len(body) != size:
            raise GitParseError("truncated frame body")
        pos += size
        if pos < n and data[pos:pos + 1] == b"\n":
            pos += 1
        if typ == b"missing":
            raise GitParseError("missing object")
        if typ != b"commit":
            raise GitParseError("not a commit")
        _require_object_id(oid)
        blank = body.find(b"\n\n")
        if blank == -1:
            headers_bytes = body
            message_bytes = b""
        else:
            headers_bytes = body[:blank]
            message_bytes = body[blank + 2:]
        lines = headers_bytes.split(b"\n") if headers_bytes else []
        tree_id = None
        parent_ids = []
        author_name = ""
        author_email = ""
        author_timestamp = 0
        author_tz_raw = "+0000"
        author_present = False
        committer_name = ""
        committer_email = ""
        committer_timestamp = 0
        committer_tz_raw = "+0000"
        committer_present = False
        encoding = None
        for line in lines:
            if line == b"":
                continue
            if line.startswith(b"tree "):
                tid = line[5:].decode("ascii")
                _require_object_id(tid)
                tree_id = tid
            elif line.startswith(b"parent "):
                pid = line[7:].decode("ascii")
                _require_object_id(pid)
                parent_ids.append(pid)
            elif line.startswith(b"author "):
                author_present = True
                rest = line[7:]
                lt = rest.rfind(b"<")
                gt = rest.find(b">", lt)
                if lt == -1 or gt == -1:
                    raise GitParseError("malformed author")
                try:
                    name = rest[:lt].strip().decode("utf-8")
                    email = rest[lt + 1:gt].decode("utf-8")
                except UnicodeDecodeError as e:
                    raise GitParseError(f"invalid utf-8 in header at offset {e.start}")
                tail = rest[gt + 1:].strip().split()
                if len(tail) != 2:
                    raise GitParseError("malformed author")
                try:
                    ts = int(tail[0])
                except ValueError:
                    raise GitParseError("malformed author")
                try:
                    tz = tail[1].decode("ascii")
                except UnicodeDecodeError as e:
                    raise GitParseError(f"invalid timezone encoding at offset {e.start}")
                author_name = name
                author_email = email
                author_timestamp = ts
                author_tz_raw = tz
            elif line.startswith(b"committer "):
                committer_present = True
                rest = line[10:]
                lt = rest.rfind(b"<")
                gt = rest.find(b">", lt)
                if lt == -1 or gt == -1:
                    raise GitParseError("malformed committer")
                try:
                    name = rest[:lt].strip().decode("utf-8")
                    email = rest[lt + 1:gt].decode("utf-8")
                except UnicodeDecodeError as e:
                    raise GitParseError(f"invalid utf-8 in header at offset {e.start}")
                tail = rest[gt + 1:].strip().split()
                if len(tail) != 2:
                    raise GitParseError("malformed committer")
                try:
                    ts = int(tail[0])
                except ValueError:
                    raise GitParseError("malformed committer")
                try:
                    tz = tail[1].decode("ascii")
                except UnicodeDecodeError as e:
                    raise GitParseError(f"invalid timezone encoding at offset {e.start}")
                committer_name = name
                committer_email = email
                committer_timestamp = ts
                committer_tz_raw = tz
            elif line.startswith(b"encoding "):
                encoding = line[9:].decode("ascii").strip()
            elif b" " not in line:
                raise GitParseError("malformed header")
        if tree_id is None:
            raise GitParseError("missing tree")
        # validate message decoding strictly
        enc = encoding or "utf-8"
        try:
            message_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError) as e:
            offset = getattr(e, "start", 0)
            raise GitParseError(f"invalid message encoding at offset {offset}")
        if author_present:
            author_tz = normalize_timezone(author_tz_raw)
        else:
            author_name = ""
            author_email = ""
            author_timestamp = 0
            author_tz = "+00:00"
        if committer_present:
            committer_tz = normalize_timezone(committer_tz_raw)
        else:
            committer_name = ""
            committer_email = ""
            committer_timestamp = 0
            committer_tz = "+00:00"
        obj = RawCommitObject(
            commit_id=oid,
            tree_id=tree_id,
            parent_ids=tuple(parent_ids),
            author_name=author_name,
            author_email=author_email,
            author_timestamp=author_timestamp,
            author_timezone=author_tz,
            author_header_present=author_present,
            committer_name=committer_name,
            committer_email=committer_email,
            committer_timestamp=committer_timestamp,
            committer_timezone=committer_tz,
            committer_header_present=committer_present,
            message_bytes=message_bytes,
            encoding=encoding,
        )
        results[oid] = obj

    for eid in expected_ids:
        if eid not in results:
            raise GitParseError("missing requested object")
    return results


def parse_name_status(data: bytes) -> tuple[RawFileChange, ...]:
    parts = data.split(b"\x00")
    i = 0
    result: list[RawFileChange] = []
    status_map = {
        b"A": GitChangeType.ADDED,
        b"M": GitChangeType.MODIFIED,
        b"D": GitChangeType.DELETED,
        b"T": GitChangeType.TYPE_CHANGED,
        b"U": GitChangeType.UNMERGED,
    }
    while i < len(parts):
        if parts[i] == b"":
            i += 1
            continue
        status = parts[i]
        if status.startswith(b"R") or status.startswith(b"C"):
            try:
                score = int(status[1:])
            except ValueError:
                raise GitParseError("bad score")
            if i + 2 >= len(parts):
                raise GitParseError("truncated record")
            try:
                old_path = parts[i + 1].decode("utf-8")
                new_path = parts[i + 2].decode("utf-8")
            except UnicodeDecodeError as e:
                offending = e.object[e.start:e.start + 4]
                raise GitParseError(f"invalid utf-8 path at offset {e.start}: {offending.hex()}")
            if old_path == "" or new_path == "":
                raise GitParseError("empty path")
            ct = GitChangeType.RENAMED if status.startswith(b"R") else GitChangeType.COPIED
            result.append(RawFileChange(ct, old_path, new_path, score))
            i += 3
        else:
            ct = status_map.get(status)
            if ct is None:
                ct = GitChangeType.UNKNOWN
            if i + 1 >= len(parts):
                raise GitParseError("truncated record")
            try:
                path = parts[i + 1].decode("utf-8")
            except UnicodeDecodeError as e:
                offending = e.object[e.start:e.start + 4]
                raise GitParseError(f"invalid utf-8 path at offset {e.start}: {offending.hex()}")
            if path == "":
                raise GitParseError("empty path")
            if status == b"X" or status not in status_map:
                # unknown
                old = None
                new = path
                ct = GitChangeType.UNKNOWN
            else:
                if status == b"D":
                    old = path
                    new = None
                else:
                    old = None
                    new = path
                if status == b"D":
                    ct = GitChangeType.DELETED
            result.append(RawFileChange(ct, old, new, None))
            i += 2
    return tuple(result)
