from dataclasses import FrozenInstanceError, fields, is_dataclass
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
    ImportInfo,
    ParameterInfo,
    ParseStatus,
    ParsedLanguage,
    SourceLocation,
    SymbolKind,
)


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
