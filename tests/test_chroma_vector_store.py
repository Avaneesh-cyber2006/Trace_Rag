"""Chroma storage-schema and repository-isolation contracts."""

from dataclasses import replace
import inspect
import json
import math
from pathlib import Path
import re
import subprocess
import sys

import pytest

from backend.embedding_vector_store.exceptions import (
    VectorStoreConfigurationError,
    VectorStoreCorruptionError,
    VectorStoreWriteError,
)
from backend.embedding_vector_store.models import (
    EmbeddingModelIdentity,
    EmbeddingVector,
    VectorRecord,
)
from backend.embedding_vector_store.stores.base import (
    CandidateIndex,
    RepositoryIndexSnapshot,
    StoredRecord,
)
from backend.embedding_vector_store.stores.chroma import (
    STORAGE_SCHEMA_VERSION,
    ChromaVectorStore,
)


NAMESPACE = "tracerag-repository-v1:github:example/資料庫"
IDENTITY = EmbeddingModelIdentity("gemini", "gemini-embedding-001", 3, "retrieval-3072-v1")
VECTOR = EmbeddingVector((0.25, -0.5, 0.75))


def _store(tmp_path: Path) -> ChromaVectorStore:
    return ChromaVectorStore(tmp_path / "external-vector-data")


def _record(**changes: object) -> StoredRecord:
    record = StoredRecord(
        NAMESPACE,
        "a" * 64,
        "b" * 64,
        "src/資料/δelta.py",
        "python",
        "symbol",
        "function",
        "資料.計算",
        "資料",
        "def 計算():\r\n    return 'λ  ' \n",
        VECTOR,
        IDENTITY,
        "tracerag-embedding-document-v1",
        STORAGE_SCHEMA_VERSION,
    )
    return replace(record, **changes)


def _snapshot(**changes: object) -> RepositoryIndexSnapshot:
    snapshot = RepositoryIndexSnapshot(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        STORAGE_SCHEMA_VERSION,
        7,
        object(),
    )
    return replace(snapshot, **changes)


def _candidate(**changes: object) -> CandidateIndex:
    candidate = CandidateIndex(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        STORAGE_SCHEMA_VERSION,
        2,
        "candidate10",
    )
    return replace(candidate, **changes)


def _vector_record(
    chunk_id: str, content_hash: str, content: str, values: tuple[float, ...]
) -> VectorRecord:
    return VectorRecord(
        chunk_id,
        content_hash,
        "src/資料/δelta.py",
        "python",
        "symbol",
        "function",
        "資料.計算",
        "資料",
        content,
        EmbeddingVector(values),
    )


def _candidate_records() -> tuple[VectorRecord, VectorRecord]:
    return (
        _vector_record(
            "c" * 64,
            "d" * 64,
            "def 計算():\r\n    return 'λ  ' \n",
            (1.0, 0.0, 0.0),
        ),
        _vector_record(
            "e" * 64,
            "f" * 64,
            "class 資料:\n    値 = 'two'\n",
            (0.0, 1.0, 0.0),
        ),
    )


def test_persistence_root_is_mandatory_resolved_and_not_inventory_inferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parameter = inspect.signature(ChromaVectorStore).parameters["persistence_root"]
    assert parameter.default is inspect.Parameter.empty
    assert tuple(inspect.signature(ChromaVectorStore).parameters) == ("persistence_root",)

    monkeypatch.chdir(tmp_path)
    store = ChromaVectorStore(Path("external") / "chroma")

    assert store._persistence_root == (tmp_path / "external" / "chroma").resolve()
    assert store._persistence_root.is_absolute()


@pytest.mark.parametrize("persistence_root", (None, "", 7))
def test_persistence_root_rejects_unsupported_values(persistence_root: object) -> None:
    with pytest.raises(VectorStoreConfigurationError):
        ChromaVectorStore(persistence_root)  # type: ignore[arg-type]


