from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from enum import Enum

import pytest

from backend.code_chunker.exceptions import (
    ChunkerConfigurationError,
    CodeChunkerError,
    InvalidChunkInventory,
    RepositoryChunkError,
)
from backend.code_chunker.models import (
    ChunkFileStatus,
    ChunkIssue,
    ChunkIssueKind,
    ChunkKind,
    ChunkedFile,
    ChunkerConfig,
    CodeChunk,
    CodeChunkInventory,
)
from backend.code_parser.models import (
    CodeParseInventory,
    ImportInfo,
    ParameterInfo,
    ParsedFile,
    ParseStatus,
    ParsedLanguage,
    SkippedParseFile,
    ParseSkipReason,
    SourceLocation,
    SymbolKind,
)
from backend.file_scanner.models import (
    FileCategory,
    FileInventory,
    IgnoredFile,
    ScannedFile,
    SkippedDirectory,
)
from backend.code_chunker.validation import ValidatedInputs, validate_inputs


_NAMESPACE = "tracerag-repository-v1:github:example/repo"


def _scanned(
    relative_path: str = "src/app.py",
    *,
    extension: str = ".py",
    language: str | None = "python",
    category: FileCategory = FileCategory.SOURCE,
) -> ScannedFile:
    return ScannedFile(
        relative_path=relative_path,
        filename=relative_path.rsplit("/", maxsplit=1)[-1],
        extension=extension,
        language=language,
        category=category,
        size_bytes=1,
    )


def _parsed(
    relative_path: str = "src/app.py",
    *,
    language: ParsedLanguage = ParsedLanguage.PYTHON,
    status: ParseStatus = ParseStatus.SUCCESS,
    digest: str | None = "a" * 64,
) -> ParsedFile:
    return ParsedFile(relative_path, language, status, (), (), (), (), digest)


def _inventories(
    *,
    scanned_files: tuple[ScannedFile, ...] = (_scanned(),),
    parsed_files: tuple[ParsedFile, ...] = (_parsed(),),
    ignored: tuple[IgnoredFile, ...] = (),
    skipped_directories: tuple[SkippedDirectory, ...] = (),
    skipped: tuple[SkippedParseFile, ...] = (),
    root: str = "/repo",
    scanner_namespace: object = _NAMESPACE,
    parser_namespace: object = _NAMESPACE,
) -> tuple[FileInventory, CodeParseInventory]:
    scanner = FileInventory(
        root,
        len(scanned_files) + len(ignored),
        len(scanned_files),
        len(ignored),
        scanned_files,
        ignored,
        skipped_directories,
        scanner_namespace,
    )
    parser = CodeParseInventory(
        root,
        len(parsed_files) + len(skipped),
        sum(item.status is ParseStatus.SUCCESS for item in parsed_files),
        sum(item.status is ParseStatus.PARTIAL for item in parsed_files),
        sum(item.status is ParseStatus.FAILED for item in parsed_files),
        len(skipped),
        parsed_files,
        skipped,
        parser_namespace,
    )
    return scanner, parser


def test_inventory_validation_returns_frozen_ordered_o1_join_pairs():
    scanner, parser = _inventories(
        scanned_files=(
            _scanned("src/A.py"),
            _scanned("src/b.ts", extension=".ts", language="typescript"),
        ),
        parsed_files=(
            _parsed("src/A.py"),
            _parsed("src/b.ts", language=ParsedLanguage.TYPESCRIPT),
        ),
    )

    result = validate_inputs(scanner, parser)

    assert result == ValidatedInputs(
        "/repo",
        _NAMESPACE,
        ((scanner.files[0], parser.files[0]), (scanner.files[1], parser.files[1])),
    )
    assert getattr(ValidatedInputs, "__slots__") == (
        "repository_path",
        "repository_namespace",
        "pairs",
    )
    assert getattr(ValidatedInputs, "__dataclass_params__").frozen is True


@pytest.mark.parametrize("value", (None, object(), "not-an-inventory"))
def test_inventory_validation_rejects_wrong_inventory_types(value):
    scanner, parser = _inventories()

    with pytest.raises(InvalidChunkInventory):
        validate_inputs(value, parser)
    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, value)


@pytest.mark.parametrize(
    "inventory_name, field_name, value",
    (
        ("scanner", "files", []),
        ("scanner", "ignored", []),
        ("scanner", "skipped_directories", []),
        ("parser", "files", []),
        ("parser", "skipped", []),
        ("scanner", "files", (object(),)),
        ("scanner", "ignored", (object(),)),
        ("scanner", "skipped_directories", (object(),)),
        ("parser", "files", (object(),)),
        ("parser", "skipped", (object(),)),
    ),
)
def test_inventory_validation_rejects_non_tuple_or_wrong_member_collections(
    inventory_name, field_name, value
):
    scanner, parser = _inventories()
    if inventory_name == "scanner":
        scanner = replace(scanner, **{field_name: value})
    else:
        parser = replace(parser, **{field_name: value})

    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, parser)


