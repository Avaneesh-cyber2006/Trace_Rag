"""Offline Module 10 Increment 1 contract and trust-boundary regression tests."""

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256

import pytest

from backend.causal_reasoner import (
    EvidenceBundle, EvidenceKind, EvidencePolicy,
    InvalidEvidenceBundle, assemble_evidence,
)
from backend.code_graph.models import GraphEdgeKind
from backend.retrieval.graph import GraphContext
from backend.retrieval.history import HistoricalChange, HistoricalContext, PathHistory
from backend.retrieval.hybrid import HybridHit
from backend.retrieval.lexical import LexicalDocument
from backend.retrieval.runtime import RuntimeContext, RuntimeFrameClue, RuntimePathContext
from backend.runtime_evidence.models import (
    FrameAssociation, PathMatchBasis, RuntimeEventKind, RuntimeFrameLanguage,
)

NS = "trace-rag/repo"


def hit(path="src/a.py", text="def alpha(): return 1", rank=1):
    return HybridHit(
        repository_namespace=NS,
        chunk_id=sha256((path + text).encode()).hexdigest(),
        content_hash=sha256(text.encode()).hexdigest(),
        relative_path=path,
        language="python",
        chunk_kind="symbol",
        symbol_kind="function",
        qualified_name="alpha",
        parent_qualified_name=None,
        content=text,
        score=0.01,
        rank=rank,
        lexical_rank=rank,
        semantic_rank=rank,
    )


def doc(path="src/b.py", text="def beta(): return alpha()"):
    return LexicalDocument(
        repository_namespace=NS,
        chunk_id=sha256((path + text).encode()).hexdigest(),
        content_hash=sha256(text.encode()).hexdigest(),
        relative_path=path,
        language="python",
        chunk_kind="symbol",
        symbol_kind="function",
        qualified_name="beta",
        parent_qualified_name=None,
        content=text,
    )


def graph(seed):
    return (GraphContext(
        document=doc(), seed_chunk_id=seed.chunk_id,
        edge_id="a" * 64, edge_kind=GraphEdgeKind.CALLS,
        direction="outgoing", rank=1,
    ),)


def history(path="src/a.py"):
    return HistoricalContext(
        paths=(PathHistory(
            relative_path=path,
            changes=(HistoricalChange("a" * 40, "b" * 64, "modified", None, 100),),
            total_matching_changes=2,
        ),),
        scope_revision="a" * 40,
        history_is_complete=False,
        source_revision_verified=False,
    )


def runtime(path="src/a.py"):
    return RuntimeContext(
        paths=(RuntimePathContext(
            relative_path=path,
            frames=(RuntimeFrameClue(
                artifact_id="a" * 64,
                event_id="b" * 64,
                frame_id="c" * 64,
                event_kind=RuntimeEventKind.ERROR,
                language=RuntimeFrameLanguage.PYTHON,
                line=20,
                column=None,
                path_match_basis=PathMatchBasis.EXACT_RELATIVE_PATH,
                association=FrameAssociation.FILE,
            ),),
            total_matching_frames=2,
        ),),
        ingestion_complete=False,
        source_revision_verified=False,
        causal_relationship_verified=False,
    )


def build(hits=None, **kwargs):
    return assemble_evidence(NS, "why alpha?", (hit(),) if hits is None else hits, **kwargs)


def test_primary_sources_are_stable_ranked_and_exactly_identity_bound():
    result = build((hit(), hit("src/b.py", "a different body", 2)))
    assert [item.label for item in result.items] == ["S1", "S2"]
    assert all(item.kind is EvidenceKind.INDEXED_SOURCE for item in result.items)
    assert result.resolve("S2").content == "a different body"
    assert result.resolve("S2").relative_path == "src/b.py"
    assert result.resolve("S9") is None
    assert result.resolve(1) is None
    assert result.omissions == ()


def test_bundle_cannot_be_marked_safe_for_provider_and_reprs_hide_source():
    secret = "PRIVATE_API_KEY_XYZ123"
    result = build((hit(text=secret),))
    assert result.provider_safe is False
    assert secret not in repr(result)
    assert secret not in repr(result.items[0])
    assert NS not in repr(result)
    with pytest.raises(FrozenInstanceError):
        result.items = ()
    with pytest.raises((AttributeError, FrozenInstanceError, TypeError)):
        result.provider_safe = True
    assert type(result.items) is tuple


def test_graph_cites_source_and_edge_without_renaming_primary_rank():
    first = hit()
    result = build((first,), graph_context=graph(first))
    assert [item.label for item in result.items] == ["S1", "S2", "G1"]
    edge = result.resolve("G1")
    assert edge.kind is EvidenceKind.REVISION_BOUND_GRAPH
    assert edge.source_label == "S2"
    assert dict(edge.details)["seed"] == "S1"
    assert dict(edge.details)["edge_kind"] == "calls"
    assert edge.source_revision_verified is True
    assert result.resolve("S2").chunk_id == doc().chunk_id
    assert result.resolve("S1").chunk_id == first.chunk_id


