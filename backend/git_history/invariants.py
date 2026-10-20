"""Invariant helpers for Module 7."""

import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence, TypeVar

EMPTY_TREE_SHA1 = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
EMPTY_TREE_SHA256 = "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321"

OBJECT_ID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
TIMEZONE_RE = re.compile(r"^[+-]\d{2}:\d{2}$")

T = TypeVar("T")


def require_object_id(value: str) -> str:
    if not isinstance(value, str) or OBJECT_ID_RE.fullmatch(value) is None:
        from .exceptions import GitParseError

        raise GitParseError(f"invalid object id: {value!r}")
    return value


def require_timezone(value: str) -> str:
    if not isinstance(value, str) or TIMEZONE_RE.fullmatch(value) is None:
        from .exceptions import GitParseError

        raise GitParseError(f"invalid timezone: {value!r}")
    return value


def require_tuple(value: Any, element_type: type = object) -> tuple:
    if not isinstance(value, tuple):
        raise TypeError("expected tuple")
    return value


def snapshot_mapping(mapping: Mapping[Any, Any]) -> MappingProxyType:
    return MappingProxyType(dict(mapping))
