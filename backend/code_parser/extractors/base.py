"""Shared, deterministic Tree-sitter extraction primitives."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Protocol, TypeVar

from tree_sitter import Node, Tree

from backend.code_parser.models import (
    CallSite,
    ImportBinding,
    ImportInfo,
    ParseIssue,
    ParseIssueKind,
    ParseStatus,
    SourceLocation,
    SymbolInfo,
)
from backend.code_parser.reader import SourceBuffer


_MAX_TEXT_BYTES = 1000
_ELLIPSIS = "…"
_ELLIPSIS_BYTES = _ELLIPSIS.encode("utf-8")
_ASCII_WHITESPACE = b" \t\n\r\f\v"
_SYNTAX_ERROR_MESSAGE = "Syntax error in source file."
_MISSING_NODE_MESSAGE = "Required syntax is missing."
_T = TypeVar("_T")


class TraversalEventKind(str, Enum):
    """The two phases of an iterative tree traversal."""

    ENTER = "enter"
    EXIT = "exit"


ENTER = TraversalEventKind.ENTER
EXIT = TraversalEventKind.EXIT


@dataclass(frozen=True, slots=True)
class TraversalEvent:
    kind: TraversalEventKind
    node: Node


@dataclass(frozen=True, slots=True)
class BoundedText:
    text: str
    was_truncated: bool


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    symbols: tuple[SymbolInfo, ...]
    imports: tuple[ImportInfo, ...]
    calls: tuple[CallSite, ...]
    issues: tuple[ParseIssue, ...]


class BaseExtractor(Protocol):
    def extract(self, tree: Tree, source: SourceBuffer) -> ExtractionResult:
        """Extract normalized public metadata from one parsed source buffer."""


@dataclass(frozen=True, slots=True)
class _ScopeEntry:
    qualified_name: str
    is_callable: bool


class ScopeStack:
    """Tracks lexical declarations without recursive traversal state."""

    def __init__(self) -> None:
        self._entries: list[_ScopeEntry] = []

    def push(self, qualified_name: str, *, is_callable: bool) -> None:
        self._entries.append(_ScopeEntry(qualified_name, is_callable))

    def pop(self) -> str:
        return self._entries.pop().qualified_name

    @property
    def nearest_callable(self) -> str | None:
        for entry in reversed(self._entries):
            if entry.is_callable:
                return entry.qualified_name
        return None


# This is an intentionally closed vocabulary.  The order is the stable public
# representation, rather than an incidental source or alphabetical order.
_MODIFIER_PRECEDENCE = (
    "public",
    "protected",
    "private",
    "static",
    "async",
    "abstract",
    "final",
    "readonly",
    "export",
    "default",
    "generator",
    "synchronized",
    "native",
    "strictfp",
    "transient",
    "volatile",
    "sealed",
    "non-sealed",
    "open",
    "override",
    "declare",
    "accessor",
    "get",
    "set",
)
_MODIFIER_ORDER = {modifier: index for index, modifier in enumerate(_MODIFIER_PRECEDENCE)}


def iter_events(root: Node) -> Iterator[TraversalEvent]:
    """Yield source-ordered enter/exit events without using Python recursion."""

    stack: list[tuple[Node, bool]] = [(root, False)]
    while stack:
        node, exiting = stack.pop()
        if exiting:
            yield TraversalEvent(EXIT, node)
            continue
        yield TraversalEvent(ENTER, node)
        stack.append((node, True))
        for child in reversed(node.children):
            stack.append((child, False))


def source_location(node: Node, source: SourceBuffer) -> SourceLocation:
    """Translate a parser-view node range into original-file byte coordinates."""

    start_byte = node.start_byte
    end_byte = node.end_byte
    if not 0 <= start_byte <= end_byte <= len(source.parse_bytes):
        raise ValueError("Tree-sitter node range is outside the source buffer.")

    start_row, start_column = node.start_point.row, node.start_point.column
    end_row, end_column = node.end_point.row, node.end_point.column
    if min(start_row, start_column, end_row, end_column) < 0:
        raise ValueError("Tree-sitter node point is invalid.")

    prefix = source.bom_prefix_bytes
    location = SourceLocation(
        start_byte + prefix,
        end_byte + prefix,
        start_row + 1,
        start_column + (prefix if start_row == 0 else 0),
        end_row + 1,
        end_column + (prefix if end_row == 0 else 0),
    )
    if location.end_byte > len(source.original_bytes):
        raise ValueError("Translated node range is outside the original source buffer.")
    return location


def bounded_node_text(node: Node, source: SourceBuffer) -> BoundedText:
    """Return stripped node text, retaining no more than 1,000 UTF-8 bytes."""

    if not 0 <= node.start_byte <= node.end_byte <= len(source.parse_bytes):
        raise ValueError("Tree-sitter node range is outside the source buffer.")
    value = source.parse_bytes[node.start_byte : node.end_byte].strip(_ASCII_WHITESPACE)
    if len(value) <= _MAX_TEXT_BYTES:
        return BoundedText(value.decode("utf-8", errors="strict"), False)

    prefix = value[: _MAX_TEXT_BYTES - len(_ELLIPSIS_BYTES)]
    while prefix:
        try:
            text = prefix.decode("utf-8", errors="strict")
            return BoundedText(text + _ELLIPSIS, True)
        except UnicodeDecodeError:
            prefix = prefix[:-1]
    return BoundedText(_ELLIPSIS, True)


def normalize_modifiers(modifiers: Iterable[str]) -> tuple[str, ...]:
    """Lowercase, deduplicate, and order known declaration modifiers."""

    normalized = {
        modifier.strip().lower()
        for modifier in modifiers
        if isinstance(modifier, str) and modifier.strip().lower() in _MODIFIER_ORDER
    }
    return tuple(sorted(normalized, key=_MODIFIER_ORDER.__getitem__))


def _normalize_symbol(symbol: SymbolInfo) -> SymbolInfo:
    return replace(symbol, modifiers=normalize_modifiers(symbol.modifiers))


def _normalize_import(item: ImportInfo) -> ImportInfo:
    return replace(item, modifiers=normalize_modifiers(item.modifiers))


def _deduplicate(values: Iterable[_T], key: Callable[[_T], object]) -> list[_T]:
    seen: set[object] = set()
    unique: list[_T] = []
    for value in values:
        identity = key(value)
        if identity not in seen:
            seen.add(identity)
            unique.append(value)
    return unique


def _location_key(location: SourceLocation) -> tuple[int, int]:
    return location.start_byte, location.end_byte


def _binding_sort_key(bindings: tuple[ImportBinding, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((binding.imported_name, "" if binding.alias is None else binding.alias) for binding in bindings)


def normalize_extraction(result: ExtractionResult) -> ExtractionResult:
    """Normalize modifier text, then deduplicate and sort extraction output."""

    symbols = _deduplicate(
        (_normalize_symbol(symbol) for symbol in result.symbols),
        lambda item: (item.kind, item.name, *_location_key(item.location)),
    )
    imports = _deduplicate(
        (_normalize_import(item) for item in result.imports),
        lambda item: (item.module, item.bindings, item.is_wildcard, *_location_key(item.location)),
    )
    calls = _deduplicate(
        result.calls,
        lambda item: (
            item.kind,
            item.callee_text,
            *_location_key(item.location),
            item.caller_qualified_name,
        ),
    )
    issues = _deduplicate(
        result.issues,
        lambda item: (
            item.kind,
            -1 if item.location is None else item.location.start_byte,
            -1 if item.location is None else item.location.end_byte,
        ),
    )
    return ExtractionResult(
        tuple(
            sorted(
                symbols,
                key=lambda item: (
                    item.location.start_byte,
                    item.location.end_byte,
                    item.kind.value,
                    item.qualified_name,
                ),
            )
        ),
        tuple(
            sorted(
                imports,
                key=lambda item: (
                    item.location.start_byte,
                    item.location.end_byte,
                    item.module,
                    _binding_sort_key(item.bindings),
                ),
            )
        ),
        tuple(
            sorted(
                calls,
                key=lambda item: (
                    item.location.start_byte,
                    item.location.end_byte,
                    item.kind.value,
                    item.callee_text,
                ),
            )
        ),
        tuple(
            sorted(
                issues,
                key=lambda item: (
                    item.location is None,
                    -1 if item.location is None else item.location.start_byte,
                    -1 if item.location is None else item.location.end_byte,
                    item.kind.value,
                    item.message,
                ),
            )
        ),
    )


def collect_syntax_issues(tree: Tree, source: SourceBuffer) -> tuple[ParseIssue, ...]:
    """Collect fixed, source-located syntax findings without recursive traversal."""

    issues: list[ParseIssue] = []
    for event in iter_events(tree.root_node):
        if event.kind is not ENTER:
            continue
        node = event.node
        if node.is_error:
            issues.append(
                ParseIssue(
                    ParseIssueKind.SYNTAX_ERROR,
                    _SYNTAX_ERROR_MESSAGE,
                    source_location(node, source),
                )
            )
        if node.is_missing:
            issues.append(
                ParseIssue(
                    ParseIssueKind.MISSING_NODE,
                    _MISSING_NODE_MESSAGE,
                    source_location(node, source),
                )
            )
    return normalize_extraction(ExtractionResult((), (), (), tuple(issues))).issues


def is_trustworthy_capture(
    node: Node,
    source: SourceBuffer,
    syntax_issues: tuple[ParseIssue, ...],
    *required_nodes: Node | None,
) -> bool:
    """Allow captures outside invalid spans, or complete captures nested in one."""

    try:
        location = source_location(node, source)
    except ValueError:
        return False
    invalid_locations = tuple(
        issue.location
        for issue in syntax_issues
        if issue.location is not None
        and issue.kind in {ParseIssueKind.SYNTAX_ERROR, ParseIssueKind.MISSING_NODE}
    )
    wholly_invalid = any(
        invalid.start_byte <= location.start_byte
        and location.end_byte <= invalid.end_byte
        for invalid in invalid_locations
    )
    if not wholly_invalid:
        return True
    if node.is_error or node.is_missing or node.has_error:
        return False
    for required in required_nodes:
        if (
            required is None
            or required.is_error
            or required.is_missing
            or required.has_error
        ):
            return False
        try:
            source_location(required, source)
        except ValueError:
            return False
    return True


def classify_parse_status(result: ExtractionResult) -> ParseStatus:
    """Classify a normalized extraction strictly from issues and structure."""

    has_structure = bool(result.symbols or result.imports or result.calls)
    if not result.issues:
        return ParseStatus.SUCCESS
    return ParseStatus.PARTIAL if has_structure else ParseStatus.FAILED


_EXTRACTOR_KEYS = frozenset({"python", "java", "javascript", "typescript", "tsx"})


def get_extractor(extractor_key: str) -> BaseExtractor:
    """Look up only trusted adapter keys; adapters register in later tasks."""

    if extractor_key not in _EXTRACTOR_KEYS:
        raise ValueError("Unknown extractor key.")
    raise LookupError("Extractor implementation is unavailable.")