def test_history_and_runtime_are_independent_unverified_clues():
    result = build(history=history(), runtime=runtime())
    assert [item.label for item in result.items] == ["S1", "H1", "R1"]
    assert result.resolve("H1").source_label == "S1"
    assert result.resolve("R1").source_label == "S1"
    assert result.resolve("H1").source_revision_verified is False
    assert result.resolve("R1").source_revision_verified is False
    assert "reported_line" in dict(result.resolve("R1").details)
    assert "history_incomplete" in result.omissions
    assert "runtime_incomplete" in result.omissions
    assert "history_truncated" in result.omissions
    assert "runtime_truncated" in result.omissions
    assert result.history_is_complete is False
    assert result.runtime_ingestion_complete is False


def test_graph_source_and_edge_budget_is_atomic():
    first = hit()
    # Primary S1 fits, but the two graph records do not fit together.
    result = build((first,), graph_context=graph(first),
                   policy=EvidencePolicy(max_items=2))
    assert [item.label for item in result.items] == ["S1"]
    assert "item_limit" in result.omissions


def test_source_limit_never_reassigns_graph_seed():
    first = hit()
    second = hit("src/c.py", "def second(): pass", 2)
    connection = (replace(graph(second)[0], seed_chunk_id=second.chunk_id),)
    result = build((first, second), graph_context=connection,
                   policy=EvidencePolicy(max_primary_sources=1))
    assert [item.label for item in result.items] == ["S1"]
    assert "primary_source_limit" in result.omissions
    assert "unselected_graph_seed" in result.omissions


def test_per_path_limits_are_deterministic_and_zero_allowed():
    result = build(history=history(), runtime=runtime(),
                   policy=EvidencePolicy(max_history_per_path=0, max_runtime_per_path=0))
    assert [item.label for item in result.items] == ["S1"]
    assert "history_truncated" in result.omissions
    assert "runtime_truncated" in result.omissions
    assert result == build(history=history(), runtime=runtime(),
                           policy=EvidencePolicy(max_history_per_path=0, max_runtime_per_path=0))


def test_byte_limit_drops_whole_items_not_partial_source_text():
    result = build(policy=EvidencePolicy(max_evidence_bytes=50))
    assert result.items == ()
    assert result.omissions == ("byte_budget",)
    assert result.resolve("S1") is None


@pytest.mark.parametrize("bad", [
    ("", "why?"), (NS, ""), (NS, "x" * 4097), ("namespace\x00bad", "why?"),
    (NS, "why\ud800"),
])
def test_invalid_question_or_namespace_is_rejected(bad):
    with pytest.raises(InvalidEvidenceBundle):
        assemble_evidence(*bad, (hit(),))


@pytest.mark.parametrize("bad", [
    lambda value: replace(value, content_hash="0" * 64),
    lambda value: replace(value, repository_namespace="other"),
    lambda value: replace(value, rank=3),
    lambda value: replace(value, relative_path="../escape.py"),
    lambda value: replace(value, content="modified without hash"),
])
def test_tampered_hit_rejected(bad):
    with pytest.raises(InvalidEvidenceBundle):
        build((bad(hit()),))


def test_duplicate_chunk_ids_and_rank_gaps_fail_closed():
    with pytest.raises(InvalidEvidenceBundle):
        build((hit(), replace(hit(), rank=2)))
    with pytest.raises(InvalidEvidenceBundle):
        build((hit(), hit("src/b.py", "other", 3)))


def test_graph_reference_requires_exact_source_and_known_seed():
    first = hit()
    with pytest.raises(InvalidEvidenceBundle):
        build((first,), graph_context=(replace(graph(first)[0], seed_chunk_id="f"*64),))
    with pytest.raises(InvalidEvidenceBundle):
        build((first,), graph_context=(replace(graph(first)[0], document=replace(doc(), content="changed")),))
    with pytest.raises(InvalidEvidenceBundle):
        build((), graph_context=graph(first))


def test_unverified_clues_cannot_upgrade_revision_trust():
    with pytest.raises(InvalidEvidenceBundle):
        build(history=replace(history(), source_revision_verified=True))
    with pytest.raises(InvalidEvidenceBundle):
        build(runtime=replace(runtime(), source_revision_verified=True))
    with pytest.raises(InvalidEvidenceBundle):
        build(runtime=replace(runtime(), causal_relationship_verified=True))


def test_invalid_path_context_cannot_attach_to_unrelated_retrieved_path():
    with pytest.raises(InvalidEvidenceBundle):
        build(history=history("src/unrelated.py"))
    with pytest.raises(InvalidEvidenceBundle):
        build(runtime=runtime("src/unrelated.py"))


def test_invalid_policy_rejected_without_bools_as_integers():
    for params in ({"max_items": True}, {"max_primary_sources": 0},
                   {"max_evidence_bytes": 0}, {"max_graph_relationships": 101}):
        with pytest.raises(InvalidEvidenceBundle):
            EvidencePolicy(**params)


def test_empty_hits_return_no_invented_context():
    result = build(())
    assert isinstance(result, EvidenceBundle)
    assert result.items == ()
    assert result.omissions == ()
