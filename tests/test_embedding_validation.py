"""Boundary validation for immutable Module 4 chunk inventories."""

from dataclasses import replace

import pytest

from backend.code_chunker.models import (
    ChunkFileStatus,
    ChunkKind,
    ChunkedFile,
    CodeChunk,
    CodeChunkInventory,
)
from backend.code_parser.models import (
    ParseStatus,
    ParsedLanguage,
    SourceLocation,
    SymbolKind,
)
from backend.embedding_vector_store.exceptions import InvalidCodeChunkInventory
from backend.embedding_vector_store.validation import (
    ChunkInput,
    validate_and_flatten_inventory,
)


_NAMESPACE = "tracerag-repository-v1:github:example/repo"


def _chunk(
    *,
    chunk_id: str = "a" * 64,
    kind: ChunkKind = ChunkKind.SYMBOL,
    content: str = "x = 1\n",
    content_hash: str = "b" * 64,
    symbol_kind: SymbolKind | None = SymbolKind.FUNCTION,
    qualified_name: str | None = "module.name",
    parent_qualified_name: str | None = None,
    start_byte: int = 0,
    end_byte: int = 6,
    fragment_index: int = 0,
) -> CodeChunk:
    return CodeChunk(
        chunk_id,
        kind,
        SourceLocation(start_byte, end_byte, 1, 0, 1, end_byte),
        content,
        content_hash,
        symbol_kind,
        "name",
        qualified_name,
        parent_qualified_name,
        (),
        None,
        (),
        (),
        (),
        fragment_index,
        1,
    )


def _file(
    relative_path: str = "src/example.py",
    *,
    language: ParsedLanguage = ParsedLanguage.PYTHON,
    status: ChunkFileStatus = ChunkFileStatus.SUCCESS,
    chunks: tuple[CodeChunk, ...] = (),
) -> ChunkedFile:
    return ChunkedFile(
        relative_path,
        language,
        ParseStatus.SUCCESS,
        status,
        "c" * 64,
        (),
        chunks,
        (),
    )


def _inventory(
    files: tuple[ChunkedFile, ...] = (), *, namespace: object = _NAMESPACE
) -> CodeChunkInventory:
    return CodeChunkInventory(
        ".",
        namespace,
        len(files),
        sum(file.status is ChunkFileStatus.SUCCESS for file in files),
        sum(file.status is ChunkFileStatus.PARTIAL for file in files),
        sum(file.status is ChunkFileStatus.FAILED for file in files),
        sum(len(file.chunks) for file in files),
        files,
    )


@pytest.mark.parametrize("value", (None, object(), "not-an-inventory"))
def test_inventory_rejects_wrong_inventory_type(value: object) -> None:
    with pytest.raises(InvalidCodeChunkInventory):
        validate_and_flatten_inventory(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("namespace", ("", None, 1))
def test_inventory_rejects_empty_or_nonstring_namespace(namespace: object) -> None:
    with pytest.raises(InvalidCodeChunkInventory):
        validate_and_flatten_inventory(_inventory(namespace=namespace))


@pytest.mark.parametrize(
    "changes",
    (
        {"total_files_requested": 1},
        {"success_files": 1},
        {"partial_files": 1},
        {"failed_files": 1},
        {"total_chunks": 1},
    ),
)
def test_inventory_rejects_inconsistent_counters(changes: dict[str, int]) -> None:
    with pytest.raises(InvalidCodeChunkInventory):
        validate_and_flatten_inventory(replace(_inventory(), **changes))


@pytest.mark.parametrize(
    ("target", "value"),
    (
        ("inventory", []),
        ("file", []),
        ("chunks", []),
        ("chunks", (object(),)),
    ),
)
def test_inventory_rejects_non_tuple_or_wrong_member_collections(
    target: str, value: object
) -> None:
    file = _file(chunks=(_chunk(),))
    if target == "inventory":
        inventory = replace(_inventory((file,)), files=value)
    elif target == "file":
        inventory = replace(_inventory((file,)), files=(value,))
    else:
        inventory = _inventory((replace(file, chunks=value),))

    with pytest.raises(InvalidCodeChunkInventory):
        validate_and_flatten_inventory(inventory)


def test_inventory_rejects_duplicate_file_paths_and_chunk_ids() -> None:
    chunk = _chunk()
    duplicate_paths = _inventory((_file(chunks=(chunk,)), _file(chunks=())))
    duplicate_ids = _inventory(
        (_file(chunks=(chunk, replace(chunk, content_hash="d" * 64))),)
    )

    with pytest.raises(InvalidCodeChunkInventory):
        validate_and_flatten_inventory(duplicate_paths)
    with pytest.raises(InvalidCodeChunkInventory):
        validate_and_flatten_inventory(duplicate_ids)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("chunk_id", "A" * 64),
        ("chunk_id", "a" * 63),
        ("content_hash", "B" * 64),
        ("content_hash", "b" * 63),
        ("content", ""),
        ("content", None),
        ("qualified_name", ""),
        ("qualified_name", 1),
        ("parent_qualified_name", ""),
        ("parent_qualified_name", 1),
    ),
)
def test_inventory_rejects_malformed_chunk_identity_names_or_content(
    field: str, value: object
) -> None:
    inventory = _inventory((_file(chunks=(replace(_chunk(), **{field: value}),)),))

    with pytest.raises(InvalidCodeChunkInventory):
        validate_and_flatten_inventory(inventory)


