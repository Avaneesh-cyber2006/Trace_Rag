"""Immutable public values produced by the Code Parser."""

from dataclasses import dataclass
from enum import Enum


class ParsedLanguage(str, Enum):
    PYTHON = "python"
    JAVA = "java"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    TSX = "tsx"


class SymbolKind(str, Enum):
    CLASS = "class"
    INTERFACE = "interface"
    FUNCTION = "function"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    ENUM = "enum"
    CONSTANT = "constant"


class CallKind(str, Enum):
    CALL = "call"
    CONSTRUCTOR = "constructor"


class ParseStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ParseSkipReason(str, Enum):
    UNSUPPORTED_LANGUAGE = "unsupported_language"


class ParseIssueKind(str, Enum):
    SYNTAX_ERROR = "syntax_error"
    MISSING_NODE = "missing_node"
    READ_ERROR = "read_error"
    FILE_CHANGED = "file_changed"
    PATH_INVALID = "path_invalid"
    LINK_UNSAFE = "link_unsafe"
    DECODING_ERROR = "decoding_error"
    PARSER_UNAVAILABLE = "parser_unavailable"
    EXTRACTION_ERROR = "extraction_error"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    start_byte: int
    end_byte: int
    start_line: int
    start_column: int
    end_line: int
    end_column: int


@dataclass(frozen=True, slots=True)
class ParameterInfo:
    name: str
    type_name: str | None
    default_value_text: str | None


@dataclass(frozen=True, slots=True)
class ImportBinding:
    imported_name: str
    alias: str | None


@dataclass(frozen=True, slots=True)
class ImportInfo:
    module: str
    bindings: tuple[ImportBinding, ...]
    is_wildcard: bool
    modifiers: tuple[str, ...]
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    name: str
    kind: SymbolKind
    qualified_name: str
    parent_qualified_name: str | None
    location: SourceLocation
    parameters: tuple[ParameterInfo, ...]
    return_type: str | None
    modifiers: tuple[str, ...]
    base_types: tuple[str, ...]
    implemented_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CallSite:
    caller_qualified_name: str | None
    callee_text: str
    kind: CallKind
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class ParseIssue:
    kind: ParseIssueKind
    message: str
    location: SourceLocation | None


@dataclass(frozen=True, slots=True)
class ParsedFile:
    relative_path: str
    language: ParsedLanguage
    status: ParseStatus
    symbols: tuple[SymbolInfo, ...]
    imports: tuple[ImportInfo, ...]
    calls: tuple[CallSite, ...]
    issues: tuple[ParseIssue, ...]


@dataclass(frozen=True, slots=True)
class SkippedParseFile:
    relative_path: str
    reason: ParseSkipReason


@dataclass(frozen=True, slots=True)
class CodeParseInventory:
    repository_path: str
    total_files_requested: int
    success_files: int
    partial_files: int
    failed_files: int
    skipped_files: int
    files: tuple[ParsedFile, ...]
    skipped: tuple[SkippedParseFile, ...]
    repository_namespace: str | None = None
