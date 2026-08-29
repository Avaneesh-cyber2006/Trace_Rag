"""Deterministic, one-file-at-a-time code inventory orchestration."""

from __future__ import annotations

from hashlib import sha256
import logging

from backend.file_scanner.models import FileCategory, FileInventory, ScannedFile

from .exceptions import (
    InvalidParseInventory,
    ParserConfigurationError,
    RepositoryParseError,
)
from .extractors import get_extractor
from .extractors.base import classify_parse_status, normalize_extraction
from .models import (
    CodeParseInventory,
    ParsedFile,
    ParsedLanguage,
    ParseIssue,
    ParseIssueKind,
    ParseSkipReason,
    ParseStatus,
    SkippedParseFile,
)
from .reader import SafeSourceReader, SourceReadError
from .registry import ParserRegistry, ParserSpec, ParserUnavailable


logger = logging.getLogger(__name__)

_INVALID_INVENTORY_MESSAGE = "Invalid file inventory."
_PARSER_UNAVAILABLE_MESSAGE = "Parser initialization is unavailable."
_EXTRACTION_ERROR_MESSAGE = "Extraction failed."
_SOURCE_ERROR_MESSAGES = {
    ParseIssueKind.READ_ERROR: "Unable to read source file.",
    ParseIssueKind.FILE_CHANGED: "Source file changed after scanning.",
    ParseIssueKind.PATH_INVALID: "Invalid source path.",
    ParseIssueKind.LINK_UNSAFE: "Source path cannot be opened safely.",
    ParseIssueKind.DECODING_ERROR: "Source file is not valid UTF-8.",
}
_CODE_CATEGORIES = (FileCategory.SOURCE, FileCategory.TEST)


def _validate_inventory(file_inventory: object) -> FileInventory:
    if not isinstance(file_inventory, FileInventory):
        raise InvalidParseInventory(_INVALID_INVENTORY_MESSAGE)

    counters = (
        file_inventory.total_files_seen,
        file_inventory.included_files,
        file_inventory.ignored_files,
    )
    collections = (
        file_inventory.files,
        file_inventory.ignored,
        file_inventory.skipped_directories,
    )
    if (
        any(type(counter) is not int or counter < 0 for counter in counters)
        or any(not isinstance(items, tuple) for items in collections)
        or not isinstance(
            file_inventory.repository_namespace,
            (str, type(None)),
        )
        or file_inventory.included_files != len(file_inventory.files)
        or file_inventory.ignored_files != len(file_inventory.ignored)
        or file_inventory.total_files_seen
        != file_inventory.included_files + file_inventory.ignored_files
    ):
        raise InvalidParseInventory(_INVALID_INVENTORY_MESSAGE)
    return file_inventory


def _is_code_candidate(file: object) -> bool:
    return getattr(file, "category", None) in _CODE_CATEGORIES


def _relative_path(file: object) -> str:
    relative_path = getattr(file, "relative_path", None)
    return relative_path if isinstance(relative_path, str) else ""


def _candidate_sort_key(file: object) -> tuple[str, str]:
    relative_path = _relative_path(file)
    return relative_path.casefold(), relative_path


def _sanitized_log_path(relative_path: str) -> str:
    sanitized = "".join(
        character if character.isprintable() else "?"
        for character in relative_path
    )
    return sanitized or "<invalid>"


def _failed_file(
    relative_path: str,
    language: ParsedLanguage,
    kind: ParseIssueKind,
    message: str,
    *,
    source_sha256: str | None = None,
) -> ParsedFile:
    return ParsedFile(
        relative_path=relative_path,
        language=language,
        status=ParseStatus.FAILED,
        symbols=(),
        imports=(),
        calls=(),
        issues=(ParseIssue(kind, message, None),),
        source_sha256=source_sha256,
    )


def _log_parsed_file(file: ParsedFile) -> None:
    relative_path = _sanitized_log_path(file.relative_path)
    logger.debug(
        "Code parse file complete: %s status=%s symbols=%d imports=%d calls=%d issues=%d",
        relative_path,
        file.status.value,
        len(file.symbols),
        len(file.imports),
        len(file.calls),
        len(file.issues),
    )
    for kind in dict.fromkeys(issue.kind for issue in file.issues):
        logger.warning(
            "Code parse recoverable issue: %s (%s)",
            relative_path,
            kind.value,
        )