@pytest.mark.parametrize(
    "scanner_changes, parser_changes",
    (
        ({"included_files": 0}, {}),
        ({"ignored_files": 1}, {}),
        ({"total_files_seen": 0}, {}),
        ({}, {"total_files_requested": 0}),
        ({}, {"success_files": 0}),
        ({}, {"partial_files": 1}),
        ({}, {"failed_files": 1}),
        ({}, {"skipped_files": 1}),
    ),
)
def test_inventory_validation_rejects_inconsistent_counters(
    scanner_changes, parser_changes
):
    scanner, parser = _inventories()

    with pytest.raises(InvalidChunkInventory):
        validate_inputs(
            replace(scanner, **scanner_changes), replace(parser, **parser_changes)
        )


def test_inventory_validation_rejects_a_parsed_file_with_an_invalid_status():
    scanner, parser = _inventories(parsed_files=(replace(_parsed(), status=object()),))

    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, parser)


def test_inventory_validation_rejects_duplicate_paths_and_unsorted_parser_files():
    scanner, parser = _inventories(
        scanned_files=(_scanned("src/a.py"), _scanned("src/a.py")),
        parsed_files=(_parsed("src/a.py"), _parsed("src/a.py")),
    )
    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, parser)

    scanner, parser = _inventories(
        scanned_files=(_scanned("src/a.py"), _scanned("src/b.py")),
        parsed_files=(_parsed("src/b.py"), _parsed("src/a.py")),
    )
    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, parser)


@pytest.mark.parametrize(
    "scanner_namespace, parser_namespace",
    (
        (None, _NAMESPACE),
        ("", _NAMESPACE),
        (object(), _NAMESPACE),
        (_NAMESPACE, None),
        (_NAMESPACE, ""),
        (_NAMESPACE, object()),
        (_NAMESPACE, "tracerag-repository-v1:github:other/repo"),
    ),
)
def test_namespace_validation_rejects_missing_empty_wrong_or_different_namespace(
    scanner_namespace, parser_namespace
):
    scanner, parser = _inventories(
        scanner_namespace=scanner_namespace, parser_namespace=parser_namespace
    )

    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, parser)


def test_inventory_validation_rejects_root_mismatch_missing_join_and_non_code_category():
    scanner, parser = _inventories()
    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, replace(parser, repository_path="/other"))

    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, replace(parser, files=(_parsed("src/missing.py"),)))

    scanner, parser = _inventories(
        scanned_files=(_scanned(category=FileCategory.CONFIG),)
    )
    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, parser)


@pytest.mark.parametrize(
    "scanned, parsed",
    (
        (_scanned(extension=".py", language="java"), _parsed()),
        (_scanned(extension=".jsx", language="javascript"), _parsed()),
        (_scanned(extension=".md", language=None), _parsed()),
    ),
)
def test_join_validation_rejects_incompatible_language_selector(scanned, parsed):
    scanner, parser = _inventories(scanned_files=(scanned,), parsed_files=(parsed,))

    with pytest.raises(InvalidChunkInventory):
        validate_inputs(scanner, parser)


@pytest.mark.parametrize(
    "status, digest, expected",
    (
        (ParseStatus.SUCCESS, None, True),
        (ParseStatus.PARTIAL, "A" * 64, True),
        (ParseStatus.SUCCESS, "f" * 63, True),
        (ParseStatus.SUCCESS, "g" * 64, True),
        (ParseStatus.FAILED, None, False),
    ),
)
def test_join_validation_enforces_processable_digest_but_allows_failed_none(
    status, digest, expected
):
    scanner, parser = _inventories(parsed_files=(_parsed(status=status, digest=digest),))

    if expected:
        with pytest.raises(InvalidChunkInventory):
            validate_inputs(scanner, parser)
    else:
        assert validate_inputs(scanner, parser).pairs == ((scanner.files[0], parser.files[0]),)


@pytest.mark.parametrize(
    "extension, scanner_language, parsed_language",
    (
        (".py", "python", ParsedLanguage.PYTHON),
        (".java", "java", ParsedLanguage.JAVA),
        (".js", "javascript", ParsedLanguage.JAVASCRIPT),
        (".jsx", "javascript", ParsedLanguage.JAVASCRIPT),
        (".ts", "typescript", ParsedLanguage.TYPESCRIPT),
        (".tsx", "typescript", ParsedLanguage.TSX),
    ),
)
def test_join_validation_accepts_closed_language_selector(
    extension, scanner_language, parsed_language
):
    scanner, parser = _inventories(
        scanned_files=(
            _scanned(extension=extension, language=scanner_language),
        ),
        parsed_files=(_parsed(language=parsed_language),),
    )

    assert validate_inputs(scanner, parser).pairs == ((scanner.files[0], parser.files[0]),)


