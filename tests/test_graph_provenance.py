"""Graph revision binding from genuine Module 2/3/4 same-source inventories."""
from dataclasses import replace
from hashlib import sha256

import pytest

from backend.code_chunker.models import (
    CodeChunk, CodeChunkInventory, ChunkedFile, ChunkFileStatus, ChunkKind,
)
from backend.code_parser.models import (
    CallKind, CallSite, CodeParseInventory, ParsedFile, ParsedLanguage,
    ParseStatus, SourceLocation, SymbolInfo, SymbolKind,
)
from backend.file_scanner.models import FileCategory, FileInventory, ScannedFile
from backend.retrieval import (
    GraphRevision, InvalidHybridEvidence, LexicalDocument, LexicalIndex, build_graph_revision,
)
from backend.retrieval.provenance import _fingerprint
from test_hybrid_orchestrator import FakeStore, FakeProvider, record, NAMESPACE, result
from backend.retrieval import HybridRetriever, InvalidRetrievalQuery, GraphRetrievalResult

ROOT = "C:/isolated-revision"


def build_evidence(*, source_change=False, chunk_change=False, missing=False):
    texts = {"alpha": "def alpha():\n    return beta()\n", "beta": "def beta():\n    return 42\n"}
    files = []
    parsed_files = []
    chunked_files = []
    for name, text in texts.items():
        path = f"src/{name}.py"
        data = text.encode("utf-8")
        digest = sha256(data).hexdigest()
        length = len(data)
        location = SourceLocation(0, length, 1, 0, 3, 0)
        files.append(ScannedFile(path, f"{name}.py", ".py", "python", FileCategory.SOURCE, length))
        symbol = SymbolInfo(name, SymbolKind.FUNCTION, name, None, location, (), None, (), (), ())
        calls = (CallSite(name, "beta", CallKind.CALL, SourceLocation(24, 28, 2, 11, 2, 15)),) if name == "alpha" else ()
        parsed_files.append(ParsedFile(path, ParsedLanguage.PYTHON, ParseStatus.SUCCESS,
                                       (symbol,), (), calls, (), digest))
        chunk = CodeChunk(
            chunk_id=sha256(name.encode()).hexdigest(), kind=ChunkKind.SYMBOL,
            location=location, content=text, content_hash=sha256(data).hexdigest(),
            symbol_kind=SymbolKind.FUNCTION, symbol_name=name, qualified_name=name,
            parent_qualified_name=None, parameters=(), return_type=None, modifiers=(),
            base_types=(), implemented_types=(), fragment_index=0, fragment_count=1,
        )
        chunked_files.append(ChunkedFile(path, ParsedLanguage.PYTHON, ParseStatus.SUCCESS,
                                         ChunkFileStatus.SUCCESS, digest, (), (chunk,), ()))
    if source_change:
        chunked_files[1] = replace(chunked_files[1], source_sha256="f" * 64)
    if chunk_change:
        chunked_files[1] = replace(chunked_files[1], chunks=(replace(chunked_files[1].chunks[0], content_hash="f" * 64),))
    if missing:
        chunked_files.pop()
    scanned = FileInventory(ROOT, 2, 2, 0, tuple(files), (), (), NAMESPACE)
    parsed = CodeParseInventory(ROOT, 2, 2, 0, 0, 0, tuple(parsed_files), (), NAMESPACE)
    chunks = CodeChunkInventory(ROOT, NAMESPACE, 2 if not missing else 1, len(chunked_files), 0, 0,
                                len(chunked_files), tuple(chunked_files))
    return scanned, parsed, chunks


def to_index(inputs):
    docs = []
    for f in inputs.files:
        for chunk in f.chunks:
            docs.append(LexicalDocument(NAMESPACE, chunk.chunk_id, chunk.content_hash,
                        f.relative_path, f.language.value, chunk.kind.value,
                        chunk.symbol_kind.value if chunk.symbol_kind else None,
                        chunk.qualified_name, chunk.parent_qualified_name, chunk.content))
    return LexicalIndex(NAMESPACE, tuple(docs))


def test_binding_to_complete_manifest():
    files, parsed, chunks = build_evidence()
    proof = build_graph_revision(files, parsed, chunks)
    assert type(proof) is GraphRevision
    assert len(proof.source_files) == 2
    proof.validate(to_index(chunks))
    assert len(proof.graph.nodes) >= 2


def test_same_sources_permuted_manifest_is_same_revision():
    evidence = build_evidence()
    proof = build_graph_revision(*evidence)
    index = to_index(evidence[2])
    other = LexicalIndex(NAMESPACE, tuple(reversed(index.documents)))
    proof.validate(other)