class CodeParser:
    """Parse immutable scanner inventories without enumerating the repository."""

    def __init__(self) -> None:
        try:
            self._registry = ParserRegistry()
        except ParserConfigurationError:
            logger.error("Code parser initialization failed: invalid registry")
            raise

    def parse_inventory(self, file_inventory: FileInventory) -> CodeParseInventory:
        try:
            inventory = _validate_inventory(file_inventory)
        except InvalidParseInventory:
            logger.error("Code parse inventory failed: invalid inventory")
            raise
        candidates = tuple(
            sorted(
                (file for file in inventory.files if _is_code_candidate(file)),
                key=_candidate_sort_key,
            )
        )
        logger.info("Code parse inventory start: %d files requested", len(candidates))
        try:
            reader = SafeSourceReader(inventory.repository_path)
        except RepositoryParseError:
            logger.error("Code parse inventory failed: repository root unavailable")
            raise
        parsed_files: list[ParsedFile] = []
        skipped_files: list[SkippedParseFile] = []

        for file in candidates:
            spec = self._select(file)
            relative_path = _relative_path(file)
            log_path = _sanitized_log_path(relative_path)
            if spec is None:
                logger.debug("Parser selection: %s (unsupported)", log_path)
                skipped_files.append(
                    SkippedParseFile(
                        relative_path,
                        ParseSkipReason.UNSUPPORTED_LANGUAGE,
                    )
                )
                logger.debug(
                    "Code parse file complete: %s status=skipped "
                    "symbols=0 imports=0 calls=0 issues=0",
                    log_path,
                )
                continue
            logger.debug("Parser selection: %s (%s)", log_path, spec.language.value)
            parsed_file = self._parse_supported(file, relative_path, spec, reader)
            parsed_files.append(parsed_file)
            _log_parsed_file(parsed_file)

        files = tuple(parsed_files)
        skipped = tuple(skipped_files)
        success_files = sum(file.status is ParseStatus.SUCCESS for file in files)
        partial_files = sum(file.status is ParseStatus.PARTIAL for file in files)
        failed_files = sum(file.status is ParseStatus.FAILED for file in files)
        result = CodeParseInventory(
            repository_path=inventory.repository_path,
            total_files_requested=len(candidates),
            success_files=success_files,
            partial_files=partial_files,
            failed_files=failed_files,
            skipped_files=len(skipped),
            files=files,
            skipped=skipped,
            repository_namespace=inventory.repository_namespace,
        )
        logger.info(
            "Code parse inventory complete: %d success, %d partial, %d failed, %d skipped",
            result.success_files,
            result.partial_files,
            result.failed_files,
            result.skipped_files,
        )
        return result

    def _select(self, file: object) -> ParserSpec | None:
        if not isinstance(file, ScannedFile):
            return None
        if not isinstance(file.language, (str, type(None))) or not isinstance(
            file.extension, str
        ):
            return None
        return self._registry.select(file)

    def _parse_supported(
        self,
        file: ScannedFile,
        relative_path: str,
        spec: ParserSpec,
        reader: SafeSourceReader,
    ) -> ParsedFile:
        try:
            source = reader.read(file)
        except SourceReadError as error:
            message = _SOURCE_ERROR_MESSAGES.get(error.kind)
            if message is None:
                raise
            return _failed_file(relative_path, spec.language, error.kind, message)

        source_sha256 = sha256(source.original_bytes).hexdigest()

        try:
            handle = self._registry.get_parser(spec)
        except ParserUnavailable:
            return _failed_file(
                relative_path,
                spec.language,
                ParseIssueKind.PARSER_UNAVAILABLE,
                _PARSER_UNAVAILABLE_MESSAGE,
                source_sha256=source_sha256,
            )

        try:
            tree = handle.parser.parse(source.parse_bytes)
            extractor = get_extractor(spec.extractor_key)
            normalized = normalize_extraction(extractor.extract(tree, source))
            status = classify_parse_status(normalized)
            return ParsedFile(
                relative_path=relative_path,
                language=spec.language,
                status=status,
                symbols=normalized.symbols,
                imports=normalized.imports,
                calls=normalized.calls,
                issues=normalized.issues,
                source_sha256=source_sha256,
            )
        except (
            InvalidParseInventory,
            ParserConfigurationError,
            RepositoryParseError,
        ):
            raise
        except Exception:
            return _failed_file(
                relative_path,
                spec.language,
                ParseIssueKind.EXTRACTION_ERROR,
                _EXTRACTION_ERROR_MESSAGE,
                source_sha256=source_sha256,
            )


def parse_code_inventory(file_inventory: FileInventory) -> CodeParseInventory:
    """Parse a scanner inventory with a new isolated parser instance."""

    return CodeParser().parse_inventory(file_inventory)