def test_namespace_mapping_is_deterministic_safe_and_separates_near_namespaces(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = "tracerag-repository-v1:github:example/repo"
    second = "tracerag-repository-v1:github:example/repó"

    first_name = store._collection_identifier(first, "abc123")
    second_name = store._collection_identifier(second, "abc123")

    assert first_name == store._collection_identifier(first, "abc123")
    assert first_name != second_name
    assert re.fullmatch(r"tr5-[0-9a-f]{64}-[a-z0-9]+", first_name)
    assert 3 <= len(first_name) <= 512


@pytest.mark.parametrize("namespace", ("", None, 5))
def test_namespace_mapping_rejects_unsupported_namespaces(
    tmp_path: Path, namespace: object
) -> None:
    with pytest.raises(VectorStoreConfigurationError):
        _store(tmp_path)._collection_identifier(namespace, "abc123")  # type: ignore[arg-type]


@pytest.mark.parametrize("generation_token", ("", "Upper", "bad-token", "λ", 7))
def test_namespace_mapping_rejects_unsafe_generation_tokens(
    tmp_path: Path, generation_token: object
) -> None:
    with pytest.raises(VectorStoreConfigurationError):
        _store(tmp_path)._collection_identifier(NAMESPACE, generation_token)  # type: ignore[arg-type]


def test_record_metadata_round_trip_preserves_every_field_and_exact_content(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    original = _record()

    encoded = store._encode_record(original)
    decoded = store._decode_record(NAMESPACE, **encoded)

    assert decoded == original
    assert encoded["document"] == "def 計算():\r\n    return 'λ  ' \n"
    assert encoded["metadata"]["repository_namespace"] == NAMESPACE
    assert encoded["metadata"]["embedding_dimensions"] == 3


def test_record_metadata_omits_optional_none_keys_and_decodes_them_reversibly(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    original = _record(symbol_kind=None, qualified_name=None, parent_qualified_name=None)

    encoded = store._encode_record(original)
    metadata = encoded["metadata"]
    decoded = store._decode_record(NAMESPACE, **encoded)

    assert "symbol_kind" not in metadata
    assert "qualified_name" not in metadata
    assert "parent_qualified_name" not in metadata
    assert decoded.symbol_kind is None
    assert decoded.qualified_name is None
    assert decoded.parent_qualified_name is None


def test_control_metadata_round_trip_preserves_identity_versions_count_and_dimensions(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    original = _snapshot()

    metadata = store._encode_control_metadata(original)
    decoded = store._decode_snapshot_metadata(NAMESPACE, metadata, object())

    assert decoded.repository_namespace == original.repository_namespace
    assert decoded.identity == original.identity
    assert decoded.identity.dimensions == 3
    assert decoded.document_version == original.document_version
    assert decoded.schema_version == STORAGE_SCHEMA_VERSION
    assert decoded.expected_chunk_count == 7


def test_forced_namespace_identifier_collision_still_fails_closed_on_full_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    first = "tracerag-repository-v1:github:example/repo-a"
    second = "tracerag-repository-v1:github:example/repo-b"
    monkeypatch.setattr(store, "_namespace_digest", lambda namespace: "f" * 64)

    assert store._collection_identifier(first, "abc123") == store._collection_identifier(
        second, "abc123"
    )
    encoded = store._encode_record(_record(repository_namespace=first))
    with pytest.raises(VectorStoreCorruptionError):
        store._decode_record(second, **encoded)


@pytest.mark.parametrize(
    ("target", "value"),
    (
        ("schema_version", "tracerag-chroma-schema-v2"),
        ("embedding_dimensions", 0),
        ("embedding_dimensions", True),
        ("expected_chunk_count", -1),
        ("repository_namespace", "other"),
        ("embedding_provider", ""),
        ("unsupported", {"nested": "value"}),
    ),
)
def test_control_metadata_rejects_unknown_schema_malformed_and_unsupported_values(
    tmp_path: Path, target: str, value: object
) -> None:
    store = _store(tmp_path)
    metadata = store._encode_control_metadata(_snapshot())
    metadata[target] = value

    with pytest.raises(VectorStoreCorruptionError):
        store._decode_snapshot_metadata(NAMESPACE, metadata, object())


@pytest.mark.parametrize(
    ("target", "value"),
    (
        ("schema_version", "tracerag-chroma-schema-v2"),
        ("repository_namespace", "other"),
        ("chunk_id", "short"),
        ("qualified_name", ""),
        ("embedding_dimensions", 2),
        ("unsupported", ["not", "a", "field"]),
    ),
)
def test_record_metadata_rejects_unknown_schema_malformed_and_namespace_mismatch(
    tmp_path: Path, target: str, value: object
) -> None:
    store = _store(tmp_path)
    encoded = store._encode_record(_record())
    encoded["metadata"][target] = value

    with pytest.raises(VectorStoreCorruptionError):
        store._decode_record(NAMESPACE, **encoded)


@pytest.mark.parametrize(
    "embedding",
    (
        (0.25, -0.5),
        (0.25, float("nan"), 0.75),
        (0.25, True, 0.75),
        (0.25, "-0.5", 0.75),
        "bad",
    ),
)
def test_record_metadata_rejects_invalid_embedding_dimensions_and_values(
    tmp_path: Path, embedding: object
) -> None:
    store = _store(tmp_path)
    encoded = store._encode_record(_record())
    encoded["embedding"] = embedding

    with pytest.raises(VectorStoreCorruptionError):
        store._decode_record(NAMESPACE, **encoded)


def test_external_vector_candidate_reopens_with_exact_evidence_and_no_auto_embedding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailIfCalledEmbeddingFunction:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, input: object) -> object:
            self.calls += 1
            raise AssertionError("Chroma automatic embedding must not run")

    store = _store(tmp_path)
    sentinel = FailIfCalledEmbeddingFunction()
    create_collection = store._client.create_collection

    def create_with_external_embeddings(*args: object, **kwargs: object) -> object:
        assert kwargs["embedding_function"] is None
        collection = create_collection(*args, **kwargs)
        collection._embedding_function = sentinel
        return collection

    monkeypatch.setattr(store._client, "create_collection", create_with_external_embeddings)
    candidate = _candidate()
    records = _candidate_records()

    store._create_candidate_collection(candidate)
    store._write_embedded_candidate(candidate, records)

    reopened = _store(tmp_path)
    manifest = reopened._read_candidate_manifest(candidate)

    assert sentinel.calls == 0
    assert manifest == (
        StoredRecord(
            NAMESPACE,
            records[0].chunk_id,
            records[0].content_hash,
            records[0].relative_path,
            records[0].language,
            records[0].chunk_kind,
            records[0].symbol_kind,
            records[0].qualified_name,
            records[0].parent_qualified_name,
            records[0].content,
            records[0].embedding,
            IDENTITY,
            "tracerag-embedding-document-v1",
            STORAGE_SCHEMA_VERSION,
        ),
        StoredRecord(
            NAMESPACE,
            records[1].chunk_id,
            records[1].content_hash,
            records[1].relative_path,
            records[1].language,
            records[1].chunk_kind,
            records[1].symbol_kind,
            records[1].qualified_name,
            records[1].parent_qualified_name,
            records[1].content,
            records[1].embedding,
            IDENTITY,
            "tracerag-embedding-document-v1",
            STORAGE_SCHEMA_VERSION,
        ),
    )


@pytest.mark.parametrize(
    "candidate,records",
    (
        (
            _candidate(),
            (
                _vector_record("1" * 64, "2" * 64, "wrong dimensions", (1.0, 0.0)),
                _candidate_records()[1],
            ),
        ),
        (
            _candidate(),
            (_candidate_records()[0], _candidate_records()[0]),
        ),
        (
            replace(_candidate(), expected_chunk_count=1),
            _candidate_records(),
        ),
        (
            _candidate(),
            (object(), _candidate_records()[1]),
        ),
    ),
    ids=("wrong_dimensions", "duplicate_ids", "count_mismatch", "malformed_records"),
)
def test_external_vector_candidate_write_fails_closed_before_partial_persistence(
    tmp_path: Path, candidate: CandidateIndex, records: tuple[object, ...]
) -> None:
    store = _store(tmp_path)
    store._create_candidate_collection(candidate)

    with pytest.raises(VectorStoreWriteError):
        store._write_embedded_candidate(candidate, records)  # type: ignore[arg-type]

    assert store._candidate_collection(candidate).count() == 0


@pytest.mark.parametrize(
    ("target", "value"),
    (
        ("embedding_provider", "other-provider"),
        ("document_version", "other-document-version"),
        ("schema_version", "tracerag-chroma-schema-v2"),
    ),
    ids=("identity", "document_version", "schema_version"),
)
def test_manifest_rejects_record_metadata_inconsistent_with_candidate_control(
    tmp_path: Path, target: str, value: str
) -> None:
    store = _store(tmp_path)
    candidate = _candidate()
    records = _candidate_records()
    store._create_candidate_collection(candidate)
    store._write_embedded_candidate(candidate, records)
    store._candidate_collection(candidate).update(
        ids=[records[0].chunk_id], metadatas=[{target: value}]
    )

    with pytest.raises(VectorStoreCorruptionError):
        store._read_candidate_manifest(candidate)


def test_huge_integer_embedding_fails_closed_as_corruption_error(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    encoded = store._encode_record(_record())
    encoded["embedding"] = (10**10_000, 0.0, 0.0)

    with pytest.raises(VectorStoreCorruptionError):
        store._decode_record(NAMESPACE, **encoded)


def test_huge_integer_candidate_embedding_fails_closed_as_write_error(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    candidate = _candidate()
    store._create_candidate_collection(candidate)
    huge = _vector_record("1" * 64, "2" * 64, "huge", (10**10_000, 0.0, 0.0))
    with pytest.raises(VectorStoreWriteError):
        store._write_embedded_candidate(candidate, (huge, _candidate_records()[1]))


def test_non_unit_external_vectors_reopen_as_chroma_authoritative_values(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = _candidate()
    records = (
        _vector_record("1" * 64, "2" * 64, "first", (0.1, -0.2, 0.3)),
        _vector_record("3" * 64, "4" * 64, "second", (0.4, 0.5, -0.6)),
    )
    store._create_candidate_collection(candidate)
    store._write_embedded_candidate(candidate, records)

    reopened = _store(tmp_path)
    manifest = reopened._read_candidate_manifest(candidate)
    physical = reopened._candidate_collection(candidate).get(
        include=["embeddings", "metadatas"]
    )

    assert "embedding_values" not in physical["metadatas"][0]
    assert tuple(record.embedding.values for record in manifest) == tuple(
        tuple(float(value) for value in row) for row in physical["embeddings"]
    )
    assert manifest[0].embedding != records[0].embedding


def test_finite_values_that_overflow_binary32_fail_before_candidate_mutation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = _candidate()
    store._create_candidate_collection(candidate)
    overflowing = _vector_record("1" * 64, "2" * 64, "overflow", (3.5e38, 0.0, 0.0))

    with pytest.raises(VectorStoreWriteError):
        store._write_embedded_candidate(candidate, (overflowing, _candidate_records()[1]))

    assert store._candidate_collection(candidate).count() == 0


@pytest.mark.parametrize(
    "values",
    (
        (0.5, -0.25, 0.125),
        (0.1, -0.2, 0.3),
        (-0.7, -1.1, 0.9),
        (1e-30, -3e-20, 2e-10),
        (0.5773502691896258, -0.5773502691896258, 0.5773502691896258),
    ),
    ids=("exact_f32", "ordinary", "negative", "small", "normalized_like"),
)
def test_projected_external_vectors_survive_fresh_child_process_reopen(
    tmp_path: Path, values: tuple[float, float, float]
) -> None:
    store = _store(tmp_path)
    candidate = _candidate()
    records = (
        _vector_record("1" * 64, "2" * 64, "first", values),
        _candidate_records()[1],
    )
    store._create_candidate_collection(candidate)
    store._write_embedded_candidate(candidate, records)
    collection_name = store._candidate_collection_name(candidate)
    parent_values = tuple(
        float(value)
        for value in store._candidate_collection(candidate).get(
            ids=[records[0].chunk_id], include=["embeddings"]
        )["embeddings"][0]
    )
    script = (
        "import chromadb,json,math,sys; from chromadb.config import Settings; "
        "c=chromadb.PersistentClient(path=sys.argv[1],settings=Settings(anonymized_telemetry=False)); "
        "r=c.get_collection(sys.argv[2],embedding_function=None).get(include=['embeddings']); "
        "v=[float(x) for x in r['embeddings'][0]]; assert len(v)==3 and all(math.isfinite(x) for x in v); print(json.dumps(v))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(store._persistence_root), collection_name],
        check=True,
        capture_output=True,
        text=True,
    )
    reopened_values = tuple(json.loads(result.stdout))

    assert len(reopened_values) == len(values)
    assert all(math.isfinite(value) for value in reopened_values)
    assert reopened_values == parent_values
    if values == (0.5, -0.25, 0.125):
        assert reopened_values == values


@pytest.mark.parametrize("invalid", (float("nan"), float("inf"), float("-inf")))
def test_nonfinite_provider_vectors_fail_before_candidate_mutation(
    tmp_path: Path, invalid: float
) -> None:
    store = _store(tmp_path)
    candidate = _candidate()
    store._create_candidate_collection(candidate)
    invalid_record = _vector_record("1" * 64, "2" * 64, "invalid", (invalid, 0.0, 0.0))

    with pytest.raises(VectorStoreWriteError):
        store._write_embedded_candidate(candidate, (invalid_record, _candidate_records()[1]))

    assert store._candidate_collection(candidate).count() == 0


def test_external_vector_records_have_no_vector_metadata_mirror_or_sidecar(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = _candidate()
    store._create_candidate_collection(candidate)
    store._write_embedded_candidate(candidate, _candidate_records())
    physical = store._candidate_collection(candidate).get(include=["metadatas"])
    required = {
        "schema_version", "repository_namespace", "chunk_id", "content_hash",
        "relative_path", "language", "chunk_kind", "embedding_provider",
        "embedding_model", "embedding_dimensions", "embedding_compatibility_version",
        "document_version", "symbol_kind", "qualified_name", "parent_qualified_name",
    }

    for metadata in physical["metadatas"]:
        assert set(metadata) == required
        assert all(not isinstance(value, list | dict) for value in metadata.values())
        assert "embedding_values" not in metadata
    assert not [path for path in store._persistence_root.rglob("*") if path.is_file() and path.suffix in {".json", ".npy", ".npz"}]


def test_direct_chroma_query_uses_persisted_external_embedding_field(tmp_path: Path) -> None:
    store = _store(tmp_path)
    candidate = _candidate()
    records = _candidate_records()
    store._create_candidate_collection(candidate)
    store._write_embedded_candidate(candidate, records)
    collection = store._candidate_collection(candidate)
    persisted = collection.get(ids=[records[0].chunk_id], include=["embeddings"])["embeddings"][0]
    result = collection.query(query_embeddings=[list(persisted)], n_results=2, include=["distances"])

    assert result["ids"][0][0] == records[0].chunk_id
    assert abs(result["distances"][0][0]) <= 1e-6
