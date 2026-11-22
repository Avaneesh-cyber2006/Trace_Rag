"""Final Module 9 regression: one pinned source and separate unverified clues."""

from dataclasses import replace
from backend.embedding_vector_store.stores.base import SourceRevisionToken

import pytest

from backend.embedding_vector_store.exceptions import InvalidSearchRequest
from backend.retrieval import (
    HybridRetriever, HistoryAnnotator, InvalidHybridEvidence,
    RelevancePolicy, build_graph_revision,
)
from test_git_history_models import _change, _commit, _inventory
from test_hybrid_orchestrator import NAMESPACE, FakeProvider, FakeStore
from test_runtime_orchestrator import runtime
from test_source_revision_persistence import with_omitted_suffix, make_store
from test_graph_provenance import same_file_call_bundle
from backend.embedding_vector_store.source_revision import build_source_revision


def _history(path="src/module.py"):
    return _inventory(repository_namespace=NAMESPACE, changes=(_change(new_path=path),))


def test_complete_combination_uses_one_source_lease_and_keeps_clues_separate():
    inventories = with_omitted_suffix(same_file_call_bundle())
    proof = build_graph_revision(*inventories, require_published_source=True)
    store = make_store(inventories[2])
    store.snapshot = replace(store.snapshot, _token=SourceRevisionToken(store.snapshot._token, build_source_revision(*inventories).encode()))
    store.active_state = replace(store.active_state, snapshot=store.snapshot)
    provider = FakeProvider()
    retriever = HybridRetriever(provider, store)
    policy = RelevancePolicy(min_query_matches=1)

    baseline = retriever.search_with_graph_relevant(
        NAMESPACE, "alpha", 1, proof, policy,
    )
    first_lease_exits = store.lease_exits
    combined = retriever.search_with_runtime(
        NAMESPACE, "alpha", 1, runtime("src/module.py"),
        relevance_policy=policy, graph_revision=proof,
        git_history=_history(),
    )

    assert combined.hits == baseline.hits
    assert combined.graph_context == baseline.context
    assert len(combined.graph_context) == 1
    assert store.lease_exits == first_lease_exits + 1 == 2
    assert provider.queries == ["alpha", "alpha"]
    assert [path.relative_path for path in combined.runtime.paths] == ["src/module.py"]
    assert combined.runtime.paths[0].total_matching_frames == 1
    assert combined.history is not None
    assert combined.history.paths[0].total_matching_changes == 1
    assert combined.runtime.source_revision_verified is False
    assert combined.runtime.causal_relationship_verified is False
    assert combined.history.source_revision_verified is False
    assert "PRIVATE RUNTIME SECRET" not in repr(combined)


def test_stale_graph_still_fails_closed_with_both_annotation_streams():
    old = with_omitted_suffix(same_file_call_bundle(), "\n  \n")
    newer = with_omitted_suffix(same_file_call_bundle(), "  \n\n")
    proof = build_graph_revision(*old, require_published_source=True)
    store = make_store(newer[2])
    store.snapshot = replace(store.snapshot, _token=SourceRevisionToken(store.snapshot._token, build_source_revision(*newer).encode()))
    store.active_state = replace(store.active_state, snapshot=store.snapshot)
    provider = FakeProvider()

    with pytest.raises(InvalidHybridEvidence):
        HybridRetriever(provider, store).search_with_runtime(
            NAMESPACE, "alpha", 1, runtime("src/module.py"),
            graph_revision=proof, git_history=_history(),
        )
    assert provider.queries == []
    assert store.lease_exits == 1


def test_duplicate_commit_is_rejected_before_embedding_or_lease_history_only():
    invalid = _inventory(repository_namespace=NAMESPACE, commits=(_commit(), _commit()))
    provider, store = FakeProvider(), FakeStore()
    with pytest.raises(InvalidHybridEvidence, match="Git history evidence is invalid"):
        HybridRetriever(provider, store).search_with_history(
            NAMESPACE, "alpha", 1, invalid,
        )
    assert provider.queries == []
    assert store.calls == []


def test_duplicate_commit_is_rejected_before_embedding_or_lease_combined():
    invalid = _inventory(repository_namespace=NAMESPACE, commits=(_commit(), _commit()))
    provider, store = FakeProvider(), FakeStore()
    with pytest.raises(InvalidHybridEvidence):
        HybridRetriever(provider, store).search_with_runtime(
            NAMESPACE, "alpha", 1, runtime(), git_history=invalid,
        )
    assert provider.queries == []
    assert store.calls == []


def test_history_tampered_change_rejected_before_lease():
    invalid = _history("src/a.py")
    original = invalid.changes[0]
    # Demonstrate rejection of a forged mutated frozen Module 7 record, not
    # just a duplicate in an otherwise valid primary commit list.
    object.__setattr__(original, "commit_id", "f" * 40)
    provider, store = FakeProvider(), FakeStore()
    with pytest.raises(InvalidHybridEvidence):
        HybridRetriever(provider, store).search_with_history(
            NAMESPACE, "alpha", 1, invalid,
        )
    assert provider.queries == [] and store.calls == []


def test_malformed_history_does_not_leak_raw_values_in_exception():
    invalid = _history()
    bad = invalid.changes[0]
    object.__setattr__(bad, "change_type", "PRIVATE_TOKEN")
    with pytest.raises(InvalidHybridEvidence) as captured:
        HistoryAnnotator(NAMESPACE, invalid)
    assert "PRIVATE_TOKEN" not in str(captured.value)


def test_history_rejects_invalid_query_before_scanning_untrusted_inventory():
    # A bad request must not force a scan of even a malformed inventory.
    malformed = _inventory(repository_namespace=NAMESPACE, commits=(_commit(), _commit()))
    provider, store = FakeProvider(), FakeStore()
    with pytest.raises(InvalidSearchRequest):
        HybridRetriever(provider, store).search_with_history(
            NAMESPACE, "", 1, malformed,
        )
    assert provider.queries == [] and store.calls == []