@pytest.mark.parametrize("mode", ["source", "content", "missing"])
def test_mismatched_construction_input_rejected(mode):
    kwargs = {"source_change": mode == "source", "chunk_change": mode == "content", "missing": mode == "missing"}
    with pytest.raises(InvalidHybridEvidence):
        build_graph_revision(*build_evidence(**kwargs))


@pytest.mark.parametrize("change", [
    {"content_hash": "e" * 64},
    {"chunk_id": "e" * 64},
    {"relative_path": "src/stale.py"},
    {"qualified_name": "stale.name"},
    {"content": "stale content"},
])
def test_stale_published_chunk_fails_closed(change):
    data = build_evidence()
    proof = build_graph_revision(*data)
    index = to_index(data[2])
    damaged = LexicalIndex(NAMESPACE, (replace(index.documents[0], **change), *index.documents[1:]))
    with pytest.raises(InvalidHybridEvidence):
        proof.validate(damaged)


def test_foreign_namespace_rejected():
    data = build_evidence()
    proof = build_graph_revision(*data)
    foreign = LexicalIndex("different", tuple(replace(doc, repository_namespace="different") for doc in to_index(data[2]).documents))
    with pytest.raises(InvalidHybridEvidence):
        proof.validate(foreign)


def test_revision_record_is_immutable_and_does_not_leak_source():
    proof = build_graph_revision(*build_evidence())
    with pytest.raises(AttributeError):
        proof.manifest_fingerprint = "f" * 64
    assert "def beta" not in repr(proof)
    assert "def alpha" not in repr(proof)


def test_fingerprint_stable_and_bounded():
    assert _fingerprint((("a", "b"), ("c", "d"))) == _fingerprint((("c", "d"), ("a", "b")))


def test_bad_bound_graph_not_used_by_live_retriever():
    data = build_evidence()
    proof = build_graph_revision(*data)
    store = FakeStore((record("a", "some other source"),))
    provider = FakeProvider()
    retriever = HybridRetriever(provider, store)
    with pytest.raises(InvalidHybridEvidence, match="revision evidence"):
        retriever.search_with_graph(NAMESPACE, "some query", 2, proof)
    assert provider.queries == []
    assert store.lease_exits == 1 and not store.lease_open


def test_explicit_invalid_graph_bound_rejected_before_collaborators():
    store = FakeStore()
    provider = FakeProvider()
    retriever = HybridRetriever(provider, store)
    for graph in (None, object(), "invalid"):
        with pytest.raises(InvalidRetrievalQuery):
            retriever.search_with_graph(NAMESPACE, "query", 1, graph)
    assert store.calls == []


@pytest.mark.parametrize("size", [0, 101, -1, True, 2.5])
def test_invalid_graph_results_limit_rejected(size):
    graph = build_graph_revision(*build_evidence())
    store = FakeStore()
    retriever = HybridRetriever(FakeProvider(), store)
    with pytest.raises(InvalidRetrievalQuery):
        retriever.search_with_graph(NAMESPACE, "query", 1, graph, graph_top_k=size)
    assert store.calls == []


def test_same_revision_search_return_shape_and_release():
    scanned, parsed, chunks = build_evidence()
    proof = build_graph_revision(scanned, parsed, chunks)
    sources = tuple(record("a" if i == 0 else "b", chunk.content,
                           chunk_id=chunk.chunk_id,
                           content_hash=chunk.content_hash,
                           relative_path=f.relative_path,
                           qualified_name=chunk.qualified_name,
                           embedding_identity=FakeProvider().identity)
                    for i, f in enumerate(chunks.files) for chunk in f.chunks)
    store = FakeStore(sources)
    store.search_results = tuple(result(r) for r in sources)
    provider = FakeProvider()
    out = HybridRetriever(provider, store).search_with_graph(NAMESPACE, "alpha", 2, proof)
    assert type(out) is GraphRetrievalResult
    assert out.hits
    assert store.lease_exits == 1 and not store.lease_open


