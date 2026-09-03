"""Fatal validation for the immutable scanner and parser inventories."""

from __future__ import annotations

from dataclasses import dataclass
import re

from backend.code_parser.models import (
    CodeParseInventory,
    ParsedFile,
    ParsedLanguage,
    ParseStatus,
    SkippedParseFile,
)
from backend.file_scanner.models import (
    FileCategory,
    FileInventory,
    IgnoredFile,
    ScannedFile,
    SkippedDirectory,
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
        and all(type(counter) is int and counter >= 0 for counter in counters)
        and _has_exact_members(inventory.files, ScannedFile)
        and _has_exact_members(inventory.ignored, IgnoredFile)
        and _has_exact_members(inventory.skipped_directories, SkippedDirectory)
        and inventory.included_files == len(inventory.files)
        and inventory.ignored_files == len(inventory.ignored)
        and inventory.total_files_seen
        == inventory.included_files + inventory.ignored_files
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
        and all(type(counter) is int and counter >= 0 for counter in counters)
        and _has_exact_members(inventory.files, ParsedFile)
        and _has_exact_members(inventory.skipped, SkippedParseFile)
        and all(
            type(item.language) is ParsedLanguage and type(item.status) is ParseStatus
            for item in inventory.files
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
