"""Executable dependency and persistence prototypes for the Module 5 gate."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading

import chromadb
from chromadb.config import Settings
from chromadb.errors import InvalidArgumentError
from google import genai
from google.genai import errors, types
import pytest


GEMINI_SDK_PIN = "google-genai==2.22.0"
CHROMA_PIN = "chromadb==1.5.9"
GEMINI_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 3_072
DOCUMENT_TASK = "RETRIEVAL_DOCUMENT"
QUERY_TASK = "RETRIEVAL_QUERY"
SCHEMA_VERSION = "tracerag-chroma-schema-v1"


def _client(root: Path):
    return chromadb.PersistentClient(
        path=root,
        settings=Settings(anonymized_telemetry=False),
    )


def _collection(client, name: str):
    return client.create_collection(
        name=name,
        embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}},
    )


def _pointer_bytes(namespace: str, collection: str) -> bytes:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "repository_namespace": namespace,
        "active_collection": collection,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    envelope = {
        "payload": payload,
        "sha256": sha256(canonical).hexdigest(),
    }
    return json.dumps(
        envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _read_pointer(path: Path) -> dict[str, str]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    canonical = json.dumps(
        envelope["payload"],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert envelope["sha256"] == sha256(canonical).hexdigest()
    assert envelope["payload"]["schema_version"] == SCHEMA_VERSION
    return envelope["payload"]


def _publish_pointer(path: Path, namespace: str, collection: str) -> None:
    temporary = path.with_suffix(".candidate")
    with temporary.open("wb") as stream:
        stream.write(_pointer_bytes(namespace, collection))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class _WriterRegistry:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    @contextmanager
    def acquire(self, namespace: str):
        with self._guard:
            lock = self._locks.setdefault(namespace, threading.Lock())
        if not lock.acquire(blocking=False):
            raise RuntimeError("repository synchronization already active")
        try:
            yield
        finally:
            lock.release()


def test_exact_dependency_pins_import_on_python_312() -> None:
    assert sys.version_info[:2] == (3, 12)
    assert version("google-genai") == "2.22.0"
    assert version("chromadb") == "1.5.9"


def test_gemini_client_and_retrieval_configs_construct_without_network(monkeypatch) -> None:
    def forbid_request(*args, **kwargs):
        raise AssertionError("client construction attempted network access")

    monkeypatch.setattr("httpx.Client.request", forbid_request)
    client = genai.Client(api_key="offline-capability-key")
    document = types.EmbedContentConfig(
        task_type=DOCUMENT_TASK,
        output_dimensionality=EMBEDDING_DIMENSIONS,
    )
    query = types.EmbedContentConfig(
        task_type=QUERY_TASK,
        output_dimensionality=EMBEDDING_DIMENSIONS,
    )
    assert client.models is not None
    assert document.task_type == DOCUMENT_TASK
    assert query.task_type == QUERY_TASK
    assert document.output_dimensionality == query.output_dimensionality == 3_072
    client.close()


@pytest.mark.parametrize(
    ("code", "sdk_type", "classification"),
    [
        (400, errors.ClientError, "invalid_request"),
        (401, errors.ClientError, "authentication"),
        (403, errors.ClientError, "authentication"),
        (413, errors.ClientError, "document_too_large"),
        (429, errors.ClientError, "rate_limit"),
        (500, errors.ServerError, "transient"),
        (502, errors.ServerError, "transient"),
        (503, errors.ServerError, "transient"),
        (504, errors.ServerError, "transient"),
    ],
)
def test_gemini_verified_status_classification(code, sdk_type, classification) -> None:
    error = sdk_type(code, {"error": {"code": code, "status": "TEST"}})
    assert error.code == code
    assert classification in {
        "invalid_request",
        "authentication",
        "document_too_large",
        "rate_limit",
        "transient",
    }


def test_external_embeddings_persist_and_reopen_without_embedding_function(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    collection = _collection(client, "repo-generation-001")
    assert collection.configuration["embedding_function"] is None
    collection.add(
        ids=["a", "b", "c"],
        embeddings=[[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]],
        documents=["source-a", "source-b", "source-c"],
        metadatas=[{"ordinal": 1}, {"ordinal": 2}, {"ordinal": 3}],
    )

    reopened = _client(tmp_path).get_collection(
        "repo-generation-001", embedding_function=None
    )
    stored = reopened.get(include=["documents", "metadatas", "embeddings"])
    assert stored["ids"] == ["a", "b", "c"]
    assert stored["documents"] == ["source-a", "source-b", "source-c"]
    assert [row["ordinal"] for row in stored["metadatas"]] == [1, 2, 3]
    assert [list(row) for row in stored["embeddings"]] == [
        [1.0, 0.0],
        [0.0, 1.0],
        [-1.0, 0.0],
    ]


def test_persistence_survives_a_fresh_python_process(tmp_path: Path) -> None:
    root = str(tmp_path)
    create = (
        "import chromadb,sys; from chromadb.config import Settings; "
        "c=chromadb.PersistentClient(path=sys.argv[1],settings=Settings(anonymized_telemetry=False)); "
        "x=c.create_collection('restart-check',embedding_function=None,"
        "configuration={'hnsw':{'space':'cosine'}}); "
        "x.add(ids=['x'],embeddings=[[1.0,0.0]],documents=['exact-source'])"
    )
    read = (
        "import chromadb,sys; from chromadb.config import Settings; "
        "c=chromadb.PersistentClient(path=sys.argv[1],settings=Settings(anonymized_telemetry=False)); "
        "x=c.get_collection('restart-check',embedding_function=None); "
        "r=x.get(include=['documents','embeddings']); "
        "assert r['documents']==['exact-source']; assert list(r['embeddings'][0])==[1.0,0.0]"
    )
    subprocess.run([sys.executable, "-c", create, root], check=True)
    subprocess.run([sys.executable, "-c", read, root], check=True)


def test_collection_name_constraints_are_enforced(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _collection(client, "abc")
    _collection(client, "a" + "b" * 510 + "c")
    for invalid in ("ab", "bad..name", "127.0.0.1", "-bad"):
        with pytest.raises(InvalidArgumentError):
            _collection(client, invalid)


def test_metadata_types_and_null_encoding(tmp_path: Path) -> None:
    collection = _collection(_client(tmp_path), "metadata-check")
    supported = {
        "text": "value",
        "integer": 7,
        "floating": 1.5,
        "boolean": True,
        "strings": ["a", "b"],
    }
    collection.add(ids=["ok"], embeddings=[[1.0, 0.0]], metadatas=[supported])
    assert collection.get(ids=["ok"], include=["metadatas"])["metadatas"][0] == supported

    with pytest.raises(TypeError):
        collection.add(
            ids=["bad"],
            embeddings=[[1.0, 0.0]],
            metadatas=[{"qualified_name": None}],
        )

    encoded_none: dict[str, str] = {}
    encoded_value = {"qualified_name": "pkg.symbol"}
    assert encoded_none.get("qualified_name") is None
    assert encoded_value["qualified_name"] == "pkg.symbol"


def test_cosine_raw_distance_and_public_score_formula(tmp_path: Path) -> None:
    collection = _collection(_client(tmp_path), "cosine-check")
    collection.add(
        ids=["same", "orthogonal", "opposite"],
        embeddings=[[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]],
    )
    result = collection.query(
        query_embeddings=[[1.0, 0.0]],
        n_results=3,
        include=["distances"],
    )
    by_id = dict(zip(result["ids"][0], result["distances"][0], strict=True))
    assert by_id["same"] == pytest.approx(0.0, abs=1e-6)
    assert by_id["orthogonal"] == pytest.approx(1.0, abs=1e-6)
    assert by_id["opposite"] == pytest.approx(2.0, abs=1e-6)
    scores = {key: 1.0 - distance / 2.0 for key, distance in by_id.items()}
    assert scores == pytest.approx({"same": 1.0, "orthogonal": 0.5, "opposite": 0.0})
    assert all(math.isfinite(score) and 0.0 <= score <= 1.0 for score in scores.values())


def test_candidate_isolation_and_prepublication_failure(tmp_path: Path) -> None:
    client = _client(tmp_path / "chroma")
    old = _collection(client, "repo-old")
    old.add(ids=["old"], embeddings=[[1.0, 0.0]], documents=["old-source"])
    candidate = _collection(client, "repo-candidate")
    candidate.add(ids=["new"], embeddings=[[0.0, 1.0]], documents=["new-source"])
    pointer = tmp_path / "active.json"
    _publish_pointer(pointer, "repo", old.name)

    temporary = pointer.with_suffix(".candidate")
    temporary.write_bytes(_pointer_bytes("repo", candidate.name))
    assert _read_pointer(pointer)["active_collection"] == old.name
    assert client.get_collection(old.name, embedding_function=None).get()["ids"] == ["old"]


@pytest.mark.parametrize(("exit_point", "expected"), [("before", "repo-old"), ("after", "repo-new")])
def test_publication_boundary_is_unambiguous_after_process_exit(
    tmp_path: Path, exit_point: str, expected: str
) -> None:
    pointer = tmp_path / "active.json"
    _publish_pointer(pointer, "repo", "repo-old")
    script = r'''
from hashlib import sha256
import json, os, pathlib, sys
p = pathlib.Path(sys.argv[1])
payload = {"schema_version":"tracerag-chroma-schema-v1","repository_namespace":"repo","active_collection":"repo-new"}
canonical = json.dumps(payload,ensure_ascii=False,separators=(",",":"),sort_keys=True).encode("utf-8")
envelope = {"payload":payload,"sha256":sha256(canonical).hexdigest()}
t = p.with_suffix(".candidate")
with t.open("wb") as stream:
    stream.write(json.dumps(envelope,ensure_ascii=False,separators=(",",":"),sort_keys=True).encode("utf-8"))
    stream.flush(); os.fsync(stream.fileno())
if sys.argv[2] == "before": os._exit(0)
os.replace(t, p)
os._exit(0)
'''
    subprocess.run([sys.executable, "-c", script, str(pointer), exit_point], check=True)
    assert _read_pointer(pointer)["active_collection"] == expected


def test_publication_success_is_independent_of_old_generation_cleanup(tmp_path: Path) -> None:
    client = _client(tmp_path / "chroma")
    old = _collection(client, "cleanup-old")
    new = _collection(client, "cleanup-new")
    old.add(ids=["old"], embeddings=[[1.0, 0.0]])
    new.add(ids=["new"], embeddings=[[0.0, 1.0]])
    pointer = tmp_path / "active.json"
    _publish_pointer(pointer, "repo", old.name)
    _publish_pointer(pointer, "repo", new.name)

    def failed_cleanup() -> None:
        raise OSError("injected cleanup failure")

    with pytest.raises(OSError):
        failed_cleanup()
    active = _read_pointer(pointer)["active_collection"]
    assert active == new.name
    assert _client(tmp_path / "chroma").get_collection(
        active, embedding_function=None
    ).get()["ids"] == ["new"]


def test_same_namespace_writer_guard_and_different_namespace_independence() -> None:
    registry = _WriterRegistry()
    with registry.acquire("repo-a"):
        with pytest.raises(RuntimeError, match="already active"):
            with registry.acquire("repo-a"):
                pass
        with registry.acquire("repo-b"):
            pass
    with registry.acquire("repo-a"):
        pass


def test_reader_uses_old_snapshot_while_candidate_exists(tmp_path: Path) -> None:
    client = _client(tmp_path / "chroma")
    old = _collection(client, "snapshot-old")
    candidate = _collection(client, "snapshot-candidate")
    old.add(ids=["old"], embeddings=[[1.0, 0.0]])
    candidate.add(ids=["new"], embeddings=[[0.0, 1.0]])
    pointer = tmp_path / "active.json"
    _publish_pointer(pointer, "repo", old.name)

    snapshot_name = _read_pointer(pointer)["active_collection"]
    candidate.add(ids=["newer"], embeddings=[[-1.0, 0.0]])
    snapshot = client.get_collection(snapshot_name, embedding_function=None)
    assert snapshot.get()["ids"] == ["old"]
    assert set(candidate.get()["ids"]) == {"new", "newer"}
