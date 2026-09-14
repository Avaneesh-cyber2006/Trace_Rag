"""Validate scanner and parser inventories without accessing source files."""

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

from .exceptions import CodeGraphValidationError


_ERROR_MESSAGE = "Invalid code graph inputs."
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_SELECTORS = {
    ("python", ".py"): ParsedLanguage.PYTHON,
    ("java", ".java"): ParsedLanguage.JAVA,
    ("javascript", ".js"): ParsedLanguage.JAVASCRIPT,
    ("javascript", ".jsx"): ParsedLanguage.JAVASCRIPT,
    ("typescript", ".ts"): ParsedLanguage.TYPESCRIPT,
    ("typescript", ".tsx"): ParsedLanguage.TSX,
}
_CODE_CATEGORIES = (FileCategory.SOURCE, FileCategory.TEST)


@dataclass(frozen=True, slots=True)
class ValidatedInputs:
    repository_namespace: str
    pairs: tuple[tuple[ScannedFile, ParsedFile], ...]


def _invalid() -> None:
    raise CodeGraphValidationError(_ERROR_MESSAGE)


def _nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _valid_path(value: object) -> bool:
    if type(value) is not str or not value or "\\" in value or "\x00" in value:
        return False
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


def _string(value: object) -> bool:
    return type(value) is str


def _optional_string(value: object) -> bool:
    return value is None or type(value) is str


def _record_tuple(value: object, item_type: type[object]) -> bool:
    return type(value) is tuple and all(type(item) is item_type for item in value)


def _string_tuple(value: object) -> bool:
    return type(value) is tuple and all(type(item) is str for item in value)


def _validate_location(value: object, size_bytes: int) -> None:
    if type(value) is not SourceLocation:
        _invalid()
    coordinates = (
        value.start_byte,
        value.end_byte,
        value.start_line,
        value.start_column,
        value.end_line,
        value.end_column,
    )
    if not all(type(coordinate) is int for coordinate in coordinates):
        _invalid()
    if not 0 <= value.start_byte <= value.end_byte <= size_bytes:
        _invalid()
    if value.start_line <= 0 or value.end_line <= 0:
        _invalid()
    if value.start_line > value.end_line:
        _invalid()
    if value.start_column < 0 or value.end_column < 0:
        _invalid()
    if (
        value.start_line == value.end_line
        and value.start_column > value.end_column
    ):
        _invalid()


def _validate_parameter(value: object) -> None:
    if type(value) is not ParameterInfo:
        _invalid()
    if not (
        _string(value.name)
        and _optional_string(value.type_name)
        and _optional_string(value.default_value_text)
    ):
        _invalid()


def _validate_symbol(value: object, size_bytes: int) -> None:
    if type(value) is not SymbolInfo:
        _invalid()
    if not (
        type(value.name) is str
        and bool(value.name)
        and type(value.kind) is SymbolKind
        and type(value.qualified_name) is str
        and bool(value.qualified_name)
        and _optional_string(value.parent_qualified_name)
        and _record_tuple(value.parameters, ParameterInfo)
        and _optional_string(value.return_type)
        and _string_tuple(value.modifiers)
        and _string_tuple(value.base_types)
        and _string_tuple(value.implemented_types)
    ):
        _invalid()
    _validate_location(value.location, size_bytes)
    for parameter in value.parameters:
        _validate_parameter(parameter)


def _validate_binding(value: object) -> None:
    if type(value) is not ImportBinding:
        _invalid()
    if not (
        _string(value.imported_name)
        and _optional_string(value.alias)
    ):
        _invalid()


def _validate_import(value: object, size_bytes: int) -> None:
    if type(value) is not ImportInfo:
        _invalid()
    if not (
        _string(value.module)
        and _record_tuple(value.bindings, ImportBinding)
        and type(value.is_wildcard) is bool
        and _string_tuple(value.modifiers)
    ):
        _invalid()
    _validate_location(value.location, size_bytes)
    for binding in value.bindings:
        _validate_binding(binding)


def _validate_call(value: object, size_bytes: int) -> None:
    if type(value) is not CallSite:
        _invalid()
    if not (
        _optional_string(value.caller_qualified_name)
        and _string(value.callee_text)
        and type(value.kind) is CallKind
    ):
        _invalid()
    _validate_location(value.location, size_bytes)


def _validate_issue(value: object, size_bytes: int) -> None:
    if type(value) is not ParseIssue:
        _invalid()
    if not (
        type(value.kind) is ParseIssueKind
        and _string(value.message)
        and (value.location is None or type(value.location) is SourceLocation)
    ):
        _invalid()
    if value.location is not None:
        _validate_location(value.location, size_bytes)


def _validate_scanned_file(value: object) -> None:
    if type(value) is not ScannedFile:
        _invalid()
    if not (
        _valid_path(value.relative_path)
        and _string(value.filename)
        and _string(value.extension)
        and _optional_string(value.language)
        and type(value.category) is FileCategory
        and _nonnegative_int(value.size_bytes)
    ):
        _invalid()


def _validate_ignored_file(value: object) -> None:
    if type(value) is not IgnoredFile:
        _invalid()
    if not (
        _valid_path(value.relative_path)
        and type(value.reason) is IgnoreReason
    ):
        _invalid()


def _validate_skipped_directory(value: object) -> None:
    if type(value) is not SkippedDirectory:
        _invalid()
    if not (
        _valid_path(value.relative_path)
        and type(value.reason) is SkippedDirectoryReason
    ):
        _invalid()