def test_chunk_enums_are_string_enums_with_stable_values():
    assert issubclass(ChunkKind, str)
    assert issubclass(ChunkKind, Enum)
    assert {member.name: member.value for member in ChunkKind} == {
        "SYMBOL": "symbol",
        "CONTEXT": "context",
        "FRAGMENT": "fragment",
    }
    assert {member.name: member.value for member in ChunkFileStatus} == {
        "SUCCESS": "success",
        "PARTIAL": "partial",
        "FAILED": "failed",
    }
    assert {member.name: member.value for member in ChunkIssueKind} == {
        "SOURCE_CHANGED": "source_changed",
        "READ_ERROR": "read_error",
        "PATH_INVALID": "path_invalid",
        "LINK_UNSAFE": "link_unsafe",
        "DECODING_ERROR": "decoding_error",
        "LOCATION_INVALID": "location_invalid",
        "PARSE_UNAVAILABLE": "parse_unavailable",
        "FRAGMENTATION_ERROR": "fragmentation_error",
    }
    assert issubclass(ChunkFileStatus, str)
    assert issubclass(ChunkFileStatus, Enum)
    assert issubclass(ChunkIssueKind, str)
    assert issubclass(ChunkIssueKind, Enum)


@pytest.mark.parametrize(
    ("model", "expected_fields"),
    [
        (ChunkerConfig, ["target_chunk_bytes", "max_chunk_bytes"]),
        (
            CodeChunk,
            [
                "chunk_id",
                "kind",
                "location",
                "content",
                "content_hash",
                "symbol_kind",
                "symbol_name",
                "qualified_name",
                "parent_qualified_name",
                "parameters",
                "return_type",
                "modifiers",
                "base_types",
                "implemented_types",
                "fragment_index",
                "fragment_count",
            ],
        ),
        (ChunkIssue, ["kind", "message", "location"]),
        (
            ChunkedFile,
            [
                "relative_path",
                "language",
                "parse_status",
                "status",
                "source_sha256",
                "imports",
                "chunks",
                "issues",
            ],
        ),
        (
            CodeChunkInventory,
            [
                "repository_path",
                "repository_namespace",
                "total_files_requested",
                "success_files",
                "partial_files",
                "failed_files",
                "total_chunks",
                "files",
            ],
        ),
    ],
)
def test_public_models_are_frozen_slotted_dataclasses_with_exact_field_order(
    model, expected_fields
):
    assert is_dataclass(model)
    assert [field.name for field in fields(model)] == expected_fields
    assert getattr(model, "__slots__") == tuple(expected_fields)
    assert getattr(model, "__dataclass_params__").frozen is True


def test_models_are_immutable_and_do_not_have_instance_dicts():
    config = ChunkerConfig()
    with pytest.raises(FrozenInstanceError):
        config.target_chunk_bytes = 1
    with pytest.raises((AttributeError, TypeError)):
        config.extra = "not allowed"
    assert not hasattr(config, "__dict__")


def test_config_defaults_and_eager_validation():
    assert ChunkerConfig() == ChunkerConfig(4_096, 8_192)
    assert ChunkerConfig(1, 1) == ChunkerConfig(1, 1)
    for invalid in (True, False, 0, -1, 1.0, "4096"):
        with pytest.raises(ChunkerConfigurationError):
            ChunkerConfig(invalid, 8_192)
        with pytest.raises(ChunkerConfigurationError):
            ChunkerConfig(4_096, invalid)
    with pytest.raises(ChunkerConfigurationError):
        ChunkerConfig(8_193, 8_192)


def test_fatal_exception_hierarchy_uses_direct_subclasses():
    assert issubclass(ChunkerConfigurationError, CodeChunkerError)
    assert issubclass(InvalidChunkInventory, CodeChunkerError)
    assert issubclass(RepositoryChunkError, CodeChunkerError)
    assert ChunkerConfigurationError.__bases__ == (CodeChunkerError,)
    assert InvalidChunkInventory.__bases__ == (CodeChunkerError,)
    assert RepositoryChunkError.__bases__ == (CodeChunkerError,)


def test_public_models_accept_parser_values_and_tuple_collections():
    location = SourceLocation(0, 8, 1, 0, 1, 8)
    parameter = ParameterInfo("value", "int", None)
    import_info = ImportInfo("pkg", (), False, (), location)
    chunk = CodeChunk(
        "chunk-id",
        ChunkKind.SYMBOL,
        location,
        "value()",
        "content-hash",
        SymbolKind.FUNCTION,
        "value",
        "module.value",
        None,
        (parameter,),
        "int",
        ("public",),
        (),
        (),
        0,
        1,
    )
    issue = ChunkIssue(ChunkIssueKind.READ_ERROR, "could not read", None)
    file = ChunkedFile(
        "src/example.py",
        ParsedLanguage.PYTHON,
        ParseStatus.SUCCESS,
        ChunkFileStatus.SUCCESS,
        "a" * 64,
        (import_info,),
        (chunk,),
        (issue,),
    )
    inventory = CodeChunkInventory(
        ".",
        "tracerag-repository-v1:github:example/repo",
        1,
        1,
        0,
        0,
        1,
        (file,),
    )
    assert chunk.parameters == (parameter,)
    assert file.imports == (import_info,)
    assert inventory.files == (file,)
    assert isinstance(chunk.parameters, tuple)
    assert isinstance(file.chunks, tuple)
    assert isinstance(inventory.files, tuple)
