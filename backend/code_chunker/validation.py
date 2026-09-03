"""Fatal validation for the immutable scanner and parser inventories."""

from __future__ import annotations

from dataclasses import dataclass
import re

from backend.code_parser.models import (
    CallKind,
    CallSite,
    CodeParseInventory,
    ImportBinding,
    ImportInfo,
    ParameterInfo,
    ParsedFile,
    ParsedLanguage,
    ParseIssue,
    ParseIssueKind,
    ParseSkipReason,
    ParseStatus,
    SkippedParseFile,
    SourceLocation,
    SymbolInfo,
    SymbolKind,
)
from backend.file_scanner.models import (
    FileCategory,
    FileInventory,
    IgnoredFile,
    IgnoreReason,
    ScannedFile,
    SkippedDirectory,
    SkippedDirectoryReason,
)

from .exceptions import InvalidChunkInventory


_INVALID_INVENTORY_MESSAGE = "Invalid chunk inventory."
_CODE_CATEGORIES = (FileCategory.SOURCE, FileCategory.TEST)
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_LANGUAGE_SELECTORS = {
    (".py", "python"): ParsedLanguage.PYTHON,
    (".java", "java"): ParsedLanguage.JAVA,
    (".js", "javascript"): ParsedLanguage.JAVASCRIPT,
    (".jsx", "javascript"): ParsedLanguage.JAVASCRIPT,
    (".ts", "typescript"): ParsedLanguage.TYPESCRIPT,
    (".tsx", "typescript"): ParsedLanguage.TSX,
}


@dataclass(frozen=True, slots=True)
class ValidatedInputs:
    repository_path: str
    repository_namespace: str
    pairs: tuple[tuple[ScannedFile, ParsedFile], ...]


def _invalid() -> None:
    raise InvalidChunkInventory(_INVALID_INVENTORY_MESSAGE)


def _has_exact_members(items: object, member_type: type[object]) -> bool:
    return type(items) is tuple and all(type(item) is member_type for item in items)


def _has_unique_paths(*collections: tuple[object, ...]) -> bool:
    paths: list[str] = []
    for collection in collections:
        for item in collection:
            relative_path = getattr(item, "relative_path", None)
            if type(relative_path) is not str or not relative_path:
                return False
            paths.append(relative_path)
    return len(paths) == len(set(paths))


def _valid_scanner_inventory(inventory: FileInventory) -> bool:
    counters = (
        inventory.total_files_seen,
        inventory.included_files,
        inventory.ignored_files,
    )
    return (
        type(inventory.repository_path) is str
        and bool(inventory.repository_path)
        and all(type(counter) is int and counter >= 0 for counter in counters)
        and _has_exact_members(inventory.files, ScannedFile)
        and _has_exact_members(inventory.ignored, IgnoredFile)
        and _has_exact_members(inventory.skipped_directories, SkippedDirectory)
        and inventory.included_files == len(inventory.files)
        and inventory.ignored_files == len(inventory.ignored)
        and inventory.total_files_seen
        == inventory.included_files + inventory.ignored_files
        and all(_valid_scanned_file(item) for item in inventory.files)
        and all(
            type(item.relative_path) is str
            and bool(item.relative_path)
            and type(item.reason) is IgnoreReason
            for item in inventory.ignored
        )
        and all(
            type(item.relative_path) is str
            and bool(item.relative_path)
            and type(item.reason) is SkippedDirectoryReason
            for item in inventory.skipped_directories
        )
        and tuple(sorted(inventory.files, key=_path_key)) == inventory.files
        and tuple(sorted(inventory.ignored, key=_path_key)) == inventory.ignored
        and tuple(sorted(inventory.skipped_directories, key=_path_key))
        == inventory.skipped_directories
        and _has_unique_paths(
            inventory.files, inventory.ignored, inventory.skipped_directories
        )
    )


def _valid_parser_inventory(inventory: CodeParseInventory) -> bool:
    counters = (
        inventory.total_files_requested,
        inventory.success_files,
        inventory.partial_files,
        inventory.failed_files,
        inventory.skipped_files,
    )
    return (
        type(inventory.repository_path) is str
        and bool(inventory.repository_path)
        and all(type(counter) is int and counter >= 0 for counter in counters)
        and _has_exact_members(inventory.files, ParsedFile)
        and _has_exact_members(inventory.skipped, SkippedParseFile)
        and all(_valid_parsed_file(item) for item in inventory.files)
        and all(
            type(item.relative_path) is str
            and bool(item.relative_path)
            and type(item.reason) is ParseSkipReason
            for item in inventory.skipped
        )
        and inventory.total_files_requested
        == len(inventory.files) + len(inventory.skipped)
        and inventory.success_files
        == sum(item.status is ParseStatus.SUCCESS for item in inventory.files)
        and inventory.partial_files
        == sum(item.status is ParseStatus.PARTIAL for item in inventory.files)
        and inventory.failed_files
        == sum(item.status is ParseStatus.FAILED for item in inventory.files)
        and inventory.skipped_files == len(inventory.skipped)
        and _has_unique_paths(inventory.files, inventory.skipped)
        and tuple(
            sorted(
                inventory.files,
                key=lambda item: (item.relative_path.casefold(), item.relative_path),
            )
        )
        == inventory.files
        and tuple(sorted(inventory.skipped, key=_path_key)) == inventory.skipped
    )


def _path_key(item: object) -> tuple[str, str]:
    path = getattr(item, "relative_path", "")
    return path.casefold(), path


