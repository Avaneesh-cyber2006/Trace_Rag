"""Normalization of untrusted runtime source references.

This module performs matching normalization only. It never resolves a path,
touches a filesystem, or opens a URL.
"""

import re
from dataclasses import dataclass
from enum import Enum


class SourceReferenceKind(str, Enum):
    NONE = "none"
    RELATIVE = "relative"
    BASENAME = "basename"
    ABSOLUTE = "absolute"
    EXTERNAL = "external"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class NormalizedSource:
    kind: SourceReferenceKind
    relative_path: str | None
    components: tuple[str, ...]
    basename: str | None


_ASCII_WHITESPACE = " \t\n\r\x0b\x0c"
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_DRIVE_ABSOLUTE = re.compile(r"^[A-Za-z]:/")
_DRIVE_RELATIVE = re.compile(r"^[A-Za-z]:")
_LEADING_SLASH_DRIVE = re.compile(r"^/[A-Za-z]:")
_INVALID = NormalizedSource(SourceReferenceKind.INVALID, None, (), None)
_EXTERNAL = NormalizedSource(SourceReferenceKind.EXTERNAL, None, (), None)
_NONE = NormalizedSource(SourceReferenceKind.NONE, None, (), None)


def _has_forbidden_text(text: str) -> bool:
    for character in text:
        codepoint = ord(character)
        if codepoint < 0x20:
            return True
        if 0xD800 <= codepoint <= 0xDFFF:
            return True
    return False


def _decode_percent_path(text: str) -> bytes | None:
    decoded = bytearray()
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character == "%":
            if index + 2 >= length:
                return None
            high = text[index + 1]
            low = text[index + 2]
            if high not in _HEX_DIGITS or low not in _HEX_DIGITS:
                return None
            decoded.append(int(high + low, 16))
            index += 3
            continue
        decoded.extend(character.encode("utf-8"))
        index += 1
    return bytes(decoded)


def _absolute(remainder: str) -> NormalizedSource:
    components = tuple(remainder.split("/"))
    if any(part in ("", ".", "..") for part in components):
        return _INVALID
    return NormalizedSource(
        SourceReferenceKind.ABSOLUTE, None, components, components[-1]
    )


def _relative(remainder: str) -> NormalizedSource:
    while remainder.startswith("./"):
        remainder = remainder[2:]
    if not remainder:
        return _INVALID
    components = tuple(remainder.split("/"))
    if any(part in ("", ".", "..") for part in components):
        return _INVALID
    basename = components[-1]
    if len(components) == 1:
        return NormalizedSource(
            SourceReferenceKind.BASENAME, None, components, basename
        )
    return NormalizedSource(
        SourceReferenceKind.RELATIVE, remainder, components, basename
    )


def _classify_local(remainder: str) -> NormalizedSource:
    if remainder.startswith("//"):
        return _EXTERNAL
    if remainder.startswith("/"):
        return _absolute(remainder[1:])
    drive = _DRIVE_ABSOLUTE.match(remainder)
    if drive is not None:
        return _absolute(remainder[drive.end():])
    if _DRIVE_RELATIVE.match(remainder) is not None:
        return _INVALID
    return _relative(remainder)


def _classify_file(remainder: str) -> NormalizedSource:
    if not remainder.startswith("/"):
        return _INVALID
    if remainder.startswith("///"):
        path = remainder[2:]
    elif remainder.startswith("//"):
        host = remainder[2:]
        if not host or host.startswith("/"):
            return _INVALID
        return _EXTERNAL
    else:
        path = remainder

    raw_bytes = _decode_percent_path(path)
    if raw_bytes is None:
        return _INVALID
    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return _INVALID
    if _has_forbidden_text(decoded):
        return _INVALID
    if "?" in decoded or "#" in decoded:
        return _INVALID
    decoded = decoded.replace("\\", "/")
    if _LEADING_SLASH_DRIVE.match(decoded) is not None:
        decoded = decoded[1:]
    return _classify_local(decoded)


def _classify_webpack(remainder: str) -> NormalizedSource:
    if not remainder.startswith("//"):
        return _INVALID
    path = remainder[2:]
    if path.startswith("//"):
        return _EXTERNAL
    if path.startswith("/"):
        path = path[1:]
    if not path:
        return _INVALID
    return _classify_local(path)


def _classify_scheme(scheme: str, remainder: str) -> NormalizedSource:
    if scheme == "file":
        return _classify_file(remainder)
    if scheme == "webpack":
        return _classify_webpack(remainder)
    return _EXTERNAL


def normalize_source_reference(
    source: str | None, max_bytes: int
) -> NormalizedSource:
    """Classify a reported source reference without resolving it."""
    if source is None:
        return _NONE
    if not isinstance(source, str):
        return _INVALID

    text = source.strip(_ASCII_WHITESPACE)
    if not text:
        return _INVALID
    if _has_forbidden_text(text):
        return _INVALID
    if len(text.encode("utf-8")) > max_bytes:
        return _INVALID

    path = text.replace("\\", "/")
    if path.startswith("//"):
        return _EXTERNAL
    if path.startswith("/"):
        return _absolute(path[1:])
    drive = _DRIVE_ABSOLUTE.match(path)
    if drive is not None:
        return _absolute(path[drive.end():])
    if _DRIVE_RELATIVE.match(path) is not None:
        return _INVALID
    scheme = _SCHEME.match(path)
    if scheme is not None:
        return _classify_scheme(
            scheme.group(0)[:-1].lower(), path[scheme.end():]
        )
    return _relative(path)
