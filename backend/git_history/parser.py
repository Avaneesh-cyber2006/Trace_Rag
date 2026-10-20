"""Pure parser for git cat-file --batch output."""

from dataclasses import dataclass, field
from typing import Sequence

from .exceptions import GitParseError
from .invariants import require_object_id as _require_object_id


def normalize_timezone(raw: str) -> str:
    raw = raw.strip()
    if len(raw) == 5 and raw[0] in "+-" and raw[1:3].isdigit() and raw[3:5].isdigit():
        return f"{raw[0]}{raw[1:3]}:{raw[3:5]}"
    if len(raw) == 6 and raw[0] in "+-" and raw[1:3].isdigit() and raw[3:5].isdigit() and raw[5] == ":":
        # already? handle ±HH:MM? Actually length 6 for +HH:MM? +00:00 length 6
        return raw
    if len(raw) == 6 and raw[0] in "+-" and raw[1:3].isdigit() and raw[3] == ":" and raw[4:6].isdigit():
        return raw
    raise GitParseError(f"invalid timezone: {raw!r}")


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


def _parse_header_line(line: bytes, header: str):
    # returns (name, email, timestamp, tz, present)?? handled inline
    pass


def parse_batch_stream(data: bytes, expected_ids: Sequence[str]) -> dict[str, RawCommitObject]:
    # parse frames
    pos = 0
    n = len(data)
    results: dict[str, RawCommitObject] = {}
    seen: set[str] = set()
    # We'll parse sequential frames until data exhausted
    while pos < n:
        # skip trailing newline
        if data[pos:pos + 1] == b"\n":
            pos += 1
            continue
        # frame header: "<oid> <type> <size>\n"
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
        # expect trailing newline
        if pos < n and data[pos:pos + 1] == b"\n":
            pos += 1
        if typ == b"missing":
            raise GitParseError("missing object")
        if typ != b"commit":
            raise GitParseError("not a commit")
        _require_object_id(oid)
        # parse body
        # split headers/message on first blank line
        # body bytes
        # find blank line
        blank = body.find(b"\n\n")
        if blank == -1:
            headers_bytes = body
            message_bytes = b""
        else:
            headers_bytes = body[:blank]
            message_bytes = body[blank + 2:]
        # Actually message includes trailing? keep bytes as is (minus one newline?)
        # Spec: message is bytes after blank line. We'll keep message_bytes including trailing newline? Tests use b"msg\n"? 
        # The _batch helper: body + b"\n" frame. body ends with ... \n\nmsg\n? Let's keep message_bytes = body[blank+2:] 
        # but if body ended with \n before frame trailing, message includes \n. Fine.

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
                # parse Name <email> ts tz
                rest = line[7:]
                # find email
                # format: name <email> timestamp tz
                # name may contain spaces; email in <>
                lt = rest.rfind(b"<")
                gt = rest.find(b">", lt)
                if lt == -1 or gt == -1:
                    raise GitParseError("malformed author")
                name = rest[:lt].strip().decode("utf-8")
                email = rest[lt + 1:gt].decode("utf-8")
                tail = rest[gt + 1:].strip().split()
                if len(tail) != 2:
                    raise GitParseError("malformed author")
                try:
                    ts = int(tail[0])
                except ValueError:
                    raise GitParseError("malformed author")
                tz = tail[1].decode("ascii")
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
                name = rest[:lt].strip().decode("utf-8")
                email = rest[lt + 1:gt].decode("utf-8")
                tail = rest[gt + 1:].strip().split()
                if len(tail) != 2:
                    raise GitParseError("malformed committer")
                try:
                    ts = int(tail[0])
                except ValueError:
                    raise GitParseError("malformed committer")
                tz = tail[1].decode("ascii")
                committer_name = name
                committer_email = email
                committer_timestamp = ts
                committer_tz_raw = tz
            elif line.startswith(b"encoding "):
                encoding = line[9:].decode("ascii").strip()
            elif line.startswith(b"gpgsig "):
                pass
            else:
                # unknown header; ignore? Malformed? For required headers handled.
                # If line has no space, malformed
                if b" " not in line:
                    raise GitParseError("malformed header")
        if tree_id is None:
            raise GitParseError("missing tree")
        # normalize tz
        author_tz = normalize_timezone(author_tz_raw) if author_present else "+00:00"
        committer_tz = normalize_timezone(committer_tz_raw) if committer_present else "+00:00"
        if not author_present:
            author_name = ""
            author_email = ""
            author_timestamp = 0
            author_tz = "+00:00"
        if not committer_present:
            committer_name = ""
            committer_email = ""
            committer_timestamp = 0
            committer_tz = "+00:00"
        # decode strict handled by analyzer? store bytes; message_bytes remains bytes
        # But test expects message_bytes bytes. Keep.
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
        seen.add(oid)

    # verify expected
    for eid in expected_ids:
        if eid not in results:
            raise GitParseError("missing requested object")
    # also verify no extra? not necessary
    return results