def _valid_scanned_file(file: ScannedFile) -> bool:
    return (
        type(file.relative_path) is str
        and bool(file.relative_path)
        and type(file.filename) is str
        and bool(file.filename)
        and type(file.extension) is str
        and isinstance(file.language, (str, type(None)))
        and type(file.category) is FileCategory
        and type(file.size_bytes) is int
        and file.size_bytes >= 0
    )


def _valid_parsed_file(file: ParsedFile) -> bool:
    digest_valid = file.source_sha256 is None or (
        type(file.source_sha256) is str
        and bool(_DIGEST_PATTERN.fullmatch(file.source_sha256))
    )
    return (
        type(file.relative_path) is str
        and bool(file.relative_path)
        and type(file.language) is ParsedLanguage
        and type(file.status) is ParseStatus
        and _has_exact_members(file.symbols, SymbolInfo)
        and _has_exact_members(file.imports, ImportInfo)
        and _has_exact_members(file.calls, CallSite)
        and _has_exact_members(file.issues, ParseIssue)
        and all(_valid_symbol(item) for item in file.symbols)
        and all(_valid_import(item) for item in file.imports)
        and all(_valid_call(item) for item in file.calls)
        and all(_valid_issue(item) for item in file.issues)
        and digest_valid
    )


def _optional_string(value: object) -> bool:
    return value is None or type(value) is str


def _string_tuple(value: object) -> bool:
    return type(value) is tuple and all(type(item) is str for item in value)


def _valid_location_shape(value: object) -> bool:
    return type(value) is SourceLocation and all(
        type(item) is int
        for item in (
            value.start_byte, value.end_byte, value.start_line,
            value.start_column, value.end_line, value.end_column,
        )
    )


def _valid_parameter(value: object) -> bool:
    return (
        type(value) is ParameterInfo
        and type(value.name) is str
        and _optional_string(value.type_name)
        and _optional_string(value.default_value_text)
    )


def _valid_binding(value: object) -> bool:
    return (
        type(value) is ImportBinding
        and type(value.imported_name) is str
        and _optional_string(value.alias)
    )


def _valid_import(value: object) -> bool:
    return (
        type(value.module) is str
        and _has_exact_members(value.bindings, ImportBinding)
        and all(_valid_binding(item) for item in value.bindings)
        and type(value.is_wildcard) is bool
        and _string_tuple(value.modifiers)
        and _valid_location_shape(value.location)
    )


def _valid_symbol(value: object) -> bool:
    return (
        type(value.name) is str
        and type(value.kind) is SymbolKind
        and type(value.qualified_name) is str
        and _optional_string(value.parent_qualified_name)
        and _valid_location_shape(value.location)
        and _has_exact_members(value.parameters, ParameterInfo)
        and all(_valid_parameter(item) for item in value.parameters)
        and _optional_string(value.return_type)
        and _string_tuple(value.modifiers)
        and _string_tuple(value.base_types)
        and _string_tuple(value.implemented_types)
    )


def _valid_call(value: object) -> bool:
    return (
        _optional_string(value.caller_qualified_name)
        and type(value.callee_text) is str
        and type(value.kind) is CallKind
        and _valid_location_shape(value.location)
    )


def _valid_issue(value: object) -> bool:
    return (
        type(value.kind) is ParseIssueKind
        and type(value.message) is str
        and (value.location is None or _valid_location_shape(value.location))
    )


def _valid_namespace(namespace: object) -> bool:
    return type(namespace) is str and bool(namespace)


def _valid_pair(scanned: ScannedFile, parsed: ParsedFile) -> bool:
    if type(scanned.category) is not FileCategory or scanned.category not in _CODE_CATEGORIES:
        return False
    if _LANGUAGE_SELECTORS.get((scanned.extension, scanned.language)) is not parsed.language:
        return False
    if parsed.status in (ParseStatus.SUCCESS, ParseStatus.PARTIAL):
        return type(parsed.source_sha256) is str and bool(
            _DIGEST_PATTERN.fullmatch(parsed.source_sha256)
        )
    return True


def validate_inputs(
    file_inventory: object, parse_inventory: object
) -> ValidatedInputs:
    """Return scanner/parser pairs after complete fatal contract validation."""
    if type(file_inventory) is not FileInventory or type(parse_inventory) is not CodeParseInventory:
        _invalid()
    if not _valid_scanner_inventory(file_inventory) or not _valid_parser_inventory(
        parse_inventory
    ):
        _invalid()
    if file_inventory.repository_path != parse_inventory.repository_path:
        _invalid()
    if (
        not _valid_namespace(file_inventory.repository_namespace)
        or not _valid_namespace(parse_inventory.repository_namespace)
        or file_inventory.repository_namespace != parse_inventory.repository_namespace
    ):
        _invalid()

    scanned_by_path = {scanned.relative_path: scanned for scanned in file_inventory.files}
    code_paths = {
        scanned.relative_path
        for scanned in file_inventory.files
        if scanned.category in _CODE_CATEGORIES
    }
    parser_paths = {
        item.relative_path for item in (*parse_inventory.files, *parse_inventory.skipped)
    }
    if (
        parser_paths != code_paths
        or parse_inventory.total_files_requested != len(code_paths)
        or any(
            (scanned := scanned_by_path.get(item.relative_path)) is None
            or scanned.category not in _CODE_CATEGORIES
            for item in parse_inventory.skipped
        )
    ):
        _invalid()
    pairs: list[tuple[ScannedFile, ParsedFile]] = []
    for parsed in parse_inventory.files:
        scanned = scanned_by_path.get(parsed.relative_path)
        if scanned is None or not _valid_pair(scanned, parsed):
            _invalid()
        pairs.append((scanned, parsed))
    return ValidatedInputs(
        file_inventory.repository_path,
        file_inventory.repository_namespace,
        tuple(pairs),
    )