def _validate_skipped_parse(value: object) -> None:
    if type(value) is not SkippedParseFile:
        _invalid()
    if not (
        _valid_path(value.relative_path)
        and type(value.reason) is ParseSkipReason
    ):
        _invalid()


def _validate_parsed_file(value: object, scanned: ScannedFile) -> None:
    if type(value) is not ParsedFile:
        _invalid()
    if not (
        _valid_path(value.relative_path)
        and type(value.language) is ParsedLanguage
        and type(value.status) is ParseStatus
        and _record_tuple(value.symbols, SymbolInfo)
        and _record_tuple(value.imports, ImportInfo)
        and _record_tuple(value.calls, CallSite)
        and _record_tuple(value.issues, ParseIssue)
        and (
            value.source_sha256 is None
            or (
                type(value.source_sha256) is str
                and _DIGEST_PATTERN.fullmatch(value.source_sha256) is not None
            )
        )
    ):
        _invalid()
    if value.relative_path != scanned.relative_path:
        _invalid()
    if _SELECTORS.get((scanned.language, scanned.extension)) is not value.language:
        _invalid()
    for symbol in value.symbols:
        _validate_symbol(symbol, scanned.size_bytes)
    for imported in value.imports:
        _validate_import(imported, scanned.size_bytes)
    for call in value.calls:
        _validate_call(call, scanned.size_bytes)
    for issue in value.issues:
        _validate_issue(issue, scanned.size_bytes)
    if value.status is ParseStatus.FAILED and (
        value.symbols or value.imports or value.calls
    ):
        _invalid()


def _validate_file_inventory(value: object) -> FileInventory:
    if type(value) is not FileInventory:
        _invalid()
    if not (
        type(value.repository_path) is str
        and bool(value.repository_path)
        and _nonnegative_int(value.total_files_seen)
        and _nonnegative_int(value.included_files)
        and _nonnegative_int(value.ignored_files)
        and _record_tuple(value.files, ScannedFile)
        and _record_tuple(value.ignored, IgnoredFile)
        and _record_tuple(value.skipped_directories, SkippedDirectory)
        and type(value.repository_namespace) is str
        and bool(value.repository_namespace)
    ):
        _invalid()
    if (
        value.included_files != len(value.files)
        or value.ignored_files != len(value.ignored)
        or value.total_files_seen != value.included_files + value.ignored_files
    ):
        _invalid()
    for file in value.files:
        _validate_scanned_file(file)
    for ignored in value.ignored:
        _validate_ignored_file(ignored)
    for skipped_directory in value.skipped_directories:
        _validate_skipped_directory(skipped_directory)
    paths = [file.relative_path for file in value.files]
    paths.extend(ignored.relative_path for ignored in value.ignored)
    paths.extend(item.relative_path for item in value.skipped_directories)
    if len(paths) != len(set(paths)):
        _invalid()
    return value


def _validate_parse_inventory(value: object) -> CodeParseInventory:
    if type(value) is not CodeParseInventory:
        _invalid()
    if not (
        type(value.repository_path) is str
        and bool(value.repository_path)
        and _nonnegative_int(value.total_files_requested)
        and _nonnegative_int(value.success_files)
        and _nonnegative_int(value.partial_files)
        and _nonnegative_int(value.failed_files)
        and _nonnegative_int(value.skipped_files)
        and _record_tuple(value.files, ParsedFile)
        and _record_tuple(value.skipped, SkippedParseFile)
        and type(value.repository_namespace) is str
        and bool(value.repository_namespace)
    ):
        _invalid()
    if not all(_valid_path(file.relative_path) for file in value.files):
        _invalid()
    if (
        value.total_files_requested != len(value.files) + len(value.skipped)
        or value.success_files
        != sum(file.status is ParseStatus.SUCCESS for file in value.files)
        or value.partial_files
        != sum(file.status is ParseStatus.PARTIAL for file in value.files)
        or value.failed_files
        != sum(file.status is ParseStatus.FAILED for file in value.files)
        or value.skipped_files != len(value.skipped)
    ):
        _invalid()
    for skipped in value.skipped:
        _validate_skipped_parse(skipped)
    paths = [file.relative_path for file in value.files]
    paths.extend(skipped.relative_path for skipped in value.skipped)
    if len(paths) != len(set(paths)):
        _invalid()
    return value


def validate_inputs(
    file_inventory: object,
    parse_inventory: object,
) -> ValidatedInputs:
    """Validate and associate supplied Module 2 and Module 3 metadata."""

    scanner = _validate_file_inventory(file_inventory)
    parser = _validate_parse_inventory(parse_inventory)
    if scanner.repository_namespace != parser.repository_namespace:
        _invalid()

    candidates = {
        file.relative_path: file
        for file in scanner.files
        if file.category in _CODE_CATEGORIES
    }
    parsed_by_path = {file.relative_path: file for file in parser.files}
    skipped_by_path = {file.relative_path: file for file in parser.skipped}
    if set(candidates) != set(parsed_by_path) | set(skipped_by_path):
        _invalid()

    pairs: list[tuple[ScannedFile, ParsedFile]] = []
    for relative_path, parsed in parsed_by_path.items():
        scanned = candidates.get(relative_path)
        if scanned is None:
            _invalid()
        _validate_parsed_file(parsed, scanned)
        pairs.append((scanned, parsed))
    for relative_path in skipped_by_path:
        scanned = candidates.get(relative_path)
        if scanned is None or _SELECTORS.get((scanned.language, scanned.extension)) is not None:
            _invalid()

    return ValidatedInputs(
        scanner.repository_namespace,
        tuple(sorted(pairs, key=lambda pair: pair[0].relative_path)),
    )