@pytest.mark.parametrize("relative_path", ("", "/absolute.py", "src\\app.py", "../app.py", "src/../app.py"))
def test_inventory_rejects_non_posix_relative_paths(relative_path: str) -> None:
    with pytest.raises(InvalidCodeChunkInventory):
        validate_and_flatten_inventory(_inventory((_file(relative_path),)))


@pytest.mark.parametrize(
    ("target", "field", "value"),
    (
        ("file", "language", "python"),
        ("chunk", "kind", "symbol"),
        ("chunk", "symbol_kind", "function"),
    ),
)
def test_inventory_rejects_wrong_enum_types(target: str, field: str, value: object) -> None:
    file = _file(chunks=(_chunk(),))
    if target == "file":
        inventory = _inventory((replace(file, **{field: value}),))
    else:
        inventory = _inventory((replace(file, chunks=(replace(file.chunks[0], **{field: value}),)),))

    with pytest.raises(InvalidCodeChunkInventory):
        validate_and_flatten_inventory(inventory)


def test_inventory_rejects_files_and_chunks_out_of_module_four_order() -> None:
    first = _file("a.py", chunks=(_chunk(chunk_id="a" * 64),))
    second = _file("b.py", chunks=(_chunk(chunk_id="b" * 64),))
    unsorted_files = _inventory((second, first))
    later = _chunk(chunk_id="c" * 64, start_byte=6, end_byte=12)
    earlier = _chunk(chunk_id="d" * 64, start_byte=0, end_byte=6)
    unsorted_chunks = _inventory((_file(chunks=(later, earlier)),))

    with pytest.raises(InvalidCodeChunkInventory):
        validate_and_flatten_inventory(unsorted_files)
    with pytest.raises(InvalidCodeChunkInventory):
        validate_and_flatten_inventory(unsorted_chunks)


def test_inventory_flattens_empty_inventory_to_immutable_values() -> None:
    namespace, inputs = validate_and_flatten_inventory(_inventory())

    assert namespace == _NAMESPACE
    assert inputs == ()
    assert isinstance(inputs, tuple)


def test_inventory_flattens_mixed_languages_in_file_and_chunk_order() -> None:
    python_chunk = _chunk(chunk_id="a" * 64)
    java_chunk = _chunk(chunk_id="b" * 64)
    files = (
        _file("a.py", language=ParsedLanguage.PYTHON, chunks=(python_chunk,)),
        _file("b.java", language=ParsedLanguage.JAVA, chunks=(java_chunk,)),
    )

    namespace, inputs = validate_and_flatten_inventory(_inventory(files))

    assert namespace == _NAMESPACE
    assert inputs == (
        ChunkInput(python_chunk, "a.py", ParsedLanguage.PYTHON),
        ChunkInput(java_chunk, "b.java", ParsedLanguage.JAVA),
    )
    assert getattr(ChunkInput, "__slots__") == ("chunk", "relative_path", "language")
    assert getattr(ChunkInput, "__dataclass_params__").frozen is True
