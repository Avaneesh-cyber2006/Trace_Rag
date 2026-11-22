"""Increment 9: runtime evidence does not change pinned retrieval results."""

import pytest

from backend.retrieval import (
    HybridRetriever, InvalidHybridEvidence, InvalidRetrievalQuery,
    RelevancePolicy, RuntimeRetrievalResult, build_graph_revision,
)
from backend.runtime_evidence import RuntimeArtifact, RuntimeArtifactFormat, RuntimeEvidenceRequest, RuntimeFrameLanguage, ingest_runtime_evidence
from test_hybrid_orchestrator import NAMESPACE, FakeProvider, FakeStore, record, result
from test_graph_provenance import same_file_call_bundle
from test_git_history_models import _change, _inventory


def runtime(path="src/a.py", *, namespace=NAMESPACE):
    artifact = RuntimeArtifact("error.log", RuntimeArtifactFormat.STACK_TRACE_TEXT,
        (f'Traceback (most recent call last):\n  File "{path}", line 15, in run\n'
         'ValueError: PRIVATE RUNTIME SECRET').encode(), RuntimeFrameLanguage.PYTHON)
    return ingest_runtime_evidence(RuntimeEvidenceRequest(namespace, (artifact,)))


def test_pinned_runtime_one_lease_same_rank_and_no_context_promotion():
    row = record()
    provider, store = FakeProvider(), FakeStore((row,))
    actual = HybridRetriever(provider, store).search_with_runtime(NAMESPACE, "alpha", 1, runtime())
    assert isinstance(actual, RuntimeRetrievalResult)
    assert actual.hits[0].chunk_id == row.chunk_id
    assert actual.runtime.paths[0].total_matching_frames == 1
    assert actual.runtime.source_revision_verified is False
    assert actual.runtime.causal_relationship_verified is False
    assert actual.graph_context == () and actual.history is None
    assert store.calls == ["acquire_active", "read_manifest", "search", "release_active"]
    assert provider.queries == ["alpha"]


def test_relevance_applies_before_runtime_path_context():
    a, b = record("a", "cache"), record("b", "cache invoice payment")
    store = FakeStore((a, b))
    store.search_results = (result(a), result(b))
    output = HybridRetriever(FakeProvider(), store).search_with_runtime(
        NAMESPACE, "cache invoice payment", 1, runtime("src/b.py"),
        relevance_policy=RelevancePolicy(min_query_matches=2),
    )
    assert [h.relative_path for h in output.hits] == ["src/b.py"]
    assert [p.relative_path for p in output.runtime.paths] == ["src/b.py"]
    assert output.runtime.paths[0].total_matching_frames == 1


def test_graph_and_history_optional_remain_separate_and_checked():
    bundle = same_file_call_bundle()
    graph = build_graph_revision(*bundle)
    rows = tuple(record("a" if i == 0 else "b", chunk.content,
                        chunk_id=chunk.chunk_id, content_hash=chunk.content_hash,
                        relative_path="src/module.py", qualified_name=chunk.qualified_name)
                 for i, chunk in enumerate(bundle[2].files[0].chunks))
    provider, store = FakeProvider(), FakeStore(rows)
    store.search_results = (result(rows[0]),)
    history = _inventory(repository_namespace=NAMESPACE, changes=(_change(new_path="src/module.py"),))
    output = HybridRetriever(provider, store).search_with_runtime(
        NAMESPACE, "alpha", 1, runtime("src/module.py"),
        graph_revision=graph, git_history=history,
    )
    assert len(output.hits) == 1 and len(output.graph_context) == 1
    assert output.runtime.paths[0].total_matching_frames == 1
    assert output.runtime.source_revision_verified is False
    assert output.history.source_revision_verified is False
    assert output.history.paths[0].total_matching_changes == 1
    assert store.lease_exits == 1


def test_mismatch_runtime_rejected_before_embedding_or_lease():
    provider, store = FakeProvider(), FakeStore()
    with pytest.raises(InvalidHybridEvidence):
        HybridRetriever(provider, store).search_with_runtime(
            NAMESPACE, "alpha", 1, runtime(namespace="not-same"),
        )
    assert provider.queries == [] and store.calls == []


@pytest.mark.parametrize("kwargs", [
    {"runtime_top_k": 0}, {"runtime_top_k": 21}, {"runtime_top_k": True},
    {"relevance_policy": object()}, {"graph_revision": object()},
    {"graph_top_k": 0}, {"graph_top_k": 101},
    {"git_history": object()},
])
def test_invalid_option_rejected_without_lease(kwargs):
    provider, store = FakeProvider(), FakeStore()
    with pytest.raises((InvalidHybridEvidence, InvalidRetrievalQuery)):
        HybridRetriever(provider, store).search_with_runtime(
            NAMESPACE, "alpha", 1, runtime(), **kwargs
        )
    assert provider.queries == [] and store.calls == []


def test_empty_index_does_not_embed_and_remains_unprovenanced():
    provider, store = FakeProvider(), FakeStore(())
    output = HybridRetriever(provider, store).search_with_runtime(NAMESPACE, "alpha", 2, runtime())
    assert output.hits == () and output.runtime.paths == ()
    assert output.runtime.source_revision_verified is False
    assert provider.queries == [] and store.lease_exits == 1


def test_stale_graph_still_fails_before_embedding_and_cannot_be_overridden_by_runtime():
    provider, store = FakeProvider(), FakeStore()
    proof = build_graph_revision(*same_file_call_bundle())
    with pytest.raises(InvalidHybridEvidence):
        HybridRetriever(provider, store).search_with_runtime(
            NAMESPACE, "alpha", 1, runtime(), graph_revision=proof
        )
    assert provider.queries == [] and store.lease_exits == 1