def same_file_call_bundle():
    """Use real Module 6 graph builder with a resolved local Python call."""
    a = "def alpha():\n    return beta()\n"
    b = "def beta():\n    return 42\n"
    text = a + b
    encoded = text.encode("utf-8")
    path = "src/module.py"
    loc_a = SourceLocation(0, len(a.encode()), 1, 0, 3, 0)
    start_b = len(a.encode())
    loc_b = SourceLocation(start_b, len(encoded), 3, 0, 5, 0)
    loc_call = SourceLocation(24, 28, 2, 11, 2, 15)
    parsed = ParsedFile(
        path, ParsedLanguage.PYTHON, ParseStatus.SUCCESS,
        (
            SymbolInfo("alpha", SymbolKind.FUNCTION, "alpha", None, loc_a, (), None, (), (), ()),
            SymbolInfo("beta", SymbolKind.FUNCTION, "beta", None, loc_b, (), None, (), (), ()),
        ), (), (CallSite("alpha", "beta", CallKind.CALL, loc_call),), (), sha256(encoded).hexdigest(),
    )
    scanned = FileInventory(
        ROOT, 1, 1, 0,
        (ScannedFile(path, "module.py", ".py", "python", FileCategory.SOURCE, len(encoded)),),
        (), (), NAMESPACE,
    )
    parser = CodeParseInventory(ROOT, 1, 1, 0, 0, 0, (parsed,), (), NAMESPACE)
    chunks = tuple(
        CodeChunk(
            chunk_id=sha256(name.encode()).hexdigest(), kind=ChunkKind.SYMBOL,
            location=location, content=snippet,
            content_hash=sha256(snippet.encode()).hexdigest(), symbol_kind=SymbolKind.FUNCTION,
            symbol_name=name, qualified_name=name, parent_qualified_name=None,
            parameters=(), return_type=None, modifiers=(), base_types=(),
            implemented_types=(), fragment_index=0, fragment_count=1,
        )
        for name, snippet, location in (("alpha", a, loc_a), ("beta", b, loc_b))
    )
    chunked = CodeChunkInventory(ROOT, NAMESPACE, 1, 1, 0, 0, 2,
        (ChunkedFile(path, ParsedLanguage.PYTHON, ParseStatus.SUCCESS, ChunkFileStatus.SUCCESS,
                     sha256(encoded).hexdigest(), (), chunks, ()),))
    return scanned, parser, chunked


def test_resolved_call_contributes_trusted_context_inside_lease():
    inventories = same_file_call_bundle()
    proof = build_graph_revision(*inventories)
    from backend.code_graph.models import GraphEdgeKind
    assert any(edge.edge_kind is GraphEdgeKind.CALLS for edge in proof.graph.edges)

    sources = tuple(record("a" if i == 0 else "b", chunk.content,
                           chunk_id=chunk.chunk_id, content_hash=chunk.content_hash,
                           relative_path="src/module.py", qualified_name=chunk.qualified_name)
                    for i, chunk in enumerate(inventories[2].files[0].chunks))
    store = FakeStore(sources)
    store.search_results = (result(sources[0]),)
    provider = FakeProvider()
    answer = HybridRetriever(provider, store).search_with_graph(NAMESPACE, "alpha", 1, proof)
    assert answer.hits[0].chunk_id == sources[0].chunk_id
    assert [context.document.chunk_id for context in answer.context] == [sources[1].chunk_id]
    assert answer.context[0].document.content == sources[1].content
    assert store.lease_exits == 1 and not store.lease_open


@pytest.mark.parametrize("start_delta", [1, -1])
def test_non_contiguous_file_chunks_cannot_attest_original_source(start_delta):
    scanned, parsed, chunks = build_evidence()
    file = chunks.files[0]
    original = file.chunks[0]
    altered_location = replace(original.location, start_byte=original.location.start_byte + start_delta)
    altered_file = replace(file, chunks=(replace(original, location=altered_location),))
    changed = replace(chunks, files=(altered_file, *chunks.files[1:]))
    with pytest.raises(InvalidHybridEvidence):
        build_graph_revision(scanned, parsed, changed)


def test_matching_claimed_file_digests_cannot_substitute_for_actual_source_bytes():
    scanned, parsed, chunks = build_evidence()
    fake_digest = "a" * 64
    changed_parsed = replace(parsed, files=(replace(parsed.files[0], source_sha256=fake_digest), *parsed.files[1:]))
    changed_chunks = replace(chunks, files=(replace(chunks.files[0], source_sha256=fake_digest), *chunks.files[1:]))
    with pytest.raises(InvalidHybridEvidence):
        build_graph_revision(scanned, changed_parsed, changed_chunks)


def test_missing_whitespace_from_published_chunks_fails_closed():
    scanned, parsed, chunks = same_file_call_bundle()
    piece = chunks.files[0].chunks[1]
    changed_piece = replace(piece, location=replace(piece.location,
                                                    start_byte=piece.location.start_byte + 1))
    changed_file = replace(chunks.files[0], chunks=(chunks.files[0].chunks[0], changed_piece))
    with pytest.raises(InvalidHybridEvidence):
        build_graph_revision(scanned, parsed, replace(chunks, files=(changed_file,)))


def test_regular_hybrid_search_requires_no_graph_inventory():
    store = FakeStore()
    provider = FakeProvider()
    hits = HybridRetriever(provider, store).search(NAMESPACE, "alpha", 1)
    assert hits and store.lease_exits == 1
